# MCP Module

Last verified: 2026-11-19 against Tyk Gateway and Dashboard 5.16.0 and Developer Portal 1.20.1.

This page covers what the `mcp` module generates and proves. For how Tyk MCP proxies work (proxy
definitions, tool selection, authentication, token exchange, per-tool access and limits,
observability), use the `tyk-mcp-gateway` skill. Public overview:
https://tyk.io/docs/ai-management/mcp-gateway/overview

## What The Module Generates

- `mcp/mcp-broker.mcp.oas.json`: an MCP proxy in front of the bundled mock MCP server (built
  from source at a pinned commit, Compose profile `mcp`), keyed, with one tool blocked and one
  tool rate limited.
- `mcp/rest-as-mcp.mcp.oas.json`: an MCP proxy whose upstream is the bundle's REST API on the
  same gateway, so that API's operations become MCP tools.
- `scripts/import-mcps.sh`: a separate import pass for the proxies. MCP proxies are a Dashboard
  resource of their own (`/api/mcps`), not an ordinary OAS API, so `import-apis.sh` does not
  import them. On 5.16.0 the OAS API endpoint also accepts an MCP definition, but stores it as an
  ordinary API: `GET /api/mcps/<id>` then answers 400 "is not an MCP Proxy".
- `scripts/mcp-check.py`: the JSON-RPC client the proofs use.
- The MCP Inspector on `127.0.0.1` (ports `MCP_INSPECTOR_PORT` and `MCP_INSPECTOR_PROXY_PORT`)
  for showing the proxies in a browser.

The generator refuses `mcp` with a Gateway or Dashboard below 5.15.0, and `mcp,portal` with a
Portal below 1.19.0. Those are the builder's floors: the Portal release notes record that MCP
proxies sync to the Portal and can be published as products from 1.19.0
(https://tyk.io/docs/developer-support/release-notes/portal).

## Proofs

| Proof | What it asserts |
| --- | --- |
| `MCP-AUTH-001` | an unauthenticated `initialize` on the broker proxy returns 401 |
| `MCP-DISCOVERY-001` | a keyed `tools/list` is non-empty and does not list the blocked tool |
| `MCP-BLOCK-001` | calling the blocked tool directly returns 403 |
| `MCP-LIMIT-001` | the rate-limited tool returns 200 then 429, while a tool without a limit still returns 200 |
| `MCP-REST-001` | the REST-as-MCP proxy lists at least one tool derived from the REST API's OAS |

With the bundled mock MCP server, `MCP-DISCOVERY-001` lists 14 tools with the blocked tool
absent, and `MCP-REST-001` derives 1 tool.

## Module Traps

- `MCP-LIMIT-001` waits out the tool's rate-limit window before it measures. Without the wait the
  first call inherits the budget an earlier run spent, and a correct limit reads as a failure.
- `import-mcps.sh` writes through the Dashboard, so an edited proxy must be one the Dashboard
  accepts; run `configure-demo.sh` after any edit and read the import result.
- With `ai-studio` also selected, the AI Studio companion imports these proxies read-only
  (`AISTUDIO-TYKMCP-001`, see `references/ai-studio.md`).
