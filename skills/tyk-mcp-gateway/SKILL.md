---
name: tyk-mcp-gateway
description: Put MCP servers behind Tyk Gateway and expose REST APIs as MCP tools, with Tyk OAS MCP proxy definitions, tool naming and selection, API key, JWT and OAuth discovery, token exchange, per-tool access, rate limits, quotas and observability, and the edition each part needs. Use when asked about Tyk MCP gateway, MCP proxy, API to MCP, REST or OpenAPI to MCP tools, MCP authentication or MCP rate limits. Not for MCP or LLM governance inside Tyk AI Studio, which the `tyk-ai-gateway` skill owns.
---

# Tyk MCP Gateway

Tyk Gateway can sit in front of a remote MCP server, or turn the operations of a REST API it
already manages into MCP tools. This skill builds either one on Gateway 5.16.0, puts
authentication and per-tool control on it, proves it with a real MCP client, and says what each
part needs and what does not work yet. The commands and responses apply to the images named in
the reference files.

## Ask first

| Question | Default when the user does not say |
| --- | --- |
| Remote MCP server, or a REST API as MCP tools? | Remote MCP server |
| Is there a licensed Tyk Dashboard, or only a Gateway? | Dashboard present: use `/api/mcps`. Gateway only: use `/tyk/mcps` |
| How do callers authenticate: API key, or tokens from an identity provider? | API key |
| Must the upstream see the end user (token exchange)? | No. If yes, read `references/token-exchange-keycloak.md` first: Enterprise Edition, remote MCP servers only |
| Which MCP clients will connect, and do they speak `2026-07-28`? | Assume session-style `2025-11-25` clients |
| How many Gateway replicas? | One. More than one matters for REST-to-MCP sessions |
| Which Gateway version? | 5.16.0. Resolve the current tag with the `tyk-facts` skill |

## References

| File | Use it for |
| --- | --- |
| `references/proxy-definitions.md` | The two proxy kinds, the APIs to create them, tool names, including and excluding operations, per-tool settings |
| `references/transports-and-sessions.md` | Streamable HTTP, protocol versions, sessions with several replicas, request checks, error codes |
| `references/authentication.md` | API key, JWT, protected-resource metadata, scopes per tool, upstream OAuth, what reaches the upstream |
| `references/token-exchange-keycloak.md` | Token exchange with Keycloak, audience settings, the two traps |
| `references/access-control-and-limits.md` | Blocking and limits in the definition, per-consumer policies, quotas |
| `references/observability.md` | Analytics and pumps, Dashboard endpoints, access logs, traces, metrics |
| `references/editions-and-limits.md` | Edition per capability, Developer Portal, the AI Studio boundary, the limitations table |
| `scripts/mcp_probe.py` | The MCP client used for every proof (Python standard library) |
| `assets/*.json` | Definitions, a policy and a Keycloak realm that ran; replace the `__NAME__` placeholders |

## Workflow

1. **Settle the edition before building.** Read the table in `references/editions-and-limits.md`.
   A proxy in front of a remote MCP server is in the open source Gateway. REST API to MCP,
   upstream OAuth and token exchange are listed as Enterprise Edition. Quote licence wording from
   the `tyk-facts` skill, never from memory.

2. **Get a Gateway 5.16.0 and set the variables.** For a demo stack with a Dashboard, generate
   one with the `tyk-demo-builder` skill (module `mcp`). For a lasting install use the
   `tyk-deployment` skill.

   ```bash
   SKILL=<folder of this skill>; probe() { python3 "$SKILL/scripts/mcp_probe.py" "$@"; }
   GW=http://localhost:8080
   DASH=http://localhost:3000; DASH_KEY=<Dashboard API key>      # Dashboard path
   GW_SECRET=<gateway secret>                                    # Gateway API path
   ```

3. **Create the proxy for a remote MCP server.**

   ```bash
   sed 's#__MCP_SERVER_URL__#http://mcp-server.example.com:7878#' \
     "$SKILL/assets/remote-mcp-proxy.oas.json" > remote.json
   # Dashboard path
   curl -s "$DASH/api/mcps" -H "authorization: $DASH_KEY" -H 'Content-Type: application/json' --data @remote.json
   #   {"Status":"OK","Message":"API created","Meta":"<db id>","ID":"orders-mcp"}
   # Gateway API path
   curl -s -X POST "$GW/tyk/mcps" -H "x-tyk-authorization: $GW_SECRET" -H 'Content-Type: application/json' --data @remote.json
   #   {"key":"orders-mcp","status":"ok","action":"added"}
   curl -s "$GW/tyk/reload" -H "x-tyk-authorization: $GW_SECRET"
   ```

   The upstream URL is the server's base; the Gateway adds `/mcp`. Clients use
   `$GW/orders-mcp/mcp`.

