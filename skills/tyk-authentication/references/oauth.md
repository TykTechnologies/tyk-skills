# OAuth 2.0: an external authorisation server, and Tyk's own

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), Keycloak 26.8.1, Ory Hydra v26.2.0

Examples use the lab in `lab.md`. Overview of the three options: https://tyk.io/docs/api-management/authentication/oauth2-authentication

| You have | Use | Where |
| --- | --- | --- |
| An identity provider that issues **JWT** access tokens (Keycloak, Entra ID, Okta, Auth0, Hydra with JWT tokens) | A JWT scheme that checks signature, issuer and audience, plus, for per-operation scopes, an `oauth2` scheme with `scopeCheck` | `jwt-and-identity-providers.md`, and below |
| An identity provider that issues **opaque** tokens and offers RFC 7662 introspection | The External OAuth block with `introspection` (deprecated since 5.7.0, still working on 5.16.0) | below |
| No identity provider; Tyk should issue tokens to API clients it registers itself | Tyk's own OAuth 2.0 server | below |

Token exchange (RFC 8693) on the `oauth2` scheme needs the Enterprise Edition Gateway and is
outside this skill: https://tyk.io/docs/api-management/authentication/token-exchange (licensing
in the `tyk-facts` skill).

## External identity provider, JWT tokens, scopes per operation

The `oauth2` scheme does not validate a token itself: it reads the scope claims and compares
them with the scopes each operation declares. Signature, issuer and audience come from a JWT
scheme on the same API. With Keycloak tokens (`partner-a` carries `orders:read`,
`partner-b` `orders:write`):

```json
"paths": {"/orders": {
  "get":  {"operationId": "listOrders",  "security": [{"idpAuth": ["orders:read"]}],  "responses": {"200": {"description": "OK"}}},
  "post": {"operationId": "createOrder", "security": [{"idpAuth": ["orders:write"]}], "responses": {"200": {"description": "OK"}}}}},
"components": {"securitySchemes": {
  "jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
  "idpAuth": {"type": "oauth2", "flows": {"clientCredentials": {
     "tokenUrl": "http://localhost:8180/realms/example/protocol/openid-connect/token",
     "scopes": {"orders:read": "Read", "orders:write": "Write"}}}}}},
"security": [{"jwtAuth": []}],
"x-tyk-api-gateway": {
  "server": {"authentication": {"enabled": true,
    "securitySchemes": {
      "jwtAuth": {"enabled": true, "header": {"enabled": true, "name": "Authorization"}, "signingMethod": "rsa",
                  "jwksURIs": [{"url": "http://keycloak:8080/realms/example/protocol/openid-connect/certs"}],
                  "identityBaseField": "azp", "allowedIssuers": ["http://localhost:8180/realms/example"],
                  "allowedAudiences": ["orders-api"], "defaultPolicies": ["orders-default"]},
      "idpAuth": {"enabled": true, "header": {"enabled": true, "name": "Authorization"},
                  "scopeCheck": {"enabled": true, "claimNames": ["scope", "scp"], "scopeSource": "operation"}}}}},
  "middleware": {"operations": {
    "listOrders":  {"scopeCheck": {"enabled": true}},
    "createOrder": {"scopeCheck": {"enabled": true}}}}}
```

Results: partner-a `GET` 200, `POST` 403 `insufficient_scope`; partner-b `POST` 200, `GET` 403.
No token: 400 `Authorization field missing` (from the JWT scheme). An unsigned token carrying
both scopes: 403 `Key not authorized: Unexpected signing method` (the JWT scheme refuses it
before any scope is read).

Three things that silently switch the check off or break it:

1. **Operation-level checks are opt-in.** Without
   `middleware.operations.<operationId>.scopeCheck.enabled: true` the operation's `security`
   scopes are not enforced: both partners get 200 on both methods.
2. **`stripAuthorizationData: true` breaks it.** The token is removed before the scope check
   runs: every request gets 401 `Authorization field missing` with
   `WWW-Authenticate: Bearer error="invalid_token", error_description="missing bearer token"`.
   Leave it off on these APIs; the upstream then receives the token.
