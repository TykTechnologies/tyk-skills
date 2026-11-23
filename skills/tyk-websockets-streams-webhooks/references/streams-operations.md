# Tyk Streams in operation: start-up, broker outages, connectors, AsyncAPI

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0`), Tyk Dashboard 5.16.0, Apache Kafka 4.4.0

Documentation: https://tyk.io/docs/api-management/event-driven-apis and
https://tyk.io/docs/api-management/stream-config. Where this file says "the docs", it means those
two pages.

## Start-up after a load, reload or restart

The reload call returns at once and the APIs load just after it; until then their paths answer
`404 Not Found` (plain text), stream APIs or not. Streams then start with the API:

| When | What the client gets |
| --- | --- |
| New stream API, publishing from 0.1 s after the reload call | `404 Not Found` (plain text) at 0.1 s and 0.6 s, `200` from 1.3 s |
| First request for a stream whose configuration uses `$tyk_meta` or `$tyk_context`, after a reload or for a caller not seen before | Sometimes `500 {"error": "invalid route handler"}` once (on a publish or an SSE consumer), then normal |
| Gateway restarted | First publish `200` in about 0.4 s, then about 3 ms each |
| Reload with the definition unchanged | Publishing keeps answering `200` throughout |

A proof or a client should retry a `404` or `500` from a stream path for a few seconds after any
deployment, and only then report a failure.

## When the broker is down

When Kafka stops while the Gateway keeps running:

| Step | What the client gets |
| --- | --- |
| Publish while Kafka is down | `408 Request timed out` (`text/plain`) after the input's `timeout` (5 s); sometimes the first attempts get `502` at once |
| Are those messages written? | No. Every request that got `408` or `502` is missing from the topic; every `200` is there |
| Publish after Kafka comes back | Still `408` for a while, from under a minute to about two minutes, sometimes followed by one `500`. Then `200` without any action on the Gateway |
| New SSE consumer while Kafka is down | Connection open, no messages; the Gateway log shows `Failed to connect to kafka: ... no such host` |

So: treat `408`, `502` and `500` from a publish path as "not delivered" and retry with backoff;
monitor the Gateway log for `Failed to connect to kafka`; expect up to minutes, not seconds, before
publishing recovers after a broker restart.

## Connectors: what is supported

The configuration reference documents these connectors for both `input` and `output`, and the
Dashboard validates their fields: **`amqp_0_9`, `amqp_1`, `broker`, `http_client`, `http_server`,
`kafka`, `mqtt`** (https://tyk.io/docs/api-management/stream-config, "Connector Validation"). The
same page documents the `avro` and `mapping` processors. The overview names Kafka, WebSocket,
Server-Sent Events and webhooks as the supported protocols; WebSocket and SSE are served by
`http_server` (`ws_path`, `stream_path`).

Anything else is **not supported**, even when it loads. Word it like this:

> "<connector> is not a supported Tyk Streams connector. The supported connectors are amqp_0_9,
> amqp_1, broker, http_client, http_server, kafka and mqtt
> (https://tyk.io/docs/api-management/stream-config)."

Do not recommend an unsupported connector, and do not describe it as working. What the products
do with other names:

- The Dashboard does not reject a connector name it does not validate: `nats`, `redis_list` and a
  made-up `made_up_thing` are all saved with `200 API created`.
- A name the Gateway does not know answers every request with
  `500 {"error": "invalid route handler"}`, and the log says
  `unable to infer output type from candidates: [made_up_thing]`. Check spelling first when you
  see this.

The Gateway also removes components it considers unsafe (the docs name the `file`, `socket` and
`stdin` inputs) unless they are listed in `streaming.allow_unsafe`. `streaming.enable_all_experimental`
in the Dashboard turns its field validation off (docs).

## Several Gateways

`http_server` inputs and outputs tie a request and its reply to the Gateway that received it.
Behind a load balancer, use a broker such as Kafka between producer and consumer so any Gateway can
serve either side (docs, "Load Balancing with http_server").

## Changing or deactivating a Streams API

The docs state that open connections keep their old configuration when a Streams API is edited or
deactivated, that new connections pick up an edit after about 10 seconds, and that deactivating
an API does not close open connections ("Active Connections During API Changes"). Plan
deployments with that in mind and prove it on your stack before you depend on it.

## Analytics

The docs state that the Dashboard shows no request analytics for Streams APIs, only connection
attempts ("Analytics"). Monitor the broker and the Gateway log for message-level visibility.

## AsyncAPI

- A Tyk Streams API is a Tyk OAS document. Its streams sit under `x-tyk-streaming` → `streams`,
  and each stream is configured as `input`, `pipeline` and `output` sections, in YAML or JSON. See
  https://tyk.io/docs/api-management/event-driven-apis and
  https://tyk.io/docs/api-management/stream-config.
- Tyk Streams does not take an AsyncAPI document as its definition, and there is no import that
  creates a Streams API from an AsyncAPI document.
- The Developer Portal can show AsyncAPI documentation for a product. The default theme has an
  AsyncAPI viewer from default theme 1.12.0 onwards, and Portal 1.14.0 added Streams APIs as
  products developers can request access to
  (https://tyk.io/docs/developer-support/release-notes/portal). On Portal 1.20.1, an AsyncAPI
  document uploaded to a product's specification
  (`POST /portal-api/products/{id}/spec-details/{spec id}/oas`) in **YAML** is shown with the
  AsyncAPI viewer; the same document in **JSON** goes to the OpenAPI renderer and fails to parse
  ("Failed to parse OpenAPI file"). Upload AsyncAPI as YAML. The viewer loads its script from a
  public CDN. See https://tyk.io/docs/portal/api-products. Products and specifications on the
  Portal: the `tyk-developer-portal-setup` skill; the theme and its CDN scripts: the
  `tyk-developer-portal-branding` skill.
