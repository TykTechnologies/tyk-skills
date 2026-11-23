# Tools, MCP and plugins in AI Studio

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`), with Tyk Dashboard and Gateway 5.16.0

The tool, REST and MCP calls below work on the Community Edition images. The Tyk Dashboard MCP
integration is an Enterprise Edition feature and needs the Enterprise images with a licence; the
example below connects to a Tyk Dashboard 5.16.0 that manages two MCP proxies.

See [Tools](https://tyk.io/docs/ai-management/ai-studio/tools),
[Tyk Dashboard MCP integration](https://tyk.io/docs/ai-management/ai-studio/tyk-mcp-integration),
[Plugins](https://tyk.io/docs/ai-management/ai-studio/plugins/overview),
[Tyk MCP Gateway](https://tyk.io/docs/ai-management/mcp-gateway/overview).

## Which MCP is which

| | AI Studio tool | Tyk Gateway MCP proxy |
| --- | --- | --- |
| What it is | A REST API described by an OpenAPI document, which AI Studio can also serve over MCP | An MCP proxy in front of a remote MCP server, or a REST API exposed as MCP, on Tyk Gateway |
| Served by | The AI Studio edge (and the embedded gateway) at `/tools/{slug}` and `/tools/{slug}/mcp` | Tyk Gateway, on the proxy's listen path |
| Caller credential | The AI Studio App secret | A Tyk access key, JWT or OAuth token, under Tyk policies |
| Edition | Both | See the `tyk-mcp-gateway` skill |
| Skill | this one | the `tyk-mcp-gateway` skill |

Use the `tyk-mcp-gateway` skill for anything about MCP proxies on Tyk Gateway: proxying a remote
MCP server, REST to MCP, tool allow lists, per-tool rate limits, OAuth for MCP clients.

## A tool, called over REST and over MCP (both editions)

Create a tool from an OpenAPI 3 document. `oas_spec` must be the base64 encoding of the
document. A new tool is usable in chat only; `rest_access_enabled` and `mcp_access_enabled`
open it on the gateway.

```bash
SPEC='{"openapi":"3.0.3","info":{"title":"Model catalogue","version":"1.0.0"},"servers":[{"url":"http://llm-beta:8080"}],"paths":{"/v1/models":{"get":{"operationId":"listModels","summary":"List the models this backend offers","responses":{"200":{"description":"A list of models","content":{"application/json":{"schema":{"type":"object"}}}}}}}}}'
B64=$(printf '%s' "$SPEC" | base64 | tr -d '\n')
python3 <skill-dir>/scripts/aistudio_admin.py request POST /api/v1/tools --data "{\"data\":{\"type\":\"tools\",\"attributes\":{\"name\":\"Model catalogue\",\"description\":\"Lists models\",\"tool_type\":\"REST\",\"oas_spec\":\"$B64\",\"privacy_score\":0,\"operations\":[\"listModels\"],\"rest_access_enabled\":true,\"mcp_access_enabled\":true}}}"
```

```json
201 {"data":{"type":"tools","id":"2","attributes":{"name":"Model catalogue","description":"Lists models","tool_type":"REST","oas_spec":"<base64>","privacy_score":0,"operations":["listModels"],"auth_key":"[redacted]","has_auth_key":false,"auth_schema_name":"","active":true,"namespace":"","file_stores":[],"filters":[],"dependencies":[],"slug":"model-catalogue","rest_access_enabled":true,"mcp_access_enabled":true,"app_grantable":true,"rest_endpoint_url":"http://localhost:9091/tools/model-catalogue","mcp_endpoint_url":"http://localhost:9091/tools/model-catalogue/mcp"}}}
```

`operations` lists the `operationId` values callers may use. The endpoint URLs in the response
come from the control plane's `PROXY_URL`. Tool id 1 already exists on a new installation (a
built-in chat tool named `Generative UI`), so the first tool you create is id 2. The tool's
server host must be reachable under the internal-address rules (`architecture-and-setup.md`).

Two refusals at creation:

- A name that differs from an existing tool's only by case, spaces or punctuation:
  `409 ... "tool name \"model catalogue\" is too close to tool 2 (\"Model catalogue\"): both have the slug \"model-catalogue\", ..."`.
- A document that is not OpenAPI 3: `400 ... "invalid OpenAPI specification: oas_spec does not decode to a usable OpenAPI 3 document: ..."`.

Grant it to an App with `tool_ids` (the `PATCH` keeps the App's other fields), then push:

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request PATCH /api/v1/apps/1 --data \
  '{"data":{"type":"apps","attributes":{"tool_ids":[2]}}}'
python3 <skill-dir>/scripts/aistudio_admin.py push
```

