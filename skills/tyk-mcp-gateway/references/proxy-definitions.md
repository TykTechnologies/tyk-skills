# MCP proxy definitions

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0` and `tykio/tyk-gateway:v5.16.0`) and Tyk Dashboard 5.16.0

See https://tyk.io/docs/ai-management/mcp-gateway/mcp-proxy-definitions,
https://tyk.io/docs/ai-management/mcp-gateway/api-to-mcp-definitions,
https://tyk.io/docs/ai-management/mcp-gateway/mcp-api-extensions.

## The two kinds of proxy

| | Remote MCP server | REST API as MCP tools |
| --- | --- | --- |
| `upstream.url` | The MCP server's base URL, for example `http://mcp-server.example.com:7878` | `tyk://<source API id>/mcp` |
| Tools come from | The upstream server, names unchanged | The OpenAPI operations of a Tyk OAS REST API loaded on the same Gateway |
| Extra block | none | optional `x-tyk-mcp-server` beside `x-tyk-api-gateway` |
| Resources and prompts | Passed through | None: tools only |
| Edition in the docs | Community and Enterprise | Enterprise only (see `editions-and-limits.md`) |

Both are Tyk OAS documents with the `x-tyk-api-gateway` extension. The classic API definition
format has no MCP support (https://tyk.io/docs/ai-management/mcp-gateway/overview). A client
connects to
`<gateway><listen path>mcp`; with `listenPath.strip: true` the Gateway forwards to
`<upstream.url>/mcp`.

## Where to create them

| | Dashboard API (licensed) | Gateway API (file mode, also the open source image) |
| --- | --- | --- |
| Create | `POST /api/mcps` | `POST /tyk/mcps`, then `GET /tyk/reload` |
| Read | `GET /api/mcps`, `GET /api/mcps/{id}` | `GET /tyk/mcps`, `GET /tyk/mcps/{id}` |
| Update | `PUT /api/mcps/{id}` | `PUT /tyk/mcps/{id}`, then reload |
| Delete | `DELETE /api/mcps/{id}` | `DELETE /tyk/mcps/{id}`, then reload |
| Auth header | `authorization: <Dashboard API key>` | `x-tyk-authorization: <gateway secret>` |

Responses:

```text
POST /api/mcps            200 {"Status":"OK","Message":"API created","Meta":"<db id>","ID":"orders-mcp"}
GET  /api/mcps            200 {"mcps":[ ...definitions... ],"pages":<n>}
PUT  /api/mcps/orders-mcp 200 {"Status":"OK","Message":"Api updated","Meta":null}
PUT  /tyk/mcps/orders-mcp 200 {"key":"orders-mcp","status":"ok","action":"modified"}
DELETE /api/mcps/<id>     200 {"Status":"OK","Message":"API deleted","Meta":null}
POST /tyk/mcps            200 {"key":"orders-mcp","status":"ok","action":"added"}
DELETE /tyk/mcps/<id>     200 {"key":"keyed-mcp","status":"ok","action":"deleted"}
```

`POST /tyk/mcps` writes two files into the Gateway's app folder, `<id>.json` and `<id>-mcp.json`.
`GET /tyk/mcps` returns a JSON array of definitions.

Two query parameters work on create, on the Dashboard API and on the Gateway API:

- `dryRun=true` validates and returns the definition without storing it. A following
  `GET /api/mcps/<id>` returns 404 `Could not retrieve MCP Proxy detail`.
- `expand=true` (with `dryRun=true`) returns the derived tool catalogue of a REST-to-MCP proxy
  under `x-tyk-mcp-server.primitives`, with read-only fields such as `inputSchema` and
  `parameterLocations`. Every operation of the source API is listed, each with `allow` set to
  `true` or `false`, so it shows which tools the proxy will expose before you save it.

Use `/api/mcps`, not `/api/apis/oas`. The same document posted to `/api/apis/oas` is accepted
with HTTP 200 as an ordinary API. It is then missing from `GET /api/mcps`, and
`GET /api/mcps/<id>` answers 400 `API with id <id> is not an MCP Proxy`.

## Minimal remote proxy

```json
{
  "openapi": "3.0.3",
  "info": {"title": "Orders MCP", "version": "1.0.0"},
  "paths": {},
  "x-tyk-api-gateway": {
    "info": {"id": "orders-mcp", "name": "Orders MCP", "state": {"active": true}},
    "upstream": {"url": "http://mcp-server.example.com:7878"},
    "server": {"listenPath": {"value": "/orders-mcp/", "strip": true}}
  }
}
```

An empty `paths` object is enough. This definition has no authentication: add it before any use
(`authentication.md`). `assets/remote-mcp-proxy.oas.json` is the same proxy with an API key, a
blocked tool and a per-tool rate limit.

## REST API as MCP tools

The source must be a Tyk OAS REST API already loaded on the Gateway. Creating the proxy first
fails:

```text
POST /api/mcps  (upstream.url tyk://no-such-api/mcp)
400 {"Status":"Error","Message":"Paired REST API no-such-api is not loaded; create it first","Meta":null}
```

The source API needs no `/mcp` path and no change. `<source API id>` is `x-tyk-api-gateway.info.id`
of the REST API. The docs add that GraphQL and classic APIs cannot be a source, and that each
version of a versioned API has its own id. See
https://tyk.io/docs/ai-management/mcp-gateway/api-to-mcp-definitions.

### Which operations become tools, and their names

With no `x-tyk-mcp-server` block, every operation becomes a tool. For this source API:

| Operation | `operationId` | Tool name | Description shown to the agent |
| --- | --- | --- | --- |
| `GET /orders` | `listOrders` | `listOrders` | `List orders` (the `summary`) |
| `POST /orders` | `createOrder` | `createOrder` | `Create an order` |
| `GET /orders/{id}` | `getOrder` | `getOrder` | `Get one order` |
| `DELETE /orders/{id}` | none | `delete_orders_id` | `Cancel an order` |

- The name is the `operationId`. Without one it is built from the method and path.
- The description is the operation's `summary`; `description` is used only when there is no
  summary.
- Path, query and header parameters, and the fields of a JSON request body, become tool
  arguments. `createOrder` gets `sku` and `quantity` from its body schema.
- Each tool gets annotations from the HTTP method: `GET` gives `readOnlyHint: true`; `POST` gives
  `destructiveHint: true, idempotentHint: false`; `DELETE` gives `destructiveHint: true,
  idempotentHint: true`. The `title` is the summary.
- Operations the source API marks internal or blocks, or leaves out of its own allow list, are
  not derived.
- A change to the source API's operations shows up in the tool list after the next reload:
  operations added to the source appear as tools with no change to the proxy.

### Choosing and renaming tools

```json
"x-tyk-mcp-server": {
  "primitives": [
    {
      "source": {"operationId": "getOrder"},
      "name": "get_order",
      "description": "Look up one order by its ID.",
      "parameters": [{"param": "id", "name": "order_id", "description": "The order to look up."}],
      "allow": true
    },
    {"source": {"operationId": "listOrders"}, "allow": true},
    {"source": {"method": "DELETE", "path": "/orders/{id}"}, "name": "cancel_order"}
  ]
}
```

Result: `tools/list` returns `get_order` and `listOrders` only.

- As soon as one entry has `allow: true`, only entries with `allow: true` are exposed. The third
  entry above has overrides but no `allow`, so it stays hidden.
- With no `allow: true` anywhere, entries only rename or describe and every operation stays a
  tool; `source` is either `operationId` or `method` plus `path`, never both. See
  https://tyk.io/docs/ai-management/mcp-gateway/api-to-mcp-definitions.
- `name`, `description` and `parameters[]` (`param` is the derived argument name) replace what
  the agent sees. The renamed argument `order_id` still fills the `{id}` path segment.
- `annotations` overrides the hints. The Dashboard wizard has no control for them.
- Tool names must match `^[A-Za-z0-9_.-]+$` and be at most 128 characters.

The last two points are documented at https://tyk.io/docs/ai-management/mcp-gateway/api-to-mcp-definitions.
- Calling a tool that is not exposed returns HTTP 200 with a JSON-RPC error:
  `{"code": -32602, "message": "unknown tool \"createOrder\""}`.

`assets/orders-rest-api.oas.json` and `assets/rest-to-mcp-proxy.oas.json` are this pair.

## Per-tool settings in the definition

Per-tool settings sit in `x-tyk-api-gateway.middleware.mcpTools.<tool name>`. A remote server's
resources and prompts have the sibling maps `mcpResources` and `mcpPrompts` (see
https://tyk.io/docs/ai-management/mcp-gateway/mcp-api-extensions). On tools, `block`, `allow`,
`rateLimit` and `security` with `scopeCheck` take effect. See `access-control-and-limits.md`.

- `rateLimit.per` is a duration string such as `"5s"` or `"1m"`. A number is refused:
  `400 MCP validation: x-tyk-api-gateway.middleware.mcpTools.generate_uuid.rateLimit.per: Invalid type. Expected: string, given: integer`.
- Settings that cannot apply to a JSON-RPC call: the Dashboard API refuses them, the Gateway API
  accepts them and the Gateway ignores them. On `/api/mcps`, `cache`, `validateRequest` and
  `urlRewrite` each return
  `400 invalid middleware configured for the MCP proxy (tools.<tool>.<setting>)`. On
  `/tyk/mcps` the same three are stored (`200 ... "action":"added"`), and calls to the tool
  return 200 unchanged, a `urlRewrite` to another path included. The Gateway drops them when it
  loads the proxy, as it does `transformRequestMethod`, `transformResponseBody`, `internal` and
  `mockResponse`.
- `transformRequestHeaders`, `requestSizeLimit` and `enforceTimeout` pass validation.
