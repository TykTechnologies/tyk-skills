# Per-tool access, rate limits and quotas

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0` and `tykio/tyk-gateway:v5.16.0`) and Tyk Dashboard 5.16.0

See https://tyk.io/docs/ai-management/mcp-gateway/policies,
https://tyk.io/docs/ai-management/mcp-gateway/mcp-middleware.

There are two layers. Settings in the proxy definition apply to every caller. Settings in a
policy apply to each consumer (key or token) separately. A policy cannot open what the definition
blocks. The policy and key objects themselves belong to the `tyk-rate-limits-keys-policies`
skill; this page covers only the MCP fields.

## In the proxy definition (all callers)

```json
"middleware": {"mcpTools": {
  "delete_user": {"block": {"enabled": true}},
  "create_user": {"rateLimit": {"enabled": true, "rate": 1, "per": "5s"}}
}}
```

| Setting | `tools/list` | `tools/call` |
| --- | --- | --- |
| `block` on `delete_user` | Tool absent (14 of the server's 15 tools listed) | `403 {"code":-32002,"message":"Requested endpoint is forbidden"}` |
| `rateLimit` 1 per `5s` on `create_user` | Listed | First call 200, next two `429 {"code":-32003,"message":"API Rate Limit Exceeded"}` |
| `allow` on two tools | Only those two listed | Any other tool `403 {"code":-32002,"message":"access to this resource has been disallowed"}` |

One `allow: {"enabled": true}` on any tool turns the proxy into an allow list. `per` is a
duration string (`proxy-definitions.md`). The counter of a limit in the definition is shared by
all callers. See https://tyk.io/docs/ai-management/mcp-gateway/core-concepts.

### One limit for every call of a method

`middleware.operations` holds settings for a JSON-RPC method as a whole, keyed
`<method>POST` (`tools/callPOST`). The key only takes effect when `paths` has a matching
operation at `/<method>`:

```json
"paths": {"/tools/call": {"post": {"operationId": "tools/callPOST", "responses": {"200": {"description": "ok"}}}}},
"x-tyk-api-gateway": {"middleware": {"operations": {
  "tools/callPOST": {"rateLimit": {"enabled": true, "rate": 1, "per": "1m"}}
}}}
```

On Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0`, definition loaded through the Gateway API)
the first `tools/call` returns 200 and the next calls, to any tool,
`429 {"code":-32003,"message":"API Rate Limit Exceeded"}`; `tools/list` keeps working. The
Gateway maps the method to the operation at `/tools/call`. With the path written
`/mcp/tools/call`, as in the documentation example on
https://tyk.io/docs/ai-management/mcp-gateway/mcp-proxy-definitions, or with no `paths` entry,
the same `operations` block loads and limits nothing: three calls in a row return 200. For a
per-consumer method limit, use `json_rpc_methods` in a policy (below).

## In a policy (per consumer)

The MCP fields sit inside the `access_rights` entry for the proxy. This is
`assets/mcp-consumer-policy.json` with the `tools/list` limit lowered from 30 to 2 per 60 s, so
the third call shows the limit:

```json
"access_rights": {"orders-mcp": {
  "api_id": "orders-mcp", "api_name": "Orders MCP", "versions": ["Default"],
  "mcp_access_rights": {"tools": {"allowed": ["get_.*", "generate_uuid"], "blocked": ["get_analytics"]}, "resources": {}, "prompts": {}},
  "mcp_primitives": [{"type": "tool", "name": "generate_uuid", "limit": {"rate": 2, "per": 60}}],
  "json_rpc_methods": [{"name": "tools/list", "limit": {"rate": 2, "per": 60}}],
  "json_rpc_methods_access_rights": {"blocked": ["prompts/list", "prompts/get"]}
}}
```

With a key on that policy:

