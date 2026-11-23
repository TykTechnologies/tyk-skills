# Signals, where they come from, and which path to choose

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`), Tyk Dashboard 5.16.0, Tyk Pump 1.18.0

Overview page: https://tyk.io/docs/api-management/observability

## The signals

| Signal | Produced by | Leaves the Gateway as | Open source Gateway alone? |
| --- | --- | --- | --- |
| Traces | Gateway | OTLP (gRPC or HTTP) to a collector | Yes |
| Metrics (request, runtime, configuration) | Gateway | OTLP to a collector | Yes |
| Access logs, one line per request | Gateway | stdout, text or JSON | Yes |
| Application logs | Gateway | stdout, text or JSON | Yes |
| Health and readiness | Gateway | `GET /hello`, `GET /ready` | Yes |
| Analytics records, one per request | Gateway writes them to Redis | Tyk Pump moves them to a back end | Yes, with Tyk Pump |
| Pump back ends (Prometheus, Elasticsearch, OpenSearch, CSV, stdout, SQL and others) | Tyk Pump | Pump writes or serves them | Yes |
| Dashboard analytics screens and Log Browser | Tyk Dashboard reads what Pump wrote to PostgreSQL or MongoDB | Dashboard UI and API | No: needs a licensed Dashboard and Pump |
| Dashboard's own metrics (gateways connected, licence, pools) | Tyk Dashboard 5.16 | OTLP | No: needs a licensed Dashboard |

The first seven rows work on the open source image with no Dashboard; the last two need
Dashboard 5.16.0. Tyk Gateway and Tyk Pump are open source; the Dashboard is licensed. For licence and package wording, use the `tyk-facts` skill.

## Which path for which question

| The user wants | Use | Reference |
| --- | --- | --- |
| Request rate, errors and latency per API in Prometheus, Grafana, Datadog or any OTLP back end | Gateway OpenTelemetry metrics | `metrics.md` |
| "Is it the Gateway or the backend?" | `tyk.gateway.request.duration` against `tyk.upstream.request.duration` | `metrics.md` |
| Follow one request across services | Gateway OpenTelemetry traces, W3C or B3 propagation | `traces.md` |
| Jump from a latency spike to the slow request | Exemplars on the latency histograms | `metrics.md` |
| Usage per consumer, team or cost centre | A custom counter with session, header or `config_data` dimensions | `attribution-and-chargeback.md` |
| A record of every request (who, what, status, latency), searchable | Analytics records through Tyk Pump to Elasticsearch, OpenSearch, SQL or another sink | `tyk-pump.md` |
| Request and response bodies for debugging | Detailed recording through Tyk Pump, per API, for a short time | `tyk-pump.md` |
| Traffic screens and the Log Browser in the Dashboard | Pump SQL pumps and, ideally, an aggregate pump | `dashboard-analytics.md` |
| Logs in Loki, Splunk, ELK or CloudWatch | Access logs on stdout, shipped by your log agent | `logs-and-health.md` |
| Load balancer or Kubernetes probes | `/ready` (fails when Redis is gone); `/hello` for detail | `logs-and-health.md` |
| MCP traffic (tools, methods, JSON-RPC errors) | The `tyk-mcp-gateway` skill | - |

## Choosing between OpenTelemetry metrics and the Pump Prometheus pump

Both reached the same Prometheus in the lab, from the same seven requests:

```text
tyk_api_requests_total{tyk_api_id="orders",http_response_status_code="200"} 4   # Gateway, OTLP
tyk_http_status{api="orders",code="200"} 4                                       # Pump Prometheus pump
```

- The Gateway's metrics come straight from the Gateway, carry latency split into Gateway and
  upstream time, a `tyk.response_flag` error class and trace exemplars, and need no Pump.
- The Pump's Prometheus pump is built from analytics records, so it needs analytics on, Redis
  and a Pump, and arrives after the purge delay.
- Prefer the Gateway's metrics for dashboards and alerts. Keep Pump for per-request records,
  the Dashboard screens, and sinks that want records rather than metrics.

## What every signal needs on the API

| API setting (Tyk OAS) | Effect when missing or false |
| --- | --- |
| `x-tyk-api-gateway.middleware.global.trafficLogs.enabled: true` | No analytics record and no API-level metric for that API, whether it was created through the Gateway API or the Dashboard API. Traces and access logs still appear |
| `server.detailedTracing.enabled: true` | Two spans per request instead of one per middleware |
| `server.detailedActivityLogs.enabled: true` | No request or response body in the analytics record |

An OAS document imported as plain OpenAPI (`/tyk/apis/oas/import`) gets `trafficLogs` switched
on; a full Tyk OAS definition sent to the create endpoint keeps whatever it says, and a missing
block means off. Set it explicitly in every definition.
Page: https://tyk.io/docs/api-management/traffic-transformation/do-not-track
