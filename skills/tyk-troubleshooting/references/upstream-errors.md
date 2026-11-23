Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`)

# Upstream failures: refused, DNS, TLS, timeouts, slow test backends

Each case below is a Tyk OAS API on Gateway 5.16.0 pointing at a broken upstream, called
through the Gateway. Every Gateway-made error carries `X-Generator: tyk.io`; every
one writes a log line at `error` with `mw=ReverseProxy`, the `api_id` and the upstream as
`server_name`, so the log names the API and the upstream that failed.

## The table

| Upstream problem | Caller receives | Gateway log `message` |
| --- | --- | --- |
| Nothing listening on the port | `500 {"error": "There was a problem proxying the request"}` | `http: proxy error: dial tcp 10.250.50.9:9: connect: connection refused` |
| Host name does not resolve | `500 {"error": "Upstream host lookup failed"}` | `http: proxy error: dial tcp: lookup no-such-host.invalid on 127.0.0.11:53: no such host` |
| TLS: certificate from a CA the Gateway does not trust | `500 … There was a problem proxying the request` | `http: proxy error: tls: failed to verify certificate: x509: certificate signed by unknown authority` |
| TLS: certificate expired | `500 … There was a problem proxying the request` | `… x509: certificate has expired or is not yet valid: current time 2026-11-19T13:45:09Z is after 2024-02-01T00:00:00Z` |
| TLS: certificate for another host name | `500 … There was a problem proxying the request` | `… x509: certificate is valid for other.example.com, not lab-upstream` |
| Slower than the Gateway default timeout | `504 {"error": "Upstream service reached hard timeout."}` | `http: proxy error: timeout awaiting response headers` |
| Slower than an API or operation timeout | `504 … Upstream service reached hard timeout.` | `http: proxy error: context deadline exceeded` |
| Upstream answers 500 or 503 itself | That status and body, unchanged, without `X-Generator` | nothing |

None of these produces a `502`. A `502` at the client comes from another hop; call the
Gateway directly and compare.

## TLS to the upstream: fixes in order of preference

1. **Expired or wrong-host certificate**: fix the certificate on the upstream. The expiry
   message prints the Gateway's current time next to the certificate's dates, so a wrong
   Gateway clock shows up here too.
2. **Private CA**: make the Gateway container trust it. Go reads extra CA files from the
   directories in `SSL_CERT_DIR`; with the CA's PEM file mounted at `/lab-ca/ca.pem` and
   `SSL_CERT_DIR=/lab-ca`, the API behind the private CA answers 200 and an API on a public
   HTTPS site still answers 200. (The Gateway has no setting of its own for an upstream CA
   bundle: [`config/config.go`](https://github.com/TykTechnologies/tyk/blob/v5.16.0/config/config.go).)
3. **Last resort, for a test only**: skip verification for one API with
   `"upstream": {"tlsTransport": {"insecureSkipVerify": true}}` (an expired certificate then
   answers 200), or for every API with `proxy_ssl_insecure_skip_verify`
   (`TYK_GW_PROXYSSLINSECURESKIPVERIFY=true`: all three TLS failures above then answer 200). Both remove the
   protection TLS gives; never leave them in production.

Upstream mutual TLS and certificate pinning: the `tyk-api-definitions-routing` skill and
https://tyk.io/docs/api-management/upstream-authentication/mtls

## Timeouts

Three places set how long the Gateway waits for the upstream's response headers:

| Level | Setting | Unit |
| --- | --- | --- |
| Gateway | `proxy_default_timeout` (`TYK_GW_PROXYDEFAULTTIMEOUT`) | integer seconds |
| API | `upstream.enforceTimeout: {"enabled": true, "duration": "10s"}` | duration string |
| Operation | `middleware.operations.<operationId>.enforceTimeout` | see the `tyk-api-definitions-routing` skill |

On Gateway 5.16.0 with `proxy_default_timeout` 5:

| API timeout | Upstream delay | Result |
| --- | --- | --- |
| none | 7 s | `504` after 5.0 s |
| `1s` | 2 s | `504` after 1.05 s |
| `10s` | 7 s | `200` after 7.0 s |
| `10s` | 12 s | `504` after 10.0 s |

So on 5.16.0 an API timeout longer than `proxy_default_timeout` **does** extend the wait. The
enforced-timeouts documentation page says the Gateway default is an upper bound that an API
timeout can only shorten. Because the two disagree, set `proxy_default_timeout` at least as
long as the longest API or operation timeout; then both readings give the same result. Also
keep `http_server_options.write_timeout` (default 120 seconds per the same page) longer than
any timeout you set, since it bounds the whole request.

Docs: https://tyk.io/docs/planning-for-production/ensure-high-availability/enforced-timeouts

## "The Gateway adds 40 ms": check the test backend first

A Python stand-in built on `http.server.BaseHTTPRequestHandler` writes headers and body in
separate packets with Nagle's algorithm on, and the body then waits for a delayed
acknowledgement from the Gateway. Through the same Gateway, over a kept-alive connection:

| Backend | Median per request | Gateway analytics: total / upstream / gateway latency (median, ms) |
| --- | --- | --- |
| Handler with `disable_nagle_algorithm = True` | 0.3 ms | 0 / 0 / 0 |
| Same handler, Nagle left on (the default) | 43.5 ms | 42 / 0 / 42 |

The wait for the body is booked as **gateway** latency, not upstream latency. Set `disable_nagle_algorithm = True` on the handler class
(or use a production-grade server) before measuring Gateway overhead. Load testing with
acceptance criteria belongs to the `tyk-poc-runbook` skill.
