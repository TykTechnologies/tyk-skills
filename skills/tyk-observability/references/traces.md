# OpenTelemetry traces from Tyk Gateway

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), OpenTelemetry Collector Contrib 0.162.0, Grafana Tempo 2.10.8

Page: https://tyk.io/docs/api-management/traces

## Settings

Use the `opentelemetry.traces` block (environment variables `TYK_GW_OPENTELEMETRY_TRACES_*`).
The older root-level fields still work; when `traces.enabled` is true the `traces` block wins
(https://github.com/TykTechnologies/tyk/blob/v5.16.0/internal/otel/config.go).

```text
TYK_GW_OPENTELEMETRY_TRACES_ENABLED=true
TYK_GW_OPENTELEMETRY_TRACES_EXPORTER=grpc                 # or http
TYK_GW_OPENTELEMETRY_TRACES_ENDPOINT=otel-collector:4317  # host:port, no scheme for grpc
TYK_GW_OPENTELEMETRY_TRACES_RESOURCENAME=tyk-gateway      # becomes service.name
```

| Setting (`opentelemetry.traces.`) | Default | Notes |
| --- | --- | --- |
| `exporter` | `grpc` | `grpc` or `http` |
| `endpoint` | `localhost:4317` | |
| `resource_name` | `tyk` | Set one per Gateway group so back ends can tell them apart |
| `sampling.type` | `AlwaysOn` | `AlwaysOn`, `AlwaysOff`, `TraceIDRatioBased` |
| `sampling.rate` | `0.5` when ratio-based | 0.0 to 1.0 |
| `sampling.parent_based` | `false` | Follow the caller's sampled flag |
| `context_propagation` | `tracecontext` | `tracecontext`, `b3`, `custom`, `composite` |
| `custom_trace_header` | none | For example `X-Correlation-ID` |
| `span_processor_type` | `batch` | `simple` exports each span at once |
| `headers`, `tls`, `connection_timeout` | none, off, 1 s | For a remote or authenticated collector |

These defaults come from the OpenTelemetry library the Gateway uses
(https://github.com/TykTechnologies/opentelemetry/blob/v0.0.26/config/config.go, the version
Gateway 5.16.0 builds with) and match the table on the docs page.

## What a trace contains

Resource attributes on every span: `service.name` (from `resource_name`),
`service.version` (`5.16.0`), `service.instance.id` and `tyk.gw.id` (the Gateway id, which
changes on every restart of the open source Gateway), `tyk.gw.dataplane`, `host.name`
(the container hostname), `process.*`.

Without detailed tracing, one request gives two spans:

```text
GET /untracked/anything/y      SERVER   the request (name = method + full request path)
HTTP GET                       CLIENT   the call to the upstream
```

With `server.detailedTracing.enabled: true` on the API, one span per middleware is added:

```text
GET /orders-api/anything/orders/42   SERVER  tyk.api.id=orders tyk.api.name="Orders API" tyk.api.orgid=default
                                             tyk.api.path=/orders-api/ tyk.original_path=... http.response.status_code=200
  VersionCheck                        tyk.api.version="Non Versioned"
  RateCheckMW, MiddlewareContextVars, TrackEndpointMiddleware
  AuthKey                             tyk.api.apikey.alias=partner-a  tyk.api.apikey=<key hash>
  KeyExpired, AccessRightsCheck, GranularAccessMiddleware, RateLimitAndQuotaCheck, RequestHeaderInjector
  HTTP GET                   CLIENT   url.full=http://upstream:8080/anything/orders/42 server.address=upstream
```

- The server span name holds the raw path. Group in your back end by `tyk.api.id`, not by span
  name.
- The `AuthKey` span carries the key alias and the hashed key, not the key.
- Every response carries `X-Tyk-Trace-Id`, and the access-log line and the analytics record
  (tag `trace-id-<id>`) carry the same id, so one id finds the request everywhere.

## Propagation

The Gateway reads the caller's context and writes it to the upstream. Results with each mode
(upstream request headers as the upstream receives them):

| Mode | Caller sent | Upstream received | `X-Tyk-Trace-Id` |
| --- | --- | --- | --- |
| `tracecontext` (default) | nothing | `traceparent: 00-<new id>-<span>-01` | the new id |
| `tracecontext` | `traceparent` with id T | `traceparent` with id T | T |
| `b3` | `X-B3-TraceId` T, `X-B3-SpanId`, `X-B3-Sampled: 1` | `X-B3-Traceid` T, a new `X-B3-Spanid`, `X-B3-Sampled: 1` | T |
| `tracecontext` + `custom_trace_header: X-Correlation-ID` | `X-Correlation-ID: 550e8400-e29b-41d4-a716-446655440000` | `traceparent: 00-550e8400e29b41d4a716446655440000-...-01` | the UUID without dashes |
| `custom` + same header | the same UUID | `X-Correlation-Id` unchanged, no `traceparent` | the UUID without dashes |
| `custom` + same header | nothing | no trace header at all | a new id |
| `composite` + same header | the same UUID | both `traceparent` and `X-Correlation-Id` | the UUID without dashes |

The custom header must hold a 32-character hex trace id or a UUID; other values do not map to a
predictable trace id (docs page, "Custom Trace Header").

## Sampling

`TraceIDRatioBased` with `rate: 0.2` and `parent_based: true`, 100 requests with no caller
context: about 20 traces reach Tempo. All 100 responses still carry an
`X-Tyk-Trace-Id`, so a trace id in a response does not mean the trace was kept.

With `parent_based: true`:

- caller `traceparent ...-01` (sampled): kept, with the caller's trace id;
- caller `traceparent ...-00` (not sampled): not in Tempo (404), and the upstream receives the
  same trace id with flag `00`.

## Collector example

`assets/lab/otel-collector.yaml` receives OTLP on 4317 and 4318, sends traces to Tempo and
serves metrics to Prometheus. It works as shown with `otel/opentelemetry-collector-contrib:0.162.0`.
Tempo 2.10 needs its OTLP receiver bound to `0.0.0.0:4317` (`assets/lab/tempo.yaml`).

Read a trace back:

```bash
curl -s http://127.0.0.1:3200/api/traces/<trace id>    # Tempo, 404 if not sampled
```

Vendor guides (Datadog, Dynatrace, Elastic, Jaeger, New Relic) are linked from the docs page.
