# JWT: signatures, identity, claims to policies, identity providers

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), Keycloak 26.8.1 (`quay.io/keycloak/keycloak:26.8.1`), Ory Hydra v26.2.0 (`oryd/hydra:v26.2.0`)

Examples use the lab in `lab.md`, through the Gateway API. JWT validation runs on the open source Gateway; no licence is
involved. Docs: https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/json-web-tokens,
https://tyk.io/docs/api-management/authentication/jwt-signature-validation,
https://tyk.io/docs/api-management/authentication/jwt-claim-validation,
https://tyk.io/docs/api-management/authentication/jwt-authorization

## The shape

The OpenAPI part declares an HTTP bearer scheme with `bearerFormat: JWT`; the Tyk part holds the
validation settings under the **same name**. Give Tyk the JWKS URL and the issuer string
exactly as the identity provider publishes them.

```json
"components": {"securitySchemes": {"keycloak": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}},
"security": [{"keycloak": []}],
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true, "stripAuthorizationData": true,
  "securitySchemes": {"keycloak": {
    "enabled": true,
    "header": {"enabled": true, "name": "Authorization"},
    "signingMethod": "rsa",
    "jwksURIs": [{"url": "http://keycloak:8080/realms/example/protocol/openid-connect/certs", "cacheTimeout": "5m"}],
    "identityBaseField": "azp",
    "allowedIssuers": ["http://localhost:8180/realms/example"],
    "allowedAudiences": ["orders-api"],
    "scopes": {"claimName": "scope", "scopeToPolicyMapping": [
      {"scope": "orders:read", "policyId": "orders-read"},
      {"scope": "orders:write", "policyId": "orders-write"}]}}}}}}
```

- The `header` block is required. Without it the Gateway refuses the definition:
  `Validation of API Definition failed. Reason: all auth sources are disabled for jwt, at least one of header/cookie/query must be enabled.`
- `Bearer ` in front of the token is optional; both forms are accepted.
- `signingMethod`: `hmac`, `rsa` or `ecdsa` (ES256/384/512, listed on the signature-validation
  page above). A token whose `alg` does not match the method, including `alg: none`, is
  refused: 403 `Key not authorized: Unexpected signing method`.

## Shared secret (HS256)

`"signingMethod": "hmac", "source": "<base64 of the shared secret>"`. The base64 is of the
secret bytes, not of a key file: `printf '%s' "$JWT_SECRET" | base64`. A token signed with
another secret gets 403 `Key not authorized`. Use this only where you control both the issuer
and the API; any holder of the secret can mint tokens.

## Public keys from a JWKS

`"signingMethod": "rsa", "jwksURIs": [{"url": "...", "cacheTimeout": "20s"}]`. Tyk picks the key
by the token's `kid`. A token signed by a key not in the set, with a `kid` not in the set, or
with no `kid` at all, gets 403 `Key not authorized`. Several URLs can be listed; a key from any
of them is accepted.

### Caching and key rotation

