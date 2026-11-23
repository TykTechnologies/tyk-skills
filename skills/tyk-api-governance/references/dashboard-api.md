# Dashboard API for governance

Last verified: 2026-11-19 against Tyk Dashboard 5.16.0, Tyk Gateway 5.16.0

Requests and responses are those of Dashboard 5.16.0. Bodies are shortened with `...`; IDs are
examples. Public pages:
https://tyk.io/docs/tyk-governance/rulesets, https://tyk.io/docs/tyk-governance/service-compliance,
https://tyk.io/docs/tyk-governance/cicd-checks.

All calls authenticate with a Dashboard user's API key in the `Authorization` header, either
bare or as `Bearer <key>` (both return 200).

```bash
DASH=http://localhost:3000
DASHBOARD_API_KEY=<Dashboard user API key>
```

## Check the licence

```bash
curl -s "$DASH/api/rulesets?is_template=true" -H "Authorization: $DASHBOARD_API_KEY"
# 200 {"rulesets":[{"ruleset_id":"spectral-owasp","name":"OWASP API Security",...,
#   "definition":"{\"extends\":[[\"spectral:owasp\",\"all\"]]}","is_template":true,...},
#   {"ruleset_id":"spectral-oas","name":"OpenAPI Best Practices",...,
#   "definition":"{\"extends\":[[\"spectral:oas\",\"all\"]]}","is_template":true,...}],"pages":1}
```

