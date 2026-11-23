# Versioning, and where authentication goes

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`) and Tyk Dashboard 5.16.0

## Versioning

In Tyk OAS each version is its own API definition with its own id. One of them is the **base
API**: clients call its listen path, and its `info.versioning` block says where the version is
read from and which API id serves each version. Docs:
https://tyk.io/docs/api-management/api-versioning

### Add a version

Create the base as a normal API. Then create the new version with the same create call plus
query parameters. The body is the new version's full Tyk OAS definition.

```bash
# open source Gateway
curl -s -X POST "$GW/tyk/apis/oas?base_api_id=orders&base_api_version_name=v1&new_version_name=v2&set_default=false" \
  -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @orders-v2.oas.json
# 200 {"key":"orders-v2","status":"ok","action":"added"}

# Dashboard
curl -s -X POST "$DASH/api/apis/oas?base_api_id=orders&base_api_version_name=v1&new_version_name=v2&set_default=false" \
  -H "Authorization: $DASHBOARD_API_KEY" -H 'Content-Type: application/json' --data @orders-v2.oas.json
# 200 {"Status":"OK","Message":"API created","Meta":"<database id>","ID":"orders-v2"}
```

| Parameter | Meaning |
| --- | --- |
| `base_api_id` | id of the base API |
| `base_api_version_name` | name to give the base's own version; needed the first time a version is added |
| `new_version_name` | name of the version being created |
| `set_default` | `true` makes the new version the one served when the request names none |

Errors: without `base_api_version_name` on the first version,
`400 you need to provide a version name for the new Base API: orders`; without
`new_version_name`, `422 The new version name is required`; unknown base,
`400 <id> is not a valid Base API version`.

The base then reads back with:

```json
"versioning": {"enabled": true, "name": "v1", "default": "self", "location": "header",
               "key": "x-api-version", "versions": [{"name": "v2", "id": "orders-v2"}]}
