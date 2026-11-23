# MCP proxies as Portal products

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.0 with Tyk Dashboard and Gateway 5.16.0

Public pages: https://tyk.io/docs/portal/mcp-documentation and
https://tyk.io/docs/portal/mcp-oauth-access (MCP products need Portal 1.19.0 or later, per both).
Building the MCP proxy itself is the `tyk-mcp-gateway` skill.

## Recipe

An MCP proxy defined in the Dashboard (auth token) is offered like an API, by its ID:

```bash
curl -s -X POST "$PORTAL_URL/portal-api/products" -H "Authorization: $PORTAL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"Name":"MCP tools","DisplayName":"MCP tools","ProviderID":1,"APIDetails":[{"APIID":"<MCP proxy id>"}],"Catalogues":[1]}'
# 201 {"APIDetails":[{"APIType":"MCP","ListenPath":"/<listen path>/",...}],"AuthType":"authToken","ID":8,...}
```

- It survives a provider synchronisation.
- The provider's Dashboard user needs no `mcp` permission for this.
- Once a developer's request with a plan is approved, the key from the app page opens an MCP
  session through the Gateway:

  ```bash
  curl -s -X POST "$GW/<listen path>/mcp" -H "Authorization: $KEY" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
  # 200, event: message, data: {"jsonrpc":"2.0","id":1,"result":{...,"serverInfo":{"name":"tyk-mock-mcp-server",...}}}
  ```

## Documentation

MCP proxies have no OpenAPI page. The docs page `/portal/catalogue-products/<Path>/<proxy id>/docs`
shows operator-written markdown and the MCP server URL. On 1.20.0,
`PUT /products/{id}/api-details/{proxy id}` with `MCPContent` and `MCPServerURL` answers 200
and echoes the URL, but the readback keeps `MCPContent: ""` and `MCPServerURL` equal to the
listen path. Write the markdown in the admin UI (product, API reference documentation, MCP) and
check the docs page afterwards.
