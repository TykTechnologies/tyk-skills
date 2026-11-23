---
name: tyk-authentication
description: Secure a Tyk OAS API with the right client authentication on Gateway 5.16 (auth token, Basic, HMAC, JWT with JWKS and identity providers, OAuth 2.0 scopes and introspection, client mTLS, several methods, custom plugins) and know each failure's exact response. Use when asked to add or debug API keys, JWT, OIDC, OAuth, mTLS or HMAC, a 400, 401 or 403 from Tyk, scopes to policies, or JWKS rotation. Not for rate limits and quotas, or for MCP proxies, which belong to the `tyk-rate-limits-keys-policies` and `tyk-mcp-gateway` skills.
---

# Tyk Authentication

Agents asked to "add JWT" or "add mTLS" to a Tyk API tend to copy a scheme from the OpenAPI
document, leave out the Tyk fields that make it work, and test only the happy path. Tyk
Gateway 5.16.0 accepts several of those definitions and then refuses every request, or lets
through more than intended. This skill gives the definitions, commands and exact responses
for each method on the open source Gateway 5.16.0 with Keycloak 26.8.1 and Ory Hydra
v26.2.0, plus a checker and two test tools.

## What you need

- Every method here runs on the **open source Gateway** with Redis, no licence. A Dashboard
  adds a UI and API over the same objects and needs a licence; token exchange needs the
  Enterprise Edition Gateway. Editions and trials: the `tyk-facts` skill.
- A stack: `references/lab.md` (copy `assets/auth-lab/`), the `tyk-demo-builder` skill for a
  Dashboard demo, or the `tyk-deployment` skill for a real installation.

## Clarify first

Ask only what the request leaves open. Defaults in brackets.

1. **Who calls the API?** Machines of known partners, end users through an app, or the public.
   [Partners: one credential per partner.]