4. **Prove the door is shut, then open it for one consumer.**

   ```bash
   probe probe "$GW/orders-mcp/mcp"
   #   "status": 401, error code -32001, "message": "Authorization field missing"
   ```

   Create a policy and a key (`references/access-control-and-limits.md` has both API forms and
   the wait between them; the policy object belongs to the `tyk-rate-limits-keys-policies`
   skill). `assets/mcp-consumer-policy.json` covers the three proxies in `assets/`; on
   `orders-mcp` it allows tools matching `get_.*` plus `generate_uuid`. Replace `__POLICY_ID__`
   with a 24-character hexadecimal id and `__ORG_ID__` with the organisation id (empty on a
   Gateway with no Dashboard). Keep the key the create call returns (`key_id` from the
   Dashboard, `key` from the Gateway API) as `KEY=<that value>`.

   ```bash
   probe list "$GW/orders-mcp/mcp" --header "Authorization: $KEY"
   #   "status": 200, "names": [ only the tools the policy allows, minus tools the proxy blocks ]
   probe call "$GW/orders-mcp/mcp" get_users --header "Authorization: $KEY"
   #   "status": 200, the tool result
   probe call "$GW/orders-mcp/mcp" delete_user --args '{"id":"1"}' --header "Authorization: $KEY"
   #   "status": 403, error code -32002
   probe list "$GW/orders-mcp/mcp" --header "Authorization: $KEY" --protocol 2026-07-28
   #   the same list, no handshake
   ```

5. **For a REST API as MCP tools (Enterprise Edition image `tykio/tyk-gateway-ee`).** The asset
   REST API adds its backend credential from `kv://env/ORDERS_BACKEND_KEY`, which reads
   `TYK_SECRET_ORDERS_BACKEND_KEY`: set that variable on every Gateway. Create the REST API, wait
   until the Gateway serves it, then create the proxy that points at its id.

   ```bash
   sed 's#__REST_UPSTREAM_URL__#http://orders.example.com:8080#' "$SKILL/assets/orders-rest-api.oas.json" > orders-api.json
   # Dashboard path
   curl -s "$DASH/api/apis/oas" -H "authorization: $DASH_KEY" -H 'Content-Type: application/json' --data @orders-api.json
   #   {"Status":"OK","Message":"API created","Meta":"<db id>","ID":"orders-api"}
   # Gateway API path
   curl -s -X POST "$GW/tyk/apis/oas" -H "x-tyk-authorization: $GW_SECRET" -H 'Content-Type: application/json' --data @orders-api.json
   curl -s "$GW/tyk/reload" -H "x-tyk-authorization: $GW_SECRET"
   # Both paths: wait until the REST API answers (401 once loaded, 404 before)
   until [ "$(curl -s -o /dev/null -w '%{http_code}' "$GW/orders-api/orders")" != 404 ]; do sleep 1; done
   # Dashboard path (dryRun + expand shows the derived tools and stores nothing)
   curl -s "$DASH/api/mcps?dryRun=true&expand=true" -H "authorization: $DASH_KEY" \
     -H 'Content-Type: application/json' --data @"$SKILL/assets/rest-to-mcp-proxy.oas.json"
   curl -s "$DASH/api/mcps" -H "authorization: $DASH_KEY" -H 'Content-Type: application/json' \
     --data @"$SKILL/assets/rest-to-mcp-proxy.oas.json"
   # Gateway API path
   curl -s -X POST "$GW/tyk/mcps" -H "x-tyk-authorization: $GW_SECRET" -H 'Content-Type: application/json' \
     --data @"$SKILL/assets/rest-to-mcp-proxy.oas.json"
   curl -s "$GW/tyk/reload" -H "x-tyk-authorization: $GW_SECRET"
   probe list "$GW/orders-rest-mcp/mcp" --header "Authorization: $KEY"
   #   "names": ["get_order", "listOrders"]
   probe call "$GW/orders-rest-mcp/mcp" get_order --args '{"order_id":"42"}' --header "Authorization: $KEY"
   #   "status": 200; result._meta.upstreamHttpStatus is the REST status; content[0].text is the REST body
   ```

   Without the reload and the wait, the Gateway API refuses the proxy with `400 ... "Paired REST
   API orders-api is not loaded; create it first"`. The key's policy must list `orders-rest-mcp`
   (the asset policy does). Tool names come from `operationId`; choose and rename tools in
   `x-tyk-mcp-server` (`references/proxy-definitions.md`). The source API's authentication does
   not cover the MCP proxy: give it its own.

6. **For tokens from an identity provider**, follow `references/authentication.md`: a JWT scheme
   with `stripAuthorizationData: false`, an `oauth2` scheme for the metadata document and scope
   checks, then prove discovery with `probe metadata "$GW/<listen path>mcp"`. For the user's
   identity at the MCP server, add token exchange from `references/token-exchange-keycloak.md`.

