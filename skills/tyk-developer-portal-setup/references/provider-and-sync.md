# Connecting the Portal to the Dashboard (provider) and synchronising

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 and 1.20.0 with Tyk Dashboard and Gateway 5.16.0

Public page: https://tyk.io/docs/portal/api-provider

A provider is the Portal's connection to one Dashboard organisation. Through it the Portal reads
APIs and policies, writes the policies behind products and plans, and creates keys and OAuth
clients when it approves access.

## 1. A Dashboard user for the Portal

Use a dedicated Dashboard user, not an administrator. The permission names the Dashboard API
accepts on 5.16.0:

| Permission (`user_permissions` key) | Level | Why |
| --- | --- | --- |
| `apis` | `write` | read APIs; DCR writes JWT settings |
| `certs` | `write` | certificates (the docs page calls it "Certificates") |
| `keys` | `write` | create and delete keys on approval |
| `policies` | `write` | products and plans are policies |
| `analytics` | `read` | |
| `users` | `read` | |
| `oauth` | `write` | **needed for DCR approvals**; not in the docs list |

- `"certificates"` as a key is refused: 400 `{"Status":"Error","Message":"Invalid group found for user permissions"}`.
- Without `oauth`, a DCR approval fails with 422 `{"errors":["failed to provision new
  credential: updating products: setting oauth for api orders-jwt: API Returned error:
  {\"Status\":\"Error\",\"Message\":\"Not authorised - You do not have permission, please
  contact your administrator\",\"Meta\":null} for http://tyk-dashboard:3000/api/apis/oauth/orders-jwt"]}`,
  after the Portal has already registered the client at the identity provider (see
  `dcr-keycloak.md`).
- MCP products work without an `mcp` permission (`mcp-products.md`).

Create the user with the Dashboard API, as an administrator (5.16.0):

```bash
curl -s -X POST "$DASHBOARD_URL/api/users" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/json' -d '{"first_name":"Developer","last_name":"Portal",
  "email_address":"portal-provider@example.com","password":"<strong password>","active":true,
  "user_permissions":{"apis":"write","certs":"write","keys":"write","policies":"write",
  "analytics":"read","users":"read","oauth":"write"}}'
# 200 {"Status":"OK","Message":"User and session have been created","Meta":"<user id>"}
```

Then get the new user's API key: sign in to the Dashboard as that user, open **Edit Profile**
from the user menu (top right), and under **Tyk Dashboard API Access Credentials** press **Show
secret** and copy it; that key answers 200 on `GET /api/apis` and a provider built with it
synchronises with status `Up`. Read by an administrator through `GET /api/users`, the new user's
`access_key` is empty on 5.16.0, so the user's own profile is the place to copy it. Keep the key in a secret store. Docs:
https://tyk.io/docs/portal/api-provider#tyk-dashboard-api-access-credentials and
https://tyk.io/docs/platform-management/dashboard-users

## 2. Create the provider

`Configuration.MetaData` is a JSON **string**, not an object. Build it with a JSON tool:

```bash
BODY=$(python3 -c 'import json,os; print(json.dumps({"Name":"Example Dashboard","Type":"tyk-pro",
  "Configuration":{"MetaData":json.dumps({"URL":"http://tyk-dashboard:3000",
  "Secret":os.environ["PROVIDER_DASH_KEY"],"OrgID":os.environ["ORG_ID"],
  "Gateway":"https://api.example.com","PoliciesTags":["portal"],"InsecureSkipVerify":False})}}))')
curl -s -X POST "$PORTAL_URL/portal-api/providers" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d "$BODY" -o /dev/null -w '%{http_code}\n'
# 201; the object reads back with "Status": "Unknown", "LastSynced": "Not synced yet"
```

| MetaData field | Meaning |
| --- | --- |
| `URL` | Dashboard URL **as the Portal container reaches it** (`http://tyk-dashboard:3000` in Compose) |
| `Secret` | the Dashboard user's API key |
| `OrgID` | the Dashboard organisation id |
| `Gateway` | Gateway base URL shown to developers |
| `PoliciesTags` | only policies carrying one of these tags are synchronised; `[]` means every partitioned policy |
| `InsecureSkipVerify` | TLS check to the Dashboard; keep `false` outside a lab |

`GET /providers` shows `"Status": "Up"` and `"LastSynced": "Just now"` a moment after
creation. A provider with a wrong `Secret` stays `"Status": "Down"`, and
`PUT /providers/{id}/synchronize` on it answers 422
`{"errors":["Unable to save changes. Authentication failed"],"status":"error"}`.

## 3. Synchronise

```bash
curl -s -X PUT "$PORTAL_URL/portal-api/providers/1/synchronize" -H "Authorization: $PORTAL_API_TOKEN"
# 200 {"message":"Action Synchronize: Executed successfully","status":"ok"}
```

- The Portal also synchronises on its own every `PORTAL_REFRESHINTERVAL` minutes (default 10;
  see https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration#portal_refreshinterval).
- A product or plan created through the Portal API is written to the Dashboard at once; no
  synchronisation is needed for it.
- With `PoliciesTags` set, the Portal adds those tags to every policy it creates (a
  product created through the Portal gets `"tags": ["portal"]`). A policy created in the
  Dashboard must carry the tag by hand.
- Synchronisation reports success even when it removes products. Read
  `products-plans-partitions.md` before changing tags or policies.

## 4. More than one provider

The docs allow several providers (environments, regions); a product or plan belongs to one
provider and credentials from one provider do not work on another's APIs. See
https://tyk.io/docs/portal/api-provider. The recipe on this page sets up one provider.