3. `scopeSource` decides which declarations count: `operation` (each operation's `security`),
   `global` (the root `security` only) or `union` (default, both). With `scopeSource: global`
   and root `security: [{"jwtAuth": [], "idpAuth": ["orders:read"]}]`, partner-a gets 200 and
   partner-b 403 on every operation, with no per-operation opt-in needed.

The JWT scheme can still map scopes to policies (`jwt-and-identity-providers.md`); the scope
check is a separate gate per operation.

## External identity provider, opaque tokens: introspection

The External OAuth block is the only built-in way to introspect opaque tokens. It is deprecated
since 5.7.0 (the Gateway logs a deprecation warning when it loads) and still works on 5.16.0.
It sits under an OpenAPI `oauth2` scheme, keyed by `providers`:

```json
"components": {"securitySchemes": {"hydra": {"type": "oauth2", "flows": {"clientCredentials": {
   "tokenUrl": "http://localhost:4444/oauth2/token", "scopes": {"orders:read": "Read orders"}}}}}},
"security": [{"hydra": []}],
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true, "stripAuthorizationData": true,
  "securitySchemes": {"hydra": {"enabled": true, "header": {"enabled": true, "name": "Authorization"},
    "providers": [{"introspection": {"enabled": true,
       "url": "http://hydra:4445/admin/oauth2/introspect",
       "clientId": "<introspection client>", "clientSecret": "<its secret>",
       "identityBaseField": "client_id",
       "cache": {"enabled": true, "timeout": 15}}}]}}}}}
```

With Hydra opaque tokens: valid 200; unknown 401 `access token is not valid`; no token 400
`authorization field missing`; introspection endpoint down 500
`error happened during the access token validation`.

What to tell the user:

- **Revocation waits for the cache.** With `cache.timeout: 15`, a token revoked at Hydra keeps
  getting 200 until the cached answer expires, then 401 (about 15 seconds). Without the cache every request calls
  the introspection endpoint.
- **No policy.** The session it creates carries no policy, and the block has no field for a
  default policy or a scope mapping, so there are no per-client limits.
- **Sessions are shared by identity.** A Hydra client whose identity already had a session from
  a JWT API (granting only that API) gets 403 `Access to this API has been disallowed` here; a
  fresh client gets 200.
- Configure one entry in `providers`; the documentation limits it to one
  (https://tyk.io/docs/api-management/client-authentication).

If the provider can issue JWT access tokens, prefer that and the JWT scheme.

## Tyk's own OAuth 2.0 server

Use it when Tyk itself should register API clients and issue their tokens, for example when
there is no identity provider. Each token Tyk issues is a Tyk key carrying the client's policy.
Docs: https://tyk.io/docs/api-management/authentication/oauth-2

The OpenAPI `oauth2` scheme's `flows` decide which grants Tyk allows; the Tyk part only switches
it on:

```json
"components": {"securitySchemes": {"tykOAuth": {"type": "oauth2",
  "flows": {"clientCredentials": {"tokenUrl": "/tyk-oauth/oauth/token", "scopes": {}}}}}},
"security": [{"tykOAuth": []}],
"x-tyk-api-gateway": {"server": {"listenPath": {"value": "/tyk-oauth/", "strip": true},
  "authentication": {"enabled": true, "stripAuthorizationData": true,
    "securitySchemes": {"tykOAuth": {"enabled": true, "header": {"enabled": true, "name": "Authorization"}}}}}}
```

```bash
# register a client (open source Gateway); the policy grants the API
curl -s -X POST "$GW/tyk/oauth/clients/create" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' \
  -d '{"api_id": "tyk-oauth", "redirect_uri": "https://partner-a.example.com/cb", "policy_id": "oauth-std"}'
# {"client_id":"f533e8…","redirect_uri":"https://partner-a.example.com/cb","policy_id":"oauth-std","secret":"<secret>",…}

# the client gets a token from the API's own /oauth/token path
curl -s -u "$CLIENT_ID:$CLIENT_SECRET" -d grant_type=client_credentials "$GW/tyk-oauth/oauth/token"
# {"access_token":"default…","expires_in":3600,"token_type":"bearer"}
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" "$GW/tyk-oauth/anything"   # 200
```

- `redirect_uri` must not be empty even for client credentials: such a client is created, and
  its token requests get 403 `unauthorized_client`.
- A grant the `flows` do not list gets 403 `unsupported_grant_type`.
- The access token is readable as a key (`GET /tyk/keys/{token}`) with the client's policy in
  `apply_policies`.
- The authorisation code flow also needs a login page of your own that Tyk redirects to
  (`authLoginRedirect`); the docs page above describes it.
