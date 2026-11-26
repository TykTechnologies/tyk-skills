Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`) and Tyk Dashboard 5.16.0 (`tykio/tyk-dashboard:v5.16.0`)

# Creating keys and policies

Three ways, and they do not mix: a Gateway that takes its configuration from a Dashboard
refuses policy and API writes on its own API. Requests and responses below are those of
Gateway and Dashboard 5.16.0.

| Deployment | Policies | Keys |
| --- | --- | --- |
| Open source Gateway (no Dashboard) | Gateway API `/tyk/policies`, or policy files | Gateway API `/tyk/keys` |
| Dashboard-managed Gateway | Dashboard API `/api/portal/policies/` | Dashboard API `/api/keys` |
| Git or Kubernetes as the source | Declarative: the `tyk-gitops-sync-operator` skill | The same skill says which tools handle keys |

Public pages: https://tyk.io/docs/tyk-gateway-api, https://tyk.io/docs/tyk-dashboard-api,
https://tyk.io/docs/api-management/policies

## Open source Gateway: the Gateway API

Every call carries the Gateway secret (`secret` in `tyk.conf`, `TYK_GW_SECRET`) in the
`x-tyk-authorization` header. A wrong secret returns HTTP 403
`{"status":"error","message":"Attempted administrative access with invalid or missing key!"}`.

```bash
gw() { curl -s -X "$1" "http://localhost:8080/tyk$2" \
         -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' "${@:3}"; }
```

### Policy

The Gateway API manages policies only when the Gateway reads them from a folder:
`"policies": {"policy_source": "file", "policy_path": "/opt/tyk-gateway/policies"}`
(`TYK_GW_POLICIES_POLICYSOURCE`, `TYK_GW_POLICIES_POLICYPATH`). The folder must be writable by
the Gateway process; the official image runs as uid 65532.

```bash
gw POST /policies -d '{
  "id": "partner-a", "name": "Partner A", "org_id": "default", "active": true,
  "rate": 5, "per": 60, "quota_max": -1,
  "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}
}'
# {"key":"partner-a","status":"ok","action":"added"}

