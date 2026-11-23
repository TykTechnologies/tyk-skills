# Neutral API Governance Assets

API Governance is embedded in the Dashboard from 5.15.0. There is no extra container: the
bundle configures it through the Dashboard API after the APIs are imported.

## Requirements

- Dashboard and Gateway `v5.15.0` or later. The bundle refuses to generate below that.
- A Dashboard licence whose scope carries the `governance` claim. The public overview page
  (https://tyk.io/docs/tyk-governance/overview) says it is disabled by default and enabled
  per organisation on request, not self-service. Without it every governance endpoint
  answers `403`, `configure-governance.sh` records `GOVERNANCE_LICENSED=false`,
  and verification reports `GOV-001` as `blocked` rather than failed.

## What Gets Created

`scripts/configure-demo.sh` calls `governance/configure-governance.sh`, which is
idempotent and:

1. Runs `governance/assign-categories.sh`, which merges the `__SLUG__-payments` category
   into whatever each governed API already carries through
   `GET`/`PUT /api/apis/oas/{apiId}/categories`, then reads it back. `scripts/import-apis.sh`
   calls the same script after every import, because categories are stored outside the OAS
   document: an API the import has just created carries none, and every ruleset would match
   nothing.
2. Creates or updates two rulesets, matched by name so a second run never duplicates them:
   - **__DISPLAY_NAME__ OWASP Baseline** — `action: warn` (the Deployment Warning toggle),
     extending `spectral:owasp` with `owasp-security-hosts-https-oas3` downgraded to
     `warn`.
   - **__DISPLAY_NAME__ Gateway Standards** — `action: none`, a custom Spectral ruleset
     whose two error-severity rules lint the `x-tyk-api-gateway` extension itself:
     `upstream-rate-limit-required` and `authentication-required`, both carrying
     `howToFix` text.

Both rulesets are scoped to the one category, so exactly the two governed APIs are
evaluated and no other API in the organisation is touched.

## The Two Governed APIs

- `apis/__SLUG__-governed-legacy.oas.json` is deliberately non-compliant: an `http://`
  server entry, no security, no `operationId`, no `info.description`, and an unbounded
  integer path parameter. It is the "before" state.
- `apis/__SLUG__-governed-standard.oas.json` is the "after" state: `https://` server, an
  API-key security scheme wired into `x-tyk-api-gateway.server.authentication`,
  `operationId` on every operation, an `info.description`, bounded `X-RateLimit-Limit`
  response headers, and an `x-tyk-api-gateway.upstream.rateLimit` block.

## Demo Flow

1. **Rulesets** — show the two rulesets, their category scope, the template extension, and
   the Deployment Warning toggle on the OWASP baseline.
2. **Governance tab** — open the legacy API and show violations with severity, path, and
   how-to-fix guidance; show the API-list compliance column, filter, and portfolio stats
   from `GET /api/apis?include_stats=true`.
3. **Fix** — show the standard API as compliant, or edit the legacy spec live and watch the
   status flip. On 5.16.0 an API save and a ruleset create or delete each re-evaluated the
   governed APIs within about two seconds.
4. **CI check** — run `governance/governance-check.sh` against both specs and show the exit
   codes. This is the enforceable gate; see the guardrails below.

## CI Compliance Gate

```bash
export TYK_DASHBOARD_URL=http://localhost:__DASHBOARD_PORT__
export TYK_DASHBOARD_TOKEN=<dashboard user token>

./governance/governance-check.sh apis/__SLUG__-governed-standard.oas.json \
  --categories __SLUG__-payments --api-id ci-standard
./governance/governance-check.sh apis/__SLUG__-governed-legacy.oas.json \
  --categories __SLUG__-payments --api-id ci-legacy
```

Exit codes are `0` compliant, `1` warnings only, `2` non-compliant, and `3` could not
evaluate (usage error, missing spec, missing binary or environment value, HTTP failure,
unparsable body, `.error` set, `truncated: true`, zero matched rulesets, or zero gating
rulesets). Treat `3` as a failure: a specification that could not be evaluated has not been
shown to be compliant.

By default every matched ruleset counts, which is what the Dashboard does. `--strictness`
maps the result onto the exit status locally and is never sent to the Dashboard: `strict`
(the default) reports the raw contract, `standard` treats warnings as a pass, `permissive`
passes anything that evaluated.

Copy `governance/github/governance-check.yml` to `.github/workflows/` to run it on pull
requests; it needs the `TYK_DASHBOARD_URL` and `TYK_DASHBOARD_TOKEN` repository secrets.

## Guardrails

- **An unknown category passes silently.** The sandbox returns `200` with `rulesets: []`
  and zero findings for a category that does not exist, so a naive gate never fails again.
  `governance-check.sh` therefore exits `3` (could not evaluate) when no ruleset matched,
  and the bundle proves that path in `GOV-002`. It is `3` rather than `2` because nothing
  was checked: the specification was not judged non-compliant, it was never judged at all.
- **Only `error` severity makes an API non-compliant**, and `action` is never consulted:
  the Dashboard marks an API non-compliant when any matched ruleset reports an error, so
  `action` is purely the Deployment Warning toggle. `warn` and `info` are advisory, so a
  warn-only run exits `1` rather than `2`.
- **The CI gate and the Dashboard can disagree if you let them.** The public docs' reference
  script skips `action: none` rulesets, so it can pass a specification the Dashboard marks
  non-compliant. `governance-check.sh` gates on all matched rulesets by default and offers
  `--honor-action` for parity with that script; under `--honor-action` a run where every
  matched ruleset is advisory exits `3` rather than reporting a pass it never checked.
- **A ruleset needs its category to exist.** Creating a ruleset whose category no API
  carries answers `400` with `categories [...] are not valid org API categories`, so the
  categories are assigned before the rulesets are written.
- **The Dashboard rewrites `servers` on every save.** It injects the gateway's own URL,
  which on an http demo gateway makes `owasp-security-hosts-https-oas3` an unfixable error
  and makes CI-sandbox numbers diverge from Dashboard numbers. The baseline overrides that
  one rule to `warn`; drop the override when the gateway is fronted by https.
- **The Deployment Warning is UI-only.** A REST `PUT` of a non-compliant API returns a
  plain `200` with no warning payload. Dashboard enforcement is advisory; the proven hard
  gate is the CI exit code.
- If the OWASP baseline reports extra error-severity findings against the standard API in
  your environment, add per-rule severity overrides to the ruleset `definition` in
  `configure-governance.sh` rather than weakening the spec.

## Operator Hard Block (Optional)

When the `operator` module is also selected the bundle writes four extra files into
`operator/`:

- `tyk-ruleset.yaml` — a `TykRuleset` with `action: block`, the only hard block that exists.
  Its one custom rule, `operation-operationid-required`, fails any operation without an
  `operationId`.
- `governed-api-compliant.yaml` and `governed-api-noncompliant.yaml` — a ConfigMap plus a
  `TykOasApiDefinition` each, both in the `__SLUG__-gitops` category; only the compliant
  one declares an `operationId`. The Operator plane has its own category, separate from the
  `__SLUG__-payments` category of the Dashboard rulesets, so each plane governs its own APIs
  and the preflight (which sees only `TykRuleset` CRs) never contradicts the Dashboard view.
- `verify-governance.sh` — the `GOV-003` proof.

```bash
./operator/install-operator-kind.sh   # disposable kind cluster, cert-manager, tyk-operator
source operator/operator-env.sh
./operator/apply-operator.sh
./operator/verify-governance.sh
```

`apply-operator.sh` renders every `operator/*.yaml` into `.runtime/operator/` with the
target namespace substituted, but applies only the upstream, context, API, and policy.
`verify-governance.sh` then deletes any twins left by an earlier run, applies the compliant
API first and waits for it to sync (it brings the `__SLUG__-gitops` category into the
organisation, and the Dashboard rejects a ruleset whose category no API carries), applies the
rendered ruleset and waits for the resync, waits for the compliant API's
`status.governancePreflight.status: passed`, and applies the non-compliant API expecting a
denial like this:

```text
Error from server (Forbidden): ... admission webhook "vtykoasapidefinition.kb.io" denied the request: governance preflight blocked this API (1 error(s)):
  - operation-operationid-required (<path>): Operation has no operationId.
Fix the violations or relax the ruleset action; ...
```

It requires the denial to name `operation-operationid-required`, confirms the rejected
object was never saved, removes the orphaned ConfigMap so re-runs stay idempotent, and writes
`evidence/operator-governance.json` with `org_id` and `recorded_at`; any abort leaves a failed
"run started but did not complete" marker instead of an earlier pass. `scripts/verify-demo.sh`
records `GOV-003` from that file (blocked until it exists), and the governance requirement
row lists `GOV-003` next to `GOV-001` and `GOV-002`, so the row is not marked `live`
until the hard block is proven too.

Always apply the rendered copies rather than `operator/*.yaml` directly: the sources still
say `namespace: default`, so applying them unrendered puts the objects in the wrong
namespace whenever `TYK_OPERATOR_NAMESPACE` is set.

- Requires `tyk-operator` **v1.5.0 or later**, the release that added `TykRuleset`; the
  hard block was proved on v1.6.0 with Dashboard and Gateway 5.16.0. The generator refuses a
  lower `--operator-version` when `governance` and `operator` are selected together.
- `action: block` is Operator-only and is stored as `warn` on the Dashboard.
- Enforcement is a validating admission webhook on `TykOasApiDefinition` create and update
  only. Editing the referenced OAS ConfigMap bypasses it: the reconciler records
  `status.governancePreflight.status: blocked` and still syncs the non-compliant spec, so
  protect ConfigMaps with the same review as the CRs and keep the CI check in the pipeline.
- The preflight sees only `TykRuleset` CRs. Dashboard-created rulesets are invisible to it,
  so manage each category's rulesets from one plane only.
- After a `TykRuleset` change, `verify-governance.sh` waits for the Operator to record it
  (`status.latestCRDSpecHash` or `status.latestSourceHash` moves) before it tests admission.
- On a fresh organisation, apply a categorised API before the `TykRuleset`; the Dashboard
  rejects a ruleset whose category no API carries.
- The Operator needs its own `TYK_OPERATOR_LICENSEKEY`. `install-operator-kind.sh` takes it
  from `TYK_LICENSE_KEY`; with a Dashboard licence carrying the governance claim the Operator
  logged `License validated successfully`.
- The chart's `webhooks.governanceFailurePolicy` defaults to `Ignore`: if the webhook
  cannot reach the Dashboard, applies are admitted rather than blocked. `verify-governance.sh`
  fails in that case instead of reporting a pass.

## Ruleset Shape

The generated rules use literal messages and self-contained rules. On Dashboard 5.16.0 a rule
message of `{{description}}` came back verbatim rather than interpolated, and
`extends: [["spectral:oas", "off"]]` plus a one-rule severity override found no errors in a
spec that breaks that rule. Write each custom rule out in full.
