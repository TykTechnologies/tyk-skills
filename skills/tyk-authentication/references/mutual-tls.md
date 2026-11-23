# Client mutual TLS: static allow list or certificate authentication

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), OpenSSL 4.1 test certificates

Examples use the lab in `lab.md` (the `tyk-gateway-tls` Gateway, which listens on HTTPS).
Docs: https://tyk.io/docs/api-management/implement-tls and
https://tyk.io/docs/api-management/authentication/certificate-auth

Both models need the Gateway itself to listen on HTTPS
(`http_server_options.use_ssl`). Over plain HTTP every client-certificate API answers 403
`TLS not enabled`.

## Which one

| Question | Static allow list | Certificate authentication |
| --- | --- | --- |
| Where the trusted certificates live | In the API definition (`server.clientCertificates.allowlist`) | In Redis: one session per certificate |
| Is it authentication? | No: a TLS check. The API can be keyless or use another method as well | Yes: the certificate is the credential and identity |
| A CA in the list | Every certificate that CA signs is accepted, with no per-client identity | Not used: a certificate is accepted only if it has a session, whoever signed it |
| Per-client policy, rate limit, quota | Only through a second method (JWT, auth token) | Yes, from the session's policy |
| Add or revoke one client | Edit and reload the API | Create or delete its session; the API is untouched |
| Tyk OAS field | `server.clientCertificates` | `server.authentication.certificateAuth` |

An OpenAPI security scheme of `"type": "mutualTLS"` is refused by the Gateway (OpenAPI 3.1:
`security scheme 'type' can't be "mutualTLS"`); client certificates are set only in the Tyk
extension, as below. The `tyk-api-definitions-routing` skill covers that rejection.

## The certificate store

Certificates are referenced by id. Upload a PEM to the Gateway's store (open source Gateway):

```bash
curl -s -X POST "$GW/tyk/certs?org_id=default" -H "x-tyk-authorization: $SECRET" --data-binary @ca.crt
# {"id":"default51fdc4…","status":"ok","message":"Certificate added"}
curl -s -X POST "$GW/tyk/certs?org_id=default" -H "x-tyk-authorization: $SECRET" --data-binary @ca.crt
# {"status":"error","message":"Certificate with default51fdc4… id already exists"}
curl -s "$GW/tyk/certs?org_id=default" -H "x-tyk-authorization: $SECRET"
# {"certs":["default51fdc4…"]}
```

The id is the organisation id followed by the SHA-256 fingerprint of the certificate, so you can
compute it before uploading:
`echo "default$(openssl x509 -in ca.crt -outform der | shasum -a 256 | cut -c1-64)"`.
Read the id from the "already exists" message to stay idempotent. With a Dashboard the store
is managed through the Dashboard API (`/api/certs`): https://tyk.io/docs/api-management/certificates

## Static allow list

```json
"x-tyk-api-gateway": {"server": {"listenPath": {"value": "/mtls-static/", "strip": true},
  "clientCertificates": {"enabled": true, "allowlist": ["<CA certificate id>"]}}}
```

With the CA's id in the list:

| Client certificate | Result |
| --- | --- |
| none | 403 `Client TLS certificate is required` |
| partner-a, signed by the CA | 200 |
| partner-b, also signed by the CA, never registered | **200** |
| signed by another CA | 403 `Certificate with SHA256 <fingerprint> not allowed` |
| signed by the CA, expired | 403 `Certificate with SHA256 <fingerprint> not allowed` |

Listing partner-a's own certificate id instead of the CA narrows it: partner-a 200, partner-b
403 `... not allowed`. The upstream receives no header describing the client certificate.

Without `authentication` the API is otherwise keyless: callers get no session, so no
per-caller policy, rate limit or quota.

### Static list plus a token

Add a JWT (or auth token) scheme to the same API; both are then required. With partner-a's
certificate pinned and an HS256 JWT scheme:

| Request | Result |
| --- | --- |
| certificate and token | 200 |
| token only | 403 `Client TLS certificate is required` |
| certificate only | 400 `Authorization field missing` |
| partner-b certificate and a valid token | 403 `Certificate with SHA256 <fingerprint> not allowed` |

The token's session then carries the per-client policy.

## Certificate authentication

```json
"x-tyk-api-gateway": {"server": {"listenPath": {"value": "/mtls-certauth/", "strip": true},
  "authentication": {"enabled": true, "certificateAuth": {"enabled": true}}}}
```

No OpenAPI security scheme is needed. Register each client: upload its certificate (the
certificate alone is enough) and create a session that names it.

```bash
PA=$(curl -s -X POST "$GW/tyk/certs?org_id=default" -H "x-tyk-authorization: $SECRET" --data-binary @partner-a.crt \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -X POST "$GW/tyk/keys/create" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' \
  -d "{\"org_id\": \"default\", \"alias\": \"partner-a\", \"certificate\": \"$PA\", \"apply_policies\": [\"cert-partners\"]}"
# {"key":"default674dd9…","status":"ok","action":"added","key_hash":"11faad5b"}
```

A second create for the same certificate is refused:
`{"status":"error","message":"Failed to create key - Key with given certificate already found:<key>"}`
(HTTP 500). The session's key is derived from the organisation and certificate id; on a
Gateway without `hash_key_function` it equals the certificate id.

| Client certificate | Result |
| --- | --- |
| none | 401 `Client certificate required` |
| partner-a, registered | 200 |
| partner-b, same CA, no session | 401 `Access to this API has been disallowed` |
| another CA, no session | 401 `Access to this API has been disallowed` |
| registered but expired | 403 `Certificate has expired` |
| no certificate, the session key sent as a token | 401 `Client certificate required` |

- Limits are per certificate: partner-a's policy (3 a minute) gives 200, 200, 429.
- Adding partner-b (upload plus session) works at once, with no API change and no reload.
- Revoking: `DELETE /tyk/keys/<partner-b's key>`; partner-b then gets 401.

The Gateway setting `allow_unsafe_dynamic_mtls_token` re-enables the older behaviour where the
token alone is accepted (see the certificate-auth page above). Leave it off.

## What Tyk OAS cannot express

- No `mutualTLS` security scheme: use the Tyk extension fields above.
- Certificate authentication and the static list are API-wide, not per operation.
