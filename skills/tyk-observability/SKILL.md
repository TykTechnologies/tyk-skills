---
name: tyk-observability
description: Make Tyk Gateway traffic visible in your own tools and in Tyk Dashboard - OpenTelemetry traces and metrics, exemplars, custom metric dimensions for chargeback, Tyk Pump to Prometheus, Elasticsearch, OpenSearch, PostgreSQL or stdout, Dashboard analytics, access logs and health checks, with a lab and an importable Grafana dashboard. Use when asked about Tyk monitoring, tracing, metrics, analytics, logs, Pump or Grafana. Not for MCP traffic, which the `tyk-mcp-gateway` skill covers.
---

# Tyk Observability

This skill makes Tyk Gateway 5.16.0 traffic visible: traces and metrics over OpenTelemetry,
per-request analytics through Tyk Pump, the Dashboard's analytics screens, access logs, and
health checks. It says which signal answers which question, and which need a Dashboard or Pump.
Commands and responses are those of the versions named in the references.

## Ask first

| Question | Default when the user does not say |
| --- | --- |
| Open source Gateway only, or a licensed Dashboard too? | Open source Gateway; the Dashboard steps are then skipped |
| Where should the data go (Prometheus, Grafana, Datadog, Elastic, Splunk, a cloud service)? | Any OTLP back end through an OpenTelemetry Collector; Prometheus, Tempo and Grafana in the lab |
| Totals and latency, or a record of every request? | Totals and latency: Gateway metrics. Add Pump only for records |
| Attribute usage to consumers, teams or cost centres? | No. If yes, read `references/attribution-and-chargeback.md` |
| Which Elasticsearch version, if any? | Ask: Pump writes to 7 and earlier only |
| What share of requests to trace? | All (`AlwaysOn`) in a lab; a ratio with `parent_based: true` in production |
| Which Gateway version? | 5.16.0. Resolve the current tag with the `tyk-facts` skill |

## References

| File | Use it for |
| --- | --- |
| `references/signals-and-paths.md` | Every signal, what produces it, what needs Pump or the Dashboard, which path for which question |
| `references/traces.md` | Trace settings, span names and attributes, detailed tracing, propagation modes, sampling |
| `references/metrics.md` | Default metrics, `api_metrics` instruments and dimensions, what replaces the defaults, exemplars, Dashboard metrics |
| `references/attribution-and-chargeback.md` | Usage per consumer, cost centre, team and endpoint |
| `references/tyk-pump.md` | What Pump moves, back ends, Elasticsearch and OpenSearch versions, aggregate against raw, detailed recording |
| `references/dashboard-analytics.md` | `trafficLogs`, the SQL pumps, `enable_aggregate_lookups`, Dashboard endpoints, data planes, usage reporting |
| `references/logs-and-health.md` | Access logs, templates, log format, `/hello` against `/ready` |
| `references/lab-and-grafana.md` | Running the lab and checking or importing the Grafana dashboard |
| `assets/lab/` | Compose lab: Gateway, Pump, Collector, Tempo, Prometheus, Grafana, two APIs |
| `assets/api-metrics-*.json` | The four default instruments, and the defaults plus a chargeback counter |
| `scripts/check_dashboard_queries.py` | Runs every query of a Grafana dashboard against Prometheus |

## Workflow

1. **Pick the signals.** Use the table in `references/signals-and-paths.md`. Traces, metrics,
   access logs and health come from the open source Gateway alone. Per-request records need Tyk
   Pump (open source). The Dashboard's screens need a licensed Dashboard and Pump; quote licence
   wording from the `tyk-facts` skill.

2. **Make every API observable.** In each Tyk OAS definition set:

   ```json
   "x-tyk-api-gateway": {
     "server": {"detailedTracing": {"enabled": true}},
     "middleware": {"global": {"trafficLogs": {"enabled": true}}}
   }
   ```

   Without `trafficLogs` an API has no analytics record and no API metric, through the Gateway
   API and the Dashboard API alike. `detailedTracing` adds one span per middleware.

