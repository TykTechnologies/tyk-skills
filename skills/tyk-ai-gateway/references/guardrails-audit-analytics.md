# Guardrails, audit trail, analytics and metrics

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`)

Filters, guardrails, compliance events and the audit trail need the Enterprise images with a
licence; analytics and metrics work on both editions.

See [Filters](https://tyk.io/docs/ai-management/ai-studio/filters),
[Guardrails](https://tyk.io/docs/ai-management/ai-studio/guardrails),
[Compliance events](https://tyk.io/docs/ai-management/ai-studio/compliance-events),
[Audit trail](https://tyk.io/docs/ai-management/ai-studio/audit-trail),
[Analytics](https://tyk.io/docs/ai-management/ai-studio/analytics),
[Observability](https://tyk.io/docs/ai-management/ai-studio/observability).

## Filters and guardrails (Enterprise Edition)

A filter is attached to an LLM and runs on requests or on responses (`response_filter`). Its
`kind` is `guardrail` (a detection provider, no code) or `script` (Tengo code). A new
Enterprise installation holds five guardrails, not attached to anything:

| Id | Name | Detectors | Action |
| --- | --- | --- | --- |
| 1 | Credentials in prompts (block) | `secrets` | block |
| 2 | Personal data before the vendor (redact) | `pii` | redact |
| 3 | Prompt injection heuristics (log) | `injection` | log |
| 4 | Prompt injection in tool results (quarantine) | `injection` | log; tool results quarantined |
| 5 | Credential leakage in responses (block) | `secrets`, `leak` | block (response filter) |

Attach filters to an LLM, then push:

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request PATCH /api/v1/llms/3 --data '{"data":{"type":"llms","attributes":{"filters":[1,2]}}}'
python3 <skill-dir>/scripts/aistudio_admin.py request PATCH /api/v1/llms/4 --data '{"data":{"type":"llms","attributes":{"filters":[5]}}}'
python3 <skill-dir>/scripts/aistudio_admin.py push
```

What callers get (the stand-in LLMs echo the prompt back):

| Request | Answer |
| --- | --- |
| A clean prompt | `200` |
| A GitHub token in the prompt, `/v1` | `400 {"error":{"code":"invalid_request_error","message":"[ERROR] msg: policy_violation err: Policy error: Credentials in prompts (block) - Request blocked by policy. Reason: the guardrail found credentials in a user message (guardrail_detected). Request ID: tyai_....","type":"invalid_request_error"}}`; nothing reached the upstream |
| The same on `/llm/call` | `400` with header `X-Tyk-AI-Guardrail-Reason: guardrail_detected` and a body carrying `"reason_code":"guardrail_detected"`, `"reason"`, `"filter":"Credentials in prompts (block)"` and `"request_id"` |
| An email address in the prompt | `200`; the upstream receives `email me at [REDACTED:EMAIL] please` |
| The answer contains a token (response filter on LLM 4) | `400 {"error":{"code":"policy_violation","message":"Response blocked by policy. Reason: the guardrail found credentials in an assistant message (guardrail_detected). Request ID: tyai_....","type":"policy_violation"}}` |
| The same, streaming | The chunks already sent reach the client, including the one with the token, then `data: {"error":{"message":"Response blocked by policy. ...","type":"policy_violation"}}` and `data: [DONE]` |
| A blocked prompt on the control plane's embedded gateway | `400`, the same block |

So: a request block or redaction acts before the vendor sees anything; a response block on a
non-streaming call replaces the answer; on a stream it ends the stream, and text already
streamed stays with the client. Match on the reason code, not the message text.

Create your own guardrail (built-in pattern library, no network call):

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request POST /api/v1/filters --data '{"data":{"type":"filters","attributes":{
  "name":"Block personal data","description":"Built-in PII detector","kind":"guardrail","response_filter":false,
  "config":{"provider":"builtin","detectors":[{"name":"pii"}],"on_detect":"block","fail_mode":"closed",
            "block_message":"Personal data is not allowed"}}}}'
