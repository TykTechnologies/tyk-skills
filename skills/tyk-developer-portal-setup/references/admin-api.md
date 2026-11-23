# Portal admin API: authentication, pagination, readback

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0 (`tykio/portal:v1.20.0`) with Tyk Dashboard and Gateway 5.16.0

Public reference: https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/api-documentation/tyk-edp-api
(the OpenAPI file is `swagger/enterprise-developer-portal-swagger.yaml` in the public tyk-docs
repository). Bootstrap, authentication and the calls of the main workflow apply to 1.20.1; the
other responses below are from 1.20.0.

## Base path and authentication

- Base path `/portal-api`. Header `Authorization: <token>`, the raw token with no `Bearer`.
  `Bearer <token>` and no header both answer 403 `{"message": "Forbidden"}`.
- The first admin and token come from `POST /portal-api/bootstrap`:

  ```bash
  curl -s -X POST "$PORTAL_URL/portal-api/bootstrap" -H 'Content-Type: application/json' \
    -d '{"username":"admin@example.com","password":"<strong password>","first_name":"Portal","last_name":"Admin"}'
  # 200 {"code":"OK","message":"Bootstrapped user successfully","data":{"api_token":"<token>"}}
  ```

  Store the token in a secret store at once; do not print it. Before bootstrap every other
  `/portal-api` path answers 404. Right after bootstrap the API can answer 404 for a few
  seconds; wait for `GET /portal-api/providers` to answer 200 before the next call.
- Later tokens: sign in to the admin UI and open **My profile** (`/admin/admin_profile`): the
  API token is shown there. It is signed with `PORTAL_JWTSIGNINGKEY` (generated and stored in the
  database when unset, on 1.20.1); changing that key, or upgrading from 1.20.0 without one, turns
  every existing token into a 403 (see `operations-and-restarts.md`).
- The admin API is not under the CSRF check; the Live Portal and admin forms are
  (`developers-and-access.md`).

## Pagination

As the API reference page above describes; for example, with 23 organisations:

| Query | Rows returned |
| --- | --- |
| none | 20, newest first |
| `?per_page=100` | 23 (all) |
| `?page=2` | 3 (the rest of 20-per-page) |
| `?per_page=10&page=3` | 3 |
| `?limit=5` | 5 |
| `?page_size=100` | 20: ignored |
| `?page=9` | `[]` |

Lists are bare JSON arrays with no total and no paging header. Read with `per_page=100` and
`page=1,2,...` until a page is shorter than 100. `scripts/portal_setup.py` does this for every
list it reads. A lookup by name on one default page misses items silently once there are more
than 20.

## Readback rules

- Partial `PUT` works: `PUT /products/4 {"DisplayName":"Orders","Description":"..."}` changes
  those two fields and keeps the rest; `PUT /plans/3 {"RateLimit":6}` keeps the quota.
- Some fields are accepted with 200 and not stored. On 1.20.0: `MCPContent` and
  `MCPServerURL` on `PUT /products/{id}/api-details/{api}` (`mcp-products.md`). Always read
  the object back and compare.
- Some readbacks differ from what was sent: a catalogue audience posted with
  `OrganisationID` reads back `0` (`catalogues-and-audiences.md`); product `Catalogues` read
  back as names, plan `Catalogues` as IDs (`["1"]`).
- Error shape: 422 `{"errors":["..."]}`, sometimes with `"status":"error"`. A wrong ID in a
  path answers 404 with an empty name, or 422 such as
  `{"errors":["getting client from context: getting client by id: record not found"]}`.
- Keep admin API responses out of logs and reports.

## Calls used by this skill

| Area | Calls |
| --- | --- |
| Providers | `GET/POST /providers`, `GET/PUT/DELETE /providers/{id}`, `PUT /providers/{id}/synchronize` |
| Products | `GET/POST /products`, `GET/PUT/DELETE /products/{id}`, `PUT /products/{id}/api-details/{api}`, `POST /products/{id}/api-details/{api}/oas` (multipart `file`), `POST /products/{id}/spec-details/{spec}/oas` |
| Plans | `GET/POST /plans`, `GET/PUT/DELETE /plans/{id}` |
| Catalogues | `GET/POST /catalogues`, `GET/PUT/DELETE /catalogues/{id}`, `GET/POST /catalogues/{id}/audiences` |
| Organisations and users | `GET/POST /organisations`, `GET /organisations/{id}/teams`, `GET/POST /users`, `GET/PUT /users/{id}` |
| Apps and access | `GET/POST /apps`, `PUT /apps/{id}/provision`, `GET /access_requests`, `PUT /access_requests/{id}/approve`, `PUT /access_requests/{id}/reject`, `GET /apps/{a}/access-requests/{r}/credentials`, `PUT .../credentials/{c}/rotate`, `DELETE .../credentials/{c}` |
| Imported credentials | `GET/POST /apps/{id}/custom_credentials`, `DELETE /apps/{id}/custom_credentials/{c}` |
| DCR | `GET/POST /oauth-providers`, `POST /oauth-providers/{id}/client-types` |
| SSO | `GET/POST /sso_profiles`, `GET /sso_profiles/{id}` |

Deleting a product or plan through this API (`DELETE /products/{id}`, `DELETE /plans/{id}`, each
200 `{"status":"ok"}`) also deletes its Dashboard policy (`GET /api/portal/policies/{id}` then
answers 404).

The General settings (registration, auto-approve, access flow, mail) have no admin API call:
they are a form at `/admin/general`; `scripts/portal_setup.py settings` drives it.