```

`default: "self"` means the base serves requests that name no version.
`GET /tyk/apis/oas/{base id}/versions` (or `/api/apis/oas/{base id}/versions`) lists them.

Writing that `versioning` block into the base by hand, pointing at an API created separately,
linked the two as well, on the Gateway API and on the Dashboard API.

### How a request picks a version

| Request to the base listen path | Served by |
| --- | --- |
| no `x-api-version` header | the default (the base while `default` is `self`) |
| `x-api-version: v2` | the `v2` API |
| `x-api-version: v1` | the base |
| `x-api-version: v9` | `404 {"error": "This API version does not seem to exist"}` |

`"fallbackToDefault": true` serves the default for an unknown version instead of the 404.

Other locations, set in the base's `versioning`:

| Setting | Request | Notes |
| --- | --- | --- |
| `"location": "url-param", "key": "version"` | `/orders/x?version=v2` | no parameter: the default |
| `"location": "url"` | `/orders/v2/x` | the upstream receives `/v2/x`; add `"stripVersioningData": true` to send `/x`. A request without a version segment (`/orders/x`) gets the 404 above |

### The version's own listen path

A version API also answers on its own listen path (`/orders-v2/x` works). To make it reachable
only through the base, set `info.state.internal: true` on the version: its own path then
returns 404 and the base still routes to it. Give each version a listen path different from the
base's.

Deleting the base does not delete its versions.

On the Dashboard, change a base that has versions by sending back its readback (see
`create-import-update.md`); a `PUT` without `info.orgId` is refused.

## Authentication: where it goes

This section shows only where the settings live. Three places must agree:

1. `components.securitySchemes` in the OpenAPI part names and types each scheme.
2. The top-level `security` list says which schemes a request needs.
3. `x-tyk-api-gateway.server.authentication` switches authentication on and holds Tyk's settings
   for each scheme, under the **same scheme name**.

```json
{
  "components": {"securitySchemes": {
    "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}},
  "security": [{"apiKey": []}],
  "x-tyk-api-gateway": {
    "server": {
      "authentication": {
        "enabled": true,
        "securitySchemes": {"apiKey": {"enabled": true}}
      }
    }
  }
}
```

With that, no key gets `401 {"error": "Authorization field missing"}`, a wrong key
`403 {"error": "Access to this API has been disallowed"}`, and a valid key 200. The header
name comes from the OpenAPI scheme; the key in `Authorization` instead of `X-API-Key` gets the
401.

- More places to read the credential go in the Tyk part:
  `"apiKey": {"enabled": true, "query": {"enabled": true, "name": "api_key"}}` accepts
  `?api_key=<key>`. `header` and `cookie` take the same shape.
- `"stripAuthorizationData": true` under `authentication` removes the credential before the
  request goes upstream. Without it the upstream receives the `X-API-Key` header.
- One operation can opt out with `ignoreAuthentication` (see `operation-middleware.md`).

### JWT

```json
"components": {"securitySchemes": {"jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}},
"security": [{"jwtAuth": []}],
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true, "securitySchemes": {
  "jwtAuth": {"enabled": true, "header": {"enabled": true, "name": "Authorization"},
              "signingMethod": "hmac", "source": "<base64 of the shared secret>",
              "identityBaseField": "sub", "defaultPolicies": ["<policy id>"]}}}}}
```

No token gets `400 {"error": "Authorization field missing"}` (401 instead when the API has
Protected Resource Metadata switched on; for MCP client authorization, see the `tyk-mcp-gateway` skill), a malformed token
`403 {"error": "Key not authorized"}`, a valid one 200. The policy in `defaultPolicies` must
exist on the Gateway, or a valid token gets
`500 {"error": "failed to create key: policy not found: \"<policy id>\""}`. Other signing
methods, JWKS URLs, claims and scopes belong to the authentication documentation:
https://tyk.io/docs/api-management/client-authentication

### More than one method

| Want | `security` | Extension |
| --- | --- | --- |
| Both required | `[{"apiKey": [], "jwtAuth": []}]` (one entry, two schemes) | both schemes under `securitySchemes`; `"baseIdentityProvider": "auth_token"` picks the method whose identity carries the limits |
| Either one | `[{"apiKey": []}, {"jwtAuth": []}]` (two entries) | both schemes, plus `"securityProcessingMode": "compliant"`, and no `baseIdentityProvider` |

- Both required: neither or only one credential is refused (400 or 401); both get 200.
- Either one, in the default processing mode (`legacy`): only the **first** entry of `security`
  is used. The key alone works; the token alone gets 401.
- Either one with `"securityProcessingMode": "compliant"`: the key alone, the token alone and
  both together get 200; neither gets 400.
- `compliant` together with `baseIdentityProvider` breaks the other method:
  `400 {"error": "Session state is missing or unset! ..."}`. Leave `baseIdentityProvider` out in
  compliant mode.

Docs: https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/multiple-auth

### Mistakes that load without complaint

| Definition | Result |
| --- | --- |
| `security` and `securitySchemes` in the OpenAPI part, no `authentication` in the extension | The API is **open**: 200 without a key |
| `authentication.enabled: true` with no `securitySchemes` in the extension | Every request: `401 Authorization field missing` |
| The extension names a scheme that `components.securitySchemes` does not have | Every request: `401 Authorization field missing` |
| No top-level `security`, schemes enabled in the extension | Authentication still applies: 401 without a key |
| A scheme of `"type": "mutualTLS"` | Rejected. 3.0: `components.securitySchemes.<name>.type must be one of the following: "oauth2"` among others. 3.1: `invalid components: security scheme "<name>": security scheme 'type' can't be "mutualTLS"` |

Client certificates are configured in the extension instead (`server.clientCertificates` and
`server.authentication.certificateAuth` are in the 5.16.0 schema), not
as an OpenAPI security scheme.

Keys, policies and limits belong to the `tyk-rate-limits-keys-policies` skill.