# 201, with defaults filled in: "scope":"all_user","timeout_ms":2000, redaction placeholder "[REDACTED:{{type}}]"
```

A script filter takes `"kind":"script"` and a base64-encoded Tengo `script`; one whose script
sets `output := {block: ..., payload: "", message: "..."}` is created with `201`. A filter
`PATCH` that sends only `description` keeps the rest. `GET /api/v1/filters/guardrail-providers`
lists the providers in both editions (built-in, Azure AI Content Safety and others).

**Community Edition refuses to create a filter:**

```text
POST /api/v1/filters
403 {"errors":[{"title":"Enterprise Feature","detail":"Filter scripting is an Enterprise feature. Visit https://tyk.io/enterprise for more information."}]}
```

## Compliance events (Enterprise Edition)

Every guardrail match records an event. After the blocks above:

```text
GET /api/v1/compliance/events?start_date=2026-11-01&end_date=2026-11-19&limit=3
200 {"events":[{"id":6,"app_id":1,"app_name":"partner-a","llm_id":3,"filter_name":"Credentials in prompts (block)",
     "filter_scope":"proxy_request","event_type":"guardrail.secrets.github_pat","severity":"critical",
     "description":"guardrail \"Credentials in prompts (block)\" (builtin): GitHub personal access token detected (1) in user; action block",
     "metadata":{"action":"block","reason_code":"guardrail_detected","request_id":"tyai_...","segments":[{"excerpt_status":"off",...,"sha256":"..."}],...}, ...}, ...]}
```

The event carries a hash of the matched segment, not the text. `GET /api/v1/compliance/summary`
gives counts. Community Edition: `GET /api/v1/compliance/available` -> `{"available":false}`,
`GET /api/v1/compliance/events` ->
`403 {"errors":[{"title":"Enterprise Feature","detail":"compliance monitoring is an Enterprise Edition feature - visit https://tyk.io/ai-studio/pricing for more information"}]}`.

## Audit trail (Enterprise Edition)

```text
GET /api/v1/audit/status
200 {"available":true,"enabled":true,"store_type":"db","detailed_recording":false,"record_reads":false,"retention_days":90,"queue_depth":0,"dropped":0}

GET /api/v1/audit/records?page_size=3
200 {"records":[{"id":33,"timestamp":"...","ip":"...","user_id":1,"user":"admin@example.com","auth_method":"api_key",
     "action":"Activate App Credential","method":"POST","url":"/api/v1/apps/4/activate-credential",
     "route":"/api/v1/apps/:id/activate-credential","status":204,"resource_type":"app","resource_id":"4",...},
    {"id":32,...,"action":"Create App","status":201,"diff":{"monthly_budget":{"old":null,"new":"0.1"},"name":{...},...}}, ...]}
```

- Changes, sign-ins (`Login`) and registrations (`Register`) are recorded; reads are not
  (`record_reads` is `false`).
- Secrets are redacted in the diff: creating a secret records
  `"value":{"old":null,"new":"[REDACTED]"}`.
- LLM traffic is not in the audit trail; that is what analytics and proxy logs are for. See
  [Audit trail](https://tyk.io/docs/ai-management/ai-studio/audit-trail).

Community Edition:

```text
GET /api/v1/audit/status   -> 200 {"available":false,"enabled":false,"store_type":"",...}
GET /api/v1/audit/records  -> 403 {"errors":[{"title":"Enterprise Feature","detail":"the audit trail is an Enterprise Edition feature - visit https://tyk.io/ai-studio/pricing for more information"}]}
```

## Analytics and proxy logs (both editions)

The edge records each call and ships the records to the control plane in batches. Calls show
up in these endpoints within about 15 seconds. Query them on the control plane with `start_date` and
`end_date` (`YYYY-MM-DD`); without them:
`400 {"errors":[{"title":"Bad Request","detail":"start_date is required, in YYYY-MM-DD format"}]}`.

```bash
python3 <skill-dir>/scripts/aistudio_admin.py request GET \
  "/api/v1/analytics/token-usage-per-app?start_date=$(date -u +%Y-%m-01)&end_date=$(date -u +%Y-%m-%d)"
