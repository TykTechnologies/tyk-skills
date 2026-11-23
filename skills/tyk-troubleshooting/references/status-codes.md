Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`)

# What the status code and body mean

## First: did Tyk send it?

| Response | Who sent it |
| --- | --- |
| `Content-Type: application/json`, `X-Generator: tyk.io`, body `{"error": "…"}` | The Gateway rejected or failed the request itself |
| `404`, `Content-Type: text/plain; charset=utf-8`, body `Not Found`, no `X-Generator` | The Gateway: no loaded API matched the path |
| No `X-Generator`, the upstream's own headers (for example `Server: …`) plus `X-Ratelimit-*` | The upstream; the Gateway passed its status and body through (a `500` or a `503` from the upstream arrives unchanged) |
| None of the above | Possibly something in front of the Gateway (load balancer, ingress, proxy): call the Gateway directly and compare |

`hide_generator_header: true` (`TYK_GW_HIDEGENERATORHEADER`) removes `X-Generator` from the
Gateway's error responses (a 401 or a 504 arrives without it); check that setting before
relying on the header. All rows hold on Gateway 5.16.0.

## Codes the Gateway sends, with the exact body

| Code | `error` value | Meaning |
| --- | --- | --- |
| 404 | (plain text `Not Found`) | No API on this path on this Gateway (`api-not-loaded.md`) |
| 401 | `Authorization field missing` | Auth token API: no credential in the expected header |
| 400 | `Authorization field missing` | JWT API: no token |
| 403 | `Access to this API has been disallowed` | Key unknown, key without access to this API, **or Redis down**: a valid key is refused once the Gateway's 10-second local copy of it expires (`redis.md`) |
| 403 | `Key not authorized` | JWT that cannot be parsed or verified |
| 401 | `Key not authorized: token has expired` | JWT `exp` passed |
| 401 | `Key not authorized: token used before issued` | JWT `iat` later than the Gateway's clock |
| 401 | `Key not authorized: token is not valid yet` | JWT `nbf` later than the Gateway's clock |
| 429 | `Rate Limit Exceeded` | The key's or its policy's rate limit |
| 403 | `Quota exceeded` | The key's quota is used up, or Redis is down and the key or JWT policy has a quota (`redis.md`) |
| 500 | `There was a problem proxying the request` | Upstream connection refused, or TLS to the upstream failed (`upstream-errors.md`) |
| 500 | `Upstream host lookup failed` | Upstream host name does not resolve |
| 504 | `Upstream service reached hard timeout.` | Upstream slower than the timeout that applies |

The authentication rows are explained, with every JWT, OAuth, HMAC and certificate message,
in the `tyk-authentication` skill. The limit and key codes (429 and 403 variants,
`API Rate Limit Exceeded`, `Key is inactive`, `Key has expired`) are explained, with their
headers, in the `tyk-rate-limits-keys-policies` skill; allow-list `403 Requested endpoint is forbidden` and validation `422` in the
`tyk-api-definitions-routing` skill. The vendor's full list of Gateway error texts:
https://tyk.io/docs/api-management/troubleshooting-debugging (section "Gateway Error Response
Status Codes").

## Clock problems with JWTs

The Gateway checks `exp`, `iat` and `nbf` against its own clock. With the default settings a
token whose `iat` is 2 seconds in the future is refused:

```text
401 {"error": "Key not authorized: token used before issued"}
```

So a token issuer whose clock runs ahead of the Gateway's gets freshly issued tokens refused.
Fix the clocks (NTP on every host)
and, where a small difference cannot be avoided, allow for it in the JWT scheme of the Tyk OAS
definition (values in seconds):

```json
"jwtAuth": {"enabled": true, "header": {"enabled": true, "name": "Authorization"},
            "signingMethod": "hmac", "source": "<base64 secret>",
            "identityBaseField": "sub", "defaultPolicies": ["basic"],
            "issuedAtValidationSkew": 5, "notBeforeValidationSkew": 5}
```

The `header` block is required: without it the Gateway refuses the definition (the
`tyk-authentication` skill has the full JWT setup). With these, tokens 2 seconds early pass
(200) and a token 120 seconds early is still refused. `expiresAtValidationSkew` is the matching field for `exp`. JWT rejections are not
logged at `info`; at `debug` the `Finished` line of `mw=JWTMiddleware` carries the error.

Docs: https://tyk.io/docs/api-management/authentication/jwt-claim-validation

Certificate validity also depends on the clock; see `upstream-errors.md` for an expired
upstream certificate.
