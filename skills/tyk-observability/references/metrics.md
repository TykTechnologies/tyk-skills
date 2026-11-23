# OpenTelemetry metrics from Tyk Gateway

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), OpenTelemetry Collector Contrib 0.162.0, Prometheus 3.15.0, Grafana 13.3.0

Pages: https://tyk.io/docs/api-management/metrics/default-metrics,
https://tyk.io/docs/api-management/metrics/custom-metrics,
https://tyk.io/docs/api-management/metrics/metrics-concepts

## Switch them on

Metrics are separate from traces: `opentelemetry.metrics.enabled` alone turns them on.

```text
TYK_GW_OPENTELEMETRY_METRICS_ENABLED=true
TYK_GW_OPENTELEMETRY_METRICS_EXPORTER=grpc
TYK_GW_OPENTELEMETRY_METRICS_ENDPOINT=otel-collector:4317
TYK_GW_OPENTELEMETRY_METRICS_RESOURCENAME=tyk-gateway
TYK_GW_OPENTELEMETRY_METRICS_EXPORTINTERVAL=10      # seconds; default 60
```

Other settings (https://github.com/TykTechnologies/opentelemetry/blob/v0.0.26/config/config.go):
`temporality` (`cumulative` default, `delta` for back ends that want it), `cardinality_limit`
(2000 data points per instrument by default), `runtime_metrics` (on by default), `retry`,
`headers`, `tls`. Empty exporter fields are copied from the traces block.

## What you get by default

In Prometheus, through the collector's Prometheus exporter (dots become underscores, a unit
suffix is added):

| Prometheus name | From | Labels |
| --- | --- | --- |
| `tyk_api_requests_total` | `tyk.api.requests.total` counter | `http_request_method`, `http_response_status_code`, `tyk_api_id` |
| `http_server_request_duration_seconds_*` | end-to-end latency histogram | method, status code, `tyk_api_id`, `tyk_response_flag` |
| `tyk_gateway_request_duration_seconds_*` | time inside the Gateway | method, `tyk_api_id`, `tyk_response_flag` |
| `tyk_upstream_request_duration_seconds_*` | time waiting for the upstream | method, `tyk_api_id`, `tyk_response_flag` |
| `tyk_http_requests_total` | all tracked requests, no labels | - |
| `tyk_gateway_apis_loaded`, `tyk_gateway_policies_loaded` | gauges | - |
| `tyk_gateway_config_reload_total`, `tyk_gateway_config_reload_duration_seconds_*` | reloads | - |
| `go_memory_used_bytes`, `go_goroutine_count`, `go_processor_limit`, ... | Go runtime | - |
| `target_info` | resource attributes | `host_name`, `service_version`, ... |

`tyk_response_flag` is the status code for a clean request and a three-letter cause otherwise.
For example `AMF` (no key, 401), `AKI` (unknown key, 403), `URS` (upstream answered 5xx). The full
list is on https://tyk.io/docs/api-management/logs/access-logs ("Error classification").

Requests to an API without `trafficLogs.enabled: true` are not counted in any API metric.

Scrape the collector with `honor_labels: true` (`assets/lab/prometheus.yaml`). Then `job` is
the Gateway's service name and `instance` is its Gateway id. Without it they arrive as
`exported_job` and `exported_instance`, and every query and dashboard has to use those names.

## Custom instruments: `api_metrics`

`opentelemetry.metrics.api_metrics` (`TYK_GW_OPENTELEMETRY_METRICS_APIMETRICS`, a JSON list on
one line) defines the request instruments. What each value does:

| Value | Request metrics exported |
| --- | --- |
| not set | the four defaults |
| a list | only the listed instruments; the four defaults disappear unless you list them |
| `[]` | none; the configuration, runtime and `tyk_http_requests_total` metrics remain |
| empty string | the Gateway does not start (`converting '' to type apimetrics.APIMetricDefinitions`) |

`assets/api-metrics-defaults.json` re-creates the four defaults; `assets/api-metrics-chargeback.json`
is the defaults plus a chargeback counter (`attribution-and-chargeback.md`).