3. **Switch on traces and metrics on every Gateway** (environment form; `tyk.conf` keys are in
   the references):

   ```text
   TYK_GW_OPENTELEMETRY_TRACES_ENABLED=true
   TYK_GW_OPENTELEMETRY_TRACES_EXPORTER=grpc
   TYK_GW_OPENTELEMETRY_TRACES_ENDPOINT=otel-collector:4317
   TYK_GW_OPENTELEMETRY_TRACES_RESOURCENAME=tyk-gateway
   TYK_GW_OPENTELEMETRY_METRICS_ENABLED=true
   TYK_GW_OPENTELEMETRY_METRICS_ENDPOINT=otel-collector:4317
   TYK_GW_OPENTELEMETRY_METRICS_RESOURCENAME=tyk-gateway
   TYK_GW_ACCESSLOGS_ENABLED=true
   TYK_GW_LOGFORMAT=json
   ```

   Point a collector at your back end; `assets/lab/otel-collector.yaml` is one that ran. For a
   demo, start the whole lab: `references/lab-and-grafana.md` copies `$SKILL/assets/lab`
   (`SKILL=<this skill's folder>`), generates `.env` with random secrets and runs
   `docker compose -p tyk-obs-lab up -d`.

4. **Prove one request end to end** (`GW`, `KEY`, `TEMPO_PORT` and `PROMETHEUS_PORT` as set in
   `references/lab-and-grafana.md`).

   ```bash
   curl -s -D - -o /dev/null $GW/orders-api/anything/orders/42 -H "Authorization: $KEY" | grep -i x-tyk-trace-id
   #   X-Tyk-Trace-Id: 01ebff0c46b16b7afa78bbacf0cb8a6b
   curl -s http://127.0.0.1:$TEMPO_PORT/api/traces/01ebff0c46b16b7afa78bbacf0cb8a6b      # Tempo: the spans
   curl -s --get http://127.0.0.1:$PROMETHEUS_PORT/api/v1/query --data-urlencode 'query=tyk_api_requests_total'
   #   {"metric":{"job":"tyk-gateway","tyk_api_id":"orders","http_response_status_code":"200",...},"value":[...,"4"]}
   ```

   The same id is in the access-log line (`trace_id`) and in the analytics record (tag
   `trace-id-<id>`).

5. **Add custom dimensions only when asked**, and re-list the defaults: a populated
   `api_metrics` list replaces the four default instruments. Start from
   `assets/api-metrics-chargeback.json`, then check the Gateway log for
   `failed to create API metric` (`references/metrics.md`).

6. **Add Tyk Pump for per-request records.** Turn on `TYK_GW_ENABLEANALYTICS=true`, run Pump
   with `TYK_PMP_OMITCONFIGFILE=true`, and add each back end as a pump in it. Every Pump that
   reads a Redis must carry the same set of pumps; two Pumps with different pump sets split the
   records (`references/tyk-pump.md`). For Elasticsearch set `META_VERSION` (3 to 7) and
   `META_BULKCONFIG_FLUSHINTERVAL`.

7. **For the Dashboard**, add the `sql` and `sql_aggregate` pumps against the Dashboard's
   analytics database and set `TYK_DB_ENABLEAGGREGATELOOKUPS=true`
   (`references/dashboard-analytics.md`). Prove it with
   `GET /api/usage/apis/{dd}/{mm}/{yyyy}/{dd}/{mm}/{yyyy}?res=day&p=-1`.

8. **Hand over a dashboard.** Import `assets/lab/grafana/dashboards/tyk-gateway-otel.json` and
   run `python3 "$SKILL/scripts/check_dashboard_queries.py" <dashboard> --prometheus <url>`; every
   panel must say `ok`.

9. **Run the verification checklist** and, for each claim in the report, say whether it was
   checked on the user's running stack (with the image tag) or comes from the public page
   cited, and say plainly when a point was not checked.

## Pitfalls

- An API definition without `middleware.global.trafficLogs.enabled: true` is invisible to
  analytics and API metrics. Traces and access logs still show it, which hides the gap.
