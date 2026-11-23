# Auth token, Basic and HMAC request signing

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source)

All three use a Tyk **key** (a session in Redis) as the identity, so the key's policy carries
the access rights, rate limit and quota. Creating keys and policies, and how limits combine, is
the `tyk-rate-limits-keys-policies` skill; this file covers what is specific to each method.
The examples use the Gateway API of an open source Gateway (`hash_keys: true`, org id
`default`).

## Auth token

Docs: https://tyk.io/docs/api-management/authentication/bearer-token

```json
"components": {"securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}},
"security": [{"apiKey": []}],
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true, "stripAuthorizationData": true,
  "securitySchemes": {"apiKey": {"enabled": true,
    "query":  {"enabled": true, "name": "api_key"},
    "cookie": {"enabled": true, "name": "session_key"}}}}}}
```

- The header name comes from the OpenAPI scheme (`in: header`, `name`). A key in
  `Authorization` on this API gets 401 `Authorization field missing`.
- `query` and `cookie` in the Tyk part add more places. `?api_key=<key>` and
  `Cookie: session_key=<key>` both get 200.
- With `stripAuthorizationData: true` the upstream does not receive the `X-API-Key` header, the
  `api_key` query parameter, or the `session_key` cookie; other cookies in the same header
  (`theme=dark`) are passed on.
- `Bearer <key>` in the header is accepted: the prefix is removed.

Create a key (open source Gateway):

```bash
curl -s -X POST "$GW/tyk/keys/create" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' \
  -d '{"org_id": "default", "alias": "partner-a", "apply_policies": ["orders-standard"]}'
# {"key":"default1f0c…","status":"ok","action":"added","key_hash":"55f7f5bd"}
```

`"expires": <unix seconds>` ends a key: after that time it gets 401
`Key has expired, please renew`. The key record stays readable (`GET /tyk/keys/{key}` 200).

Revoking: `DELETE /tyk/keys/{key}` answers `{"key":"…","status":"ok","action":"deleted"}`, and
a Gateway that has just served the key keeps accepting it for about 10 seconds (403 from just
after 10 s): each Gateway keeps a session it read from Redis for 10 seconds. The length
is fixed; the only setting is `local_session_cache.disable_cached_session_state` (`true`
turns the cache off); see https://tyk.io/docs/tyk-oss-gateway/configuration. Tell users a revocation takes effect within
seconds, not instantly.

## Basic

Docs: https://tyk.io/docs/api-management/authentication/basic-authentication

```json
"components": {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
"security": [{"basicAuth": []}],
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true, "stripAuthorizationData": true,
  "securitySchemes": {"basicAuth": {"enabled": true, "header": {"enabled": true, "name": "Authorization"}}}}}}
```

The `header` block is required and the Gateway does not say so: without it the definition
loads and **every** request, correct password included, gets 401 `Authorization field missing`.
`scripts/check_auth.py` reports it.

A user is a key whose id is the user name, created with `POST /tyk/keys/{username}`:

```bash
curl -s -X POST "$GW/tyk/keys/partner-a" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' \
  -d '{"org_id": "default", "apply_policies": ["basic-users"], "basic_auth_data": {"password": "<password>"}}'
# {"key":"","status":"ok","action":"added","key_hash":"3346d0bd"}
```

The empty `key` in the answer is normal. `curl -u partner-a:<password>` then gets 200; a wrong
password or unknown user 401 `User not authorised`; a malformed header 400 (see `responses.md`).
`"hash_type": "bcrypt"` in `basic_auth_data` also works. With `stripAuthorizationData: true`
the upstream does not receive the `Authorization` header.

## HMAC request signing

Docs: https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/hmac-signatures

HMAC is a Tyk method, not an OpenAPI scheme: it sits in `authentication.hmac`, with no entry in
`components.securitySchemes`.

```json
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true, "stripAuthorizationData": true,
  "hmac": {"enabled": true, "header": {"enabled": true, "name": "Authorization"},
           "allowedAlgorithms": ["hmac-sha256", "hmac-sha512"], "allowedClockSkew": 5000}}}}
```

- Without the `header` block the Gateway refuses the definition:
  `all auth sources are disabled for hmac, at least one of header/cookie/query must be enabled.`
- `allowedClockSkew` is in **milliseconds**. With 5000, a `Date` 60 seconds old gets 400. Without
  it, a request dated 1 January 2024 gets 200: any old signed request can be replayed. Set it.
- `allowedAlgorithms` limits what clients may use; `hmac-sha1` is refused when not listed.

The client needs a key created with `"hmac_enabled": true`. The Gateway generates the secret;
read it from the key's `hmac_string` field:

```bash
KEY=$(curl -s -X POST "$GW/tyk/keys/create" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' \
  -d '{"org_id": "default", "alias": "partner-a", "apply_policies": ["hmac-clients"], "hmac_enabled": true}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
export HMAC_SECRET=$(curl -s "$GW/tyk/keys/$KEY" -H "x-tyk-authorization: $SECRET" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["hmac_string"])')
```

Sign and send with the bundled script:

```bash
python3 "$SKILL/scripts/sign_hmac_request.py" --url "$GW/hmac/anything" --key-id "$KEY" \
  --secret-env HMAC_SECRET --algorithm hmac-sha256 --send
# HTTP 200
python3 "$SKILL/scripts/sign_hmac_request.py" --url "$GW/hmac/anything" --key-id "$KEY" --secret-env HMAC_SECRET
# Date: Thu, 19 Nov 2026 13:32:33 GMT
# Authorization: Signature keyId="<key>",algorithm="hmac-sha256",headers="(request-target) date",signature="<sig>"
```

What the Gateway checks:

- The signed string has one line per name in `headers`: `(request-target): <method in lower
  case> <path>` and `<header in lower case>: <value>`. The path is the one the client sends,
  **listen path included**; signing `/anything` instead of `/hmac/anything` gets 400.
- The signature is base64 of the HMAC, then URL-encoded.
- A `Date` header is always required and must parse; with `--headers '(request-target)'` and
  no `Date` sent, the request gets 400. A client that cannot set `Date` can send `X-Aux-Date`
  instead (no `Date` at all); the `date` line of the signed string then reads
  `x-aux-date: <value>`. `--header "X-Aux-Date: <date>"` on the script does this (200). If a
  client sends both, keep them equal: a stale `Date` beside a fresh `X-Aux-Date` gets 400.
- Extra headers can be signed: `--header 'X-Request-Id: 42' --headers '(request-target) date x-request-id'`
  gets 200, and the same signature with `X-Request-Id: 43` gets 400. Sign the headers whose
  integrity matters; the body is not signed.
- Every failure is the same 400 `Authorization field missing, malformed or invalid`; read the
  Gateway log to see which check failed.

The HMAC key is also an ordinary key: on an API that accepts both, the same key works as an
auth token, and both uses count against the same rate limit.
