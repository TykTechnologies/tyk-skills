# OAuth clients through dynamic client registration (DCR), with Keycloak

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.0, Tyk Dashboard and Gateway 5.16.0, Keycloak 26.8.1

Public pages: https://tyk.io/docs/tyk-stack/tyk-developer-portal/enterprise-developer-portal/api-access/dynamic-client-registration,
https://tyk.io/docs/api-management/client-idp-registry. JWT validation on the Gateway in
general: the `tyk-authentication` skill.

With DCR, approving an access request makes the Portal register an OAuth client for the
developer's app at the identity provider (IdP). The developer gets a client ID and secret,
asks the IdP for a token with the product's and plan's scopes, and calls the API; the Gateway
maps each scope to the product or plan policy.

## What has to be in place

1. **Portal setting** `TYK_PORTAL_ENABLEIDPREGISTRY=true` (restart). The Portal then keeps the
   IdP's JWKS address and the scope-to-policy mappings in the Dashboard's Identity Provider
   Registry (`GET /api/clientidps`) instead of writing them into API definitions. The docs
   recommend it from Portal 1.18.0 with Dashboard 5.14.0.
2. **Dashboard user for the provider has `oauth: write`** (`provider-and-sync.md`).
3. **IdP**: a realm, one OAuth scope per DCR product and per DCR plan, and an initial access
   token. In Keycloak, scopes are client scopes with "include in token scope" on, added to the
   realm's default **optional** client scopes:

   ```bash
   # admin token from the master realm, then (realm "example"):
   POST /admin/realms/example/client-scopes  {"name":"orders_api","protocol":"openid-connect",
        "attributes":{"include.in.token.scope":"true","display.on.consent.screen":"false"}}      # 201
   PUT  /admin/realms/example/default-optional-client-scopes/<scope id>                          # 204
   POST /admin/realms/example/clients-initial-access {"count":50,"expiration":86400}            # {"token":"..."}
   ```

4. **The API**: a Tyk OAS API with JWT authentication and no key source; the Portal and the
   registry supply it. Definition fragment:

   ```json
   "authentication": {"enabled": true, "securitySchemes": {"jwtAuth": {
     "enabled": true, "header": {"enabled": true, "name": "Authorization"},
     "signingMethod": "rsa", "identityBaseField": "sub"}}}
   ```

   Saving a DCR product adds `"jtiValidation": {"enabled": false}` and `"subjectClaims": ["sub"]`
   to that scheme; nothing else in the definition changes, then or on approval.
5. **Network**: the Portal reads the IdP discovery document server-side and calls the
   `registration_endpoint` it finds there; the Gateway fetches the JWKS; developers' clients
   call the token endpoint. Each must reach its address. For example, with Keycloak in Compose:
   `KC_HOSTNAME=http://localhost:48020` and `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`, Portal
   `WellKnownURL` on `http://keycloak:8080/...`. Discovery read from inside the network then
   gives issuer `http://localhost:48020/realms/example` (what tokens carry) and registration,
   token and JWKS endpoints on `keycloak:8080`.

## Portal configuration

