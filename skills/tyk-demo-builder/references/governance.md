# Governance Module

Last verified: 2026-11-19 against Tyk Dashboard and Gateway 5.16.0 and Tyk Operator 1.6.0

This file covers the bundle's `governance` module only: what it generates, how to run it, what
its proofs assert, and the traps the module works around. For how Tyk API governance works
(rulesets, templates, the evaluation engine, the governance API), use the `tyk-api-governance`
skill; for Operator product behaviour, the `tyk-gitops-sync-operator` skill; for licensing,
the `tyk-facts` skill. Public documentation: https://tyk.io/docs/tyk-governance/overview.

## Requirements

- Dashboard and Gateway `v5.15.0` or later. The generator refuses an explicit lower
  `--dashboard-version` or `--gateway-version`; the latest-stable defaults satisfy it.
- A Dashboard licence whose scope carries the `governance` claim. The public overview page
  says the claim is "disabled by default and enabled per organization on request", is not
  self-service, and that without it every governance endpoint returns `403`. The module then
  records `GOVERNANCE_LICENSED=false` and `GOV-001` is reported `blocked`, never `failed`.
- For `GOV-003`: the `operator` module as well, a disposable kind cluster, and Tyk Operator
  `v1.5.0` or later (the release notes list the `TykRuleset` CRD and its admission webhook as
  added in 1.5.0: https://tyk.io/docs/developer-support/release-notes/operator). The
  generator default is the latest release. Governance adds no
  container: it is part of the Dashboard.

## What It Generates

- `apis/<slug>-governed-legacy.oas.json`: the "before" API. An `http://` server, no security,
  no `operationId`, no `info.description`, an unbounded integer path parameter.
- `apis/<slug>-governed-standard.oas.json`: the "after" API. An `https://` server, an API-key
  scheme wired into `x-tyk-api-gateway.server.authentication`, `operationId` on every
  operation, `info.description`, `X-RateLimit-Limit` response headers and
  `x-tyk-api-gateway.upstream.rateLimit`.
- `governance/assign-categories.sh`: merges the `<slug>-payments` category into whatever each
  governed API already carries, through `GET`/`PUT /api/apis/oas/{id}/categories`, and reads
  it back. `configure-governance.sh` and `scripts/import-apis.sh` both call it, because a
  newly created API carries no category.
- `governance/configure-governance.sh` (run by `scripts/configure-demo.sh`): probes the
  licence, assigns the category, then creates or updates two rulesets matched by name, both
  scoped to `<slug>-payments`:
  - `<Name> OWASP Baseline`: `action: warn`, extends `spectral:owasp` with
    `owasp-security-hosts-https-oas3` lowered to `warn` (see Traps).
  - `<Name> Gateway Standards`: `action: none`, two error-severity rules on the Tyk extension,
    `upstream-rate-limit-required` and `authentication-required`, each with `howToFix` text.
- `governance/governance-check.sh`: the CI gate (below).
- `governance/github/governance-check.yml`: a pull-request workflow that runs the gate with
  the `TYK_DASHBOARD_URL` and `TYK_DASHBOARD_TOKEN` repository secrets.
- With `operator` also selected, in `operator/`: `tyk-ruleset.yaml` (a `TykRuleset` with
  `action: block` and one self-contained rule, `operation-operationid-required`),
  `governed-api-compliant.yaml` and `governed-api-noncompliant.yaml` (a ConfigMap plus a
  `TykOasApiDefinition` each, both in the `<slug>-gitops` category), and
  `verify-governance.sh`.

## Run It

```bash
./scripts/start-demo-stack.sh
./scripts/bootstrap-demo-stack.sh
./scripts/configure-demo.sh          # idempotent: a second run leaves the same two rulesets
./scripts/verify-demo.sh             # GOV-001, GOV-002; GOV-003 blocked until the next block
# Operator hard block (operator module selected):
./operator/install-operator-kind.sh  # disposable kind cluster; your kubectl context is unchanged
source operator/operator-env.sh
./operator/apply-operator.sh         # OPERATOR-001
./operator/verify-governance.sh      # GOV-003
./scripts/verify-demo.sh
```

On Dashboard and Gateway 5.16.0 with Operator 1.6.0 (chart `tyk-operator` 1.6.0, cert-manager
v1.15.3) this sequence passes every row (`GOV-001`, `GOV-002`, `GOV-003`, `OPERATOR-001`), and
a second `apply-operator.sh` plus `verify-governance.sh` passes again in seconds.

`apply-operator.sh` renders every `operator/*.yaml` into `.runtime/operator/` with the target
namespace substituted but applies only the reconciliation resources. Always apply the rendered
copies, and run kubectl by hand only through `./operator/kubectl.sh`, which targets the kind
cluster through `.runtime/operator-kubeconfig`; a bare `kubectl` reaches your own current
context.

## Proofs

| Id | Asserts | Result on 19 Nov 2026 |
| --- | --- | --- |
| `GOV-001` | `GET /api/governance/apis/<slug>-governed-legacy/compliance` is `non_compliant` with at least one ruleset reporting errors, every violation from `GET /api/governance/evaluation-details/<slug>-governed-legacy?p=-1` has `how_to_fix` text, and the standard API is `compliant`. `blocked` when the licence lacks the claim. | passed: legacy 8 errors (OWASP Baseline) and 2 (Gateway Standards); standard 0 errors, 5 warnings |
| `GOV-002` | `governance-check.sh` exits `2` for the legacy spec, `0` or `1` for the standard spec, and `3` for an unknown category. | passed: `legacy=2 standard=1 unknown-category=3` |
| `GOV-003` | From `evidence/operator-governance.json`: the block `TykRuleset` synced, the compliant twin was admitted with `status.governancePreflight.status: passed`, and the non-compliant twin was denied naming `operation-operationid-required` and never saved. `verify-demo.sh` accepts the file only for this stack's `ORG_ID` and while the ruleset still answers `200` from the Dashboard. | passed |

The denial `verify-governance.sh` captured:

```text
Error from server (Forbidden): error when creating ".runtime/operator/governed-api-noncompliant.yaml": admission webhook "vtykoasapidefinition.kb.io" denied the request: governance preflight blocked this API (1 error(s)):
  - operation-operationid-required (paths./orders/{id}.operationId): Operation has no operationId.
Fix the violations or relax the ruleset action; the full violation list is available on the Dashboard.
```

## CI Gate

`governance-check.sh <spec> (--categories a,b | --ruleset-ids a,b) [--api-id id] [--api-name name] [--strictness strict|standard|permissive] [--honor-action]`
posts the raw OAS document to the Dashboard sandbox (`POST /api/evaluations/sandbox/oas`),
which stores nothing. Exit codes:

| Exit | Meaning |
| --- | --- |
| `0` | compliant: no error and no warning findings |
| `1` | warnings only |
| `2` | non-compliant: at least one error-severity finding |
| `3` | could not evaluate: usage error, missing spec, missing tool or environment value, HTTP failure (an unknown `--ruleset-ids` value answers `404`), unparsable body, `.error` set, `truncated: true`, no matched ruleset, or no gating ruleset |

Treat `3` as a failure: a spec that was not evaluated was never shown to be compliant.
`--strictness` is applied by the script, never sent to the Dashboard: `strict` (the script's
default) reports the raw contract, `standard` turns `1` into `0`, `permissive` turns any
evaluated result into `0`. With the bundled specs: legacy `strict` 2, `standard` 2, `permissive` 0; standard
`strict` 1, `standard` 0.

By default every matched ruleset counts, as in the Dashboard. `--honor-action` ignores
`action: none` rulesets, as the reference script on
https://tyk.io/docs/tyk-governance/cicd-checks does; the legacy spec then reports 8 errors
instead of 10, because the Gateway Standards ruleset is `action: none`.

Token: on 5.16.0 a Dashboard user with only `apis: read` gets `200` from the sandbox. The
cicd-checks page asks for
`apis: write`; give the CI user what the page asks for if your Dashboard refuses `read`.

## Traps The Module Handles

- **An unknown category passes silently.** The sandbox answers `200` with `"rulesets": []` and
  zero findings, so the gate exits `3` when nothing matched, and `GOV-002` proves it.
- **Severity decides compliance, `action` does not.** A ruleset with `action: none` and one
  failing error rule turns the compliant standard API `non_compliant` within two seconds, and
  deleting it restores `compliant`
  (https://tyk.io/docs/tyk-governance/core-concepts: "A service is non-compliant when at least
  one rule is failing at Error severity"). That is why the gate counts every matched ruleset
  by default.
- **The Dashboard adds server URLs on save.** The standard spec declares one `https://` server;
  the stored copy also holds `http://tyk-gateway:8080/...`, the bare listen path and the
  gateway's published URL. On an http demo gateway `owasp-security-hosts-https-oas3` would
  then always fail, so the baseline lowers that one rule to `warn`. Drop the override when the
  gateway is served over https. The sandbox lints the file as written, so its counts can
  differ from the Dashboard's for this rule.
- **Compliance follows every save.** Removing `x-tyk-api-gateway.upstream.rateLimit` from the
  standard API by `PUT` turns it `non_compliant` within a second, and restoring it turns it
  back, so a live "break it, fix it" beat works without waiting.
- **A REST save is never stopped.** `PUT /api/apis/oas/<legacy id>` of the non-compliant API
  answers `200` `{"Status":"OK","Message":"Api updated","Meta":null}` with the warn-action
  baseline active. Dashboard enforcement is advisory; the CI exit code and the Operator
  admission webhook are the gates.
- **A ruleset needs its category to exist.** `POST /api/rulesets` with a category no API
  carries answers `400` `categories [<name>] are not valid org API categories`. The module
  assigns categories before creating rulesets, and `verify-governance.sh` applies the
  compliant twin (which brings `<slug>-gitops` into the organisation) before the `TykRuleset`.
- **Categories live outside the OAS document.** A `PUT` of the OAS body leaves them as they
  were; a newly created API has none, hence `assign-categories.sh` after every import.
- **The Operator preflight sees only `TykRuleset` resources.** A Dashboard-created ruleset on
  `<slug>-gitops` makes the compliant twin `non_compliant` in the Dashboard while its preflight
  says `passed`. The module therefore keeps the Operator ruleset on its own
  `<slug>-gitops` category, apart from the Dashboard rulesets on `<slug>-payments`.
- **`action: block` exists only in the cluster.** The synced ruleset reads `action: warn` in
  the Dashboard.
- **Editing the referenced ConfigMap skips admission.** Removing `operationId` from the
  compliant twin's ConfigMap is accepted, the preflight becomes `blocked`, and the change still
  reaches the Dashboard. A spec change through the CR itself (pointing it at a non-compliant
  ConfigMap) is denied. Review ConfigMaps like the CRs and keep the CI gate in the pipeline.
- **An unreachable Dashboard admits.** Chart 1.6.0 sets `webhooks.governanceFailurePolicy:
  Ignore`, and the installed `vtykoasapidefinition.kb.io` webhook has `failurePolicy: Ignore`,
  so applies are admitted when the preflight cannot run. `verify-governance.sh` fails rather
  than passes in that case, because it requires the denial.
- The generated rules use literal messages and self-contained rules: on 5.16.0 a message of
  `"Probe {{description}} literal?"` comes back verbatim, and `extends: [["spectral:oas",
  "off"]]` plus a one-rule override finds 0 errors in the legacy spec. The
  `tyk-api-governance` skill covers ruleset authoring.

## Presenting It

Each step below runs through the API or the terminal; show the
matching Dashboard screens (Governance, the API list, the API's Governance tab) alongside.

1. The two rulesets on `<slug>-payments`: `GET /api/rulesets` lists `<Name> OWASP Baseline`
   (`action: warn`) and `<Name> Gateway Standards` (`action: none`).
2. Portfolio: `GET /api/apis?include_stats=true` returns `stats` of 4 APIs, 1 compliant,
   1 non-compliant and 2 unassessed (the two APIs outside the category).
3. The legacy API: `GET /api/governance/evaluation-details/<slug>-governed-legacy?p=-1` lists
   each violation with `rule_id`, `severity`, `path` and `how_to_fix`; the standard API's
   compliance is `compliant`.
4. Terminal: `./governance/governance-check.sh` on both specs and on `--categories no-such`
   (exits `2`, `1`, `3`).
5. Cluster: `./operator/kubectl.sh get tykruleset -A` shows `ACTION block`;
   `./operator/kubectl.sh apply -f .runtime/operator/governed-api-noncompliant.yaml` is denied
   (its ConfigMap is still created, so delete it afterwards with
   `./operator/kubectl.sh delete configmap <slug>-governed-block-bad-oas`).

## Limits

- No hard block in the Dashboard or its REST API; the bundle's gates are the CI exit code and,
  with `operator`, admission of `TykOasApiDefinition` create and update.
- `GOV-003` needs a Kubernetes cluster; without one the row stays `blocked`.
- The governed APIs and rulesets cover Tyk OAS APIs only.
