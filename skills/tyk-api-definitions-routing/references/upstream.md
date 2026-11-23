# The upstream section

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`) and Tyk Dashboard 5.16.0

Everything here sits under `x-tyk-api-gateway.upstream`.

## URL

```json
"upstream": {"url": "http://orders.example.com:8080"}
```

- Give a scheme. Without one the definition is accepted and every request fails with
  `500 {"error": "There was a problem proxying the request"}`.
- A path in the URL is put in front of the request path: `/base` plus `/a/b`. It also stops
  response body transforms from running (see `operation-middleware.md`), so prefer a bare
  scheme, host and port.
- The upstream receives the upstream's own host in the `Host` header. To pass the caller's
  host through, add `"preserveHostHeader": {"enabled": true}`: with it the upstream saw
  `api.example.com` instead of `orders.example.com:8080`.

## Load balancing

```json
"upstream": {
  "loadBalancing": {"enabled": true, "targets": [
    {"url": "http://orders-a.example.com:8080", "weight": 3},
    {"url": "http://orders-b.example.com:8080", "weight": 1}]}
}
```

- `url` can be left out when `loadBalancing` is given. Twenty calls went 15 to the first target
  and 5 to the second.
- When both `url` and enabled targets are present, the targets are used and `url` is not.

Docs: https://tyk.io/docs/planning-for-production/ensure-high-availability/load-balancing

## Service discovery

```json
"upstream": {
  "url": "http://placeholder.invalid",
  "serviceDiscovery": {"enabled": true,
    "queryEndpoint": "http://registry.example.com:8080/sd/single",
    "dataPath": "hostname", "portDataPath": "port",
    "cache": {"enabled": true, "timeout": 10}}
}
```

With the registry answering `{"hostname": "orders-b", "port": 8080}`, every call goes to
`orders-b:8080`; the `url` is not used. The other fields (`useNestedQuery`, `parentDataPath`,
`endpointReturnsList`, `useTargetList`, `targetPath`) describe other response shapes and are in
the docs: https://tyk.io/docs/planning-for-production/ensure-high-availability/service-discovery

## Timeout for the whole API

```json
"upstream": {"enforceTimeout": {"enabled": true, "duration": "1s"}}
```

A 2-second upstream call gets `504 {"error": "Upstream service reached hard timeout."}` after one
second. An operation's own `enforceTimeout` overrides it.

## Upstream authentication (Enterprise Edition Gateway)

```json
"upstream": {"authentication": {"enabled": true,
  "basicAuth": {"enabled": true, "username": "svc-user", "password": "svc-pass"}}}
```

```json
"upstream": {"authentication": {"enabled": true,
  "oauth": {"enabled": true, "allowedAuthorizeTypes": ["clientCredentials"],
    "clientCredentials": {"clientId": "gw-client", "clientSecret": "gw-secret",
      "tokenUrl": "http://idp.example.com/token", "scopes": ["read"]}}}}
```

On the Enterprise Edition Gateway with a Dashboard:

- Basic auth: the upstream receives `Authorization: Basic c3ZjLXVzZXI6c3ZjLXBhc3M=`
  (`svc-user:svc-pass`). A caller's own `Authorization` header is replaced by it.
- Add `"header": {"enabled": true, "name": "X-Upstream-Auth"}` inside `basicAuth` to send the
  credential in another header; the caller's `Authorization` then passes through unchanged.
- OAuth client credentials: the Gateway fetches a token from `tokenUrl` and the upstream
  receives `Authorization: Bearer <that token>`.
- Enabling both modes is rejected:
  `Validation of API Definition failed. Reason: multiple upstream authentication modes not allowed.`

On the open source Gateway the same block is accepted, the upstream receives no credential, and
the log says `Upstream basic auth is supported only in Tyk Enterprise Edition`. The `tyk-facts`
skill has the edition detail. Docs: https://tyk.io/docs/api-management/upstream-authentication

Keep real secrets out of the definition with a key-value store reference:
https://tyk.io/docs/tyk-configuration-reference/kv-store

## TLS to the upstream

An upstream certificate the Gateway does not trust fails every call with
`500 {"error": "There was a problem proxying the request"}`; the log says
`tls: failed to verify certificate: x509: certificate signed by unknown authority`. Give the
Gateway the CA (the standard `SSL_CERT_FILE` environment variable works), or for a test only:

```json
"upstream": {"tlsTransport": {"insecureSkipVerify": true}}
```

`tlsTransport` also takes `minVersion`, `maxVersion` (`"1.0"` to `"1.3"`), `ciphers` and
`forceCommonNameCheck`, all defined in the schema.

### Mutual TLS: the Gateway presents a client certificate

1. Upload the certificate and its private key, as one PEM file, to the certificate store. On the
   open source Gateway:

   ```bash
   curl -s -X POST "$GW/tyk/certs" -H "x-tyk-authorization: $SECRET" --data-binary @client-bundle.pem
   # 200 {"id":"<certificate id>","status":"ok","message":"Certificate added"}
   ```

   With a Dashboard, `POST /api/certs` as a multipart upload (`curl -F "data=@client-bundle.pem"`).
2. Map the upstream host to the id:

   ```json
   "upstream": {"url": "https://orders.example.com:8443",
     "mutualTLS": {"enabled": true, "domainToCertificateMapping": [
       {"domain": "orders.example.com", "certificate": "<certificate id>"}]}}
   ```

Without it an upstream that requires a client certificate returns 500. With it the call
succeeds and the upstream sees the certificate. `domain` matches as the bare host, as
`host:port` and as `*`.

### Certificate pinning: accept only a known upstream key

```json
"upstream": {"url": "https://orders.example.com:8443",
  "certificatePinning": {"enabled": true, "domainToPublicKeysMapping": [
    {"domain": "orders.example.com", "publicKeys": ["<public key id or PEM>"]}]}}
```

- `publicKeys` holds ids of public keys uploaded to the certificate store (PEM, uploaded the
  same way as above), or the PEM text of the public key itself. Both work.
- **`domain` must be the bare host name** (or `*`). With `orders.example.com:8443` the pin is
  not applied at all: a call succeeds even with the wrong key pinned.
- A wrong key, or an id that cannot be read, fails the call with 500 and the log line
  `certificate public key pinning error. Public keys do not match.`
- On the open source Gateway, upload the public key **without** `?org_id=`. An id returned for
  `POST /tyk/certs?org_id=default` cannot be read back for pinning and every call fails.
  Ids returned by the Dashboard's `POST /api/certs` work as they are.
- Pinning replaces the normal CA check for that upstream: with the right key pinned, an upstream
  whose CA the Gateway does not trust is accepted.

Docs: https://tyk.io/docs/api-management/upstream-authentication/mtls

## Other upstream fields

`rateLimit` (see `operation-middleware.md`), `uptimeTests`, `proxy`, `dnsDiscovery` and
`preserveTrailingSlash` are defined in the 5.16.0 schema. `dnsDiscovery`
needs `loadBalancing` enabled as well and cannot be combined with `serviceDiscovery`; the
Gateway rejects the definition otherwise.
