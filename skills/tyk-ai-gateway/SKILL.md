---
name: tyk-ai-gateway
description: Stand up Tyk AI Studio as an AI gateway and configure it through its admin API (LLM providers, app keys, the unified endpoint, failover, budgets, guardrails, audit, metrics), with the Community versus Enterprise boundary. Use when someone asks for Tyk AI Studio, an AI or LLM gateway or proxy with Tyk, one endpoint for several LLM providers, per-app LLM keys, token cost tracking, LLM budgets, failover or model routing, AI governance, guardrails or PII redaction on prompts, AI Studio tools over MCP, or the Tyk Dashboard MCP integration. Not for MCP proxies on Tyk Gateway, which belong to the tyk-mcp-gateway skill.
---

# Tyk AI Gateway

Tyk's AI gateway is Tyk AI Studio: a control plane with an admin API, and edge gateways that
applications call. It is a separate product from Tyk Gateway and Tyk Dashboard, with its own
images, database and API. This skill stands it up for evaluation, configures it through the API
and says what each edition does. It is written for AI Studio 2.3, Community and Enterprise
Edition.

## Ask first

| Question | Default when the user does not say |
| --- | --- |
| Community Edition or Enterprise Edition? | Community Edition (no licence). Read `references/editions.md` before promising budgets that block, routers, guardrails, audit, teams, roles or SSO: those need Enterprise Edition |
| Real LLM providers or stand-ins? | The two stand-in backends in the stack (no provider keys, no spend) |
| Which version? | 2.3: step 1 looks up the 2.3 image tag on Docker Hub and puts it in `.env` as `AISTUDIO_TAG`. Both images use the same tag |
| Do the LLMs run on your own network (Ollama, an in-cluster server)? | No. If yes, add their host names to `LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS` in both env files |
| Evaluation or a real installation? | Evaluation on a workstation. Anything long-lived goes to the `tyk-deployment` skill |

## Workflow

`SK` is this skill's directory. Keep everything you generate outside it, in the user's
workspace (`${TYK_WORKSPACE:-~/tyk-workspace}/<project>/demos/ai-gateway`).

1. **Start the stack.** Control plane, one edge, two stand-in LLM backends.

   ```bash
   DEST="${TYK_WORKSPACE:-$HOME/tyk-workspace}/<project>/demos/ai-gateway"
   mkdir -p "$(dirname "$DEST")" && cp -R "$SK/assets/eval-stack" "$DEST" && cd "$DEST"
   ./make_secrets.sh
   AISTUDIO_TAG=$(curl -fsS --max-time 20 \
     "https://hub.docker.com/v2/namespaces/tykio/repositories/tyk-ai-studio/tags?page_size=100&name=v2.3." |
     python3 -c 'import json, re, sys; tags = [t["name"] for t in json.load(sys.stdin)["results"] if "unstable" not in t["name"]]; tags or sys.exit("no 2.3 image tag found"); print(max(tags, key=lambda s: ("-" not in s, [int(n) for n in re.findall(r"\d+", s)])))') &&
     echo "AISTUDIO_TAG=$AISTUDIO_TAG" >> .env && echo "using $AISTUDIO_TAG"
   docker compose up -d
   curl -s http://localhost:8080/auth/features   # "edition":"community"; retry for a few seconds
   ```

   The lookup lists the 2.3 tags of `tykio/tyk-ai-studio`, skips unstable builds and takes the
   highest by version order (a plain `vX.Y.Z` tag ranks above any tag with a suffix). Use the
   exact tag it printed, for both images and when you report the version; the edge image is
   published under the same tag. If it prints an error instead of `using v2.3...`, stop: there
   is no tag to run.

   Ports: admin UI and API `8080`, edge `9091`, stand-ins `9101` and `9102`; change them in
   `.env` (`ADMIN_PORT`, `EDGE_PORT`, ...). PostgreSQL, Enterprise images and every setting
   are in `references/architecture-and-setup.md`.

2. **Create the first admin and an API token.** The first account registered becomes the admin.

   ```bash
   export AISTUDIO_URL=http://localhost:8080
   export AISTUDIO_ADMIN_PASSWORD='<8+ chars with upper, lower, digit and special>'
   python3 "$SK/scripts/aistudio_admin.py" bootstrap --email admin@example.com --name "Example Admin"
   ```

   The token goes to `./.aistudio-token`. Then set `ALLOW_REGISTRATIONS=false` in `studio.env`
   and run `docker compose up -d studio`, so nobody else can sign up. The control plane takes a
   few seconds to answer again and the edge reconnects on its own.

