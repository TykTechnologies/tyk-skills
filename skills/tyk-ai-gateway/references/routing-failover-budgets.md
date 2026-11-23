# Failover, model routing, budgets and rate limits

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`)

Failover and cost tracking work on both editions. Model Routers, Semantic Routers and budget
enforcement need the Enterprise images with a licence; their Community answers are given too.

See [LLM management and failover](https://tyk.io/docs/ai-management/ai-studio/llm-management),
[Model Router](https://tyk.io/docs/ai-management/ai-studio/model-router),
[Semantic Router](https://tyk.io/docs/ai-management/ai-studio/semantic-router),
[Budgeting](https://tyk.io/docs/ai-management/ai-studio/budgeting),
[Model prices](https://tyk.io/docs/ai-management/ai-studio/model-prices).

## LLM failover (both editions)

Each LLM can carry a failover waterfall: an ordered list of other LLMs, each with the model to
use there. It is part of the LLM object.

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request PATCH /api/v1/llms/3 --data '{
  "data":{"type":"llms","attributes":{"failover":{
    "targets":[{"llm_id":4,"model":"beta-fast"}],
    "triggers":{"on_connection_error":true,"on_timeout":true,"attempt_timeout_seconds":10}}}}}'
python3 <skill-dir>/scripts/aistudio_admin.py push
```

The stored value reads back as
`{"targets":[{"llm_id":4,"model":"beta-fast"}],"triggers":{"on_timeout":true,"on_connection_error":true,"attempt_timeout_seconds":10}}`.
The `failover` value is replaced as a whole: a `PATCH` that sends only `targets` drops the
`triggers`. `"failover": null` clears it; leaving the key out of a `PATCH` keeps it.

What a caller sees (the primary is `stub-alpha`, the fallback `stub-beta`):

| Primary upstream | Answer on `/v1` and `/ai/` |
| --- | --- |
| Healthy | `200`, `X-Tyk-Served-LLM: stub-alpha` |
| Answers `500` | `200`, `X-Tyk-Failover: true`, `X-Tyk-Served-LLM: stub-beta`, `X-Tyk-Served-Model: beta-fast`; the body's `model` is `beta-fast` |
| Answers `429` | `200` from the fallback, same headers |
| Accepts the request and never answers, with `attempt_timeout_seconds: 5` | `200` from the fallback after about 5 seconds |
| Container stopped (connection error) | `200` from the fallback |
| Primary and fallback both answer `500` | `500` with the last attempt's error |

**Failover does not apply on `/llm/call`.** With the primary answering `500`, the same request
on `/llm/call/stub-alpha/v1/chat/completions` returns the upstream's
`500 {"error": {"message": "stub alpha is failing", "type": "server_error"}}`.

Rules:

- The App needs a grant for the primary only: the fallback serves an App granted only the
  primary. The documentation says the same, and that "the gateway still applies the budget of
  the fallback provider" ([LLM management](https://tyk.io/docs/ai-management/ai-studio/llm-management)).
- A fallback must be at least as private as the primary. Raising the primary's
  `privacy_score` above its fallback's is refused:
  `400 {"errors":[{"title":"Bad Request","detail":"failover target 1: LLM \"Stub Beta\" has a lower privacy score (0) than this LLM (10)"}]}`.
- An LLM that is a fallback of another cannot be deleted:
  `409 {"errors":[{"title":"Conflict","detail":"failover: LLM \"Stub Beta\" is a failover target of: [Stub Alpha]; remove it from those waterfalls first"}]}`.
- Default triggers are upstream status `408`, `429`, `500`, `502`, `503`, `504`, a timed-out
  attempt and a failed connection; `401` and `403` never fail over; a waterfall holds at most
  10 fallbacks, which must be active and in the same namespace or global. See
  [LLM management](https://tyk.io/docs/ai-management/ai-studio/llm-management).
- Failover keeps working while the control plane is down (`hub-and-edge.md`).

Evidence of a failover besides the headers: the edge metric
`aistudio_llm_failover_total{from_llm="stub-alpha",reason="status_500",to_llm="stub-beta"}`,
and proxy log rows with `failover_attempt` `1` and `failover_from_llm_id` for the fallback.

## Model Router (Enterprise Edition)

A Model Router has a slug and pools. Each pool has a model pattern (glob), a selection
algorithm (`round_robin` or `weighted`), a priority (higher first) and vendors; a vendor points
at an LLM and can map the requested model name to the vendor's model
([Model Router](https://tyk.io/docs/ai-management/ai-studio/model-router)).

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request POST /api/v1/model-routers --data '{"data":{"type":"model-routers","attributes":{
  "name":"Example Router","slug":"example-router","description":"Routes by model name","active":true,
  "pools":[
    {"name":"fast-family","model_pattern":"fast-*","selection_algorithm":"round_robin","priority":10,
     "vendors":[{"llm_id":3,"weight":1,"active":true,"mappings":[{"source_model":"fast-chat","target_model":"alpha-fast"}]}]},
    {"name":"deep-family","model_pattern":"deep-*","selection_algorithm":"round_robin","priority":5,
     "vendors":[{"llm_id":4,"weight":1,"active":true,"mappings":[{"source_model":"deep-chat","target_model":"beta-deep"}]}]}]}}}'
