---
name: tyk-api-governance
description: Set up Tyk API governance on Dashboard 5.16 (rulesets, OWASP and custom Spectral-format rules on the OpenAPI document and the Tyk extension, categories, compliance results) and enforce it where a bad API can be stopped: a CI gate and the Tyk Operator admission webhook. Use when asked about API governance, API linting, rulesets, compliance, OWASP checks, Spectral or vacuum rules, or blocking non-compliant APIs. Not for writing the API definitions, which the tyk-api-definitions-routing skill owns.
---

# Tyk API Governance

Tyk Governance is built into the Tyk Dashboard. Rulesets in the Spectral format are linked to
API categories, and every Tyk OAS API in a linked category is linted, both its OpenAPI document
and its `x-tyk-api-gateway` extension. An `error` finding makes the API non-compliant. The
Dashboard never refuses a save: its strongest response is a dialog the user can click past. Hard
enforcement lives in two places this skill sets up, a CI gate and the Tyk Operator's admission
webhook. Everything here describes Dashboard and Gateway 5.16.0 and Operator 1.6.0; the
references carry the commands and their responses.

## What you need

- A Tyk Dashboard 5.16 whose licence has the `governance` scope. The vendor describes
  it as "disabled by default and enabled per organization on request" and "not self-service"
  (https://tyk.io/docs/tyk-governance/overview). Step 1 checks it; anything else about licences
  goes to the `tyk-facts` skill.
- Tyk OAS APIs (Tyk documents governance for them only; this skill does not cover MCP proxies).
- For the hard block in Kubernetes: Tyk Operator 1.6.0 with its webhooks, installed as the
  `tyk-gitops-sync-operator` skill describes.
- No stack yet: the `tyk-demo-builder` skill builds one (module `governance`).

## Clarify first

Ask only what the request leaves open. Defaults in brackets.

1. Which APIs are governed, and how are they grouped? [one category per exposure or domain,
   for example `payments`; every governed API carries it]
2. Which standards? [a copy of the OWASP template plus `assets/rulesets/tyk-gateway-baseline.json`]
3. Should owners see a warning when they save a non-compliant API in the Dashboard? [yes:
   `action: warn`]
4. Where must a non-compliant API be stopped? [pull requests, with the CI gate; also
   `kubectl apply` if APIs are delivered by Tyk Operator]
5. Are APIs delivered through Kubernetes with Tyk Operator? [no]
6. Is the Dashboard reachable from CI? [yes; if not, run only the offline lint there]

## Workflow

Set these once. `<skill-dir>` is this skill's folder.

```bash
export DASH=http://localhost:3000                 # the Dashboard
export DASHBOARD_API_KEY=<Dashboard user API key> # admin for set-up
G=<skill-dir>/scripts/governance_check.py
```

The script also reads `TYK_DASHBOARD_URL` and `TYK_DASHBOARD_TOKEN` (the public CI page's
names, used in CI), which win when set. The examples use two APIs, `orders` and
`orders-legacy`, both carrying the category.

1. **Check the licence.**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' "$DASH/api/rulesets?is_template=true" \
     -H "Authorization: $DASHBOARD_API_KEY"
   ```

   200 (and two templates, `spectral-owasp` and `spectral-oas`, in the body) means governance is
   on. 403 means the licence lacks the `governance` scope: stop and hand over to `tyk-facts`.
   Without it the Operator does not block either: it admits every API and records `skipped`.

2. **Put the category on the APIs first.** A ruleset may name only categories that an API
   already carries (`400 categories [x] are not valid org API categories`). `PUT` replaces the
   list, so merge with what `GET` returns.

   ```bash
   curl -s -X PUT "$DASH/api/apis/oas/orders/categories" -H "Authorization: $DASHBOARD_API_KEY" \
     -H 'Content-Type: application/json' -d '{"categories":["payments"]}'
   # {"Status":"OK","Message":"Api categories updated","Meta":null}
   ```

   The path uses the API's `x-tyk-api-gateway.info.id`. Re-importing the OAS document does not
   carry categories; set them again after any tool that replaces the API.

3. **Create the rulesets.** `apply` sends every field (a `PUT` that omits `active` switches a
   ruleset off), then reads it back:

   ```bash
   python3 $G apply --ruleset-id owasp-baseline --name "OWASP baseline" \
     --rules <skill-dir>/assets/rulesets/owasp-baseline.json --categories payments --action warn
   # created ruleset owasp-baseline: active=True action=warn categories=['payments']
   python3 $G apply --ruleset-id tyk-gateway-baseline --name "Tyk gateway baseline" \
     --rules <skill-dir>/assets/rulesets/tyk-gateway-baseline.json --categories payments --action warn
   ```

   Custom rules: `references/writing-rules.md`. Try a draft first with `--inactive` and a
   sandbox call by ID (step 6).

4. **Read the results.** Evaluation runs in the background on ruleset changes, category
   changes and API saves. Poll until `overall_status` is not `not_evaluated`:

   ```bash
   curl -s "$DASH/api/governance/apis/orders-legacy/compliance" -H "Authorization: $DASHBOARD_API_KEY"
   # {"overall_status":"non_compliant","rulesets":[{"ruleset_id":"owasp-baseline",...,"error_count":4,"warn_count":7,...}, ...]}
   curl -s "$DASH/api/governance/evaluation-details/orders-legacy?p=-1" -H "Authorization: $DASHBOARD_API_KEY"
   ```

   Each violation has `rule_id`, `severity`, `message`, `path` and `how_to_fix`. Portfolio:
   `GET /api/apis?include_stats=true&compliance_status=non_compliant`. All endpoints and bodies:
   `references/dashboard-api.md`.

5. **Fix the HTTPS false positive before anyone reads the numbers.** The Dashboard rewrites
   `servers` in its stored copy (Gateway URL first, plus a relative entry), so the OWASP HTTPS
   rule fails on entries the owner never wrote: `assets/orders.oas.json`, clean in the sandbox,
   is `non_compliant` once saved. The default fix: re-apply the Dashboard ruleset with the rule
   at `warn` (after which that API reads `compliant`) and keep it at `error` in CI (step 6):

   ```bash
   python3 $G apply --ruleset-id owasp-baseline --name "OWASP baseline" \
     --rules <skill-dir>/assets/rulesets/owasp-dashboard.json --categories payments --action warn
   ```

   Custom domains plus `TYK_DB_HOSTCONFIG_GENERATEHTTPS=true` also clear it, but add a custom
   domain only for routing: the API then answers only on that host (`owasp-and-security-rules.md`).

6. **Gate pull requests.** The sandbox evaluates a file without saving it; the script turns
   the answer into an exit code and fails when no ruleset matched:

   ```bash
   python3 $G check apis/orders-legacy.json --categories payments --api-id orders-legacy
   # Result: non-compliant (6 error(s) in gating rulesets).   -> exit 2
   ```

   Exit 0 pass, 1 warnings with `--strictness strict`, 2 errors, 3 could not evaluate. A
   category gate with the HTTPS rule at `warn` passes an `http://` server; a second call with
   `--ruleset-ids spectral-owasp` fails it (exit 0, then exit 2).
   `assets/github-actions/api-governance.yml` makes both calls per file, plus an offline vacuum
   job. CI token: the CI page asks for `apis: write`; `apis: read` is enough on 5.16.0, so
   start with read and grant write if the sandbox answers 403. More: `references/ci-gates.md`.

7. **Hard-stop Kubernetes delivery** (only with Tyk Operator, and only with the `governance`
   scope from step 1). Apply an API carrying the category, then a `TykRuleset` with
   `action: block`, then try the non-compliant API:

   ```bash
   kubectl create namespace tyk
   kubectl apply -f <skill-dir>/assets/operator/api-compliant.yaml
   kubectl apply -f <skill-dir>/assets/operator/tykruleset-block.yaml
   kubectl apply -f <skill-dir>/assets/operator/api-noncompliant.yaml
   # Error from server (Forbidden): ... admission webhook "vtykoasapidefinition.kb.io" denied the request:
   #   governance preflight blocked this API (1 error(s)): ...
   ```

   Decide `webhooks.governanceFailurePolicy` (`Ignore` admits everything when the webhook is
   down). Details and gaps: `references/operator-admission.md`.

8. **Prove it.** Run the verification checklist below and show the output.

## References

| File | Read it for |
| --- | --- |
| `references/concepts.md` | The model, what is and is not evaluated, severities, triggers, categories, RBAC, licence wording |
| `references/dashboard-api.md` | Every governance endpoint with real requests, responses and rejections |
| `references/writing-rules.md` | Rule shape, `extends` and overrides, rules on `x-tyk-api-gateway`, paths that silently pass |
| `references/owasp-and-security-rules.md` | The OWASP template's rules, what they cannot see, the stored-`servers` problem |
| `references/warn-and-block.md` | What the UI, REST, CI and Operator each do with a non-compliant API |
| `references/ci-gates.md` | The sandbox gate, offline vacuum lint, the linter comparison, the workflow |
| `references/operator-admission.md` | `TykRuleset`, the admission webhook, outcomes, failure policy, bypasses |
| `scripts/governance_check.py` | `check` (CI gate), `export` (rulesets to files), `apply` (create or replace and verify) |
| `assets/` | Three rulesets (OWASP, OWASP with the HTTPS rule at `warn`, Tyk gateway baseline), a spec that passes them, the GitHub Actions workflow, Operator manifests |

## Pitfalls

- **An unknown or misspelt category in a sandbox call returns 200 with no rulesets and zero
  errors.** A gate that reads only `error_count` passes for ever. `check` exits 3.
- **`action` is not enforcement.** `warn` only adds a UI dialog; REST saves answer a plain 200.
  With every ruleset at `none`, the UI dialog said "This API is in compliance" for an API that
  had 8 errors. Read compliance from the API, not from the dialog.
- **A ruleset created without `active: true` is inactive; a `PUT` without it deactivates.**
- **The Dashboard lints its stored copy.** Injected `servers` entries make CI and Dashboard
  results differ for `servers` rules.
- **Results lag a save by one to three seconds** (`not_evaluated` with no rulesets), and right
  after a ruleset is created a violation can be listed twice. Poll; count distinct findings.
- **A rule whose `given` points at a missing block reports nothing.** Point `given` at a node
  that always exists and use a dotted `field` (`writing-rules.md`).
- **Object-form overrides are rejected** (`operation-operationId: {severity: error}`); use
  `operation-operationId: error`. **`[["spectral:oas","off"]]` plus an override evaluates
  nothing. `{{description}}` in a message is printed literally.**
- **A typo in a function name is accepted** and makes every governed API non-compliant.
- **Operator: editing the referenced ConfigMap bypasses admission**, rulesets created in the
  Dashboard are invisible to the webhook, and the default failure policy fails open.

## Limitations

- No hard block in the Dashboard or its REST API; `action` is `none` or `warn` there.
- Tyk Classic, GraphQL and TCP APIs are not evaluated. MCP proxies are outside this skill.
- Rules see the document only. They cannot check live Gateway behaviour, keys or policies.
- Sandbox limits: 20 categories or ruleset IDs, 10 `api_ids` on `/api/evaluations/sandbox`
  (`400 api_ids exceeds the maximum of 10`), 1,000 listed issues (`truncated: true`, docs).
- The Spectral CLI cannot run the rulesets as the Dashboard does (it rejects `howToFix` and
  cannot resolve `spectral:owasp`); vacuum v0.26.7 matched in every comparison made, newer
  vacuum releases did not.

## Verification checklist

- [ ] `GET /api/rulesets?is_template=true` answers 200.
- [ ] Every ruleset reads back with `active: true`, the intended `action` and `categories`.
- [ ] Every governed API's `GET .../categories` lists the category.
- [ ] One known-bad API is `non_compliant` with the expected `rule_id`s; one known-good API is
      `compliant` (polled past `not_evaluated`).
- [ ] The OWASP HTTPS rule result was checked against the stored `servers`.
- [ ] `check` exits 2 on a bad file, 0 on a good one, and 3 on a misspelt category.
- [ ] Offline lint and the sandbox agree on rule IDs and severities for the repository's files.
- [ ] With Operator: a non-compliant `TykOasApiDefinition` is denied, the compliant one shows
      `PREFLIGHTSTATUS passed` (`skipped` means not governed), and the failure policy is stated.
- [ ] No token or licence value appears in Git, a command line or the output.

## Hand-offs

- Writing or fixing the Tyk OAS definitions themselves: the `tyk-api-definitions-routing` skill.
- Installing Tyk Operator and delivering APIs through Tyk Sync, Operator or Git: the
  `tyk-gitops-sync-operator` skill.
- Licences, packages, versions: the `tyk-facts` skill. A stack to try this on: the
  `tyk-demo-builder` skill. Authentication schemes the rules ask for: the `tyk-authentication`
  skill. Rate limits and quotas: the `tyk-rate-limits-keys-policies` skill.
