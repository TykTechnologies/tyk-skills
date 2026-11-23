---
name: tyk-websockets-streams-webhooks
description: Put WebSocket, Server-Sent Events and Kafka traffic through Tyk Gateway 5.16 and react to Gateway events - WebSocket and SSE proxying, Tyk Streams (Kafka over HTTP, SSE and WebSocket) and event webhooks, with exact settings and responses. Use when asked about WebSockets, SSE, Kafka through the Gateway, Tyk Streams, event-driven or async APIs, AsyncAPI, Gateway event webhooks or rate-limit and quota events. Not for keys, quotas and rate limits, which belong to the tyk-rate-limits-keys-policies skill, or MCP servers, which belong to the tyk-mcp-gateway skill.
---

# Tyk WebSockets, Streams and Webhooks

Agents asked for "Kafka behind Tyk" or "a webhook when a client hits its limit" tend to copy the
docs' getting-started example, test one happy request and stop. On Tyk Gateway 5.16.0 that misses
most of what users meet: SSE streams that end after two minutes, a "Server-Sent Events" path that
browsers cannot read, consumers that steal each other's messages, a stream that answers `408` and
drops the message while Kafka is down, and webhooks that fire once and never again. This skill
gives the definitions, commands and real responses for each on Gateway and Dashboard 5.16.0 with
Kafka 4.4.0, plus a definition checker, a WebSocket probe and a webhook receiver.

## What you need

- **WebSocket, SSE and webhooks:** the open source Gateway (`tykio/tyk-gateway`) with Redis; no
  licence. Global webhooks (defined once, shared by APIs) need a Dashboard, which needs a licence.
