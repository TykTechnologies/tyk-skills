# Editions, Developer Portal, AI Studio boundary and limitations

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`), Dashboard 5.16.0, Pump 1.18.0 and Developer Portal 1.20.0

## Which edition each part needs

"Docs" is what Tyk publishes: the Editions page https://tyk.io/docs/apim and the requirements
table on https://tyk.io/docs/ai-management/mcp-gateway/overview. "Works on" is the Gateway 5.16.0
image (and Dashboard or Portal release) on which the behaviour works. For licence wording, what a
licence covers and what
happens when one lapses, use the `tyk-facts` skill; state no price and no package inclusion from
this page.

| Capability | Docs | Works on |
| --- | --- | --- |
| MCP proxy in front of a remote MCP server (from Gateway 5.13) | Community and Enterprise | Open source image and Enterprise image |
| Create it with the Gateway API `/tyk/mcps` | | Open source image |
| API key and JWT authentication on the proxy | Community and Enterprise (Client Authentication table) | Open source image and Enterprise image |
| OAuth protected-resource metadata and per-tool scope check | Community and Enterprise ("MCP client authorization with OAuth 2.0 and Protected Resource Metadata") | Open source image and Enterprise image |
| Blocking and rate limits per tool in the definition | | Open source image and Enterprise image |
| Allow list per tool in the definition | | Enterprise image |
| Per-consumer tool access and tool rate limits in a policy | | Open source image and Enterprise image |
| REST API as MCP tools (from Gateway 5.15) | Enterprise only | Enterprise image |
| Upstream OAuth 2.0 for MCP servers | Enterprise only | Enterprise image. On the open source image it does not run (below) |
| Token exchange for MCP servers (from Gateway 5.14) | Enterprise only | Enterprise image. On the open source image it does not run (below) |
| `/api/mcps`, the MCP screens, Activity by MCP | Needs Tyk Dashboard (licensed) | Dashboard 5.16.0 |
| MCP products in the Developer Portal (from Portal 1.19.0) | Needs the Developer Portal (licensed) and a Dashboard | Portal 1.20.0 |

An empty Docs cell means the Editions page has no row for that item.

What the open source image does with the two Enterprise settings it accepts:

- `upstream.authentication.oauth`: the definition loads, the caller's `Authorization` header is
  forwarded unchanged, and the Gateway logs
  `Upstream OAuth is supported only in Tyk Enterprise Edition`.
- `tokenExchange`: the definition loads, the caller's token is forwarded unchanged, and the
  Gateway logs
  `OAuth2 token exchange is supported only in Tyk Enterprise Edition; the configured tokenExchange block is being ignored on this build`.

Both fail open: the request still succeeds with the wrong credential going upstream. Check the
Gateway log after enabling either.

Build REST-to-MCP proxies on the Enterprise Edition image (`tykio/tyk-gateway-ee`) with a
licensed Dashboard: that is what the docs require, and every REST-to-MCP behaviour in this skill
applies to that image. A separate MIT-licensed command-line tool,
https://github.com/TykTechnologies/api-to-mcp, turns an OpenAPI file into a standalone MCP
server; it is not this Gateway feature (the `tyk-facts` skill has the detail).

## Publishing an MCP proxy in the Developer Portal

It can be done from Portal 1.19.0 (see
https://tyk.io/docs/developer-support/release-notes/portal). On Portal 1.20.0 with Dashboard
5.16.0:

1. In the Dashboard, create a policy that grants access to the MCP proxy and has only the access
   partition set (`"partitions": {"acl": true, "quota": false, "rate_limit": false, "complexity": false, "per_api": false}`).
2. Synchronise the Portal's Dashboard provider:
   `PUT /portal-api/providers/<id>/synchronize` returns
   `{"message": "Action Synchronize: Executed successfully", "status": "ok"}`.
3. The policy appears as a product. `GET /portal-api/products/<id>` shows, under `APIDetails`,
   `"APIType": "MCP"`, `"MCPServerURL": "<the proxy's listen path>"`, and the product's
   `AuthType` (`authToken`).

It needs a licensed Developer Portal 1.19.0 or later, connected to the Dashboard as a provider,
and a product policy as above. To reach developers the product also needs a plan and a
catalogue, as any Portal product does. Documentation for an MCP product is written in the
product's Markdown editor, and the Portal has a built-in MCP inspector (see
https://tyk.io/docs/portal/api-products, https://tyk.io/docs/portal/mcp-inspector-playground,
https://tyk.io/docs/portal/mcp-oauth-access). Portal theming belongs to the
`tyk-developer-portal-branding` skill.

## Where Tyk AI Studio fits

Tyk Gateway serves and governs MCP traffic: the client calls the proxy's listen path on the
Gateway, and the Gateway applies access rules, limits and quotas. The Tyk Dashboard MCP
integration in AI Studio (Enterprise Edition, from AI Studio 2.2.0) connects to a Tyk Dashboard,
imports its MCP proxies into the AI Portal and, when the connection runs in broker or full trust
mode, issues Tyk keys to its Apps from policies an administrator selects; AI Studio is not on
the request path (see
https://tyk.io/docs/ai-management/ai-studio/tyk-mcp-integration).

AI Studio can also serve an OpenAPI description as MCP tools from its own gateway, in both
editions (see https://tyk.io/docs/ai-management/ai-studio/tools); that is the
`tyk-ai-gateway` skill, as are LLM access and MCP governance inside AI Studio.

## Limitations in Gateway 5.16.0

| Limitation | What you see |
| --- | --- |
| REST-to-MCP: the user's identity cannot reach the REST backend. No caller header, no exchanged token and no JWT claim arrives | `authentication.md`, last table |
| REST-to-MCP: the source REST API's client authentication does not cover calls through the MCP proxy, so the proxy needs its own | Calls through the proxy succeed without the source API's credential (`authentication.md`) |
| REST-to-MCP: session-style clients are tied to the Gateway replica that created the session | `404 session not found` on the second replica |
| REST-to-MCP: a backend response over 1 MiB is cut | A 2 MB response comes back with `_meta.truncated: true` and text ending `truncated after 1048576 bytes. The content above is incomplete.` |
| REST-to-MCP: a multipart request body is not usable | The tool for a `multipart/form-data` operation is listed with an empty input schema |
| REST-to-MCP: tools only, JSON answers only, one source API per proxy | `GET` without a session returns 405; the last point is documented at https://tyk.io/docs/ai-management/mcp-gateway/api-to-mcp-definitions |
| JWT on an MCP proxy cannot be stripped or removed before the upstream | `stripAuthorizationData: true` and a header-removal transform both give `401 Authorization field missing` |
| A client cannot answer a server's request (sampling, elicitation, roots) | A body with no `method` returns `400 -32600` |
| JSON-RPC batches are refused | `400 -32700 parse error` |
| Request bodies over 1 MiB (1,048,576 bytes) are refused | `400 -32600 invalid request` |
| The root-level metadata address does not use the metadata set on the proxy's `oauth2` scheme: it relays the upstream server's document, or answers `502 upstream PRM unavailable` when the upstream has none | `502` against an upstream with no metadata (`authentication.md`) |
| The scope check does not hide tools from `tools/list` | A caller without the scope still sees the tool |
| Only Streamable HTTP is supported; stdio and the older HTTP+SSE transport are not | https://tyk.io/docs/ai-management/mcp-gateway/core-concepts |
| Default metrics carry no MCP label, and MCP records skip the ordinary pumps | `observability.md` |
| Trusted browser origins and the OAuth broker are set in the definition only, not in the Dashboard screens | https://tyk.io/docs/ai-management/mcp-gateway/protocol-versions and https://tyk.io/docs/api-management/authentication/oauth2-authentication |
