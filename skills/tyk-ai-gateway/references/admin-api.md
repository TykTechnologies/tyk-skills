# The admin API

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`)

Objects that exist only in Enterprise Edition need the Enterprise images with a licence; their
Community Edition answer is given too.

See [API reference](https://tyk.io/docs/ai-management/ai-studio/ai-studio-swagger),
[LLM management](https://tyk.io/docs/ai-management/ai-studio/llm-management),
[Apps](https://tyk.io/docs/ai-management/ai-studio/admin-apps),
[user management](https://tyk.io/docs/ai-management/ai-studio/user-management),
[RBAC](https://tyk.io/docs/ai-management/ai-studio/rbac),
[teams](https://tyk.io/docs/ai-management/ai-studio/teams),
[secrets](https://tyk.io/docs/ai-management/ai-studio/secrets).

## Conventions

- Base: the control plane, `http://localhost:8080` in the evaluation stack. Admin routes are
  under `/api/v1`; a user's own routes under `/common`; sign-in under `/auth`.
- Authentication: `Authorization: Bearer <user API token>` (see `architecture-and-setup.md`
  for the first token). The token has the permissions of its user.
- Bodies use a JSON:API-like envelope: `{"data":{"type":"<type>","attributes":{...}}}`. The
  roles, bindings and Workspaces routes take plain JSON (shown below).
- Errors: `{"errors":[{"title":"...","detail":"..."}]}`. Authentication failures are
  `401 {"error":"Unauthorized"}`.
- A token without the permission: `403 {"errors":[{"title":"Forbidden","detail":"missing permission llms:write","code":"permission_denied","permission":"llms:write"}]}`.
- **An unknown route answers `200` with the admin UI's HTML page**, not `404`. Check the content
  type (or use `scripts/aistudio_admin.py request`, which reports this as an error).
- Lists are paged. The LLM list reports totals in the `X-Total-Count` and `X-Total-Pages`
  response headers; `?page_size=50`, `?page=2` and `?all=true` work.
- **`PATCH` changes only the fields you send**, for LLMs, Apps, users, Model Routers and
  filters alike. An empty list (`"llm_ids":[]`) clears a grant list; `null` clears a budget.
- The helper prints the status and the body:
  `python3 <skill-dir>/scripts/aistudio_admin.py request <METHOD> <path> [--data '<json>' | --data-file f.json]`.

## LLMs (`/api/v1/llms`)

A new installation already holds two sample LLMs (ids 1 and 2, named `OpenAI` and
`Anthropic`; their keys point at secrets that hold no value), so the first LLM you create gets
id 3. Read the id from the response.

```json
POST /api/v1/llms
{"data":{"type":"llms","attributes":{
  "name":"Stub Alpha","vendor":"openai","api_endpoint":"http://llm-alpha:8080/v1",
  "api_key":"stub-key-alpha","default_model":"alpha-fast",
  "allowed_models":["alpha-fast","alpha-deep"],"active":true}}}
```

```json
201 {"data":{"type":"llms","id":"3","attributes":{"name":"Stub Alpha","api_key":"[redacted]","has_api_key":true,"credential_status":"inline","api_endpoint":"http://llm-alpha:8080/v1","privacy_score":0,...,"vendor":"openai","active":true,"filters":[],"default_model":"alpha-fast","allowed_models":["alpha-fast","alpha-deep"],"monthly_budget":null,"budget_start_date":null,"namespace":"","dont_log_bodies":false,"plugins":[],"vendor_workspace_id":"","price_multipliers":null,...,"allow_internal_host":false,"workspace_id":1}}}
```

Fields: `name` (its slug becomes the route, `Stub Alpha` -> `stub-alpha`), `vendor`
(`GET /api/v1/vendors/llm-drivers` lists the drivers), `api_endpoint`, `api_key`,
`default_model`, `allowed_models`, `privacy_score`, `active`, `monthly_budget`, `failover`
(`routing-failover-budgets.md`), `filters` (Enterprise Edition), `allow_internal_host`,
`workspace_id`.

Rules:

- `active` defaults to `false` when left out, and an inactive LLM is not served. Send
  `"active": true`.
- Creation does not validate much: a body with only a `name` returns `201` (no vendor, no
  endpoint, inactive).