3. **Add LLM providers.** Two sample LLMs are seeded, so yours start at id 3: read the id from
   each response. Send `"active": true`.

   ```bash
   python3 "$SK/scripts/aistudio_admin.py" request POST /api/v1/llms --data '{"data":{"type":"llms","attributes":{"name":"Stub Alpha","vendor":"openai","api_endpoint":"http://llm-alpha:8080/v1","api_key":"stub-key-alpha","default_model":"alpha-fast","allowed_models":["alpha-fast","alpha-deep"],"active":true}}}'
   python3 "$SK/scripts/aistudio_admin.py" request POST /api/v1/llms --data '{"data":{"type":"llms","attributes":{"name":"Stub Beta","vendor":"openai","api_endpoint":"http://llm-beta:8080/v1","api_key":"stub-key-beta","default_model":"beta-fast","allowed_models":["beta-fast","beta-deep"],"active":true}}}'
   ```

   For a real provider change `vendor`, `api_endpoint`, `api_key` and the models. Any service
   that speaks the chat completions API is `vendor: openai` with its own endpoint.

4. **Add failover** (optional, both editions): when Stub Alpha (id 3) fails, use Stub Beta (id 4).

   ```bash
   python3 "$SK/scripts/aistudio_admin.py" request PATCH /api/v1/llms/3 --data '{"data":{"type":"llms","attributes":{"failover":{"targets":[{"llm_id":4,"model":"beta-fast"}],"triggers":{"on_connection_error":true,"on_timeout":true,"attempt_timeout_seconds":10}}}}}'
   ```

5. **Create an App and get its key.** The App is the caller; `llm_ids` are its grants.

   ```bash
   python3 "$SK/scripts/aistudio_admin.py" request POST /api/v1/apps --data '{"data":{"type":"apps","attributes":{"name":"partner-a","description":"Example application","user_id":1,"llm_ids":[3]}}}'
   python3 "$SK/scripts/aistudio_admin.py" app-secret 1 --out .app-secret
   ```

6. **Push to the edges.** LLM and model price changes do not reach an edge until pushed.

   ```bash
   python3 "$SK/scripts/aistudio_admin.py" push   # "edge edge-01: connected, in_sync"
   ```

7. **Call a model.** The model name is `{llm-slug}/{model}`; the slug comes from the LLM name.

   ```bash
   KEY=$(cat .app-secret)
   curl -s -i http://localhost:9091/v1/chat/completions -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"model":"stub-alpha/alpha-fast","messages":[{"role":"user","content":"hello"}]}'
   # 200, X-Tyk-Served-LLM: stub-alpha, content "SERVED-BY=alpha model=alpha-fast echo: hello"
   curl -s http://localhost:9091/v1/models -H "Authorization: Bearer $KEY"
   ```

   A client SDK for the chat completions API needs only `base_url=http://localhost:9091/v1`
   and the App key. Details and every error body: `references/calling-models.md`.

8. **Prove failover.** Make the stand-in fail, call again, restore it.

   ```bash
   curl -s -X POST http://localhost:9101/_mode -d '{"mode":"error"}'
   curl -s -i http://localhost:9091/v1/chat/completions -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"model":"stub-alpha/alpha-fast","messages":[{"role":"user","content":"hello"}]}' | grep -E 'HTTP|X-Tyk'
   # HTTP/1.1 200 OK / X-Tyk-Failover: true / X-Tyk-Served-LLM: stub-beta / X-Tyk-Served-Model: beta-fast
   curl -s -X POST http://localhost:9101/_mode -d '{"mode":"ok"}'
   ```

9. **Read usage and metrics.** Usage reaches the control plane within about 15 seconds.

   ```bash
   python3 "$SK/scripts/aistudio_admin.py" request GET "/api/v1/analytics/token-usage-per-app?start_date=$(date -u +%Y-%m-01)&end_date=$(date -u +%Y-%m-%d)"
   curl -s -H "Authorization: Bearer $(grep '^METRICS_AUTH_TOKEN=' secret.env | cut -d= -f2)" \
     http://localhost:9091/metrics | grep '^aistudio_llm_requests_total'
   ```

10. **Enterprise features** (Enterprise images and licence): routers, budgets, guardrails, audit
    and access control, with the exact bodies, are in the reference files below.

11. **Tear down** when done: `docker compose down -v`.

## References