Tyk caches each key set per API, for `cacheTimeout` (per URL) or else the Gateway setting
`jwks.cache.timeout` (`TYK_GW_JWKS_CACHE_TIMEOUT`, seconds), default 240 seconds
(https://tyk.io/docs/api-management/authentication/jwt-signature-validation).
It does **not** refetch when it meets an unknown `kid`. With `cacheTimeout: "20s"`:

| Change at the JWKS endpoint | Effect |
| --- | --- |
| New key `k2` added beside `k1` | Tokens signed with `k2`: 403 until the cached set expires (up to 20 s), then 200 |
| Old key `k1` removed | Tokens signed with `k1` keep working until the cached set expires, about 20 s after the last fetch |
| JWKS endpoint down, key set cached | 200 |
| JWKS endpoint down, key set not cached | 403 `Key not authorized`; 200 again once it is back |

What refreshes the cache at once:

```bash
curl -s -X DELETE "$GW/tyk/cache/jwks/<api id>" -H "x-tyk-authorization: $SECRET"   # one API
# {"status":"ok","message":"cache invalidated"}
curl -s -X DELETE "$GW/tyk/cache/jwks" -H "x-tyk-authorization: $SECRET"            # every API
```

A changed API definition and a Gateway restart also refresh it. A `GET /tyk/reload/group`,
or a `PUT` of an unchanged definition followed by a reload, does **not**. The Dashboard has
`DELETE /api/cache/jwks/{apiID}` for all connected Gateways (same page).

Rotation that never refuses a valid token: publish the new key, wait at least the cache
timeout (or call the flush endpoint on every Gateway), then start signing with it; keep the old
key published until the last token signed with it has expired.

## Identity: which claim names the caller

For an API with `source` or `jwksURIs`, Tyk builds one session per identity value and stores it
under the organisation id plus the MD5 of that value. The identity is `identityBaseField`,
else `sub`. With `identityBaseField: tenant` and no `tenant` claim, the session is keyed on
`sub`. The Tyk OAS field `subjectClaims` is a list; when it is set the Gateway uses it instead
of `identityBaseField` and takes the first listed claim present in the token, then `sub`
(see https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/mw_jwt.go#L736-L760). If both
are set, `subjectClaims` wins.

- Different subjects signed by the same key get **separate** sessions: with a 3-per-minute
  policy, `client-a` reaches 429 while `client-b` still gets 200.
- Every token for the same identity shares one session and one set of counters. Two Keycloak
  tokens for client `partner-a`: the second is refused 429 once the first has used the limit.
- With Keycloak client-credentials tokens, `sub` is the service account's UUID; `azp` and
  `client_id` carry the client id. Hydra puts the client id in `sub` and `client_id`.
- The identity is **not** namespaced by issuer. One API trusting Keycloak and Hydra, each with a
  client called `partner-a`, `identityBaseField: client_id`: after three Keycloak calls the
  Hydra token gets 429. Use an identity claim that is unique across your identity providers, or
  one API per provider.

### `skipKid`

`skipKid` changes which value `allowedSubjects` compares. Without it, a token with a `kid`
header is compared by its `kid`: `allowedSubjects: ["partner-a"]` refuses partner-a's Keycloak
token with 401 `Key not authorized: invalid subject value: cS1w77lt...`. With `skipKid: true`
partner-a gets 200 and partner-b 401 `invalid subject value: partner-b`. It does not change the
session identity for a JWKS or `source` API (that is `identityBaseField` or `sub` either way).

## Issuer, audience, expiry, custom claims

| Setting | Behaviour |
| --- | --- |
| `allowedIssuers` | wrong `iss`: 401 `invalid issuer claim: <iss>`; no `iss`: 401 `issuer claim is required but not present in token` |
| `allowedAudiences` | matches a string `aud` or any element of an array `aud`; a Keycloak token without an audience mapper (`aud: "account"`) gets 401 `no matching audience found in token: [account]` |
| `exp`, `nbf`, `iat` | always checked: 401 `token has expired`, `token is not valid yet`, `token used before issued`. `expiresAtValidationSkew`, `notBeforeValidationSkew` and `issuedAtValidationSkew` (seconds) allow clock drift |
| `customClaimValidation` | `{"azp": {"type": "exact_match", "allowedValues": ["partner-a"]}}`, `{"scope": {"type": "contains", "allowedValues": ["orders:read"]}}` (substring of a string claim), `{"tenant": {"type": "required"}}`; failures are 401 with the claim named |

Set `allowedIssuers` and `allowedAudiences` on every API that trusts a shared identity
provider: without `allowedAudiences`, a token the provider issued for another API is accepted.

## Claims to policies

The session's policies decide access rights, rate limit and quota. Three sources, in this order
when the session is created:

1. **Policy claim**: `"policyFieldName": "pol"` (and `"basePolicyClaims": ["pol"]`). A token
   with `pol: jwt-gold` gets the `jwt-gold` policy; a later token for the same subject with a
   different `pol` switches the session to that policy. `pol` naming a policy that does not
   exist: 403 `key not authorized: no matching policy`.
   Written through the Gateway API, `basePolicyClaims` alone is stored and **ignored**: every
   token gets the default policy. Set `policyFieldName` as well.
2. **Scopes**: `"scopes": {"claimName": "scope", "scopeToPolicyMapping": [...]}` (or
   `"claims": ["scope", "scp"]`; the first claim present is used, so Keycloak's space-separated
   `scope` string and Hydra's `scp` array both map). Each matched scope adds its policy; the
   session gets all of them. A token whose scope claim is present but matches no entry gets 403
   `key not authorized: no matching policy found in scope claim`, **also when
   `defaultPolicies` is set**: map every scope a valid caller can carry.
3. **Default**: `"defaultPolicies": ["<policy id>"]` when the token has neither. The policy must
   exist on the Gateway, or a valid token gets 500
   `failed to create key: policy not found: "<policy id>"`.

The policy (or policies) must grant this API in `access_rights`, or the request gets 403
`Access to this API has been disallowed`. How limits combine across several policies is in the
`tyk-rate-limits-keys-policies` skill.

To see what a caller got, read its session. The session id depends on the Gateway's
`hash_key_function` (the Docker image's `tyk.conf` sets `murmur64`; the lab's does not):

```bash
ID=$(printf %s '<identity>' | md5)                     # Linux: md5sum | cut -c1-32
SID=$(printf '{"org":"default","id":"%s","h":"murmur64"}' "$ID" | base64 | tr -d '\n')   # with hash_key_function
SID=default$ID                                         # without it
curl -s "$GW/tyk/keys/$SID" -H "x-tyk-authorization: $SECRET"
```

`default` is the API's `orgId`. Each form reads the session back on a Gateway with the matching setting. Its `apply_policies`,
`rate` and `alias` show the mapping. Deleting that key resets the caller's session.

## Two identity providers on one API

```json
"jwksURIs": [{"url": "http://keycloak:8080/realms/example/protocol/openid-connect/certs"},
             {"url": "http://hydra:4444/.well-known/jwks.json"}],
"allowedIssuers": ["http://localhost:8180/realms/example", "http://hydra:4444"],
"identityBaseField": "client_id",
"scopes": {"claims": ["scope", "scp"], "claimName": "scope", "scopeToPolicyMapping": [{"scope": "orders:read", "policyId": "orders-read"}]}
```

Keycloak and Hydra client-credentials tokens both get 200 with the `orders-read` policy. A Hydra
opaque token on this API: 403 `Key not authorized` (it is not a JWT; see `oauth.md`).

## Keycloak and Hydra specifics

| Point | Keycloak 26.8.1 | Ory Hydra v26.2.0 |
| --- | --- | --- |
| JWKS URL (from the Gateway) | `http://keycloak:8080/realms/<realm>/protocol/openid-connect/certs` | `http://hydra:4444/.well-known/jwks.json` |
| `iss` | the `KC_HOSTNAME` URL plus `/realms/<realm>` (the lab sets `KC_HOSTNAME`) | `URLS_SELF_ISSUER` exactly |
| Audience | add an audience mapper (`oidc-audience-mapper`); without it `aud` is `account` | the client's `audience` list, requested with `audience=orders-api` |
| Scopes | `scope`, space-separated string | `scp`, JSON array |
| Client id | `azp` and `client_id` | `sub` and `client_id` |

Setup commands for both are in `lab.md`.