- **Names must differ by more than case, spaces or punctuation.** A second `Stub Alpha`, and
  `stub-alpha`, are refused:
  `400 {"errors":[{"title":"Bad Request","detail":"LLM name \"Stub Alpha\" is too close to LLM 3 (\"Stub Alpha\"): both have the slug \"stub-alpha\", and LLM names must differ by more than case, spaces or punctuation"}]}`.
- `api_key` is never returned (`"[redacted]"`). `"api_key":"$SECRET/NAME"` points at a stored
  secret instead of an inline value; the response then shows `"credential_status":"secret"`.

Other calls: `GET /api/v1/llms/{id}`, `GET /api/v1/llms/{id}/dependents` (the Apps, catalogues,
routers and other objects that use it), `DELETE /api/v1/llms/{id}` (`204`).

- Deleting an LLM that Apps are granted is allowed. The App's `llm_ids` lost the id, and the
  edge went on serving the deleted LLM until the next push. Read `dependents` first.
- Deleting an LLM that is another LLM's failover target is refused with `409`.

## Apps and credentials (`/api/v1/apps`, `/api/v1/credentials`)

An App is the caller identity on the gateway: it owns one credential and holds grants.

```json
POST /api/v1/apps
{"data":{"type":"apps","attributes":{"name":"partner-a","description":"Example application","user_id":1,"llm_ids":[3]}}}
```

```json
201 {"data":{"type":"app","id":"1","attributes":{"name":"partner-a","description":"Example application","user_id":1,"credential_id":1,"credential_active":false,"datasource_ids":[],"llm_ids":[3],"tool_ids":[],"monthly_budget":null,"budget_start_date":null,"team_id":1,"is_active":true,"is_orphaned":false,"workspace_id":1,"mcp_server_ids":[],"model_router_ids":[],"semantic_router_ids":[],"tracing":{},"guardrail_capability":""}}}
```

Fields: `name`, `description`, `user_id` (the owner), `llm_ids`, `tool_ids`,
`datasource_ids`, `monthly_budget`, and in Enterprise Edition `model_router_ids`,
`semantic_router_ids`, `mcp_server_ids`.

The credential is created with the App and starts inactive:

1. `POST /api/v1/apps/{id}/activate-credential` -> `204`.
2. `GET /api/v1/credentials/{credential_id}` returns `"secret"`, the bearer token the
   application sends to the gateway.

`python3 <skill-dir>/scripts/aistudio_admin.py app-secret <app id> --out .app-secret` does both
and writes the secret to a file. Key values are generated by the product; the App create
request has no field for supplying your own. Before activation the gateway answers
`401 {"status":401,"message":"Invalid or expired bearer token"}`.

Update: `PATCH /api/v1/apps/{id}` with only `{"description":"changed"}` changes the
description and keeps the name, owner and grants. Add a grant by sending the full list you want
(`"llm_ids":[3,4]`).

Other calls: `POST /api/v1/apps/{id}/deactivate` and `/activate` (a deactivated App gets
`403 {"status":403,"message":"app is inactive"}`), `POST /api/v1/apps/{id}/deactivate-credential`
(`204`; the key then gets `401`). Both reach connected edges within about two seconds
(`hub-and-edge.md`). `DELETE /api/v1/apps/{id}` (`204`).

## Users (`/api/v1/users`)

```json
POST /api/v1/users
{"data":{"type":"users","attributes":{"email":"dev@example.com","name":"Example Developer","password":"<compliant password>","is_admin":false,"show_portal":true,"show_chat":true,"email_verified":true}}}
```

```json
201 {"data":{"type":"users","id":"2","attributes":{"email":"dev@example.com","name":"Example Developer","is_admin":false,"show_chat":true,"show_portal":true,"email_verified":true,"has_api_key":false,...,"role":"Developer","account_type":"","groups":[{"type":"groups","id":"1",...}],"auth_source":"admin","disabled":false,"budget_team_id":null}}}
```

- Every new user joins the `Default` team (group 1).
- `POST /api/v1/users/{id}/roll-api-key` returns the user with a new `api_key`; the user's own
  route is `POST /common/me/api-key/roll`. Rolling replaces the previous token.
- A `PATCH` with only `name` changes the name and keeps the email, flags and role.
- Other calls: `POST /api/v1/users/{id}/disable` and `/enable`, `DELETE /api/v1/users/{id}`.

## Model prices and secrets

`POST /api/v1/model-prices` with `model_name`, `vendor`, `cpt` (cost per completion token),
`cpit` (cost per prompt token), `currency`; example in `routing-failover-budgets.md`. Push after
adding one.