```bash
# the OAuth provider: Type is a capitalised enum (Keycloak, Okta, Gluu, Other)
curl -s -X POST "$PORTAL_URL/portal-api/oauth-providers" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d "{\"Name\":\"Example Keycloak\",\"Type\":\"Keycloak\",
  \"WellKnownURL\":\"http://keycloak:8080/realms/example/.well-known/openid-configuration\",
  \"ScopeClaimName\":\"scope\",\"RegistrationAccessToken\":\"$KC_INITIAL_ACCESS_TOKEN\"}" -o /dev/null -w '%{http_code}\n'
# 201.  "Type":"keycloak" -> 422 {"errors":["Field 'Type' contains unacceptable value: 'keycloak'"]}
# Dashboard GET /api/clientidps now lists {"name":"Example Keycloak","issuer":"http://localhost:48020/realms/example",
#   "jwks_uri":"http://keycloak:8080/realms/example/protocol/openid-connect/certs","scope_claim_name":"scope","api_mappings":{}}

# a client type (the docs' "Client Profile"; the product's Templates)
curl -s -X POST "$PORTAL_URL/portal-api/oauth-providers/1/client-types" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"Name":"Server to server","Description":"client credentials",
  "GrantType":"client_credentials","ResponseTypes":"token","TokenEndpointAuthMethod":"client_secret_basic"}'
# 201 {"ID":1,...}

# the product (scope + client type) and a plan (scope)
curl -s -X POST "$PORTAL_URL/portal-api/products" -H "Authorization: $PORTAL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"Name":"Orders JWT product","DisplayName":"Orders (OAuth)","ProviderID":1,"APIDetails":[{"APIID":"orders-jwt"}],
  "DCREnabled":true,"Scopes":"orders_api","Templates":[1],"Catalogues":[1]}'
# 201 {"AuthType":"jwt","DCREnabled":true,"Scopes":"orders_api","Templates":["Server to server"],...}
curl -s -X POST "$PORTAL_URL/portal-api/plans" -H "Authorization: $PORTAL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"Name":"JWT free plan","DisplayName":"Free (OAuth)","ProviderID":1,"RateLimit":5,"Per":60,
  "UnlimitedQuota":true,"JWTScope":"free_plan","Catalogues":[1]}'
# 201 {"JWTScope":"free_plan",...}
```

Allowed values (published API schema): `GrantType` `authorization_code`, `pkce`,
`client_credentials`, `device_code`, `refresh_token`, `password`, `implicit`;
`TokenEndpointAuthMethod` `client_secret_basic`, `client_secret_post`. The recipe on this page
uses `client_credentials` with `client_secret_basic`.

## The developer side

1. The developer requests the product with the plan; the cart shows the client type. Request:
   `pending`, `AuthType` `jwt`, `DCREnabled` true.
2. Approval: 200. The registry entry gains
   `"api_mappings":{"orders-jwt":{"scope_to_policy":{"free_plan":"<plan policy>","orders_api":"<product policy>"}}}`.
   Keycloak gains a client named after the app, service accounts on, optional scopes
   `free_plan` and `orders_api`.
3. The app page shows **Client ID** and **Secret**. Token, then call:

   ```bash
   TOKEN=$(curl -s -u "$CLIENT_ID:$CLIENT_SECRET" "$KC/realms/example/protocol/openid-connect/token" \
     -d grant_type=client_credentials -d 'scope=orders_api free_plan' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
   curl -s -o /dev/null -w '%{http_code}\n' "$GW/orders-jwt/anything" -H "Authorization: Bearer $TOKEN"   # 200
   ```

| Call | Answer |
| --- | --- |
| token with both scopes | 200; plan limit 5 per 60 s then 429 |
| token requested without `scope` | 403 `{"error": "key not authorized: no matching policy found in scope claim"}` |
| no token | 400 `{"error": "Authorization field missing"}` |
| the first call seconds after the **first** approval for a product | 403 `{"error": "Key not authorized"}`; 200 once the Gateway has reloaded (its log shows `reload: complete`), within seconds |

The first approval is what writes the product's scope mappings to the registry, so the Gateways
pick them up on their next reload. Later approvals for the same product reuse the mapping and
work at once. `portal_setup.py verify` retries a 401 or 403 for 20 seconds (`--wait`).

`portal_setup.py verify --token-url <token endpoint> --scope "orders_api free_plan"` runs this
journey end to end.

## Lifecycle

- `PUT /apps/{a}/access-requests/{r}/credentials/{c}/rotate` -> 200: the old Keycloak client is
  deleted and a new one registered (new client ID, new credential ID).
- `DELETE /apps/{a}/access-requests/{r}/credentials/{c}` -> 200 `{"status":"ok"}`: the Keycloak
  client is deleted.
- A failed approval (the provider user without `oauth`) has already registered a client at
  Keycloak; it stays there after the request is approved on a second attempt. After a failed
  DCR approval, look for an orphan client named after the app in the IdP and delete it.

## Other identity providers

`Type: "Other"` is plain RFC 7591 registration. Okta, Gluu, Auth0, Curity and Descope have
their own notes on the public DCR page (initial access token or not, which grants they accept,
whether a client can be updated after registration). This page covers Keycloak; for the others,
follow the public DCR page.
