# Requirements And Evidence

Last verified: 2026-11-19 against the builder's scripts and the component versions in `references/standalone-runtime.md`.

## Traceability

Assign stable IDs such as `REQ-001`. Record one row per independently testable behavior:

| Requirement | Tyk capability | Asset | Test ID | Status | Evidence |
| --- | --- | --- | --- | --- | --- |

Use only these statuses:

- `live`: a current command, response, log, metric, trace, or focused screenshot proves it.
- `talk-track`: the design is valid but intentionally not implemented locally.
- `blocked`: the intended live proof cannot run because an input or prerequisite is missing.

Do not treat generated configuration as evidence that runtime behavior works.

## Proof Shape

For every live path include:

- one successful request
- relevant rejection paths
- upstream reflection when routing, transformation, or header behavior is claimed
- exact status, headers, and JSON assertions for errors
- Dashboard readback for imported OAS and policies
- browser evidence for Dashboard or Portal UI claims

Store machine results in `evidence/results.json` and the readable summary in `notes/validation.md`. `verify-demo.sh` rewrites only the block between the `BEGIN GENERATED RESULTS` and `END GENERATED RESULTS` markers in `notes/validation.md` (`scripts/render-validation.py`), so exact commands, responses and source citations written around it survive every run; a note without the markers keeps its text and gains the block at the end.

`scripts/update-requirements.py` updates rows whose ID starts with `REQ-` or with the prefix in `demo.config.json` `requirement_id_prefix` (for example `"ACME"` for `ACME-014`), so set it when the rows are renumbered with your own requirement IDs. Curated evidence in a row is never dropped: only an earlier machine segment and the generator's `Pending verification` placeholder are replaced.
A row is `live` only when every test ID it lists has a passing result in the current run; when any
did not run, a `live` row (including one left over from an earlier run) becomes `blocked`, a curated
`talk-track` or `blocked` stays, and the evidence cell names the tests that did not run. The
machine segment names the results file and its run (the UTC time `verify-demo.sh` wrote it); IDs
`verify-demo.sh` does not record, such as `AISTUDIO-*`, are read from the companion stack's own
`companion/<name>/evidence/results.json`.

Every bundle gets `HEALTH-001` (component health endpoints and the admin API refusing a caller without the node secret; see `references/observability.md`), with its requirement row directly under the `rest` row.

## Proof Object Hygiene

Repeated runs of a proof script are the normal case, so every object a proof creates is found by a stable key and updated, never duplicated:

- Policies: a fixed custom `id` plus `meta_data.stable_name`; look the policy up by the stable name, `PUT` when it exists, `POST` otherwise.
- Users and groups: a fixed e-mail address per role (`readonly@<slug>.invalid`, or your own domain for accounts you own), never a random suffix per run; delete and recreate only when the credential must rotate.
- Rulesets, categories, filters, routers and applications: look up by name; rename in place through the API rather than creating a sibling.
- Before the final screenshot pass, filter each list view to this bundle's objects. Remove only
  obsolete objects that carry this bundle's ownership marker (for example a superseded name); never
  delete anything the bundle does not own, even when it clutters the view.

Names follow the **Visible Naming** rule in the skill: internal markers live in tags and metadata, not in what a list page shows.

## Screenshot Capture

A capture is a claim that a page showed something. Scripts and agents must:

1. Log in once, then navigate and check the URL (and a page-specific text marker) before every
   save; a URL ending in `/login` or a redirect back to the overview means the capture failed.
2. Never fall back to saving the current screen when a link is not found. Report "no link for
   X" and exit non-zero so the missing image is noticed.
3. Look up ids through the API (for example an MCP proxy's Dashboard id) instead of guessing
   routes.
4. Write into `evidence/screenshots/` with stable file names, and recapture the whole set after
   any rename.
5. For the Portal, register each capture with
   `scripts/record-screenshot.sh <name> <portal-path> <marker>`; `PORTAL-UI-001` counts only
   registered captures whose file, Portal, organisation and brand kit are unchanged (see
   `references/portal-and-dcr.md`).

## Rehearsal Gate

Run the presenter sequence from a fresh terminal:

1. Start (`start-demo-stack.sh` prints what it starts and the ports it binds) and wait for health.
2. Bootstrap and load runtime state.
3. Configure twice.
4. Display credentials only through `scripts/show-credentials.sh`.
5. Run positive and negative calls (`verify-demo.sh`).
6. Show Dashboard configuration and analytics.
7. Show Portal or observability views when selected.
8. Restart and rerun core tests (`rehearse-demo.sh` does 3, 5 and 8).

Proofs that run outside `verify-demo.sh` have their own runners, and the requirement rows wait
for their evidence:

| Module | Runner | Evidence `verify-demo.sh` or `update-requirements.py` reads |
| --- | --- | --- |
| `tyk-sync`, `cicd` | `./ci/tyk-sync-local.sh promote apis` (also run by `rehearse-demo.sh`) | `.runtime/tyk-sync/latest-promotion.json` |
| `operator` | `./operator/install-operator-kind.sh`, `source operator/operator-env.sh`, `./operator/apply-operator.sh` | `evidence/operator-reconciliation.json` |
| `governance` with `operator` | `./operator/verify-governance.sh` | `evidence/operator-governance.json` |
| `ai-studio` | `companion/ai-studio/start.sh`, `configure-ai-studio.sh`, `verify-ai-studio.sh` | `companion/ai-studio/evidence/results.json` |
| `portal-branding` | browser captures plus `scripts/record-screenshot.sh` | `evidence/screenshots/manifest.json` |

A bundle is complete when `verify-demo.sh` has run after every selected runner and no requested
row is `failed`; rows a runner has not proved stay `blocked` with the test ids that did not run.

Record commands that require real external credentials as blocked until supplied.