| Field | Result |
| --- | --- |
| `mcp_access_rights.tools.allowed` | `tools/list` returns `generate_uuid, get_anything, get_posts, get_products, get_users`. `create_post`: `403 {"code":-32002,"message":"tool 'create_post' is not available"}` |
| `mcp_access_rights.tools.blocked` | `get_analytics` matches `get_.*` and is blocked: absent from the list, call refused with the same message. Blocked wins |
| `mcp_primitives` (tool `generate_uuid`, 2 per 60 s) | Two calls 200, third `429 {"code":-32003,"message":"Rate Limit Exceeded"}`. Another key is not affected. Other tools on the same key keep working |
| `json_rpc_methods` (`tools/list`, 2 per 60 s) | Third `tools/list` in the window: `429 {"code":-33004,"message":"Rate Limit Exceeded"}` (stateless style); `tools/call` keeps working |
| `json_rpc_methods_access_rights.blocked` | `prompts/list`: `403 {"code":-32002,"message":"method 'prompts/list' is not available"}`. The `prompts` capability is also removed from the `initialize` result for that key |

- Entries in `allowed` and `blocked` are regular expressions that must match the whole name.
- `mcp_primitives[].type` is `tool`, `resource` or `prompt`; `limit.per` here is a number of
  seconds, unlike the duration string in the definition.
- In `mcp_primitives` and `json_rpc_methods`, limits are rate limits only.
- A method allow list (`json_rpc_methods_access_rights.allowed`) denies everything it does not
  name, so include `initialize`, `notifications/initialized` and `server/discover`.
- `mcp_access_rights` and `mcp_primitives` behave the same on the open source image, with the
  policy and key created through `POST /tyk/policies` and `POST /tyk/keys`.

https://tyk.io/docs/ai-management/mcp-gateway/policies documents the whole-name regular
expressions, the rate-limit-only limits and the method allow list.

## Quotas

A quota is a policy-level setting (`quota_max`, `quota_renewal_rate`), not per tool. Every
JSON-RPC request counts, the handshake included: with `quota_max: 4`, four requests pass and
the fifth returns

```text
403 {"jsonrpc":"2.0","error":{"code":-33003,"message":"Quota exceeded","data":{"http_code":403}},"id":1}
```

In the session style the code is `-32002` with the same message. A session-style client spends
two requests on `initialize` and `notifications/initialized` before its first tool call, so size
quotas for MCP in requests, not in tool calls.

## Creating the policy and the key

Dashboard API:

```bash
curl -s "$DASH/api/portal/policies/" -H "authorization: $DASH_KEY" -H 'Content-Type: application/json' --data @policy.json
# 200 {"Status":"OK","Message":"<policy database id>","Meta":null}
curl -s "$DASH/api/keys" -H "authorization: $DASH_KEY" -H 'Content-Type: application/json' \
  -d '{"org_id":"<org id>","apply_policies":["<policy id>"]}'
# 200 {... "key_id":"<the key>" ...}
```

Policy names are unique in the Dashboard: a second policy with the same name returns
`{"Status":"Error","Message":"This policy name has already been used. Enter a unique policy name","Meta":null}`.
Give the policy a 24-character hexadecimal `id`; that is the value `apply_policies` and
`defaultPolicies` refer to.

Wait a few seconds between the two calls. A key requested before the Gateway has loaded the new
policy is not created; the Gateway logs `policy not found: "<policy id>"`.

Gateway API (file mode, also the open source image):

```bash
curl -s -X POST "$GW/tyk/policies" -H "x-tyk-authorization: $GW_SECRET" -H 'Content-Type: application/json' --data @policy.json
# 200 {"key":"<policy id>","status":"ok","action":"added"}
curl -s "$GW/tyk/reload" -H "x-tyk-authorization: $GW_SECRET"
curl -s -X POST "$GW/tyk/keys" -H "x-tyk-authorization: $GW_SECRET" -H 'Content-Type: application/json' \
  -d '{"org_id":"","apply_policies":["<policy id>"]}'
# 200 {"key":"<the key>", ...}
```

For JWT callers there is no key: the policy comes from `defaultPolicies` or a scope-to-policy
mapping on the JWT scheme. A policy change applies to existing keys without reissuing them.

## Policy or scope check?

- Use a policy to hide tools from a consumer and to limit how often it calls them.
- Use the OAuth scope check (`authentication.md`) when the identity provider decides who may call
  a tool. It refuses the call and leaves the tool visible in `tools/list`.
