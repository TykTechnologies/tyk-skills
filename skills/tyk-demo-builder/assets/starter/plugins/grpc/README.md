# gRPC coprocess Post hook

This executable server implements Tyk's `coprocess.Dispatcher` service. Its
`RequestEnrichmentHook` runs immediately before proxying and has two observable
paths:

- A request containing `X-Deny-Request: true` receives a local JSON `403`
  through `ReturnOverrides` with body
  `{"error":{"code":"PLUGIN_DENIED"}}`, `Content-Type: application/json`, and
  `override_error: true`; it must not reach the upstream.
- Every other accepted request receives `X-GRPC-Enriched: true` upstream.

`go.mod` takes the coprocess protocol types from the public
`github.com/TykTechnologies/tyk` module at commit
`8a742352382108a7b905983a14c9deb8c97af4ed`, the commit tagged `v5.13.1`. The
Gateway talks to this server over gRPC, so the server is not rebuilt per
Gateway release: built from this pin, it passes `PLUGIN-GRPC-001` against
Gateway 5.16.0. Move the pin (and the Dockerfile comment) only to
use protocol fields a newer release added, and rehearse the module after.

## Build and test

```bash
go test ./...
docker build -t neutral-tyk-coprocess .
```

The Dockerfile emits a static, non-root, multi-architecture server and includes
a gRPC health check. The generated Compose service already builds this directory
and points Gateway at `tcp://grpc-plugin:5555`.

Merge `config/oas-middleware.fragment.json` into
`x-tyk-api-gateway.middleware` on a protected API. The Post stage is important:
built-in authentication and access checks complete before it runs.

## Proof

Record the upstream request counter, then issue these requests with valid API
credentials:

```bash
__GATEWAY_CURL_CMD__ -H 'Authorization: <demo-key>' __GATEWAY_SCHEME__://localhost:<gateway-port>/<listen-path>/v1/echo
__GATEWAY_CURL_CMD__ -i -H 'Authorization: <demo-key>' -H 'X-Deny-Request: true' __GATEWAY_SCHEME__://localhost:<gateway-port>/<listen-path>/v1/echo
```

The first request must reach the upstream with `X-GRPC-Enriched: true`. The
second must return `403`, JSON error code `PLUGIN_DENIED`, and the upstream
counter must remain unchanged. Also send an invalid credential and confirm the
normal Gateway auth response occurs before either Post-hook behavior.
