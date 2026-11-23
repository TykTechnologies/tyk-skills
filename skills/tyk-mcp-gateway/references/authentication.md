# Authentication in front of MCP, and what reaches the upstream

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0` and `tykio/tyk-gateway:v5.16.0`), Dashboard 5.16.0 and Keycloak 26.8.1

See https://tyk.io/docs/ai-management/mcp-gateway/oauth-2-1,
https://tyk.io/docs/api-management/authentication/oauth2-authentication,
https://tyk.io/docs/ai-management/mcps/api-to-mcp.

An MCP proxy uses the same `server.authentication` block as any Tyk OAS API. Put authentication
on the MCP proxy itself, always: see "REST API as MCP tools" below for why the source API's own
authentication does not help.

## API key

```json
"components": {"securitySchemes": {"authToken": {"type": "apiKey", "in": "header", "name": "Authorization"}}},
"security": [{"authToken": []}],
"x-tyk-api-gateway": {"server": {"authentication": {
  "enabled": true,
  "stripAuthorizationData": true,
  "securitySchemes": {"authToken": {"enabled": true, "header": {"enabled": true, "name": "Authorization"}}}
}}}
```

Without a key:

```text
HTTP 401
WWW-Authenticate: Bearer realm="tyk", resource_metadata="http://localhost:8080/orders-mcp/.well-known/oauth-protected-resource"
{"jsonrpc":"2.0","error":{"code":-32001,"message":"Authorization field missing","data":{"http_code":401}},"id":1}
```

With `stripAuthorizationData: true` the key is not sent on: the MCP server receives no
`Authorization` header, and it does receive the caller's other headers (for example `X-Caller`).

## JWT from an identity provider

```json
"components": {"securitySchemes": {"jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}},
"security": [{"jwtAuth": []}],
"x-tyk-api-gateway": {"server": {"authentication": {
  "enabled": true,
  "stripAuthorizationData": false,
  "securitySchemes": {"jwtAuth": {
    "enabled": true,
    "header": {"enabled": true, "name": "Authorization"},
    "signingMethod": "rsa",
    "identityBaseField": "sub",
    "allowedIssuers": ["https://idp.example.com/realms/demo"],
    "allowedAudiences": ["tyk-gateway"],
    "jwksURIs": [{"url": "https://idp.example.com/realms/demo/protocol/openid-connect/certs", "cacheTimeout": "5m"}],
    "defaultPolicies": ["<policy id>"]
  }}
}}}
```

Three things to get right:

1. **`stripAuthorizationData` must be `false` on a JWT MCP proxy.** With `true`, every call
   fails with `401 Authorization field missing`, with a valid token too. Removing the header with
   `middleware.global.transformRequestHeaders.remove: ["Authorization"]` fails the same way.
2. **So the caller's JWT reaches the MCP server.** With the setting above the MCP server receives
   `Authorization: Bearer <the caller's token>`. To send something else, use upstream OAuth or
   token exchange (both Enterprise Edition, below and `token-exchange-keycloak.md`).
3. **Issuer and key URLs are two different addresses when the provider runs beside the Gateway.**
   `allowedIssuers` must equal the `iss` claim in the token (the address the client used).
   `jwksURIs` is fetched by the Gateway, so it needs an address the Gateway can reach.

A token that fails validation returns `403 {"code":-32002,"message":"Key not authorized"}`.
`defaultPolicies` names the policy every valid token gets; the policy must list the MCP proxy in
`access_rights`. For scope-to-policy mapping and the policy object, use the
`tyk-rate-limits-keys-policies` skill.

## OAuth protected-resource metadata (what MCP clients need for discovery)

An OAuth-aware MCP client starts with no token, reads the 401 challenge, fetches the metadata
document it names, then goes to the authorization server listed there.

Add an `oauth2` scheme beside the JWT scheme. The JWT scheme still validates the signature; the
`oauth2` scheme publishes the metadata and checks scopes.

```json
"components": {"securitySchemes": {
  "jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
  "idpAuth": {"type": "oauth2", "flows": {"authorizationCode": {
    "authorizationUrl": "https://idp.example.com/realms/demo/protocol/openid-connect/auth",
    "tokenUrl": "https://idp.example.com/realms/demo/protocol/openid-connect/token",
    "scopes": {"orders-reader": "Read orders", "orders-admin": "Change orders"}}}}
}},
"security": [{"jwtAuth": [], "idpAuth": []}],
"x-tyk-api-gateway": {"server": {"authentication": {"securitySchemes": {
  "idpAuth": {
    "enabled": true,
    "protectedResourceMetadata": {
      "enabled": true,
      "resource": "https://gateway.example.com/orders-mcp-oauth/",
      "authorizationServers": ["https://idp.example.com/realms/demo"]
    }
  }
}}}}
```

Result (`mcp_probe.py metadata <url>`):

```text
POST <listen path>mcp without a token
  401, WWW-Authenticate: Bearer realm="tyk", resource_metadata="http://localhost:8080/orders-mcp-oauth/.well-known/oauth-protected-resource"
GET  <listen path>.well-known/oauth-protected-resource   (no credential needed)
  200 {"resource":"http://localhost:8080/orders-mcp-oauth/","authorization_servers":["http://localhost:8081/realms/demo"],
       "scopes_supported":["orders-admin","orders-reader"],"bearer_methods_supported":["header"]}
```

Facts to plan around:

- Set `resource` and `authorizationServers` yourself. An MCP proxy that sets neither runs in
  mirror mode and relays the upstream MCP server's own metadata. See
  https://tyk.io/docs/ai-management/mcp-gateway/oauth-2-1.
- Every 401 from an MCP proxy carries the `resource_metadata` challenge, also on a proxy with only
  an API key. On that proxy the address it names returns `401 Authorization field missing`: there
  is no metadata document to read, so configure the key in the client directly.
- Clients must use the address in the challenge. The other form some clients try,
  `/.well-known/oauth-protected-resource/<listen path>` at the Gateway root, does not use the
  metadata set on the `oauth2` scheme: it relays the upstream MCP server's document, or answers
  `502 upstream PRM unavailable` when the upstream publishes none.
- `scopes_supported` comes from the `flows.scopes` list of the `oauth2` scheme. Scopes named in
  `security` entries are added as well unless `autoDeriveScopes` is `false`. See
  https://tyk.io/docs/ai-management/mcp-gateway/oauth-2-1.
- With this scheme the identity provider issues the tokens, not the Gateway. The client still
  needs, at the authorization server: a client id (registered by hand, or through the server's
  own dynamic client registration), a redirect URI it accepts, and a token whose `aud` is in
  `allowedAudiences`. With Keycloak this takes an audience mapper on the client
  (`token-exchange-keycloak.md`).
- Works on the open source image too: JWT validation, the metadata document and the scope check
  below all work on `tykio/tyk-gateway:v5.16.0`.

## Scopes per tool

```json
"idpAuth": {"enabled": true, "scopeCheck": {"enabled": true, "claimNames": ["scope", "roles"], "scopeSource": "operation"}}
```

```json
"middleware": {"mcpTools": {"create_user": {"security": [{"idpAuth": ["orders-admin"]}], "scopeCheck": {"enabled": true}}}}
```

A caller whose token has `orders-admin` in `scope` or `roles` gets 200. A caller without it gets:

```text
HTTP 403
WWW-Authenticate: Bearer error="insufficient_scope", error_description="missing required scope: orders-admin", scope="orders-admin", resource_metadata="..."
{"jsonrpc":"2.0","error":{"code":-32002,"message":"insufficient_scope","data":{"http_code":403}},"id":2}
```

- The scope check does not hide the tool: the denied caller still sees `create_user` in
  `tools/list`. To hide tools per consumer, use a policy (`access-control-and-limits.md`).
- `claimNames` lists the token claims to read. In the example above the match comes from a
  `roles` claim holding an array (Keycloak realm roles mapped to a flat claim).
- `scopeSource` left out means `union`: the tool's scopes and the root `security` scopes are
  combined (see https://tyk.io/docs/ai-management/mcp-gateway/oauth-2-1). `operation` uses the
  tool's own list.

## Upstream OAuth (the Gateway logs in to the MCP server as itself)

```json
"upstream": {"url": "http://mcp-server.example.com:7878", "authentication": {"enabled": true, "oauth": {
  "enabled": true, "allowedAuthorizeTypes": ["clientCredentials"],
  "clientCredentials": {"clientId": "tyk-gateway", "clientSecret": "env://UPSTREAM_CLIENT_SECRET",
    "tokenUrl": "https://idp.example.com/realms/demo/protocol/openid-connect/token", "scopes": []}}}}
```

On the Enterprise Edition image the MCP server receives a token issued to the `tyk-gateway`
client (`azp: tyk-gateway`, the client's service account), whatever `Authorization` the caller
sends. One token is shared by all consumers. See
https://tyk.io/docs/ai-management/mcp-gateway/oauth-2-1. On the open source image the same
definition loads, the caller's `Authorization` header goes to the MCP server unchanged, and the
Gateway logs `Upstream OAuth is supported only in Tyk Enterprise Edition`.

`env://UPSTREAM_CLIENT_SECRET` reads the environment variable `UPSTREAM_CLIENT_SECRET` of the
Gateway process, so the secret stays out of the definition (legacy notation, accepted in any
API definition string field: https://tyk.io/docs/tyk-configuration-reference/kv-store-legacy).
On the Enterprise Edition image the token endpoint receives the value of that variable and the
MCP server gets the issued token. A wrong secret fails every call with
`500 {"code":-32603,"message":"failed to get OAuth token: oauth2: \"invalid_client\""}`.

## REST API as MCP tools: what reaches the REST backend

| Sent by | Reaches the REST backend? |
| --- | --- |
| Caller's `Authorization` header (key or JWT) | No |
| Caller's other request headers | No |
| Tool argument mapped to a header parameter of the operation (`X-Tenant`) | Yes |
| Tool argument mapped to a header parameter named `Authorization` | No, dropped |
| Header added by the MCP proxy's own `transformRequestHeaders` | No |
| Header added by the source REST API's `transformRequestHeaders` | Yes |
| Token from token exchange on the MCP proxy | No |
| `$tyk_context.jwt_claims_*` used in a header on the source REST API | Empty |

The documentation lists the other header names that are never forwarded from tool arguments,
among them `Cookie`, `Host`, `Proxy-Authorization` and anything starting `X-Forwarded-` or `Mcp-`.
See https://tyk.io/docs/ai-management/mcp-gateway/api-to-mcp-definitions.

Two consequences:

- **Calls that arrive through a paired MCP proxy are not checked against the source API's
  client authentication**. The
  MCP proxy's own authentication is what protects the tools, so give every REST-to-MCP proxy
  its own authentication.
- **A static backend credential goes on the source REST API**, for example
  `middleware.global.transformRequestHeaders.add` there, with a secret reference as the value.
  `assets/orders-rest-api.oas.json` uses `kv://env/ORDERS_BACKEND_KEY`, which the Gateway reads
  from `TYK_SECRET_ORDERS_BACKEND_KEY` when it loads the API (built-in `env` store,
  https://tyk.io/docs/tyk-configuration-reference/kv-store-providers). Through the MCP proxy the
  backend receives the variable's value in `X-Backend-Key`, on the Dashboard path and the
  Gateway API path (`tykio/tyk-gateway-ee:v5.16.0`), and the stored definition keeps the
  reference. A per-user credential cannot reach the backend through a REST-to-MCP proxy in
  5.16.0.