Each instrument: `name`, `type` (`counter` or `histogram`), `description`, `dimensions`, and for
a histogram `histogram_source` (`total`, `gateway` or `upstream`) and optional
`histogram_buckets`; optional `filters` (`api_ids`, `methods`, `status_codes` such as `"5xx"`).

A dimension is `{"source", "key", "label", "default"}`. Sources:

| Source | Keys | Notes |
| --- | --- | --- |
| `metadata` | `method`, `response_code`, `response_flag`, `api_id`, `api_name`, `org_id`, `listen_path`, `endpoint`, `api_version`, `host`, `scheme`, `ip_address`, and the MCP keys | `endpoint` is the path template of a tracked endpoint (`trackEndpoint`), empty otherwise |
| `session` | `alias`, `api_key` (last 6 characters), `oauth_id`, `portal_app`, `portal_org` | key `meta_data` is not a source |
| `header` | any request header | read after the API's request header transforms |
| `response_header` | any response header | |
| `context` | context variables such as `jwt_claims_<claim>` | needs context variables on the API (https://tyk.io/docs/api-management/metrics/custom-metrics) |
| `config_data` | any key in the API's `pluginConfig.data.value` | |

The key lists are those of Gateway 5.16.0
(https://github.com/TykTechnologies/tyk/blob/v5.16.0/internal/otel/apimetrics/dimension.go).
An unknown `metadata` or `session` key fails the same start-up check as a missing
`histogram_source` below.

### One bad instrument removes them all

A histogram without `histogram_source` logs

```text
"error":"api_metrics[\"my.latency\"]: histogram_source is required for histogram type",
"message":"failed to create API metric instruments, falling back to no API metrics"
```

and the Gateway exports no request metric at all, including the valid counter in the same
list. After any change to `api_metrics`, grep the Gateway log for `failed to create API metric`
and query one instrument.

### Cardinality

Every distinct label combination is a series. Keep dimensions bounded: `alias`, `portal_app`,
team or tier values, `endpoint` templates. Avoid `api_key`, `ip_address`, raw paths and request
ids. The Gateway warns at start-up about session dimensions on histograms and about more than
ten dimensions.

## Exemplars: from a latency point to its trace

When traces are on and a request is sampled, the latency histograms carry the trace id. Three
things must be in place:

1. The collector's Prometheus exporter serves OpenMetrics: `enable_open_metrics: true`.
2. Prometheus runs with `--enable-feature=exemplar-storage`.
3. The Grafana Prometheus data source maps the `trace_id` exemplar label to the trace data
   source (`assets/lab/grafana/provisioning/datasources/datasources.yaml`).

```bash
curl -s --get http://127.0.0.1:9090/api/v1/query_exemplars \
  --data-urlencode 'query=http_server_request_duration_seconds_bucket' \
  --data-urlencode "start=$(( $(date +%s) - 900 ))" --data-urlencode "end=$(date +%s)"
#   {"seriesLabels":{"tyk_api_id":"orders","le":"0.05",...},
#    "exemplars":[{"labels":{"span_id":"3787352655d00247","trace_id":"01ebff0c46b16b7afa78bbacf0cb8a6b"},"value":"0.031",...}]}
```

The trace id matches the `X-Tyk-Trace-Id` of the request and opens in Tempo. With
`enable_open_metrics: false`, new requests produce no exemplar; switching it back on gives
exemplars again.

## Tyk Dashboard metrics

Dashboard 5.16 exports its own OTLP metrics when `TYK_DB_OPENTELEMETRY_METRICS_ENABLED=true`
with `TYK_DB_OPENTELEMETRY_METRICS_ENDPOINT` set, among them `tyk_dashboard_gateways_connected`,
`tyk_dashboard_license_expiry_timestamp_seconds`, `tyk_dashboard_health`,
`tyk_dashboard_objects_apis_count`, Redis and datastore pool gauges, and `process_uptime_seconds`.
The full list is on the default-metrics page.