Without the `governance` scope on the licence every governance endpoint answers 403
(https://tyk.io/docs/tyk-governance/overview).

## Categories

```bash
curl -s -X PUT "$DASH/api/apis/oas/orders/categories" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/json' -d '{"categories":["example-payments"]}'
# 200 {"Status":"OK","Message":"Api categories updated","Meta":null}
curl -s "$DASH/api/apis/oas/orders/categories" -H "Authorization: $DASHBOARD_API_KEY"
# 200 {"categories":["example-payments"]}
curl -s "$DASH/api/apis/categories" -H "Authorization: $DASHBOARD_API_KEY"
# 200 {"categories":[{"name":"example-payments","count":4},...]}
```

`PUT` replaces the API's list; read it first and merge if the API already has categories. The
same endpoint accepted a category on a Tyk Classic API, but Classic APIs are never evaluated.

## Rulesets

| Call | Result |
| --- | --- |
| `GET /api/rulesets` | `{"rulesets":[...],"pages":1}` |
| `GET /api/rulesets?is_template=true` | the two templates |
| `POST /api/rulesets` | `201 {"Status":"success","Message":"ruleset created","Meta":"<database id>"}` |
| `GET /api/rulesets/{ruleset_id}` | the ruleset (works with your `ruleset_id` or the database id) |
| `PUT /api/rulesets/{ruleset_id}` | 200 and the stored ruleset; **replaces** the record |
| `DELETE /api/rulesets/{ruleset_id}` | `200 {"Status":"success","Message":"ruleset deleted","Meta":null}` |
| `PATCH /api/rulesets/{ruleset_id}` | `404 Not Found`; there is no partial update |

Create body: fields at the top level; `definition` is a **string** holding the JSON or YAML
ruleset.

```bash
cat > house-standards.ruleset.json <<'EOF'
{"ruleset_id": "house-standards",
 "name": "House standards",
 "description": "Example Corp gateway and design rules",
 "active": true,
 "resource_type": "oas-api",
 "action": "warn",
 "categories": ["example-payments"],
 "definition": "{\"rules\":{\"operation-operationId\":{\"description\":\"Every operation needs an operationId.\",\"message\":\"Operation has no operationId.\",\"severity\":\"error\",\"given\":\"$.paths[*][get,put,post,delete,patch]\",\"then\":{\"field\":\"operationId\",\"function\":\"truthy\"},\"howToFix\":\"Add a unique operationId.\"}}}"}
EOF
curl -s -X POST "$DASH/api/rulesets" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/json' --data @house-standards.ruleset.json
# 201 {"Status":"success","Message":"ruleset created","Meta":"6aff02fa0f32b80001812efa"}
```

To keep the rules as a readable file and send them as a string, build the body with a script:
`python3 -c 'import json,sys; b=json.load(open(sys.argv[1])); b["definition"]=open(sys.argv[2]).read(); print(json.dumps(b))' meta.json rules.json`.

Fields and what the Dashboard does with them:

| Field | Behaviour on 5.16.0 |
| --- | --- |
| `ruleset_id` | Optional. Your own stable ID; without it the database id is used. A duplicate gives `409 ruleset with given ruleset_id already exists` |
| `name` | Required, unique in the organisation (`409 ruleset with given name already exists`) |
| `active` | Defaults to `false` when omitted on create, and is set to `false` by a `PUT` that omits it |
| `action` | `none` (default) or `warn`. `block` gives `400 invalid action: must be 'none' or 'warn'` |
| `resource_type` | `oas-api` (default) or `mcp-api`; anything else: `400 invalid resource_type: must be 'oas-api' or 'mcp-api'`. This skill uses only `oas-api`; Tyk documents governance for Tyk OAS APIs only |
| `categories` | Each must already be on some API: `400 categories [x] are not valid org API categories` |
| `definition` | Required string. A JSON object instead of a string, or text that does not parse: `400 invalid request body`. A rule override in the wrong shape: `400 definition is not a valid Spectral ruleset: ...` |

The Dashboard stores `definition` as compact JSON, also when you sent YAML, so a read-back
never equals your file byte for byte; compare the parsed rules.

Other rejections: fields nested under `ruleset` or `metadata` give
`400 name is required`; a `PUT` without `definition` gives `400 definition is required`.

**Update safely: send the whole record.** `scripts/governance_check.py apply` does it (create
when the ID is new, full `PUT` otherwise, then read back and compare). A `PUT` carrying only `name` and
`definition` returns 200 and leaves the ruleset with `active: false` and an empty description.
Read back after every write and check `active`, `action` and `categories`.

## Compliance and findings for saved APIs

```bash
curl -s "$DASH/api/governance/apis/orders-legacy/compliance" -H "Authorization: $DASHBOARD_API_KEY"
# 200 {"overall_status":"non_compliant","rulesets":[{"ruleset_id":"house-standards",
#   "ruleset_name":"House standards","status":"completed","compliance":"non_compliant",
#   "evaluated_at":"2026-11-19T14:09:57.120012Z","error_count":2,"warn_count":0,"info_count":0,
#   "hint_count":0,"compliance_level":0}]}

curl -s "$DASH/api/governance/evaluation-details/orders-legacy?p=-1" -H "Authorization: $DASHBOARD_API_KEY"
# 200 {"pages":0,"violations":[{"api_id":"orders-legacy","ruleset_id":"house-standards",
#   "rule_id":"operation-operationId","rule_summary":"Every operation needs an operationId.",
#   "how_to_fix":"Add a unique operationId.","message":"Operation has no operationId.",
#   "severity":"error","path":"operationId",
#   "location":{"start_line":1,"start_char":118,"end_line":1,"end_char":118},...},...]}
```

- The path segment is the API's `x-tyk-api-gateway.info.id`. The database id gives
  `404 {"Status":"Error","Message":"api not found","Meta":null}`.
- An API with no matching active ruleset answers `{"overall_status":"not_evaluated","rulesets":[]}`.
- `location` refers to the Dashboard's stored copy (one line of minified JSON), and `path` in
  saved results can be shorter than in sandbox results (`operationId` against
  `paths./orders/{id}.operationId`). Use `rule_id`, `message` and `how_to_fix` to locate the fix.

Portfolio and notifications:

```bash
curl -s "$DASH/api/apis?include_stats=true&compliance_status=non_compliant" -H "Authorization: $DASHBOARD_API_KEY"
# 200 {"apis":[{..., "categories":["example-payments"], "compliance_status":"non_compliant", ...}],
#   "stats":{"total_apis":3,"compliant":0,"non_compliant":3,"unassessed":0,"total_issues":42}, ...}
curl -s "$DASH/api/org/notifications" -H "Authorization: $DASHBOARD_API_KEY"
# 200 [{"kind":"critical","topic":"governance.compliance","title":"Governance compliance failure",
#   "message":"API orders-legacy fails governance ruleset 6aff026a...",
#   "data":{"api_id":"...","ruleset_id":"...","status":"failure"}, ...}]
```

Each API row carries `compliance_status`: `compliant`, `non_compliant` or `not_evaluated`
(Classic APIs and APIs without a matching ruleset). With a `compliance_status` filter, `stats`
counts only the filtered rows.

## Evaluate without saving

Two sandbox endpoints; neither stores anything or changes compliance.

```bash
# A file that need not exist in the Dashboard (the CI endpoint)
curl -s -X POST "$DASH/api/evaluations/sandbox/oas?categories=example-payments&api_id=orders-legacy" \
  -H "Authorization: $DASHBOARD_API_KEY" -H 'Content-Type: application/json' --data-binary @orders-legacy.json
# 200 {"rulesets":[{"id":"house-standards","name":"House standards","resource_type":"oas-api",
#   "active":true,"action":"warn","error_count":2,"warn_count":0,"info_count":0,"hint_count":0}],
#   "issues":[{..."rule_id":"operation-operationId","severity":"error",
#   "path":"paths./orders/{id}.operationId","how_to_fix":"Add a unique operationId.",...},...],
#   "total":2,"truncated":false,"error_count":2,"warn_count":0,"info_count":0,"hint_count":0,
#   "evaluated_apis":1,"elapsed_ms":243}

# APIs already saved (what the Test panel uses)
curl -s -X POST "$DASH/api/evaluations/sandbox" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/json' -d '{"categories":["example-payments"],"api_ids":["orders-legacy","orders"]}'
# 200 {"rulesets":[...],"issues":[...],"total":18,"truncated":false,"error_count":9,...,"evaluated_apis":2,...}
```

Sandbox inputs and answers:

| Input | Answer |
| --- | --- |
| `categories=` a category no ruleset uses (or a typo) | **200** `{"rulesets":[],"issues":[],"total":0,...,"error_count":0,...}` |
| `ruleset_ids=nope` | `404 {"Status":"Error","Message":"ruleset nope not found","Meta":null}` |
| neither selector | `400 exactly one of ruleset_ids or categories must be set` |
| 21 IDs | `400 ruleset_ids exceeds the maximum of 20` |
| `ruleset_ids=spectral-owasp` (a template) | 200; templates are reachable by ID |
| YAML body with `Content-Type: application/yaml` | 200 |
| A body that is not JSON or YAML | `400 body is not a valid JSON or YAML document` |
| `{"openapi":"3.0.3"}` | `400 body is not a valid OpenAPI document: (root): info is required ...` |

`ruleset_ids` evaluates a ruleset even when it is inactive or has no categories, which makes it
the way to try a draft. The CI page says category resolution returns only active, non-template
rulesets (https://tyk.io/docs/tyk-governance/cicd-checks).
