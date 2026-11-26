# Transports, protocol versions and sessions

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0`, two replicas on one Redis and one Dashboard)

See https://tyk.io/docs/ai-management/mcp-gateway/protocol-versions,
https://tyk.io/docs/ai-management/mcp-gateway/core-concepts,
https://tyk.io/docs/ai-management/mcp-gateway/error-codes.

## Transport

The documented and supported transport is Streamable HTTP on one path, `<listen path>mcp`. The
docs state that the older HTTP+SSE transport (separate `/sse` and message endpoints) and stdio
are not supported; put a stdio-to-HTTP bridge in front of a stdio server. See
https://tyk.io/docs/ai-management/mcp-gateway/core-concepts. Design on Streamable HTTP only.

What a remote MCP proxy does with a server that streams:

| Request | Result through the Gateway |
| --- | --- |
| `POST /mcp`, server answers `application/json` | Passed through unchanged |
| `POST /mcp tools/call`, server answers `text/event-stream` with three progress events one second apart, then the result | Events reach the client at 0.04 s, 1.04 s, 2.04 s and 3.04 s: streamed, not buffered |
| `GET /mcp` with `Mcp-Session-Id` (server-to-client stream) | Events arrive one second apart; the stream closes when the server closes it |
| `DELETE /mcp` with `Mcp-Session-Id` | Forwarded to the server; the server's 200 reaches the client |
| `notifications/initialized` | The server's 202 reaches the client |

A REST-to-MCP proxy answers `POST` with plain JSON only. `GET` with no session returns
`405 Method Not Allowed` with `Allow: POST`.

## Protocol versions

The Gateway reads the protocol version from the `MCP-Protocol-Version` header, from
`params._meta`, or from the body of `initialize`. A request that declares none is treated as a
session-style request. See https://tyk.io/docs/ai-management/mcp-gateway/protocol-versions.

- A request for version `2024-11-05` is refused with HTTP 400:
  `{"code":-32022,"message":"unsupported protocol version","data":{"requested":"2024-11-05","supported":["2026-07-28","2025-11-25","2025-06-18","2025-03-26"]}}`.
  The documentation page lists `2026-07-28` and `2025-11-25` as the supported versions; Gateway
  5.16.0 reports the four above.
- Gateway 5.13 to 5.15 handle `2025-11-25` only; `2026-07-28` needs 5.16. See
  https://tyk.io/docs/ai-management/mcp-gateway/overview.

| | Session style (`2025-11-25`) | Stateless style (`2026-07-28`) |
| --- | --- | --- |
| First call | `initialize`, then `notifications/initialized` | none; `server/discover` to read capabilities |
| Headers on each call | `Mcp-Session-Id`, `MCP-Protocol-Version` | `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` on `tools/call`, `prompts/get`, `resources/read` |
| Body | plain JSON-RPC | `params._meta` must carry `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` |
| HTTP methods | `POST`, `GET`, `DELETE` | `POST` |

Both styles work against the same proxy at the same time, for a remote server and for a
REST-to-MCP proxy. `initialize` sent in the stateless style returns
`404 {"code":-32601,"message":"method removed in the modern protocol"}`.

A stateless `tools/call`, as sent by `scripts/mcp_probe.py --protocol 2026-07-28`:

```text
POST /orders-mcp/mcp
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: generate_uuid

{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"generate_uuid","arguments":{},
 "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}
```

## Sessions and more than one Gateway replica

| Proxy kind | Who holds the session | Session created on replica 1, used on replica 2 |
| --- | --- | --- |
| Remote MCP server | The upstream server; the Gateway passes `Mcp-Session-Id` through | `tools/call` returns 200 |
| REST API as MCP tools, session style | The Gateway process that answered `initialize` | `404 session not found` |
| REST API as MCP tools, stateless style | Nobody | `tools/list` returns 200 on either replica |

So with several replicas and a REST-to-MCP proxy, a session-style client must keep reaching the
replica that created its session, and a stateless client needs nothing. A session on a
REST-to-MCP proxy keeps working after a hot reload of the Gateway's API definitions.

## Request checks the Gateway makes

| Request | Response |
| --- | --- |
| `POST` whose `Content-Type` is not `application/json` | `400 {"code":-32600,"message":"MCP POST requires application/json"}` |
| Body larger than 1 MiB (1,048,576 bytes) | `400 {"code":-32600,"message":"invalid request"}` |
| A JSON array (batch) | `400 {"code":-32700,"message":"parse error"}` |
| A body with no `method`, such as a client's answer to a server request | `400 {"code":-32600,"message":"invalid request"}` |
| `Origin` header from a site that is not trusted | `403 Forbidden` (plain text) |
| `Origin` listed in `x-tyk-api-gateway.server.mcp.trustedOrigins` | passes (200) |
| No `Origin` header (most non-browser clients) | passes |

`trustedOrigins` entries are scheme, host and optional port, with no path or wildcard; the field
is set in the definition only, not in the Dashboard screens. See
https://tyk.io/docs/ai-management/mcp-gateway/protocol-versions.

Because a body without `method` is refused, a client cannot answer a server's
`sampling/createMessage`, `elicitation/create` or `roots/list` request through the Gateway.
The pages listed at the top describe these features.

## Error codes

Gateway errors come back as JSON-RPC errors with `data.http_code` equal to the HTTP status.
Errors produced by the upstream MCP server pass through unchanged.

| Situation | HTTP | Session style | Stateless style | Message |
| --- | --- | --- | --- | --- |
| No credential | 401 | `-32001` | | `Authorization field missing` |
| Bad JWT | 403 | `-32002` | | `Key not authorized` |
| Tool blocked in the definition | 403 | `-32002` | | `Requested endpoint is forbidden` |
| Tool outside the definition's allow list | 403 | `-32002` | | `access to this resource has been disallowed` |
| Tool not allowed by the caller's policy | 403 | `-32002` | | `tool 'create_post' is not available` |
| Method blocked by the caller's policy | 403 | `-32002` | | `method 'prompts/list' is not available` |
| Missing OAuth scope | 403 | `-32002` | | `insufficient_scope` |
| Tool rate limit in the definition | 429 | `-32003` | `-33004` | `API Rate Limit Exceeded` |
| Tool rate limit in the caller's policy | 429 | `-32003` | | `Rate Limit Exceeded` |
| Method rate limit in the caller's policy | 429 | | `-33004` | `Rate Limit Exceeded` |
| Quota used up | 403 | `-32002` | `-33003` | `Quota exceeded` |

An empty cell means that combination is not covered here. The error-codes page lists `-32005`
for a used-up quota in the session style; Gateway 5.16.0 returns `-32002` with the message
`Quota exceeded`. Match on the message, or on both codes.
