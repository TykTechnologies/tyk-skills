# Several methods on one API, keyless APIs, custom authentication

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source)

Examples use the lab in `lab.md`.

## Several methods on one API

Docs: https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/multiple-auth.
For the basics of combining methods, use the `tyk-api-definitions-routing` skill. This section adds
the behaviour on Gateway 5.16.0.

### Tyk methods in compliant mode

HMAC and custom plugins have no OpenAPI scheme. In compliant mode they join the alternatives
through the Tyk extension's own `security` list, which is added to the OpenAPI one:

```json
"security": [{"apiKey": []}, {"jwtAuth": []}],
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true,
  "securityProcessingMode": "compliant",
  "security": [["hmac"]],
  "hmac": {"enabled": true, "header": {"enabled": true, "name": "Signature"}, "allowedAlgorithms": ["hmac-sha256"]},
  "securitySchemes": {"apiKey": {"enabled": true}, "jwtAuth": {"enabled": true, "header": {"enabled": true, "name": "Authorization"}, "...": "..."}}}}}
```

A key alone, a JWT alone, and an HMAC signature alone (sent in the `Signature` header, since the
JWT owns `Authorization`) each get 200.

### Which error a client sees

When every alternative fails, the answer is the **last** alternative's error. Here that is
HMAC: no credential, or only a wrong key, gets 400 `Authorization field missing, malformed or invalid`.
Put last the method whose error you want clients to read.

### Whose limits apply

Each method keeps its own session. The first alternative that succeeds decides the session:

- A key with a 2-a-minute policy reaches 429 while a JWT on the same API still gets 200.
- A request carrying the over-limit key **and** a valid JWT gets 429: the key matches first and
  its limit applies; Tyk does not fall back to the JWT.

### Legacy mode with a Tyk method enabled

With the same definition in legacy mode, a JWT alone gets 400 from HMAC: an enabled Tyk method
(here `hmac`) is required in addition to the first OpenAPI requirement. Use compliant mode
whenever more than one method is meant to be optional.

## Keyless

Leave `server.authentication` out (or set `enabled: false`). Docs:
https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/open-keyless

- No credential: 200. A credential that would be refused elsewhere (`Authorization: nope`): 200.
- No session, so no per-caller rate limit or quota. An API-wide limit goes in the API
  definition (the `tyk-api-definitions-routing` and `tyk-rate-limits-keys-policies` skills).
- OpenAPI `security` on a keyless definition does not protect anything (see the
  `tyk-api-definitions-routing` skill).
- One operation of a protected API can be made public with `ignoreAuthentication`; see the
  `tyk-api-definitions-routing` skill.

## Custom authentication with a plugin

When none of the built-in methods fits (a proprietary token format, a lookup in your own
user store), a plugin can do the authentication and hand Tyk a session. Writing, packaging and
deploying plugins (Go, gRPC, Python, JavaScript) belongs to the `tyk-custom-plugins` skill. Docs:
https://tyk.io/docs/api-management/authentication/custom-auth

A plugin of this kind reads the request, decides, and returns a session: the session's access
rights, rate limit and quota then apply as for any key, and the plugin chooses every refusal's
status and message. The point below holds for a JavaScript auth plugin on the open source
Gateway 5.16.0.

**Give each caller its own counter.** Take a plugin that returns sessions with a 2-a-minute
rate limit. Without a `rate_limit_pattern`, every session it returns shares **one** rate-limit
counter: with two partner tokens, partner-a gets 200, 200, 429 and partner-b's **first** call
is already 429. With `"meta_data": {"partner": "<partner>", "rate_limit_pattern": "$tyk_meta.partner"}`
in each returned session, each partner gets 200, 200, 429 on its own. Custom rate-limit keys
are documented at https://tyk.io/docs/api-management/rate-limit.

The counter is keyed by the pattern's **value** alone, across APIs. With the same plugin on
two APIs and `$tyk_meta.partner`, partner-a gets 200, 200, 429 on the first API and 429 on its
first request to the second. For a limit per API and per caller, put both in the value, for
example `"rate_key": config.config_data.api_id + ":" + partner` with
`"rate_limit_pattern": "$tyk_meta.rate_key"` (an `api_id` entry in each API's plugin config
data): each API then gives partner-a 200, 200, 429.