- A populated `api_metrics` list removes the default instruments. One invalid instrument (a
  histogram without `histogram_source`) removes every request metric. An empty
  `TYK_GW_OPENTELEMETRY_METRICS_APIMETRICS=` stops the Gateway from starting.
- `/hello` answers 200 when Redis is down; only its body says `fail` (`warn` on a Dashboard-managed Gateway). Probe `/ready`.
- The Pump image's own `pump.conf` points at MongoDB and the Pump exits; use
  `TYK_PMP_OMITCONFIGFILE=true` and `TYK_PMP_DONTPURGEUPTIMEDATA=true`.
- Every Pump that reads a Redis must carry the same set of pumps; two Pumps with different pump
  sets split the records.
- Records waiting in Redis are deleted about `storage_expiration_time` seconds (default 60)
  after the list gets its expiry, whether or not traffic continues; set `-1` to keep them until
  Pump drains them (watch Redis memory).
- Elasticsearch pump: version 8 makes Pump exit; without a flush interval nothing is written
  until 1,000 records.
- `X-Tyk-Trace-Id` is returned for unsampled requests too; the trace may not exist.
- Exemplars need `enable_open_metrics: true` on the collector's Prometheus exporter and
  `--enable-feature=exemplar-storage` on Prometheus.
- Scrape the collector with `honor_labels: true`, or labels arrive as `exported_job`.
- The open source Gateway's id (`instance`) changed on every restart, so old series linger
  until they expire; aggregate by `job` or `tyk_api_id`, not by `instance`.
- A cost-centre header injected from key metadata is not set on a request refused before the
  transform runs (seen with a missing key, 401): the metric carries what the caller sent.
- Detailed recording stores bodies as sent; only `Authorization` is masked.

## Limitations (Gateway 5.16.0, Pump 1.18.0)

- Key `meta_data` is not a metric dimension source; copy it into a header first.
- The Elasticsearch pump writes to Elasticsearch 3, 5, 6 and 7 (and OpenSearch 2 in the version 7
  mode); not to Elasticsearch 8 or later.
- The Dashboard's Log Browser needs raw records (`sql` or `mongo` pump); aggregates alone feed
  the charts only.
- Trace server spans are named after the raw request path; group by `tyk.api.id`.
- With `context_propagation: custom`, no trace header is sent upstream unless the caller sent one.

## Verification checklist

- [ ] Every API definition in scope has `trafficLogs.enabled: true` (read it back from the API).
- [ ] One request returns `X-Tyk-Trace-Id`, and that trace id returns 200 from the trace back end.
- [ ] `tyk_api_requests_total` (or your own counter) shows the API with the expected status codes.
- [ ] With custom `api_metrics`: no `failed to create API metric` in the Gateway log, and each
      instrument has at least one series.
- [ ] Exemplars, when asked: `query_exemplars` returns a `trace_id` that opens in the back end.
- [ ] Pump: a record for the request reaches each configured back end; Pump stays running.
- [ ] Dashboard: `GET /api/usage/apis/...` lists the API with the right hit count.
- [ ] `GET /ready` returns 200, and 503 with Redis stopped, if the user relies on it.
- [ ] `check_dashboard_queries.py` exits 0 against the user's Prometheus.
- [ ] No key, password or licence is in a definition, a dashboard or a shared log excerpt.

## Hand-offs

- Licence wording, packages, versions and images: the `tyk-facts` skill.
- MCP proxy analytics, access-log fields and metrics: the `tyk-mcp-gateway` skill.
- AI Studio and LLM usage analytics: the `tyk-ai-gateway` skill.
- Tyk OAS definitions beyond the observability fields: the `tyk-api-definitions-routing` skill.
- Keys, policies, aliases and rate-limit behaviour: the `tyk-rate-limits-keys-policies` skill.
- Analytics storage and Pump capacity: the `tyk-infra-sizing` skill.
- A demo stack with a Dashboard: the `tyk-demo-builder` skill. A structured proof of concept:
  the `tyk-poc-runbook` skill.
- Installing Tyk, Pump and the data-plane charts for real: the `tyk-deployment-planner` skill.