| File | Use it for |
| --- | --- |
| `references/architecture-and-setup.md` | Parts, images, ports, settings, internal addresses, first admin and token, health, telemetry |
| `references/admin-api.md` | Authentication, objects, request bodies, update rules, roles, teams, Workspaces, service accounts |
| `references/calling-models.md` | Endpoints, model naming, streaming, SDKs, what the gateway changes in a request, caller errors |
| `references/routing-failover-budgets.md` | Failover, Model and Semantic Routers, costs, budget enforcement, rate limits |
| `references/guardrails-audit-analytics.md` | Guardrails and filters, compliance events, audit trail, analytics, metrics |
| `references/hub-and-edge.md` | Pushes, key checks and revocation, control plane outages, several edges and replicas |
| `references/mcp-tools-and-plugins.md` | OpenAPI tools over REST and MCP, the Tyk Dashboard MCP integration, plugins |
| `references/editions.md` | Community versus Enterprise, feature by feature, as each image behaved |

## Pitfalls

- **Push or it is not live.** A new or changed LLM answers `403 vendor '...' not found or not
  supported by your access rights` on the edge until `push`; a new model price is ignored
  (calls cost `0`) and a changed App grant may take minutes, or keep working when removed,
  until `push`.
- **Internal addresses are blocked.** An LLM on a private address fails with `502 ... blocked by
  LLM_UPSTREAM_BLOCK_INTERNAL` unless its host is in `LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS` on
  the control plane and every edge. The stack allows its two stand-ins only.
- **Keep `TYK_AI_SECRET_KEY`.** It encrypts stored keys; `./make_secrets.sh --force` keeps it.
- **LLM and tool names must differ by more than case or punctuation**; a near-duplicate is
  refused at creation.
- **An unknown admin route answers `200` with an HTML page.** The helper script reports it.
- **Deleting an LLM in use is allowed.** Apps lose the grant. Check
  `GET /api/v1/llms/{id}/dependents` first.
- **Usage telemetry is on by default.** The stack sets `TELEMETRY_ENABLED=false`.
- **Close registration** after the first admin exists.
- **Stand-in backends must stream.** `/v1` always calls the upstream with `stream: true` and a
  chunked body. The bundled `stub_llm.py` handles both.

## Limitations

- **Community Edition does not enforce budgets, and has no budget alerts, routers, filters or
  guardrails, semantic caching, audit trail, teams, roles, Workspaces, catalogues or SSO.**
  `references/editions.md` has the answer for each.
- **No built-in request rate limit** in either edition; rate limiting is a plugin.
- **`/v1` and `/ai/` rebuild the request.** `top_p`, `user`, `logprobs`, `logit_bias`,
  `reasoning_effort` and a list-form `stop` are dropped, and `temperature` is sent as `0` when
  absent. Tool results and content parts pass. `/llm/call/{llm-slug}/...` forwards the body
  unchanged, but failover does not apply there.
- **No `/v1/embeddings`.** Use `/llm/call/{llm-slug}/v1/embeddings`.
- **The Anthropic Messages endpoint works for Bedrock LLMs only.**
- **Routers run on edges only**, not on the control plane's embedded gateway.
- **During a control plane outage** a key the edge has not seen gets `503`, and a restarted edge
  exits unless it starts from its last configuration; only Enterprise full credential sync
  lets it validate keys then (`references/hub-and-edge.md`).

## Before reporting success

- [ ] `GET /auth/features` shows the edition the user asked for and a `2.3` version.
- [ ] `push` ended with every edge `connected, in_sync`.
- [ ] A call on `/v1/chat/completions` returned `200`, `X-Tyk-Served-LLM` names the expected
      LLM and the answer has content (with stand-ins, `SERVED-BY=`).
- [ ] `GET /v1/models` lists exactly the models the App should have.
- [ ] A call to an LLM the App is not granted returned `403`.
- [ ] If failover was configured: with the primary failing, the answer carries
      `X-Tyk-Failover: true`; with it restored, it does not.
- [ ] The call appears in `/api/v1/analytics/proxy-logs-for-app` and in the edge's `/metrics`.
- [ ] Every Enterprise claim made to the user is in `references/editions.md`.
- [ ] Registration is closed and no secret, token or licence was printed or written into a
      shared file.

## Hand-offs

- Licences, packaging, pricing, versions and images: the `tyk-facts` skill.
- Production or long-lived installation: the `tyk-deployment` skill.
- MCP proxies, REST to MCP and MCP policies on Tyk Gateway: the `tyk-mcp-gateway` skill.
- A full demo bundle with proofs, including AI Studio beside Tyk Gateway: the
  `tyk-demo-builder` skill.
- Moving from another AI gateway: the `tyk-migration-planner` skill.
- Rate limits, quotas and keys on Tyk Gateway APIs: the `tyk-rate-limits-keys-policies` skill.
- Observability of Tyk Gateway APIs: the `tyk-observability` skill.
- Upgrading an AI Studio installation: no skill in this plugin covers it. Read the
  [release notes](https://tyk.io/docs/developer-support/release-notes/ai-studio) for every
  version between the running one and the target before changing the tag.