2. **Does an identity provider already issue tokens, and are they JWTs?** [Yes: JWT scheme with
   its JWKS. Opaque only: introspection. None: auth token, or Tyk's own OAuth server.]
3. **What decides a caller's rights and limits?** A claim, a scope, or a fixed plan.
   [Scopes to policies; a default policy only when every caller gets the same.]
4. **Is a client certificate required, and is it the identity?** [No. If yes and per partner:
   certificate authentication.]
5. **Is more than one method needed, and must callers present one or all?** [One method.]
6. **Dashboard or open source Gateway?** [Find out: a Dashboard-managed Gateway shows a
   `dashboard` check in `curl -s $GW/hello`.]

## Choose the method

| Situation | Method | Reference |
| --- | --- | --- |
| Partners with a shared secret, simplest | Auth token in a header (also query or cookie) | `token-basic-hmac.md` |
| A legacy client that only does user and password | Basic | `token-basic-hmac.md` |
| Requests must be signed, not only carry a secret | HMAC request signing | `token-basic-hmac.md` |
| An identity provider issues JWTs (Keycloak, Entra ID, Okta, Auth0, Hydra) | JWT with `jwksURIs`, issuer and audience checks, scopes to policies | `jwt-and-identity-providers.md` |
| Different scopes per operation | JWT plus an `oauth2` scheme with `scopeCheck` | `oauth.md` |
| Opaque tokens from an external server | External OAuth introspection (deprecated) | `oauth.md` |
| Tyk should issue tokens itself | Tyk's OAuth 2.0 server | `oauth.md` |
| Certificate per partner as the credential | Certificate authentication | `mutual-tls.md` |
| Certificate as an extra TLS gate | Static client-certificate list, plus a token | `mutual-tls.md` |
| "Key or JWT", "any of these" | Compliant multi-auth | `multiple-keyless-custom.md` |
| Public API | Keyless | `multiple-keyless-custom.md` |
| A credential format Tyk does not know | Custom authentication plugin | `multiple-keyless-custom.md` |

## Workflow

Set these once. `$SKILL` is this skill's folder.

```bash
SKILL=<this skill's folder>
GW=http://localhost:8080            # Gateway, HTTP
GWS=https://localhost:8443          # Gateway on HTTPS, for client certificates
SECRET=<gateway secret>             # "secret" in tyk.conf, or TYK_GW_SECRET
DASH=http://localhost:3000          # Dashboard, when there is one
DASHBOARD_API_KEY=<Dashboard user API key>
```

1. **Pick the method** from the table above and read its reference file.

2. **Write the authentication block.** Three places must agree: the scheme in
   `components.securitySchemes`, the top-level `security`, and
   `x-tyk-api-gateway.server.authentication.securitySchemes.<same name>` with `"enabled": true`.
   HMAC, certificate authentication and custom plugins live only in the Tyk part. Always add
   the `header` block (`{"enabled": true, "name": "Authorization"}`) to Basic, JWT and HMAC.
   Example for an identity provider, with JWKS, issuer and audience checks and
   scopes to policies:

   ```json
   "components": {"securitySchemes": {"idp": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}},
   "security": [{"idp": []}],
   "x-tyk-api-gateway": {"server": {"authentication": {"enabled": true, "stripAuthorizationData": true,
     "securitySchemes": {"idp": {"enabled": true, "header": {"enabled": true, "name": "Authorization"},
       "signingMethod": "rsa",
       "jwksURIs": [{"url": "http://keycloak:8080/realms/example/protocol/openid-connect/certs", "cacheTimeout": "5m"}],
       "identityBaseField": "azp",
       "allowedIssuers": ["http://localhost:8180/realms/example"], "allowedAudiences": ["orders-api"],
       "scopes": {"claimName": "scope", "scopeToPolicyMapping": [{"scope": "orders:read", "policyId": "orders-read"}]}}}}}}
   ```

3. **Check it before loading.** Both checkers, both clean:

   ```bash
   python3 "$SKILL/scripts/check_auth.py" api.json
   # 0 error(s), 0 warning(s), 0 note(s)
   python3 <tyk-api-definitions-routing skill>/scripts/check_tyk_oas.py api.json
   ```

   Fix every `ERROR`; explain every `WARN` or `NOTE` to the user before going on.

4. **Load the API and what the credentials need.** Create it, the policies it maps to, and a
   reload (the `tyk-api-definitions-routing` and `tyk-rate-limits-keys-policies` skills). Then the
   credential for each test consumer: a key, a Basic user, an HMAC key, an identity provider
   client, an OAuth client or a registered certificate, as in the method's reference file.

5. **Prove the four cases per method.** Valid, missing, invalid, expired, against
   `references/responses.md`. Test tools:

   ```bash
   export JWT_SECRET=<shared secret>   # HS256 APIs only
   T=$(python3 "$SKILL/scripts/mint_jwt.py" hs256 --secret-env JWT_SECRET \
         --claim iss=https://idp.example.com --claim aud=orders-api --claim sub=partner-a --ttl -60)
   curl -s -H "Authorization: Bearer $T" "$GW/orders/anything"
   # 401 {"error": "Key not authorized: token has expired"}
   python3 "$SKILL/scripts/sign_hmac_request.py" --url "$GW/hmac/anything" --key-id "$KEY" \
     --secret-env HMAC_SECRET --send
   # HTTP 200
   ```

   For identity-provider tokens, get real ones from the provider (`lab.md` has Keycloak and
   Hydra) and use `mint_jwt.py` with your own key set for the cases the provider will not
   issue (wrong audience, wrong issuer, unknown `kid`).

6. **Prove the identity and its rights.** Two consumers with different policies must get
   different results, and a burst over one consumer's limit must not affect the other. For a
   JWT API read the session the Gateway built for an identity and check `apply_policies`:

   ```bash
   ID=$(printf %s partner-a | md5)     # Linux: md5sum | cut -c1-32
   SID=$(printf '{"org":"default","id":"%s","h":"murmur64"}' "$ID" | base64 | tr -d '\n')
   # the Docker image's tyk.conf sets hash_key_function murmur64; without it: SID=default$ID
   curl -s "$GW/tyk/keys/$SID" -H "x-tyk-authorization: $SECRET"   # "alias":"partner-a","apply_policies":["orders-read"]
   ```

7. **Prove the lifecycle the user will meet.** Revoke one credential and time it (keys: about
   10 seconds; certificates: delete the session). For JWKS, rotate a key and flush with
   `curl -s -X DELETE "$GW/tyk/cache/jwks/<api id>" -H "x-tyk-authorization: $SECRET"`.

8. **Tell the user** which method, what each failure returns, where the identity comes from,
   how revocation and key rotation behave, and what was not checked on their stack.

## References

| File | Read it for |
| --- | --- |
| `references/responses.md` | Exact status and body for missing, invalid and expired credentials, every method |
| `references/token-basic-hmac.md` | Auth token sources, Basic users, HMAC signing and keys, revocation timing |
| `references/jwt-and-identity-providers.md` | HS256 and JWKS, caching and rotation, identity claim, issuer, audience, custom claims, claims and scopes to policies, Keycloak and Hydra |
| `references/oauth.md` | Scope checks per operation, introspection, Tyk's own OAuth server |
| `references/mutual-tls.md` | Certificate store, static list versus certificate authentication, mTLS plus a token |
| `references/multiple-keyless-custom.md` | Compliant multi-auth with HMAC, errors and limits; keyless; what a custom auth plugin must return for per-caller limits |
| `references/pitfalls-and-limitations.md` | Twenty-one pitfalls, the product's limits, what needs a licence |
| `references/lab.md` | The lab: two Gateways, Keycloak, Hydra, your own key set |

Scripts (Python standard library): `scripts/check_auth.py`, `scripts/mint_jwt.py` (needs the
`openssl` command for RS256), `scripts/sign_hmac_request.py`. Assets: `assets/auth-lab/`.

## Pitfalls

The costliest; all twenty-one are in `references/pitfalls-and-limitations.md`.

- Basic without a `header` block loads and refuses everyone with 401.
- `basePolicyClaims` alone is ignored through the Gateway API; set `policyFieldName` too.
- A scope claim that matches no mapping is 403, even with `defaultPolicies`.
- No `allowedAudiences`: tokens issued for other APIs are accepted.
- New JWKS keys are refused until the cache expires (default 240 s); reloads do not flush it.
- Tokens for one identity share one session; two identity providers with the same client id
  share one rate limit.
- The `oauth2` scope check is opt-in per operation and breaks with `stripAuthorizationData`.
- A CA in the static certificate list trusts every certificate it signs.
- HMAC without `allowedClockSkew` accepts replayed requests.
- No token on a JWT API is 400, not 401.
- MCP proxies: the `tyk-mcp-gateway` skill. A JWT-protected MCP proxy needs
  `stripAuthorizationData: false`; with `true` every call gets 401 there.

## Limitations

- No JWKS refetch on an unknown `kid`; no per-operation client certificates.
- Introspection is a deprecated block: one provider, cached results, no policies.
- The `oauth2` scope check does not validate signatures; a JWT scheme must sit beside it.
- HMAC does not sign the body, and all its failures look the same to the client.
- Token exchange needs the Enterprise Edition Gateway; this skill's recipes use the open source
  Gateway, so for it follow https://tyk.io/docs/api-management/authentication/token-exchange.

## Verification checklist

Before reporting success:

- [ ] `check_auth.py` and `check_tyk_oas.py` report no errors, and every warning is explained.
- [ ] For each method: a valid credential got 200, and missing, invalid and expired got the
      status and `error` in `references/responses.md`.
- [ ] Two consumers with different policies got different access or limits, and the
      session (or key) shows the policy you expected.
- [ ] JWT: a token with the wrong `iss` and one with the wrong `aud` were refused with 401.
- [ ] Scope checks: a token without the operation's scope got 403 `insufficient_scope`.
- [ ] Client certificates: a certificate from the same CA but not registered (or pinned) was
      refused.
- [ ] Revocation was timed, and JWKS rotation was tried when the user rotates keys.
- [ ] `stripAuthorizationData` matches what the upstream may see (checked in the echo).
- [ ] No secret, key, token or certificate key is written into a file you leave behind.

## Hand-offs

- Writing the rest of the API definition, `ignoreAuthentication`, versions: the
  `tyk-api-definitions-routing` skill.
- Keys, policies, rate limits and quotas: the `tyk-rate-limits-keys-policies` skill.
- Writing and deploying plugins: the `tyk-custom-plugins` skill.
- OAuth for MCP clients and Protected Resource Metadata: the `tyk-mcp-gateway` skill.
- Developer Portal credentials and client registration: the `tyk-developer-portal-setup` skill.
- Errors you cannot explain from the tables: the `tyk-troubleshooting` skill.
- Licensing and editions: the `tyk-facts` skill. A demo: the `tyk-demo-builder` skill. A real
  installation: the `tyk-deployment` skill.
