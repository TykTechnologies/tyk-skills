# Pitfalls and limitations

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), Keycloak 26.8.1, Ory Hydra v26.2.0

The file that holds the detail is named in brackets. `scripts/check_auth.py` flags the ones a definition
can show.

## Pitfalls: accepted, then wrong

1. **Basic without a `header` block** loads and refuses every request with 401
   `Authorization field missing`, correct password included. JWT and HMAC without one are
   refused at load instead. (`token-basic-hmac.md`)
2. **HMAC without `allowedClockSkew`** accepts a request dated 1 January 2024: captured
   requests can be replayed. The value is milliseconds. (`token-basic-hmac.md`)
3. **HMAC signs the full path.** Signing the path without the listen path gives 400, and all
   HMAC failures give the same 400. (`token-basic-hmac.md`)
4. **A revoked key keeps working for about 10 seconds** on a Gateway that served it recently
   (local session cache). (`token-basic-hmac.md`)
5. **`basePolicyClaims` alone is ignored** when the definition is written through the Gateway
   API: every token gets the default policy. Also set `policyFieldName`. (`jwt-and-identity-providers.md`)
6. **An unmatched scope is a 403 even with `defaultPolicies`**: a scope claim that maps to no
   policy gives `no matching policy found in scope claim`. (`jwt-and-identity-providers.md`)
7. **No `allowedAudiences`** means a token the identity provider issued for another API is
   accepted. A Keycloak client without an audience mapper has `aud: account`. (`jwt-and-identity-providers.md`)
8. **`allowedSubjects` compares the `kid`** unless `skipKid: true`. (`jwt-and-identity-providers.md`)
9. **A new signing key is refused until the JWKS cache expires** (default 240 seconds), and a
   removed key keeps working until then. A plain reload does not refresh it;
   `DELETE /tyk/cache/jwks/{apiID}` does. (`jwt-and-identity-providers.md`)
10. **Identity is not namespaced by issuer.** Two identity providers on one API with the same
    client id share one session and one rate limit. (`jwt-and-identity-providers.md`)
11. **Every token for one identity shares one session.** Rate limits and quotas are per
    identity value (`identityBaseField`), not per token. Pick the claim that names the
    consumer you want to limit. (`jwt-and-identity-providers.md`)
12. **The `oauth2` scope check is opt-in per operation** (`middleware.operations.<id>.scopeCheck`),
    and **`stripAuthorizationData: true` breaks it** with 401 `missing bearer token`. (`oauth.md`)
13. **Introspection results are cached**: a revoked token keeps working until the cache entry
    expires (15 seconds with `cache.timeout: 15`). (`oauth.md`)
14. **Tyk's OAuth server needs a `redirect_uri`** even for client credentials; an empty one
    makes every token request fail with `unauthorized_client`. (`oauth.md`)
15. **A CA in the static client-certificate list trusts every certificate it signs**, with no
    identity per client. Pin individual certificates, or use certificate authentication.
    (`mutual-tls.md`)
16. **Client certificates need the Gateway on HTTPS**; over HTTP the answer is 403
    `TLS not enabled`. (`mutual-tls.md`)
17. **Several `security` entries in legacy mode use only the first**, and an enabled Tyk method
    (HMAC) is still required; use compliant mode for "any one of". (`multiple-keyless-custom.md`)
18. **Compliant mode answers with the last alternative's error** and applies the first matching
    method's limits, without falling back to another credential in the same request.
    (`multiple-keyless-custom.md`)
19. **A missing default policy is a 500** for valid tokens:
    `failed to create key: policy not found: "<id>"`. (`jwt-and-identity-providers.md`)
20. **No token on a JWT API is 400, not 401**, unless the API publishes Protected Resource
    Metadata. Clients that expect 401 to trigger a token refresh need to treat 400 the same.
    (`responses.md`)
21. **Sessions from a custom auth plugin share one rate-limit counter** unless the session's
    `meta_data` sets `rate_limit_pattern` (for example `$tyk_meta.partner`): a second
    partner's first request gets 429. The pattern's value is shared across APIs, so include
    the API in it for per-API limits. (`multiple-keyless-custom.md`)

## Limitations: what the product does not do

- Tyk does not refetch a JWKS when a token carries an unknown `kid`; it waits for the cache to
  expire or for a flush.
- The External OAuth (introspection) block is deprecated, uses one provider, and its sessions
  carry no policy: no per-client limits for opaque tokens.
- The `oauth2` scheme's scope check does not validate signatures; it needs a JWT scheme beside
  it.
- Tyk OAS has no `mutualTLS` security scheme; client certificates are configured in the Tyk
  extension, API-wide, not per operation.
- HMAC signing covers headers and the request target, not the body.
- Every HMAC failure returns the same 400; only the Gateway log says which check failed.
- Token exchange (RFC 8693) needs the Enterprise Edition Gateway; the recipes here use the open
  source Gateway. Follow https://tyk.io/docs/api-management/authentication/token-exchange, and
  the `tyk-facts` skill for editions.

## What needs what

Everything in this skill ran on the **open source Gateway** with Redis and no licence: auth
token, Basic, HMAC, JWT (HMAC and JWKS), scope checks, External OAuth introspection, Tyk's OAuth
server, static client certificates, certificate authentication, compliant multi-auth, keyless
and a JavaScript custom-auth plugin. A Dashboard adds a UI and its own API for the same objects
(keys, policies, certificates, OAuth clients) and needs a licence. For editions, licences and
trials, use the `tyk-facts` skill; do not state them from this file.