```json
POST /api/v1/secrets
{"data":{"type":"secrets","attributes":{"var_name":"STUB_DELTA_KEY","value":"..."}}}
```

`201` with `"var_name":"STUB_DELTA_KEY","has_value":true,"referenced_by":[]`. Reference it as
`$SECRET/STUB_DELTA_KEY` in an LLM's `api_key`.

## Tools, edges, analytics

- Tools: `POST /api/v1/tools` with a base64-encoded OpenAPI document; see
  `mcp-tools-and-plugins.md`.
- Edges and synchronisation: `GET /api/v1/edges`, `POST /api/v1/edges/reload-all`,
  `GET /api/v1/sync/status`, `GET /api/v1/sync/pending-changes`, `GET /api/v1/cluster/status`;
  see `hub-and-edge.md`.
- Analytics: `GET /api/v1/analytics/...`; see `guardrails-audit-analytics.md`.

## Access control (Enterprise Edition)

Community Edition has administrators, standard users and service accounts, and one team (`Default`).
`GET /api/v1/rbac/me` works in both editions and reports the caller's permissions.

Enterprise Edition, run with a licence:

```text
GET  /api/v1/rbac/roles      -> Owner, Administrator, Editor, Viewer, Auditor (ids 1-5)
POST /api/v1/rbac/roles      {"name":"App provisioner","description":"Creates Apps and reads keys","permissions":["apps:write","apps:read","credentials:read","llms:read"]}
                             -> 201, "slug":"app-provisioner","is_system":false
POST /api/v1/rbac/bindings   {"subject_type":"user","subject_id":2,"role_id":6}  -> 201, "scope_type":""
```

With that role, user 2's API token reads `GET /api/v1/llms` (`200`), creates an App (`201`) and
is refused an LLM:
`403 {"errors":[{"title":"Forbidden","detail":"missing permission llms:write","code":"permission_denied","permission":"llms:write"}]}`.

| Object and route | Enterprise Edition | Community Edition |
| --- | --- | --- |
| Teams: `POST /api/v1/groups`, `POST /api/v1/groups/{id}/users` with `{"data":{"type":"users","id":"2"}}` | `201`, `204`; `GET /api/v1/groups/{id}/users` lists the member | `402 {"errors":[{"detail":"Group management requires Enterprise Edition","title":"Payment Required"}]}` (`GET /api/v1/groups` works and shows `Default`) |
| Catalogues: `POST /api/v1/catalogues` `{"data":{"type":"catalogues","attributes":{"name":"Team A models"}}}` | `201` | `402 ... "LLM catalogue management requires Enterprise Edition"` |
| Workspaces: `GET /api/v1/workspaces`, `POST /api/v1/workspaces` `{"name":"Team A","description":"..."}` | `200` with `Default workspace`; `201` | `402 {"errors":[{"title":"Enterprise Feature","detail":"Workspaces are an Enterprise Edition feature - visit https://tyk.io/ai-studio/pricing for more information","code":"enterprise_required"}]}` |
| Service accounts: `POST /api/v1/users` with `{"data":{"type":"users","attributes":{"name":"ci-pipeline","account_type":"service","role_ids":[6]}}}` | `201`, `"role":"Service account"`, a generated `svc-...@service-account.invalid` email, no Portal or Chat, and the `api_key` once | Also `201`, with `"is_admin":false` in place of `role_ids` |
| Roles: `/api/v1/rbac/roles` | above | `402 {"errors":[{"title":"Enterprise Feature","detail":"role-based access control is an Enterprise Edition feature - visit https://tyk.io/ai-studio/pricing for more information","code":"enterprise_required"}]}` |
| SSO profiles: `/api/v1/sso-profiles` | `200`, an empty list | `402 {"errors":[{"detail":"SSO profile management is an Enterprise feature","title":"Payment Required"}]}` |
| Namespaces: `/api/v1/namespaces` | `200`, `global` and `default` | `402 ... "Multi-tenant namespaces require Enterprise Edition"` |
| Team budgets: `/api/v1/groups/{id}/budget` | `200` | `402 ... "team budgets are an Enterprise Edition feature ..."` |

Objects carry `workspace_id` (`1`, the default Workspace, unless set). Workspace roles, the
Portal, SSO set-up and the other Enterprise objects are described in the linked documentation
pages.