# 201, "slug":"example-router"
python3 <skill-dir>/scripts/aistudio_admin.py request PATCH /api/v1/apps/1 --data '{"data":{"type":"apps","attributes":{"model_router_ids":[1]}}}'
python3 <skill-dir>/scripts/aistudio_admin.py push
```

| Request on the edge (App granted the router) | Answer |
| --- | --- |
| `/v1/chat/completions`, `"model":"example-router/fast-chat"` | `200`, `X-Tyk-Router: example-router`, `X-Tyk-Route: fast-family`, `X-Tyk-Route-Reason: model_pattern`, `X-Tyk-Served-LLM: stub-alpha`, `X-Tyk-Served-Model: alpha-fast` |
| `"model":"example-router/deep-chat"` | `200`, `X-Tyk-Route: deep-family`, served by `stub-beta` / `beta-deep` |
| `"model":"example-router/other"` | `400 {"error":{"code":"invalid_request_error","message":"router 'example-router' cannot route model 'other': no route matches the requested model: no pool matches the requested model","type":"invalid_request_error"}}` |
| `/router/example-router/v1/chat/completions` or `/ai/example-router/v1/chat/completions` with `"model":"fast-chat"` | `200`, same headers as the first row |
| `GET /v1/models` | lists `example-router/fast-chat` and `example-router/deep-chat` (`"owned_by":"model_router"`) |
| The same model on the control plane's embedded gateway | `403 vendor 'example-router' not found ...`: routers run on edges only |

- Routers and LLMs share one set of names: a router with slug `stub-alpha` is refused with
  `400 ... "route name is already used by another LLM or router: the LLM \"Stub Alpha\" answers to \"stub-alpha\""`.
- An App that calls a router it was not granted is still served from the LLMs it holds
  directly, and only those: an App holding LLM 3 only gets `200` for `fast-chat` and
  `403 ... "the router has no target this app may use"` for `deep-chat`. The edge logs
  `App called a Model Router it has not been granted ... (deprecated; grant the router to the
  app)`. Grant the router.
- A `PATCH` that sends only `description` keeps the pools.

## Semantic Router (Enterprise Edition)

A Semantic Router picks a route from the prompt. Stages run in order: an explicit route
(`{slug}/{route}`), session affinity, keywords, embeddings, an optional LLM judge, then the
default route ([Semantic Router](https://tyk.io/docs/ai-management/ai-studio/semantic-router)).
The keyword stage needs no embedder:

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request POST /api/v1/semantic-routers --data '{"data":{"type":"semantic-routers","attributes":{
  "name":"Smart","slug":"smart","description":"Routes by prompt","active":true,
  "settings":{"allow_explicit_route":true,"default_route":"general"},
  "routes":[
    {"name":"code","description":"Programming questions","priority":10,
     "keywords":[{"pattern":"python"},{"pattern":"stack trace"}],
     "target":{"type":"llm","llm_id":4,"model":"beta-deep"}},
    {"name":"general","description":"Everything else","target":{"type":"llm","llm_id":3,"model":"alpha-fast"}}]}}}'
# 201, "models":["smart/auto","smart/code","smart/general"]
python3 <skill-dir>/scripts/aistudio_admin.py request PATCH /api/v1/apps/1 --data '{"data":{"type":"apps","attributes":{"semantic_router_ids":[1]}}}'
python3 <skill-dir>/scripts/aistudio_admin.py push
```

| Request (`/v1/chat/completions`) | Answer |
| --- | --- |
| Before the grant | `403 {"error":{"code":"permission_error","message":"router 'smart' cannot route model 'auto': the router has no target this app may use","type":"permission_error"}}` |
| `smart/auto`, "my python script fails" | `200`, `X-Tyk-Route: code`, `X-Tyk-Route-Reason: keyword`, served by `stub-beta` / `beta-deep` |
| `smart/auto`, "tell me a story" | `200`, `X-Tyk-Route: general`, `X-Tyk-Route-Reason: default` |
| `smart/code`, any prompt | `200`, `X-Tyk-Route-Reason: explicit` |
| `/ai/smart/v1/chat/completions`, `"model":"auto"` | `200`, routed the same way |

