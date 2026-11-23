# What Tyk API governance is in 5.16

Last verified: 2026-11-19 against Tyk Dashboard 5.16.0, Tyk Gateway 5.16.0, Tyk Operator 1.6.0

Public pages: https://tyk.io/docs/tyk-governance/overview,
https://tyk.io/docs/tyk-governance/core-concepts,
https://tyk.io/docs/tyk-governance/scoping-rulesets.

## The model in one paragraph

Governance runs inside the Tyk Dashboard; it is not a separate service and the Gateways take no
part in it. A **ruleset** is a Spectral-format document (JSON or YAML) stored in the Dashboard.
A ruleset is linked to one or more **API categories**; every Tyk OAS API that carries one of
those categories is evaluated against every **active** ruleset linked to it. Each finding has a
severity. An API is **non-compliant** when any matched ruleset reports at least one `error`;
`warn`, `info` and `hint` findings never change compliance. The ruleset's **action** (`none` or
`warn`) only decides whether the Dashboard UI shows a warning dialog when someone saves the
API. Nothing in the Dashboard or its REST API refuses a save. A hard stop exists in two places
only: your CI pipeline (exit code) and the Tyk Operator admission webhook (`action: block`).

## The engine

The docs name Vacuum, an open source Go linter that reads the Spectral ruleset format, as the
engine (https://tyk.io/docs/tyk-governance/core-concepts). On Dashboard 5.16.0 the rule IDs
and severities the Dashboard reports match the open source `vacuum` CLI v0.26.7 on every spec
and ruleset in `ci-gates.md`; vacuum v0.41.0 and the Spectral CLI do not.

## What is evaluated

| Object | Evaluated on 5.16.0? |
| --- | --- |
| A Tyk OAS API with a matching category | Yes: the OpenAPI document **and** the `x-tyk-api-gateway` extension. A rule with `given: "$['x-tyk-api-gateway'].server"` reports `x-tyk-api-gateway.server.authentication is missing.` |
| The same API with `state.active: false` | Yes: an inactive API with errors is `non_compliant` |
| A Tyk Classic API carrying the category | No. Compliance stays `not_evaluated`; the Dashboard log says `has no OAS spec, skipping evaluation` |
| MCP proxies | Outside this skill. Tyk documents governance for Tyk OAS APIs only: the release notes say Tyk Governance "supports Tyk OAS APIs only" (https://tyk.io/docs/developer-support/release-notes/dashboard), and the CI page lists no MCP support for the sandbox (https://tyk.io/docs/tyk-governance/cicd-checks) |

**The Dashboard evaluates its stored copy, not your file.** On every save it rewrites
`servers`: it puts the Gateway URL (built from the Dashboard's Gateway host setting) first and,
unless the API has a custom domain, adds a relative entry such as `/orders-legacy`. Your own
entries follow. Rules on `servers` (the OWASP HTTPS rule is the common one) therefore see
entries you never wrote. Details and the fix: `owasp-and-security-rules.md`.

## Severities and compliance

- Severities: `error`, `warn`, `info`, `hint`. A rule without `severity` is `warn` (the
  sandbox reports `warn` for such a rule).
- `GET /api/governance/apis/{api_id}/compliance` returns `overall_status`
  (`compliant`, `non_compliant` or `not_evaluated`) and, per ruleset, `error_count`,
  `warn_count`, `info_count`, `hint_count` and `compliance_level` (0 to 100).
- `action` does not affect compliance. With every matching ruleset set to `action: none`, an API
  with errors stays `non_compliant`.

## When evaluation runs

Evaluation runs on: creating or editing a ruleset (every API in its categories is evaluated),
changing an API's categories, and saving an API. Small APIs settle in one to three seconds.

- Right after an API save, `compliance` answers `{"overall_status":"not_evaluated","rulesets":[]}`
  for about one to three seconds, then the real result. Poll until `overall_status` is not
  `not_evaluated` before you read it.
- Right after a ruleset is **created**, the compliance and violation lists for some APIs can show
  the same ruleset and the same violation twice (two evaluations run at once). The next save of
  the API clears it. Count distinct `(ruleset_id, rule_id, path)` when you assert on counts.

## Scoping through categories

- Categories live beside the API definition, not inside it: `PUT /api/apis/oas/{api_id}/categories`.
  Re-importing or `PUT`-ing the OAS document does not carry them.
- A ruleset may only name categories that some API already carries. Otherwise:
  `400 {"Status":"Error","Message":"categories [example-payments] are not valid org API categories","Meta":null}`.
  Assign the category to an API first, then create the ruleset.
- A ruleset created without `"active": true` is stored inactive and evaluates nothing (see
  `dashboard-api.md`).
- Category names match exactly; a misspelt category in a ruleset or a CI call matches nothing.

## Who may do what (RBAC)

On Dashboard 5.16.0, for users created with only the permissions shown:

| User permissions | `GET /api/rulesets` | `POST /api/rulesets` | sandbox, compliance, evaluation details |
| --- | --- | --- | --- |
| `apis: read` | 403 | 403 | 200 |
| `apis: write` | 403 | 403 | 200 |
| `apis: read`, `rulesets: read` | 200 | 403 | 200 |
| `apis: read`, `rulesets: write` | 200 | 201 | 200 |
| `rulesets: write` only | 200 | 201 | 403 |

`rulesets: deny` is refused at user creation (`invalid user permission level set for rulesets`);
leave `rulesets` out to deny it. The CI page asks for `apis: write` for the sandbox token
(https://tyk.io/docs/tyk-governance/cicd-checks); on 5.16.0 `apis: read` is enough. Start with read; if
the sandbox answers 403, grant what the page asks for.

## Licensing

Governance needs a Dashboard licence with the `governance` scope. The public
wording: "Tyk Governance is delivered as a license claim toggle on Tyk Dashboard. It is disabled
by default and enabled per organization on request. It is not self-service." and "Without the
governance scope on your license, every governance endpoint returns `403 Forbidden`"
(https://tyk.io/docs/tyk-governance/overview). The release
notes call it "a new Enterprise Edition capability"; Enterprise Edition there means the
licensed software, not a package (packages: the `tyk-facts` skill). Check it on a stack in one
call: listing the templates (`GET /api/rulesets?is_template=true`) returns 200 with two
templates when the scope is present. Without it the Operator admits every API and records `skipped`
(`operator-admission.md`). Everything else about licences, packages and trials belongs to the
`tyk-facts` skill.
