# What a client sees: missing, invalid and expired credentials

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), Keycloak 26.8.1, Ory Hydra v26.2.0

Rows are for Gateway 5.16.0 with the lab in `lab.md`. Bodies are
shown on one line; the Gateway sends indented JSON, so match on the `error` value, not on the
bytes. Use this table to tell a client which credential failed and why.

## Auth token (API key)

| Request | Status | Body `error` |
| --- | --- | --- |
| No credential in any configured place | 401 | `Authorization field missing` |
| Unknown or deleted key; also a valid key while Redis is down (the `tyk-troubleshooting` skill) | 403 | `Access to this API has been disallowed` |
| Key whose policy grants another API only | 403 | `Access to this API has been disallowed` |
| Key in `Authorization` when the scheme reads `X-API-Key` | 401 | `Authorization field missing` |
| Key past its `expires` | 401 | `Key has expired, please renew` |
| Valid key with `Bearer ` in front | 200 | (the prefix is stripped) |

## Basic

| Request | Status | Body `error` |
| --- | --- | --- |
| No `Authorization` header | 401 | `Authorization field missing`, plus `WWW-Authenticate: Basic realm="<API name>"` |
| Wrong password, or unknown user | 401 | `User not authorised` |
| Not `Basic <base64>` (for example `Bearer abc`, or bad base64) | 400 | `Attempted access with malformed header, auth data not encoded correctly` |
| Base64 without a colon | 400 | `Attempted access with malformed header, values not in basic auth format` |
| User past its `expires` | 401 | `Key has expired, please renew` |

## HMAC request signing

Every failure gives the same answer: **400** `Authorization field missing, malformed or invalid`.
That covers no header, unknown key id, wrong secret, an algorithm not in `allowedAlgorithms`,
a signed header changed after signing, a `Date` older than `allowedClockSkew`, a missing or
unparsable `Date`, a path signed without the listen path, and a key without `hmac_enabled`.
The Gateway log says which (`Signature string does not match!`, `Date parsing failed`,
`Algorithm not supported`).

## JWT

| Request | Status | Body `error` |
| --- | --- | --- |
| No token | 400 | `Authorization field missing` (401 when the API publishes Protected Resource Metadata; see the `tyk-mcp-gateway` skill) |
| Not a JWT, wrong signature, unknown `kid`, no `kid` with a JWKS | 403 | `Key not authorized` |
| `alg` not the configured `signingMethod` (including `none`) | 403 | `Key not authorized: Unexpected signing method` |
| `exp` in the past | 401 | `Key not authorized: token has expired` |
| `nbf` in the future | 401 | `Key not authorized: token is not valid yet` |
| `iat` in the future | 401 | `Key not authorized: token used before issued` |
| `iss` not in `allowedIssuers` | 401 | `Key not authorized: invalid issuer claim: <iss>` |
| No `iss` while `allowedIssuers` is set | 401 | `Key not authorized: issuer claim is required but not present in token` |
| `aud` not in `allowedAudiences` | 401 | `Key not authorized: no matching audience found in token: [<aud>]` |
| Subject not in `allowedSubjects` | 401 | `Key not authorized: invalid subject value: <subject>` |
| `customClaimValidation` `exact_match` fails | 401 | `Key not authorized: claim <name> value does not match any expected values` |
| `customClaimValidation` `contains` fails | 401 | `Key not authorized: claim <name> value does not contain any expected values` |
| `customClaimValidation` `required` claim absent | 401 | `Key not authorized: custom claim <name> is required but not present in token` |
| No identity claim (neither `identityBaseField` nor `sub`) | 403 | `no suitable claims for user ID were found` |
| Policy claim names a policy that does not exist | 403 | `key not authorized: no matching policy` |
| Scope claim present, no scope maps to a policy | 403 | `key not authorized: no matching policy found in scope claim` |
| JWKS endpoint unreachable when the key set is not cached | 403 | `Key not authorized` |

## OAuth 2.0 scope check (`oauth2` scheme with `scopeCheck`)

| Request | Status | Body |
| --- | --- | --- |
| Token lacks the operation's scope | 403 | `{"error":"insufficient_scope","error_description":"token does not satisfy required scopes: orders:write","scope":"orders:write"}` and `WWW-Authenticate: Bearer error="insufficient_scope", error_description="missing required scope: orders:write", scope="orders:write"` |
| Token removed before the check (`stripAuthorizationData: true`) | 401 | `Authorization field missing`, with `WWW-Authenticate: Bearer error="invalid_token", error_description="missing bearer token"` |

## External OAuth with introspection (deprecated block)

| Request | Status | Body `error` |
| --- | --- | --- |
| No token | 400 | `authorization field missing` |
| Inactive, revoked or unknown token | 401 | `access token is not valid` |
| Introspection endpoint unreachable | 500 | `error happened during the access token validation` |
| Identity already holds a session for another API only | 403 | `Access to this API has been disallowed` |

## Tyk's own OAuth 2.0 server

| Request | Status | Body |
| --- | --- | --- |
| API call without a token | 400 | `{"error": "Authorization field missing"}` |
| API call with an unknown token | 403 | `{"error": "Key not authorised"}` (British spelling, unlike JWT) |
| Token request with a wrong client secret | 403 | `{"error":"unauthorized_client","error_description":"The client is not authorized to request a token using this method."}` |
| Token request from a client with an empty `redirect_uri` | 403 | same `unauthorized_client` body |
| Token request with a grant type the API does not allow | 403 | `{"error":"unsupported_grant_type","error_description":"The authorization grant type is not supported by the authorization server."}` |

## Client certificates

| Setup and request | Status | Body `error` |
| --- | --- | --- |
| Static allow list, no certificate | 403 | `Client TLS certificate is required` |
| Static allow list, certificate not covered by the list (other CA, sibling of a pinned certificate) | 403 | `Certificate with SHA256 <fingerprint> not allowed` |
| Static allow list, expired certificate from the listed CA | 403 | `Certificate with SHA256 <fingerprint> not allowed` |
| Any client-certificate API reached over plain HTTP | 403 | `TLS not enabled` |
| Certificate authentication, no certificate | 401 | `Client certificate required` |
| Certificate authentication, certificate with no session (same CA or any other) | 401 | `Access to this API has been disallowed` |
| Certificate authentication, expired registered certificate | 403 | `Certificate has expired` |
| Certificate authentication, the session key sent as a token without the certificate | 401 | `Client certificate required` |

## Several methods on one API (`securityProcessingMode: compliant`)

When every alternative fails, the client gets the error of the **last** alternative tried. With
`security: [{apiKey}, {jwtAuth}]` and `authentication.security: [["hmac"]]`, a request with no
credential or with a bad key gets **400** `Authorization field missing, malformed or invalid`
(the HMAC answer). Order the alternatives so the last one gives the error you want clients to see.

## Rate limits after authentication

Once a caller is authenticated, its session's limits apply: 429 `Rate Limit Exceeded` and 403
`Quota exceeded`. Those belong to the `tyk-rate-limits-keys-policies` skill.