gw GET /reload/group
# {"status":"ok","message":""}
```

| Field | Needed | What happens when it is wrong |
| --- | --- | --- |
| `id` | Yes | Missing: HTTP 400 `{"status":"error","message":"Unable to create policy without id."}` |
| `org_id` | Yes, equal to the API's `x-tyk-api-gateway.info.orgId` and to the key's `org_id` | Missing or different: the policy is saved and listed, and every key that names it is refused (see Key below) |
| `access_rights` | Yes, one entry per API, and the map key must be the API id | A wrong API id is accepted; keys are created, and every request gets HTTP 403 `Access to this API has been disallowed` |
| `rate`, `per` | For a rate limit | Absent or 0: no rate limit |
| `quota_max`, `quota_renewal_rate` | For a quota | Absent, 0 or -1: no quota |
| `name`, `active`, `api_id`, `api_name`, `versions` | Not enforced on the open source Gateway for this case | A policy holding only `id`, `org_id`, `rate`, `per` and `{"orders": {}}` works. Keep the full form: the Dashboard shows these fields |

- **The policy does nothing until the Gateway reloads.** Before `GET /tyk/reload/group`,
  `GET /tyk/policies` does not list it and a key naming it is refused. The create call still
  answers `"status":"ok"`.
- `POST /tyk/policies` writes `<policy_path>/<id>.json`. `PUT /tyk/policies/<id>` replaces it
  (`"action":"modified"`), `DELETE /tyk/policies/<id>` removes it (`"action":"deleted"`),
  `GET /tyk/policies` lists what is loaded. Each change needs a reload.
- `/tyk/reload/group` reloads every Gateway on the same Redis; `/tyk/reload` only the one
  called.

### Policy files, without the API

Two layouts, both loaded by Gateway 5.16.0:

- `policy_path` set: one JSON file per policy in that folder, each a policy object with its
  `id` inside. This is what the Gateway API writes.
- `policy_path` not set and `policy_record_name` set to a file: one JSON object mapping policy
  id to policy:

```json
{"partner-a": {"org_id": "default", "active": true, "rate": 5, "per": 60, "quota_max": -1,
  "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}}}
```

In the single-file layout `POST /tyk/policies` fails with HTTP 500
`{"status":"error","message":"Failed to create file!"}`: edit the file and reload.

### Key

```bash
gw POST /keys -d '{"org_id": "default", "alias": "partner-a", "apply_policies": ["partner-a"]}'
# {"key":"default68b0…1273","status":"ok","action":"added","key_hash":"f8d4d6e4"}
```

The `key` value is what the client sends (in the `Authorization` header for the auth token
scheme used here). With `hash_keys: true` the Gateway stores only a hash, so this response is
the one time the key is shown.

A key without a policy carries its own limits and access rights:

```bash
gw POST /keys -d '{"org_id": "default", "alias": "partner-a", "rate": 5, "per": 60, "quota_max": -1,
  "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}}'
```

Every failure below returns the same HTTP 500
`{"status":"error","message":"Failed to create key, ensure security settings are correct."}`.
The cause is only in the Gateway log:

| Cause | Gateway log line |
| --- | --- |
| The key has no `org_id` | `policy not found: "partner-a"` |
| The key's `org_id` differs from the policy's | `policy not found: "partner-a"` |
| The policy has no `org_id` | `policy not found: "no-org"` |
| The policy does not exist, or was not reloaded yet | `policy not found: "nope"` |
| No policy and no `access_rights` (with `allow_master_keys` off, the default) | `Master keys disallowed in configuration, key not added.` |
| A `per_api` policy mixed with a partitioned one | `cannot apply multiple policies when some have per_api set and some are partitioned` |

### When the API definition has no `orgId`

The open source Gateway does not fill in a missing `x-tyk-api-gateway.info.orgId`; the API
then belongs to the empty organisation. On Gateway 5.16.0, results against such an API, with
`org_id` set on neither, either or both of the policy and the key:

| Policy `org_id` | Key `org_id` | Result |
| --- | --- | --- |
| `"default"` | `"default"` | Key refused with the HTTP 500 above; log `policy not found: "<policy id>"` |
| `""` or absent | `""` or absent | Key created; requests 200, and the policy's rate limit applied |
| `""` | `"default"` | Key refused |
| `"default"` | `""` | Key refused |
| (no policy; `access_rights` on the key) | `"default"` or `""` | Key created; requests 200 |

Two fixes:

- **Give the API an organisation** (preferred): `GET /tyk/apis/oas/<id>`, add
  `"orgId": "default"` under `x-tyk-api-gateway.info`, `PUT` it back, reload. Keys and policies
  with `"org_id": "default"` then work. Do this before creating keys: a key made while the API
  had no `orgId` gets HTTP 403 `Access to this API has been disallowed` once the API gains one
  (log: `policy not found: "<policy id>"`). Recreate such keys and policies with the new value.
- **Leave it empty everywhere**: no `org_id` (or `""`) on the policy and on the key.

Other key calls:

| Call | Result |
| --- | --- |
| `GET /tyk/keys/<key>` | The stored session, including the limits copied from its policies |
| `PUT /tyk/keys/<key>` with the full body | `"action":"modified"`. Resets the key's quota counter and starts a new rate window |
| `PUT /tyk/keys/<key>?suppress_reset=1` | Updates the key and keeps the quota counter |
| `POST /tyk/keys/<custom id>` | Creates a key with your own value. The response shows `"key":"default<custom id>"`; the client sends the bare custom id |
| `DELETE /tyk/keys/<key>` | `{"key":"…","status":"ok","action":"deleted"}`. Requests keep passing for up to 10 seconds (the Gateway's key cache), then get 403 |
| `GET /tyk/keys` with `hash_keys: true` | HTTP 404 `Hashed key listing is disabled in config (enable_hashed_keys_listing)`; from 5.16.3 `Hashed key listing is disabled in the Gateway config: set enable_hashed_keys_listing to true in tyk.conf (or TYK_GW_ENABLEHASHEDKEYSLISTING)` |

To move a key to another plan, `PUT` it with the new `apply_policies`. The new limits applied
after the 10-second key cache expired.

## Dashboard-managed Gateway: the Dashboard API

Every call carries a Dashboard user's API key in the `authorization` header. The Dashboard
fills in `org_id` from that user, stores the object and tells the Gateways to reload, so there
is no reload call: a key naming a new policy worked three seconds after the policy was
created.

```bash
dash() { curl -s -X "$1" "http://localhost:3000$2" \
           -H "authorization: $DASHBOARD_API_KEY" -H 'Content-Type: application/json' "${@:3}"; }
```

### Policy

```bash
dash POST /api/portal/policies/ -d '{
  "id": "partner-a", "name": "Partner A", "active": true,
  "rate": 5, "per": 60, "quota_max": -1,
  "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}
}'
# {"Status":"OK","Message":"6afc64c815a14d0001b5fce4","Meta":null}
```

- `Message` is the policy's database id (`_id`). With `"id": "partner-a"` in the body the
  policy also has that custom id; without it, `id` equals the database id. A key can name the
  policy by either: both worked in `apply_policies`. Use a custom `id` so the same name works
  in every environment.
- `name` must be unique: a second policy with the same name returns HTTP 400
  `{"Status":"Error","Message":"This policy name has already been used. Enter a unique policy name","Meta":null}`.
- `GET /api/portal/policies/?p=-1` lists all policies (without `p=-1` the call returns only
  the first 10);
  `GET /api/portal/policies/<database id>` returns one;
  `PUT /api/portal/policies/<database id>` replaces it and answers
  `{"Status":"OK","Message":"Data updated","Meta":null}`;
  `DELETE /api/portal/policies/<database id>` removes it
  (`{"Status":"OK","Message":"Data deleted","Meta":null}`).
- **A `GET` returns `access_rights` and also `access_rights_array`, and on `PUT` the array
  wins.** Sending the `GET` body back with a changed `access_rights` answers `Data updated`
  and changes nothing. Delete `access_rights_array` from the body before the `PUT`, then read
  the policy back.
- Every save stamps `last_updated`, which is what makes a changed rate reach keys already in
  use (see `objects-and-merge-rules.md`).
- The limit fields, `partitions`, `endpoints` and the merge rules are the same as on the open
  source Gateway; the partition and endpoint examples in this skill were run through both
  APIs.

### Key

```bash
dash POST /api/keys -d '{"alias": "partner-a", "apply_policies": ["partner-a"]}'
# {"api_model":{},"key_id":"eyJvcmciOi…","data":{…the session…},"key_hash":"59ce2f455e9e02df"}
```

`key_id` is what the client sends. No `org_id` is needed in the body.

| Call | Result |
| --- | --- |
| `POST /api/keys` with own limits and `access_rights`, no policy | Creates a key with those limits |
| `POST /api/keys/<custom id>` | Creates a key with your own value; the client sends the bare custom id |
| `GET /api/keys/<key_id>` | `{"api_model":{},"key_id":"…","data":{…}}` |
| `PUT /api/keys/<key_id>` with `{"alias": …, "apply_policies": […]}` | Updates the key and resets its quota counter |
| `PUT /api/keys/<key_id>?suppress_reset=1` | Updates the key and keeps the quota counter |
| `DELETE /api/keys/<key_id>` | `{"Status":"OK","Message":"Key deleted successfully","Meta":null}` |

Errors are HTTP 403 with the reason in the body:

| Cause | `Message` |
| --- | --- |
| Unknown policy | `Failed to save new session object to Tyk: Failed to create key - policy not found: "nope"` |
| No policy and no access rights | `Failed to save new session object to Tyk: Failed to create key, keys must have at least one Access Rights record set.` |
| Only rate or quota partitions, nothing granting access | `Failed to save new session object to Tyk: Failed to create key - key has no valid policies to be applied` |

### The Gateway API on a Dashboard-managed Gateway

| Call | Result |
| --- | --- |
| `POST /tyk/policies` | HTTP 500 `{"status":"error","message":"Due to enabled service policy source, please use the Dashboard API"}` |
| `POST /tyk/apis/oas` | HTTP 500 `{"status":"error","message":"Due to enabled use_db_app_configs, please use the Dashboard API"}` |
| `GET /tyk/policies` | Lists the policies the Gateway loaded from the Dashboard |
| `POST /tyk/keys` with the organisation id and a policy id | Creates a key. Prefer the Dashboard API so that the organisation id is filled in for you |

## Declarative

Keeping policies and keys in Git or as Kubernetes resources is the
`tyk-gitops-sync-operator` skill. The objects are the ones described here: the same limit
fields, the same `org_id` rule, the same merge rules. Writing the API definition, including
the two definition-level limits, is the `tyk-api-definitions-routing` skill.