Push after the grant. The `403` above can continue for up to about 20 seconds after the grant
and a push, or the call can answer `200` straight after the push. Retry for half a minute before
you conclude the grant failed.
`GET /v1/models` lists `smart/auto`, `smart/code` and `smart/general`.

Community Edition answers the router endpoints with `402`:

```text
GET /api/v1/model-routers     -> 402 {"errors":[{"title":"Error","detail":"model router is an Enterprise Edition feature - visit https://tyk.io/ai-studio/pricing for more information"}]}
GET /api/v1/semantic-routers  -> 402 {"errors":[{"title":"Payment Required","detail":"semantic router is an Enterprise Edition feature - visit https://tyk.io/ai-studio/pricing for more information"}]}
```

Community Edition can still spread or protect traffic without a router: callers choose the LLM
per request with the `{llm-slug}/{model}` prefix, and each LLM can fail over to another.

## Costs and budgets

Cost tracking works in both editions. A model price gives the cost per completion token
(`cpt`) and per prompt token (`cpit`):

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request POST /api/v1/model-prices --data '{
  "data":{"type":"model-prices","attributes":{"model_name":"alpha-fast","vendor":"openai","cpt":0.01,"cpit":0.005,"currency":"USD"}}}'
python3 <skill-dir>/scripts/aistudio_admin.py push    # edges cost calls only with the prices they were pushed
```

```json
{"data":{"type":"model-prices","id":"1","attributes":{"model_name":"alpha-fast","vendor":"openai","cpt":0.01,"cpit":0.005,"cache_write_pt":0,"cache_read_pt":0,"currency":"USD"}}}
```

A new price is a pending change: calls an edge serves before the push are recorded at cost
`0`, and the edge's `aistudio_llm_cost_total` starts counting after it. Spend shows in
`GET /api/v1/analytics/budget-usage` (`"spent"`) and the other analytics endpoints
(`guardrails-audit-analytics.md`).

Budgets are a `monthly_budget` number on an App or an LLM. `null` (or leaving the field out)
means no limit; `0` means no spending allowed
(see [Budgeting](https://tyk.io/docs/ai-management/ai-studio/budgeting)).

**Community Edition records budgets and does not enforce them.** An App created with
`"monthly_budget": 0` keeps getting `200`.

**Enterprise Edition enforces them**, on the edge and on the embedded gateway:

| Case | Answer |
| --- | --- |
| App with `"monthly_budget": 0`, on `/v1` (blocked about 20 seconds after the App is created, with the next budget update to the edge) | `403 {"error":{"code":"budget_exceeded","message":"Budget limit exceeded: app monthly budget is 0","type":"permission_error"}}` |
| The same, streaming | the same `403` body, no stream |
| The same, on `/llm/call` | `403 {"status":403,"message":"Budget limit exceeded","error":"budget exceeded: app monthly budget is 0"}` |
| App with `"monthly_budget": 0.1`, one call costing `0.18` | the call `200`, the next calls on the same edge `403 budget_exceeded` at once |

A refused App is not failed over to the fallback LLM (the edge logs `failover target skipped
... budget exceeded`). Team budgets (`GET /api/v1/groups/{id}/budget`) answer on Enterprise
Edition with `"enforcement":"alert_only"` until set; Community Edition answers
`402 {"errors":[{"detail":"team budgets are an Enterprise Edition feature - ...","title":"Payment Required"}]}`.

## Rate limits

There is no built-in request rate limit in either edition. The documentation says
`ENABLE_RATE_LIMITING` and `GATEWAY_DEFAULT_RATE_LIMIT` "had no effect" and were removed in
v2.2.0: "To limit request rates, use a rate limiting plugin"
(see [edge gateway variables](https://tyk.io/docs/ai-management/ai-studio/edge-gateway-env)).
200 calls sent 50 at a time with one App key all return `200`.

As of 2026-11-19 the public plugin marketplace index lists a "Rate Limiter" plugin (licence
AGPL v3, maturity `beta`):
https://raw.githubusercontent.com/TykTechnologies/tyk-ai-studio-plugins-ce/main/index.yaml.
See `mcp-tools-and-plugins.md` for the plugin list.

An upstream's own `429` is passed to the caller, or triggers failover when a waterfall is set.

For rate limits, quotas and keys on Tyk Gateway APIs (not AI Studio), use the
`tyk-rate-limits-keys-policies` skill.