REST call (the body names the operation; a request without a body gets
`400 {"status":400,"message":"invalid request body","error":"EOF"}`):

```bash
curl -s -X POST http://localhost:9091/tools/model-catalogue \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"operation_id":"listModels","params":{},"payload":{},"headers":{}}'
```

```json
{"data":[{"id":"beta-fast","object":"model","owned_by":"beta"},{"id":"beta-deep","object":"model","owned_by":"beta"}],"object":"list"}
```

MCP call (streamable HTTP, JSON-RPC). `initialize` returns a session id header to send on the
following requests:

```bash
curl -s -i -X POST http://localhost:9091/tools/model-catalogue/mcp \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'
```

```text
HTTP/1.1 200 OK
Content-Type: application/json
Mcp-Session-Id: mcp-session-aa23051a-8913-4580-bbcd-3146b7d37f77

{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26","capabilities":{"tools":{"listChanged":true}},"serverInfo":{"name":"Model catalogue","version":"1.0.0"}}}
```

With `-H "Mcp-Session-Id: <id>"` added:

```text
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
-> {"jsonrpc":"2.0","id":2,"result":{"tools":[{"annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true},"description":"List the models this backend offers","inputSchema":{"properties":{},"type":"object"},"name":"listModels"}]}}

{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"listModels","arguments":{}}}
-> {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"data\":[{\"id\":\"beta-fast\",\"object\":\"model\",\"owned_by\":\"beta\"},{\"id\":\"beta-deep\",\"object\":\"model\",\"owned_by\":\"beta\"}],\"object\":\"list\"}"}]}}
```

Each selected operation becomes one MCP tool named after its `operationId`.

| Case | Answer |
| --- | --- |
| No credentials on `/tools/{slug}/mcp` | `401 {"status":401,"message":"missing Authorization header or apiKey query parameter for tool request"}` with a `WWW-Authenticate: Bearer realm="MCPResources", resource_metadata_uri=...` header |
| An App that does not hold the tool | `401 {"status":401,"message":"invalid credential"}` |