- **Tyk Streams:** an Enterprise Edition feature. It runs on the Enterprise Edition Gateway image
  (`tykio/tyk-gateway-ee`) with `streaming.enabled`; a Dashboard that manages Streams APIs needs
  `streaming.enabled` too and a licence that includes the `streams` scope
  (https://tyk.io/docs/api-management/event-driven-apis). The Enterprise Edition image is
  commercially licensed; whether and how you may run it, packaging and trials: the `tyk-facts`
  skill.
- A stack: `references/lab.md` (copy `assets/streams-lab/`), the `tyk-demo-builder` skill (module
  `streams`) for a Dashboard demo, or the `tyk-deployment` skill for an installation.

## Clarify first

Ask only what the request leaves open. Defaults in brackets.

1. **Proxy an existing WebSocket or SSE server, or bridge to a broker?** [Existing server: plain
   proxying on any edition. A broker: Tyk Streams.]
2. **Which broker or protocol?** [Kafka. Only the documented connectors are supported:
   `amqp_0_9`, `amqp_1`, `broker`, `http_client`, `http_server`, `kafka`, `mqtt`.]
3. **Must every client get every message, or should clients share the work?** [Every client: one
   consumer group per caller identity.]
4. **How long do streams stay open?** [Over 120 s: raise `write_timeout` for SSE.]
5. **Who calls, and how do they authenticate?** [Browsers: key in the query string, since
   browsers cannot set headers on WebSockets. Methods: the `tyk-authentication` skill.]
6. **Which events need a webhook, and what may the receiver see?** [Rate limit, quota, auth
   failure and key expiry, to one receiver, with a custom template without the key.]
7. **Dashboard or open source Gateway?** [Find out: the `tyk-api-definitions-routing` skill, step 1.]

## Workflow

Set these once. `$SKILL` is this skill's folder.

```bash
SKILL=<this skill's folder>
GW=http://localhost:8080            # Gateway (open source or Enterprise Edition)
GWE=http://localhost:8081           # Enterprise Edition Gateway with Streams on, if separate
SECRET=<gateway secret>             # "secret" in tyk.conf, or TYK_GW_SECRET
DASH=http://localhost:3000          # Dashboard, when there is one
DASHBOARD_API_KEY=<Dashboard user API key>
HOOKS=http://localhost:9000         # the webhook receiver (scripts/webhook_receiver.py)
# Keys KEY, SLOW (open source APIs), KA, KB (Streams APIs): create them as in references/lab.md
```

1. **Set the Gateway up for long-lived traffic.** In `tyk.conf` (or `TYK_GW_HTTPSERVEROPTIONS_*`):
   `http_server_options.enable_websockets: true`; `write_timeout` in seconds above the longest SSE
   stream you need (it ends SSE streams; WebSockets are not affected); `flush_interval` in
   milliseconds only for slow non-SSE responses. For Streams: the `tykio/tyk-gateway-ee` image and
   `TYK_GW_STREAMING_ENABLED=true`. Detail: `references/websocket-and-sse.md`.

2. **Write the definition.** Start from `assets/streams-lab/definitions/`: `chat.json`
   (WebSocket), `realtime.json` (SSE), `order-events-in.json` (HTTP to Kafka),
   `order-events-out.json` (Kafka to HTTP, SSE and WebSocket, one group per partner),
   `alerts.json` (webhooks). On every Streams API set `stripAuthorizationData: true`; on an SSE
   output set `stream_format: event_source`.

3. **Check it before loading.** Both checkers:

   ```bash
   python3 "$SKILL/scripts/check_events_def.py" api.json
   # 0 error(s), 0 warning(s), 0 note(s)
   python3 <tyk-api-definitions-routing skill>/scripts/check_tyk_oas.py api.json
   ```

   Fix every `ERROR`; explain every `WARN` and `NOTE` to the user. On a Streams API
   `check_tyk_oas.py` notes the empty `upstream.url`: Streams APIs do not use the upstream, and
   this skill leaves it `""`. `check_events_def.py` checks every event handler; the Gateway API
   checks only the first, so a later bad handler is stored without complaint.

4. **Load it.** Gateway API: `POST $GW/tyk/apis/oas` then `GET $GW/tyk/reload/group` (Streams
   definitions too). Dashboard: `POST $DASH/api/apis/streams` with
   `Content-Type: application/vnd.tyk.streams.oas` for Streams, `POST $DASH/api/apis/oas` for the
   rest. For Kafka, create the topics before any consumer connects. Wait a few seconds after a
   reload: until the APIs have loaded, their paths answer `404 Not Found`, and the first request
   for a per-caller stream can get `500 {"error": "invalid route handler"}`.

5. **Prove the happy path.** Keys as in `references/lab.md` (`KEY` for the open source APIs,
   `KA` for partner-a on the Streams APIs):

   ```bash
   python3 "$SKILL/scripts/ws_probe.py" "ws://localhost:8080/chat/room/1?access_token=$KEY" --send hi
   # handshake: 101 Switching Protocols / > hi / < hi
   curl -s -N -H "Authorization: $KEY" "$GW/realtime/sse?count=3&duration=3s&delay=0"   # 3 events over 3 s, each as sent
   curl -s -N -H "Authorization: $KA" $GWE/order-events/sse &                       # consumer
   curl -s -w '%{http_code}\n' -X POST $GWE/order-events-in/publish -H "Authorization: $KA" -d '{"order":1}'
   # 200, then the consumer prints: data: {"order":1,"partner":"partner-a"}
   ```

6. **Prove the failures the user will meet.** No key and a wrong key (`401`, `403`); the rate
   limit (`429`, and for WebSockets per connection); a stream open longer than `write_timeout`;
   two consumers (round-robin or both, as intended); Kafka stopped
   (`docker compose stop kafka`: publish gets `408 Request timed out`, message not written) and
   the time to recover after it returns.

7. **Prove the webhooks.** Run `scripts/webhook_receiver.py` where the Gateway can reach it, fire
   each event and read what arrived:

   ```bash
   for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code} ' -H "Authorization: $SLOW" $GW/alerts/get; done
   # 200 200 429
   curl -s $HOOKS/_received   # POST /tyk/ratelimit, User-Agent Tyk-Hookshot, body from your template
   ```

   Check the body is your template (not the default), the cooldown behaves as intended, and what
   happens when the receiver is down (nothing is retried).

8. **Tell the user** what each client sees on success and failure, how long streams stay open,
   who gets which message, what happens while the broker is down, which events fire webhooks and
   what the receiver gets, and what was not proved on their stack.

## References

| File | Read it for |
| --- | --- |
| `references/websocket-and-sse.md` | Gateway settings and their effect, middleware on upgrades, query-string keys for browsers, probing |
| `references/streams-definitions.md` | What Streams needs, the definition, Gateway and Dashboard APIs, HTTP, SSE and WebSocket outputs, Kafka groups, authentication and identity |
| `references/streams-operations.md` | Start-up, broker outages and recovery, supported connectors and how to word the rest, changes, analytics, AsyncAPI |
| `references/gateway-events-and-webhooks.md` | Event types, what fires when, webhook and log handlers in Tyk OAS, templates, cooldown, retries, global webhooks |
| `references/pitfalls-and-limitations.md` | Twenty-five pitfalls and the product's limits |
| `references/lab.md` | The lab: open source and Enterprise Edition Gateways, Kafka, echo servers, webhook receiver, main recipe |

Scripts (Python standard library): `scripts/check_events_def.py`, `scripts/ws_probe.py`,
`scripts/webhook_receiver.py`. Assets: `assets/streams-lab/`.

## Pitfalls

The costliest; all twenty-five are in `references/pitfalls-and-limitations.md`.

- SSE streams end after `write_timeout` **seconds** (default 120).
- A Streams `stream_path` is plain text unless `stream_format: event_source`.
- One consumer group per API means clients share messages round-robin.
- A new consumer group replays the topic from the oldest message.
- While the broker is down, publishing answers `408` and the message is lost; publishing can
  keep failing for up to two minutes after the broker returns.
- The client's credential reaches the stream unless `stripAuthorizationData: true`.
- WebSocket authentication and rate limits apply to the upgrade only; deleting a key does not
  close an open connection.
- A webhook `bodyTemplate` of `templates/...` silently falls back to the default template.
- `"cooldownPeriod": "0s"` sends an identical webhook once, ever. Webhooks are never retried.

## Limitations

- Tyk Streams needs the Enterprise Edition Gateway image (`tykio/tyk-gateway-ee`) with
  `streaming.enabled`.
- Only the documented connectors are supported. Anything else is "not supported", even if the
  Dashboard accepts it; never recommend one.
- No request analytics for Streams APIs in the Dashboard (docs); no per-frame checks on
  WebSockets; no webhook signature, retry or queue.
- A Streams API is a Tyk OAS document (`x-tyk-streaming`); Tyk Streams does not take an AsyncAPI
  document as its definition, and no import creates a Streams API from AsyncAPI. The Developer
  Portal can show AsyncAPI documentation for a product: on Portal 1.20.1 upload it as YAML, since
  JSON goes to the OpenAPI renderer and fails to parse (`references/streams-operations.md`).

## Verification checklist

Before reporting success:

- [ ] `check_events_def.py` and `check_tyk_oas.py` report no errors; every warning and note is
      explained.
- [ ] A WebSocket opened (`101`) and echoed; a missing and a wrong credential got `401` and `403`.
- [ ] An SSE stream delivered events as they were sent and stayed open as long as required.
- [ ] Streams: a publish got `200` and the message is in the broker; each consumer received what
      the user intended (all messages, or a share).
- [ ] Streams: the broker was stopped and started, and the client responses and recovery time
      were recorded.
- [ ] Each configured event fired its webhook, with your template, and the receiver's response
      code was checked in the Gateway log.
- [ ] Nothing unsupported is in a definition, and no key or secret is in a file you leave behind.

## Hand-offs

- The rest of the API definition: the `tyk-api-definitions-routing` skill. Authentication methods: the
  `tyk-authentication` skill. Keys, policies, quotas and rate limits: the
  `tyk-rate-limits-keys-policies` skill.
- Products and API documentation on the Developer Portal: the `tyk-developer-portal-setup`
  skill. Logs, metrics and traces: the `tyk-observability` skill. Errors these tables do not
  explain: the `tyk-troubleshooting` skill. JavaScript event handlers and plugins: the
  `tyk-custom-plugins` skill.
- Licensing and editions: the `tyk-facts` skill. A Dashboard demo: the `tyk-demo-builder` skill.
  An installation: the `tyk-deployment` skill.
