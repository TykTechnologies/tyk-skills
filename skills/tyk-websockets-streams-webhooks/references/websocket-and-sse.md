# WebSocket and Server-Sent Events on an ordinary API

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), Redis 7.2, go-httpbin 2.25.0, Python `websockets` 15.0.1

Everything here runs on the open source Gateway; no licence. The lab in `lab.md` reproduces it.
See https://tyk.io/docs/advanced-configuration/websockets and
https://tyk.io/docs/advanced-configuration/sse-proxy.

## The API definition

A WebSocket or SSE upstream needs nothing special in the Tyk OAS definition: an ordinary API
whose upstream URL points at the server. Use `ws://` and `wss://` only in the client; the
upstream URL stays `http://` or `https://`.

```json
{
  "openapi": "3.0.3",
  "info": {"title": "Chat", "version": "1.0.0"},
  "paths": {},
  "components": {"securitySchemes": {"key": {"type": "apiKey", "in": "query", "name": "access_token"}}},
  "security": [{"key": []}],
  "x-tyk-api-gateway": {
    "info": {"id": "chat", "orgId": "default", "name": "Chat", "state": {"active": true}},
    "upstream": {"url": "http://ws-echo:8080"},
    "server": {"listenPath": {"value": "/chat/", "strip": true},
      "authentication": {"enabled": true, "stripAuthorizationData": true,
        "securitySchemes": {"key": {"enabled": true}}}},
    "middleware": {"global": {
      "contextVariables": {"enabled": true},
      "transformRequestHeaders": {"enabled": true,
        "add": [{"name": "X-Request-Id", "value": "$tyk_context.request_id"}], "remove": ["X-Debug"]}}}
  }
}
```

Browsers cannot set an `Authorization` header on a WebSocket, which is why this example reads the
key from the query string. With `stripAuthorizationData: true` the upstream sees
`"path": "/room/1"` for `ws://.../chat/room/1?access_token=<key>`: the token is removed.

## Gateway settings that matter

All sit under `http_server_options` in `tyk.conf` (environment `TYK_GW_HTTPSERVEROPTIONS_*`).

| Setting | WebSocket | Server-Sent Events (`text/event-stream`) |
| --- | --- | --- |
| `enable_websockets` | Must be `true`. With `false` the Gateway does not pass the upgrade on; the client gets whatever the upstream answers to a plain GET (the lab upstream: `426 Upgrade Required`) | Not needed: SSE streams with `false` (40 events in 41 s). The SSE docs page says to set it; the Gateway does not need it |
| `write_timeout` (seconds) | No effect: with 5 s set, a connection stays open (25 s and longer), with and without traffic | Ends the stream after that many **seconds**: with 5, a one-event-per-second stream ends after 5 events (6 s); with 30, after 30 events (31 s). The SSE docs page says milliseconds; the Gateway uses seconds. Default 120 (https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/proxy_muxer.go) |
| `read_timeout` (seconds) | No effect (5 s set, a connection stays open 25 s and longer) | No effect (5 s set, a 30 s stream runs to the end) |
| `proxy_default_timeout` (top level, seconds) | Not covered here | Limits only the wait for the first response bytes: with 5 s, an upstream that starts at once streams for 40 s; one that waits 8 s gets `504 {"error": "Upstream service reached hard timeout."}` |
| `flush_interval` (milliseconds) | n/a | Not needed: each SSE event reaches the client as the upstream sends it (events 1.5 s apart arrive 1.5 s apart) |

A slow response that is **not** `text/event-stream` and has a `Content-Length` (for example
go-httpbin `/drip`) reaches the client in one piece at the end (4 bytes at 4.1 s). With
`flush_interval: 100` the same bytes arrive as they are sent (0.2, 1.5, 2.8, 4.1 s).

Analytics: with `enable_analytics` and `analytics_config.enable_detailed_recording` both `true`,
SSE events still arrive on time (0.1, 3.1, 6.1 s for events sent 3 s apart).

## What middleware applies

| Check | Behaviour |
| --- | --- |
| Authentication (auth token in header) | Checked on the upgrade request: no key `401 {"error": "Authorization field missing"}`, wrong key `403 {"error": "Access to this API has been disallowed"}`, valid key `101` |
| Authentication on an open connection | Not checked again: after the key is deleted, the open connection keeps echoing frames (11 more over 21 s); a new connection with the same key gets `403` |
| Rate limit | Counts the upgrade request only. Key with rate 2 per 60 s: connection 1 sends 6 frames, all echoed; connection 2 opens; connection 3 gets `429 {"error": "Rate Limit Exceeded"}` |
| Request header transform | Applied to the upgrade request (the upstream sees the added `X-Request-Id` and not the removed `X-Debug`) |
| Query token with `stripAuthorizationData` | Removed before the upstream |

Frames are not inspected or changed. To cut a client off, close its connection at the upstream
or restart the Gateway; deleting the key only stops new connections.

## Test it

```bash
SKILL=<this skill's folder>
python3 "$SKILL/scripts/ws_probe.py" "ws://localhost:8080/chat/room/1?access_token=$KEY" -H "X-Debug: 1" \
  --send headers --send hi --listen 2
# handshake: 101 Switching Protocols
# > headers
# > hi
# < {"path": "/room/1", "headers": {..., "x-request-id": "a61cccdb-...", ...}}
# < hi
python3 "$SKILL/scripts/ws_probe.py" ws://localhost:8080/chat/ --send hi        # no key
# handshake refused: HTTP/1.1 401 Unauthorized
# {"error": "Authorization field missing"}   (shown indented by the Gateway)
python3 "$SKILL/scripts/ws_probe.py" "ws://localhost:8080/chat/?access_token=$KEY" --hold 70 --keepalive 5
# keepalive frames echoed: 13
# still open after 70.0 s
curl -s -N -H "Authorization: $KEY" "http://localhost:8080/realtime/sse?count=3&duration=3s&delay=0"
# event: ping / data: {"id":0,...} ... one event per second
```

`--hold` with `--keepalive` tells you how long the whole chain keeps a connection open. Measure
the upstream alone too: go-httpbin's `/websocket/echo` closes every connection after 10 s
(its own limit, not Tyk's), which is why the lab adds a separate echo server.
