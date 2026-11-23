# Tyk Streams: definitions, Kafka, HTTP, SSE and WebSocket

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0`), Tyk Dashboard 5.16.0, Apache Kafka 4.4.0

The lab in `lab.md` (Gateway API) and a Dashboard bundle from the `tyk-demo-builder` skill
(Dashboard API) reproduce everything here. See
https://tyk.io/docs/api-management/event-driven-apis (overview, set-up, troubleshooting) and
https://tyk.io/docs/api-management/stream-config (every input, output and processor field).

## What it needs

- **Enterprise Edition Gateway image** `tykio/tyk-gateway-ee` with `streaming.enabled: true`
  (`TYK_GW_STREAMING_ENABLED=true`). WebSocket outputs also need
  `http_server_options.enable_websockets: true`.
- **With a Dashboard:** `streaming.enabled: true` in the Dashboard too
  (`TYK_DB_STREAMING_ENABLED=true`), and a Dashboard licence that includes the `streams` scope.
  The docs give the error when it does not: `Your license does not support adding/modifying streams
  API configuration`.
- Tyk Streams is an Enterprise Edition feature. Packaging, trials and what a licence includes: the
  `tyk-facts` skill.
- Tyk Streams runs on the Enterprise Edition Gateway image (`tykio/tyk-gateway-ee`) with
  `streaming.enabled`; load Streams definitions there only.

## The definition

A Streams API is a Tyk OAS definition with an `x-tyk-streaming` block beside `x-tyk-api-gateway`.
`upstream.url` is not used; leave it `""`. Each named stream has one `input`, one `output` and an
optional `pipeline` of processors. Classic definitions cannot carry streams.

Publish: HTTP POST in, Kafka out (`assets/streams-lab/definitions/order-events-in.json`):

```json
"x-tyk-streaming": {"streams": {"publish": {
  "input": {"http_server": {"path": "/publish", "allowed_verbs": ["POST"], "timeout": "5s"}},
  "pipeline": {"processors": [{"mapping": "root = this\nroot.partner = \"$tyk_meta.partner\""}]},
  "output": {"kafka": {"addresses": ["kafka:9092"], "topic": "orders", "max_in_flight": 10}}
}}}
```

`POST /order-events-in/publish` answers `200` with an empty body once Kafka has the message.

Subscribe: Kafka in, HTTP out on three paths at once (`order-events-out.json`):

```json
"x-tyk-streaming": {"streams": {"subscribe": {
  "input": {"kafka": {"addresses": ["kafka:9092"], "topics": ["orders"],
    "consumer_group": "order-feed-$tyk_meta.partner", "start_from_oldest": false}},
  "output": {"http_server": {"path": "/next", "stream_path": "/sse", "stream_format": "event_source",
    "ws_path": "/ws", "allowed_verbs": ["GET"]}}
}}}
```

| Client calls | Gets |
| --- | --- |
| `GET /order-events/next` | Waits for the next message, then `200`, `Content-Type: application/octet-stream`, body = the message |
| `GET /order-events/sse` with `stream_format: event_source` | `200`, `Content-Type: text/event-stream`, one `data: {...}` event per message (headers arrive at once) |
| `GET /order-events/sse` **without** `stream_format` | `200`, `Content-Type: text/plain; charset=utf-8`, messages separated by newlines, no `data:` prefix. A browser `EventSource` cannot read this |
| WebSocket to `/order-events/ws` | One text frame per message |
| Any other path under the listen path | `404 {"error": "not found"}` |

`stream_format` comes from the stream engine's `http_server` output and is in the Gateway's schema,
but the Tyk configuration reference does not list it; the table above holds on 5.16.0.

## Load it

```bash
# Gateway API (no Dashboard): the same call as any Tyk OAS API, then a reload
curl -s -X POST "$GWE/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" --data @order-events-in.json
# {"key":"order-events-in","status":"ok","action":"added"}
curl -s "$GWE/tyk/reload/group" -H "x-tyk-authorization: $SECRET"

# Dashboard API: the Streams media type (the plain /api/apis/oas endpoint also works)
curl -s -X POST "$DASH/api/apis/streams" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/vnd.tyk.streams.oas' --data @order-events-in.json
# {"Status":"OK","Message":"API created","Meta":"<database id>","ID":"order-events-in"}
curl -s "$DASH/api/apis/streams/order-events-in" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Accept: application/vnd.tyk.streams.oas'
# the definition; without that Accept header: 400
# {"Status":"Error","Message":"invalid accept header type - allowed accept header types: [application/vnd.tyk.streams.oas]","Meta":null}
```

The Dashboard checks the fields of the documented connectors: `addresses` as a string and
`max_in_flight` as `"ten"` get `400` with
`output.kafka.addresses: Invalid type. Expected: array, given: string` and
`output.kafka.max_in_flight: Invalid type. Expected: number, given: string`. The Gateway API does
not check them: the same definition gets `200 added`. Validate through the Dashboard, or check the
fields against the configuration reference, before you load through the Gateway API.

## Authentication and the caller's identity

Authentication, policies and rate limits apply to stream paths as to any API: on a publish API
with an auth token, no key gets `401 {"error": "Authorization field missing"}`, a wrong key `403
{"error": "Access to this API has been disallowed"}`, and a key with rate 3 per 60 s gets
`200 200 200 429`. Webhooks fire on stream APIs too (`gateway-events-and-webhooks.md`).

What reaches the stream:

| You want | What happens |
| --- | --- |
| The caller's identity in each message | `$tyk_meta.<field>` (key metadata) inside the stream configuration is filled per request: `"partner": "$tyk_meta.partner"` in a `mapping` writes `"partner":"partner-a"` into the Kafka message |
| A header added by `transformRequestHeaders` | Not seen by the stream: `meta("X-Partner")` is `null` |
| The client's `Authorization` header | Seen by the stream as metadata (`meta("Authorization")` returns the key) **unless** `stripAuthorizationData: true`, after which it is `null`. Always set it on Streams APIs |
| `$tyk_context.<name>` values | Filled only with `middleware.global.contextVariables.enabled: true`; without it they are empty strings |

## One message to one client, or every message to every client

A Kafka input with a fixed `consumer_group` feeds all connected clients of that API as **one**
consumer: messages are shared round-robin. One SSE and one WebSocket client on the same stream
receive alternate messages (for example orders 30, 32, 34 and 31, 33, 35). For every client to receive every message,
give each client its own consumer group by putting the caller's identity in the group name:

- `"consumer_group": "order-feed-$tyk_meta.partner"`: keys for partner-a and partner-b create
  the groups `order-feed-partner-a` and `order-feed-partner-b`, and each client receives every
  order.
- `"consumer_group": "feed-$tyk_context.headers_X_Client_Id"` with context variables on: groups
  `feed-a` and `feed-b`, and each client gets every message. Without context variables the group
  is `feed-` for everyone. A client-supplied header is not an identity; prefer key metadata or a
  JWT claim.

Clients of the same identity still share their group round-robin. `start_from_oldest` is `true`
unless set: a new group's first client receives every message still in the topic. Set it to
`false` for live feeds.

Create topics before consumers start. A consumer that connects before the topic exists misses
the first message published to it; with the topic created first
(`kafka-topics.sh --create --if-not-exists`), every client receives every message.

## Other patterns from the docs

The overview page shows HTTP polling into Kafka (`http_client` input), filtering and enrichment
with `mapping` and `branch` processors, and `broker` inputs and outputs with `fan_out` to several
destinations (https://tyk.io/docs/api-management/event-driven-apis, "Use Cases"). They use the
same connectors; prove each one on your stack before you rely on it.
