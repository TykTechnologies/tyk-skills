# Pitfalls and limitations

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, `tykio/tyk-gateway-ee:v5.16.0`), Tyk Dashboard 5.16.0, Apache Kafka 4.4.0

The lab in `lab.md` or a `tyk-demo-builder` bundle reproduces each pitfall; the reference named
in brackets has the commands and responses. `scripts/check_events_def.py`
catches the ones marked (checker).

## Pitfalls

### WebSocket and SSE (`websocket-and-sse.md`)

1. `http_server_options.write_timeout` ends every SSE stream after that many **seconds**
   (default 120). Raise it for long-lived streams; the docs page's "milliseconds" is wrong.
2. `enable_websockets: false` silently stops upgrades: the upstream gets a plain GET.
3. Authentication and rate limits are checked on the upgrade only. A deleted key's open
   WebSocket keeps working; a rate limit counts connections, not frames.
4. A slow response that is not `text/event-stream` and has a `Content-Length` reaches the client
   in one piece at the end unless `flush_interval` (milliseconds) is set.
5. Measure the upstream's own limits too: the lab's go-httpbin closes WebSockets after 10 s.

### Tyk Streams (`streams-definitions.md`, `streams-operations.md`)

6. `stream_path` without `stream_format: event_source` is newline-separated `text/plain`, not
   Server-Sent Events; browsers cannot consume it with `EventSource` (checker).
7. One `consumer_group` for an API shares messages round-robin among all its clients. Put the
   caller's identity in the group name for every-client delivery (checker).
8. A new consumer group starts from the oldest message (`start_from_oldest` defaults to `true`)
   (checker).
9. A consumer that starts before its topic exists misses the first messages. Create topics first.
10. Header transforms do not reach the stream, but the client's `Authorization` header does,
    unless `stripAuthorizationData: true` (checker).
11. `$tyk_context.` values are empty strings unless `middleware.global.contextVariables` is
    enabled; a per-client group silently becomes one shared group (checker).
12. Until a reload has finished (about a second), paths answer `404 Not Found`; the first
    request for a per-caller configuration sometimes gets `500 {"error": "invalid route handler"}`.
    Retry before you report a failure.
13. With the broker down, publishing answers `408 Request timed out` (or `502`) and the message
    is not written; after the broker returns, publishing can take up to about two minutes to
    recover.
14. The Gateway API stores a Streams definition with wrong connector field types (`200 added`);
    only the Dashboard validates documented connector fields (`400` with the field errors).
15. The Dashboard accepts connector names it does not validate. They are not supported; an unknown
    name answers `500 {"error": "invalid route handler"}` (checker).
16. The Gateway API checks the fields of the first event handler only; a later handler with a
    wrong trigger, type or missing field is stored and does not work (checker).
17. `GET /api/apis/streams/{id}` needs `Accept: application/vnd.tyk.streams.oas`, or it answers
    `400 invalid accept header type`.

### Webhooks (`gateway-events-and-webhooks.md`)

18. A `bodyTemplate` is resolved under `template_path`; `templates/x.json` (the docs examples'
    form) falls back to the default template with only a log warning (checker).
19. `"cooldownPeriod": "0s"` sends an identical payload once and never again (checker).
20. A timestamp or other changing value in the template defeats the cooldown: every event is sent.
21. No retries: one attempt per event, failures only in the Gateway log.
22. Template output is HTML-escaped (`+` as `&#43;`, `"` as `&#34;`).
23. The default template includes the `key` field (docs); write your own (checker).
24. A webhook `url` without `http://` or `https://` is accepted and never delivered (checker).
25. Event names are case-sensitive: `RatelimitExceeded`, not `RateLimitExceeded`; the OAuth 2.0
    events cannot be a Tyk OAS trigger (checker).

## Limitations

- Tyk Streams needs the Enterprise Edition Gateway image (`tykio/tyk-gateway-ee`) with
  `streaming.enabled` and, with a Dashboard, a licence with the `streams` scope
  (https://tyk.io/docs/api-management/event-driven-apis); the `tyk-facts` skill covers licensing.
- Tyk Streams supports only the documented connectors: `amqp_0_9`, `amqp_1`, `broker`,
  `http_client`, `http_server`, `kafka`, `mqtt` (https://tyk.io/docs/api-management/stream-config).
- The Dashboard shows no request analytics for Streams APIs, only connection attempts (docs).
- `http_server` inputs and outputs bind a request and its reply to one Gateway; use a broker
  between Gateways (docs).
- WebSocket frames pass through unchanged: no per-message authentication, rate limiting,
  validation or transformation.
- The Gateway adds no signature to webhook requests (the receiver gets only `User-Agent:
  Tyk-Hookshot`, `Content-Type` and the configured headers). Authenticate the receiver with a
  secret header from `headers` and keep the receiver on a private network.
- Webhook delivery is at most once per event, with no retry or queue.