# {"labels":["App 1","App 3","App 2"],"data":[200,32,10]}
```

| Endpoint (under `/api/v1/analytics/`) | Extra parameters | Answer (shortened) |
| --- | --- | --- |
| `token-usage-per-app` | none | above |
| `token-usage-and-cost-for-app` | `app_id` | `{"labels":["2026-11-19"],"datasets":[{"label":"Token Usage","data":[27],...},{"label":"Cost","data":[0.225],...}]}` |
| `cost-analysis` | none | `{"USD":{"labels":["2026-11-19"],"data":[0.27]}}` |
| `total-cost-per-vendor-and-model` | none | `[{"model":"alpha-fast","modelPriceId":1,"totalCost":...,"totalTokens":994,"promptTokens":419,"responseTokens":575,"requestCount":60,"appCount":4,...}, ...]` |
| `most-used-llm-models` | none | `{"labels":["alpha-fast","beta-fast"],"data":[36,3]}` |
| `budget-usage` | dates optional | `[{"budget":null,"entity_id":3,"entity_type":"LLM","name":"Stub Alpha","spent":0.27,...}, ...]` |
| `proxy-logs-for-app` | `app_id` | below |

A proxy log row:

```json
{"type":"proxy_log","id":"35","attributes":{"app_id":1,"user_id":1,"llm_id":3,"model_name":"alpha-fast","time_stamp":"2026-11-19T13:29:49.42257136Z","vendor":"openai","request_body":"","response_body":"","response_code":200,"failover_attempt":0,"request_id":"tyai_01a1f3ffca617a20b59cb11d7f5c1ad5"}}
```

`request_id` is the value of the `X-Tyk-AI-Request-ID` header the caller gets. `request_body`
and `response_body` are empty with the evaluation stack's default settings. Cost figures need a
model price that has been pushed (`routing-failover-budgets.md`).

## Prometheus metrics (both editions)

Both the control plane and the edge serve `/metrics`, protected by a bearer token.

```bash
curl -s -H "Authorization: Bearer $METRICS_AUTH_TOKEN" http://localhost:9091/metrics | grep '^aistudio_llm'
```

```text
aistudio_llm_requests_total{app_id="1",model="stub-alpha",status_code="200",vendor="openai"} 30
aistudio_llm_requests_total{app_id="1",model="stub-alpha",status_code="500",vendor="openai"} 2
aistudio_llm_requests_total{app_id="1",model="stub-beta",status_code="200",vendor="openai"} 1
aistudio_llm_failover_total{from_llm="stub-alpha",reason="status_500",to_llm="stub-beta"} 1
aistudio_llm_cost_total{app_id="3",model="alpha-fast",vendor="openai"} 2700
```

(The samples also carry `otel_scope_*` labels, removed here.) Without a token the edge answers
`401`.

Metric families on the edge: `aistudio_llm_requests_total`, `aistudio_llm_tokens_total`,
`aistudio_llm_request_duration_seconds`, `aistudio_llm_inflight_requests`,
`aistudio_llm_failover_total`, `aistudio_llm_cost_total`, the OpenTelemetry-style
`gen_ai_client_token_usage`, `gen_ai_server_request_duration_seconds`,
`gen_ai_server_time_to_first_token_seconds`, `gen_ai_server_time_per_output_token_seconds`, and
`microgateway_*` gauges and counters, among them `microgateway_control_connected`,
`microgateway_config_source`, `microgateway_config_age_seconds`,
`microgateway_analytics_pulse_backlog_bytes` and, with full credential sync,
`microgateway_credentials_synced`. In `aistudio_llm_requests_total` the `model` label holds the
LLM slug, not the model name. Each edge reports only its own traffic, so scrape every edge.

Without `METRICS_AUTH_TOKEN` (and without `METRICS_ALLOW_UNAUTHENTICATED=true`) the endpoint is
not registered. See [Observability](https://tyk.io/docs/ai-management/ai-studio/observability).

For MCP proxies on Tyk Gateway, use the `tyk-mcp-gateway` skill. For observability of Tyk
Gateway APIs, use the `tyk-observability` skill.