From the [Tools](https://tyk.io/docs/ai-management/ai-studio/tools) page:
a call over an access method that is switched off is refused with `403`; a tool can be used with
an LLM only when the tool's privacy level is not higher than the LLM's; MCP clients can also
sign in with OAuth instead of sending the App secret.

## Tyk Dashboard MCP integration (Enterprise Edition)

AI Studio connects to a Tyk Dashboard and imports the MCP proxies that Dashboard manages, so
they can be published in the AI Portal beside LLMs and tools
([Tyk Dashboard MCP integration](https://tyk.io/docs/ai-management/ai-studio/tyk-mcp-integration)).
Tyk Gateway serves the MCP traffic; AI Studio is not on that path. On the Enterprise images,
against Tyk Dashboard and Gateway 5.16.0 (the AI Studio container on the Dashboard's Docker
network):

```text
GET  /api/v1/tyk-mcp/status    -> 200 {"available":true,"enabled":true,...,"connections":0,...}
POST /api/v1/tyk-connections   {"name":"Example Dashboard","kind":"dashboard","dashboard_url":"http://tyk-dashboard:3000",
                                "gateway_base_url":"http://localhost:<gateway port>","dashboard_access_token":"<Dashboard user API key>",
                                "org_id":"<organisation id>","allow_internal_host":true}
                               -> 201, "status":"pending","declared_mode":"catalogue","has_token":true (the key is never returned)
POST /api/v1/tyk-connections/1/probe    -> 200, "reachable":true, capabilities apis_read, mcp_read, mcp_supported,
                                           policies_read, rest_to_mcp_supported all "ok"
POST /api/v1/tyk-connections/1/activate -> 200
POST /api/v1/tyk-connections/1/sync     -> 202
GET  /api/v1/mcp-servers                -> both MCP proxies of the Dashboard, each with "origin":"dashboard",
                                           "tyk_api_id", "listen_path", "endpoint_url" on the Gateway, its tools
                                           under "primitives", and "is_active":false
```

- `allow_internal_host` is needed when the Dashboard is on a private address, as in a Compose
  network.
- Publishing an imported server needs a privacy score first:
  `POST /api/v1/mcp-servers/{id}/activate` answers
  `409 ... "an administrator must set a privacy score before publishing"`; after
  `PATCH /api/v1/mcp-servers/{id}` with `{"privacy_score":0,"lock_version":0}` it answers
  `"is_active":true`.
- A new connection is in `catalogue` mode: it lists and imports. Asking it for a key for an App
  answers `422 ... "the connection runs in catalogue mode; keys are minted by the platform team"`.
  Per the documentation page, `broker` mode also issues, changes and revokes
  keys from the selected policies, and `full` mode also creates and edits MCP proxies and
  policies on the Dashboard.
- An imported MCP server is not the same thing as an AI Studio tool served over MCP.

Community Edition:

```text
GET /api/v1/tyk-connections  -> 403 {"errors":[{"title":"Enterprise Feature","detail":"the Tyk Dashboard MCP integration is an Enterprise Edition feature - visit https://tyk.io/ai-studio/pricing for more information"}]}
GET /api/v1/mcp-servers      -> 403 (same body)
```

Building the MCP proxies themselves is the `tyk-mcp-gateway` skill's subject.

## Plugins

AI Studio and the edge can load plugins (see
[Plugins](https://tyk.io/docs/ai-management/ai-studio/plugins/overview); the feature table on
the overview page lists the plugin system in both editions). `GET /api/v1/plugins` on a fresh
installation returns an empty list in both editions:
`{"data":[],"meta":{"total_count":0,"total_pages":0,"page_size":20,"page_number":1},"updates_available":0}`.

The plugin marketplace reads a public index. As of 2026-11-19 it lists these plugins, all marked
`maturity: beta`
(https://raw.githubusercontent.com/TykTechnologies/tyk-ai-studio-plugins-ce/main/index.yaml):

| Plugin | Licence in the index |
| --- | --- |
| Rate Limiter | AGPL v3 |
| LLM Response Cache | AGPL v3 |
| LLM Firewall | AGPL v3 |
| LLM Price Sync | AGPL v3 |
| Git RAG Ingestion | AGPL v3 |
| Advanced LLM Cache | Commercial; semantic caching is Enterprise Edition only (`editions.md`) |
| LLM Load Balancer | Commercial |
| OAuth2 Client Credentials Auth | Commercial |
| Asset Catalog (Enterprise) | Commercial; Enterprise Edition only (`editions.md`) |

The control plane log states the precondition: `AI_STUDIO_OCI_CACHE_DIR` "unset disables OCI
plugins and the plugin marketplace". With
`MARKETPLACE_ENABLED=false`, as in the evaluation stack, `GET /api/v1/marketplace/plugins` is
not registered and returns the admin UI page. Re-read the index before naming a plugin to a
user; it changes independently of AI Studio releases.