7. **Add per-consumer limits and check observability** when asked:
   `references/access-control-and-limits.md`, `references/observability.md`.

8. **Run the verification checklist below** and, for each claim, say whether you checked it
   on the user's running stack (name the image tag) or it comes from the public page cited, and
   say when you did not check a point.

## Pitfalls

- Post MCP definitions to `/api/mcps`. `/api/apis/oas` accepts the same document as an ordinary
  API and it never shows as an MCP proxy.
- `rateLimit.per` in the definition is a duration string (`"5s"`). In a policy `per` is a number
  of seconds.
- JWT on an MCP proxy with `stripAuthorizationData: true` fails every call with
  `401 Authorization field missing`. Use `false`; the caller's token then reaches the MCP server
  unless upstream OAuth or token exchange replaces it.
- With token exchange, `allowedAudiences` must also list the exchange audience, and the exchanged
  token must carry the claims your scope checks read. Otherwise calls fail with 401 or 403 after
  a successful exchange.
- On the open source image, `tokenExchange` and upstream OAuth load without error and do nothing:
  the caller's credential goes upstream. Read the Gateway log.
- A REST API's own authentication does not protect a REST-to-MCP proxy built on it.
- Every 401 from an MCP proxy names a metadata address. It only answers when the proxy has
  `protectedResourceMetadata` with `resource` and `authorizationServers` set.
- Create the key after the Gateway has loaded the policy, or the key is not created.
- Quotas count every JSON-RPC request, the handshake included.
- Dashboard MCP charts stay empty without the MCP aggregate pump and `enable_aggregate_lookups`.

## Limitations (Gateway 5.16.0)

The full table with the proof for each line is in `references/editions-and-limits.md`.

- A REST API exposed as MCP tools cannot be called in the user's context: no caller header,
  exchanged token or JWT claim reaches the REST backend. Only static credentials set on the
  source API do.
- A REST-to-MCP session lives in one Gateway replica; another replica answers
  `404 session not found`. Stateless `2026-07-28` requests work on any replica.
- REST-to-MCP gives tools only, answers in JSON only, cuts backend responses at 1 MiB, and cannot
  carry multipart bodies.
- A client cannot answer a server's request, so sampling, elicitation and roots do not work
  through the Gateway. JSON-RPC batches and bodies over 1 MiB are refused.
- Only Streamable HTTP is supported; stdio and the older HTTP+SSE transport are not.
- The scope check refuses a call but does not hide the tool from `tools/list`.

## Verification checklist

Run these before reporting success, and paste the status and the relevant line of each result.

- [ ] `probe` with no credential returns 401 (never 200) on every MCP proxy, REST-to-MCP included.
- [ ] `list` with a consumer credential returns the tools that consumer should see, and nothing
      else: blocked tools and tools outside the policy are absent.
- [ ] `call` of one allowed tool returns 200 with a result, and `call` of one blocked tool
      returns 403.
- [ ] A tool with a rate limit returns 429 once the limit is passed.
- [ ] For REST-to-MCP: the tool names match the plan, and the REST backend received the expected
      path and static credential.
- [ ] For OAuth: `metadata` returns 200 with the right `resource` and `authorization_servers`;
      a caller without the scope gets `403 insufficient_scope`.
- [ ] For token exchange: the token at the MCP server has the upstream audience and the user's
      `sub`, and the Gateway log shows `oauth2_exchange_outcome=ok`, not the Enterprise Edition
      warning.
- [ ] With more than one replica and REST-to-MCP: the client is stateless, or a session-style
      call was repeated against each replica.
- [ ] The definition contains no secret; secrets come from `kv://` or `env://` references
      (`references/authentication.md`).

## Hand-offs

- Licence wording, packages, trial path, current versions and images: the `tyk-facts` skill.
- Policies, keys, quotas and rate limit behaviour beyond the MCP fields: the
  `tyk-rate-limits-keys-policies` skill.
- Tyk OAS definitions for the source REST API: the `tyk-api-definitions-routing` skill.
- MCP proxies in Git, Tyk Sync or Tyk Operator: the `tyk-gitops-sync-operator` skill.
- A demo stack with a Dashboard, a mock MCP server and an MCP inspector: the `tyk-demo-builder`
  skill. A structured proof of concept: the `tyk-poc-runbook` skill.
- Publishing an MCP proxy as a Developer Portal product: the `tyk-developer-portal-setup` skill.
  Portal theming: the `tyk-developer-portal-branding` skill.
- MCP and LLM governance inside Tyk AI Studio: the `tyk-ai-gateway` skill.
- Installing Tyk for real: the `tyk-deployment` skill.
