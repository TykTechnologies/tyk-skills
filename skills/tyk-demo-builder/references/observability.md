# Observability Module And Health Proof

Last verified: 2026-11-19 against Tyk Gateway and Dashboard 5.16.0, Pump 1.18.0 and Developer Portal 1.20.1.

This page covers what the bundle generates for health checks and the `otel` module, and what
their proofs assert. For how to make Tyk traffic visible in your own tools (OpenTelemetry
settings, metrics, Pump back ends, Dashboard analytics), use the `tyk-observability` skill;
for diagnosing a failing stack, the `tyk-troubleshooting` skill.

## HEALTH-001 (Every Bundle)

`verify-demo.sh` checks, in every bundle:

- each gateway's `/hello` answers 200 with its `redis` and `dashboard` components passing;
- the Dashboard's `/hello` answers 200;
- with `portal`, the Portal's `/live` and `/ready` answer 200;
- Pump's `/health` on port 8083 answers 200, read from inside the Compose network (Pump publishes
  no host port);
- the gateway admin API `/tyk/apis` answers 403 without the node secret and with a wrong one,
  and 200 with it.

A passing run reports `gateway /hello 200 (redis and dashboard pass: true); dashboard
/hello 200; portal /live 200, /ready 200; pump /health 200; gateway /tyk/apis without secret
403, wrong secret 403, node secret 200`.

Every gateway gets a readable Compose `hostname:` (`<slug>-gateway`, or
`<slug>-gateway-internal` and `<slug>-gateway-external` with `gateway-sharding`), because the
Dashboard's node list shows gateways by hostname.

## What The otel Module Generates

- An OpenTelemetry Collector (`otel/collector.yaml`), Tempo for traces and Prometheus for
  metrics, under the Compose profile `otel`, published on `127.0.0.1` (`OTEL_GRPC_PORT`,
  `OTEL_HTTP_PORT`, `TEMPO_PORT`, `PROMETHEUS_PORT`).
- Gateway tracing switched on with `TYK_GW_OPENTELEMETRY_TRACES_ENABLED`,
  `TYK_GW_OPENTELEMETRY_TRACES_EXPORTER=grpc` and
  `TYK_GW_OPENTELEMETRY_TRACES_ENDPOINT=otel-collector:4317`.
- A `spanmetrics` connector in the Collector that turns gateway spans into a `calls_total`
  metric for Prometheus, and a `filelog` receiver that reads the bundled upstream's JSON log
  (`/var/log/demo/upstream.jsonl`), in which the upstream writes the trace id of the
  `traceparent` header it received.

Every generated API sets `server.detailedActivityLogs`, and the gateway-wide
`TYK_GW_ANALYTICSCONFIG_ENABLEDETAILEDRECORDING` stays `false`. Recording detail reads the
whole response body for the analytics record
(see https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/handler_success.go), so an API that streams large downloads leaves `detailedActivityLogs` off.

## OTEL-001

One request with a fixed `traceparent` goes through the gateway; the proof then requires all
three: the trace in Tempo (`/api/traces/<trace id>`), a non-empty
`calls_total{service_name="tyk-gateway"}` in Prometheus, and the trace id in the Collector's
log of the upstream's structured log line.

What it does not prove: an external back end (Datadog, Dynatrace, Splunk, CloudWatch). Mark such
a requirement `live` only when its exporter is configured and its data is observed.
