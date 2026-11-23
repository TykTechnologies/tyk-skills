# Calling models through the gateway

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`)

The requests and responses below apply to the Community and Enterprise Edition images, with the
stand-in backends in `assets/eval-stack/`. The edge listens on `http://localhost:9091` in that
stack. `$KEY` is an App secret (see `admin-api.md`).

See [Edge Gateway and endpoints](https://tyk.io/docs/ai-management/ai-studio/proxy),
[LLM management](https://tyk.io/docs/ai-management/ai-studio/llm-management).

## Endpoints

| Endpoint | The LLM is chosen by | Format | What the gateway does to the request |
| --- | --- | --- | --- |
| `POST /v1/chat/completions` (unified endpoint, "Main Ingress") | `model`: `{llm-slug}/{model}` or `{router-slug}/{model}` | Chat completions | Rebuilds it (see "The bridge rewrites the request") |
| `GET /v1/models` | the calling App's grants | List | Nothing is sent upstream |
| `POST /v1/completions` | `model`: `{llm-slug}/{model}` | Legacy completions | Answers `200` with the text in `choices[0].text`; its `usage` block comes back as zeros |
| `POST /ai/{llm-slug}/v1/chat/completions` | the URL | Chat completions, plain model name | Rebuilds it |
| `POST /llm/call/{llm-slug}/<vendor path>` | the URL | The vendor's own API | Forwards the body and the caller's headers; swaps in the provider key |
| `POST /llm/rest/{llm-slug}/...` | the URL | The vendor's own API | Behaves like `/llm/call` |
| `POST /anthropic/{llm-slug}/v1/messages` | the URL | Anthropic Messages | Bedrock LLMs only |

The LLM slug is made from the LLM's name: `Stub Alpha` answers to `stub-alpha`. Routers are in
`routing-failover-budgets.md`.

Data sources are a further entry point, `/datasource/`, under the same App grant check
(see [Edge Gateway and endpoints](https://tyk.io/docs/ai-management/ai-studio/proxy)). This
skill does not cover data sources.

The same endpoints answer on the control plane's embedded gateway (port `9090`). Give
applications the edge address.

## Authentication

Send the App secret as a bearer token: `Authorization: Bearer $KEY`.

| Case | Answer |
| --- | --- |
| No credentials | `401 {"status":401,"message":"missing credentials for route"}` |
| A key the control plane does not know, or a deactivated one | `401 {"status":401,"message":"Invalid or expired bearer token"}` |
| The App secret in an `x-api-key` header on a `vendor: openai` LLM | `401 {"status":401,"message":"missing credentials for route"}` |
| The App has been deactivated | `403 {"status":403,"message":"app is inactive"}` |

## The unified endpoint

```bash
curl -s -i http://localhost:9091/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"stub-alpha/alpha-fast","messages":[{"role":"user","content":"hello"}]}'
```

```text
HTTP/1.1 200 OK
Content-Type: application/json
X-Tyk-Ai-Request-Id: tyai_01a1f3fd8a5a756497b60f0e023dcce8
X-Tyk-Served-LLM: stub-alpha
X-Tyk-Served-Model: alpha-fast

{"id":"e938c56a-...","object":"chat.completion","created":1795094842,"model":"alpha-fast","choices":[{"index":0,"message":{"role":"assistant","content":"SERVED-BY=alpha model=alpha-fast echo: hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":4,"total_tokens":5}}
```

`X-Tyk-Served-LLM` and `X-Tyk-Served-Model` name what answered. After a failover the response
also carries `X-Tyk-Failover: true`. `X-Tyk-AI-Request-ID` is the request's id; the same value
is the `request_id` of its proxy log row and compliance events.

Model discovery lists what the calling App is granted, with the prefix:

```json
GET /v1/models
{"object":"list","data":[{"id":"stub-alpha/alpha-deep","object":"model","owned_by":"openai","capabilities":["chat","embeddings","decisions"]},{"id":"stub-alpha/alpha-fast","object":"model","owned_by":"openai","capabilities":["chat","embeddings","decisions"]}]}
```

Granted routers appear too (`routing-failover-budgets.md`).

Streaming: add `"stream": true`. The answer is `Content-Type: text/event-stream` with
`chat.completion.chunk` events, a final chunk that carries `usage`, then `data: [DONE]`.

## Errors a caller sees

| Case | Answer |
| --- | --- |
| Model without a slug prefix on `/v1` | `400 {"error":{"code":"invalid_request_error","message":"model 'alpha-fast' must be in '<vendor>/<model>' format (e.g. 'openai/gpt-4o'); the vendor prefix is the LLM route slug configured on this gateway","type":"invalid_request_error"}}` |
| An LLM the App is not granted, an unknown slug, an inactive LLM, or an LLM that was never pushed to this edge | `403 {"status":403,"message":"vendor 'stub-beta' not found or not supported by your access rights"}` |
| A model outside the LLM's `allowed_models` | `403 {"error":{"code":"permission_error","message":"Model 'other-model' is not allowed","type":"permission_error"}}` |
| The LLM's host is on a private address and not allowed | `502 ... "connection to internal address 10.245.1.4 blocked by LLM_UPSTREAM_BLOCK_INTERNAL"` (`architecture-and-setup.md`) |
| `POST /v1/embeddings`, `GET /ai/{slug}/v1/models` | `404 404 page not found` |
| `/anthropic/{slug}/v1/messages` on a non-Bedrock LLM | `400 {"error":{"message":"the Anthropic Messages bridge only supports Bedrock LLMs, not \"openai\"","type":"invalid_request_error"},"type":"error"}` |

Budget, guardrail and router refusals are in `routing-failover-budgets.md` and
`guardrails-audit-analytics.md`.

## Client SDKs

A client that speaks the chat completions API works by changing the base URL and the key. With
the `openai` Python package, version 3.55.1:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:9091/v1", api_key=KEY)
print(sorted(m.id for m in client.models.list().data))
# ['stub-alpha/alpha-deep', 'stub-alpha/alpha-fast']
r = client.chat.completions.create(model="stub-alpha/alpha-fast",
                                   messages=[{"role": "user", "content": "hello from the SDK"}])
print(r.choices[0].message.content)
# SERVED-BY=alpha model=alpha-fast echo: hello from the SDK
stream = client.chat.completions.create(model="stub-alpha/alpha-fast", stream=True,
                                        messages=[{"role": "user", "content": "stream please"}])
print("".join(c.choices[0].delta.content or "" for c in stream if c.choices))
# SERVED-BY=alpha model=alpha-fast echo: stream please
```

There is no model listing on the per-LLM endpoint (`/ai/{slug}/v1/models` answers `404`), so
clients pointed at `/ai/{slug}/v1` must be told the model names.

## The bridge rewrites the request

`/v1` and `/ai/` are a translation layer, not a pass-through. Everything here applies to
`vendor: openai` LLMs. The upstream receives this for a plain non-streaming call:

```json
{"model":"alpha-fast","messages":[{"role":"user","content":"hello"}],"stream":true,"stream_options":{"include_usage":true},"temperature":0}
```

So, on these two endpoints:

- The upstream is always called with `stream: true` and a chunked request body, also when the
  caller did not ask for streaming. The gateway assembles the answer for a non-streaming caller.
  An upstream (or a stand-in) that cannot stream, or cannot read a chunked body, does not work
  here.
- `temperature` is sent as `0` when the caller leaves it out.
- `max_tokens` is forwarded as `max_completion_tokens`; `max_completion_tokens` as sent.
- Forwarded as sent: `temperature`, `n`, `seed`, `presence_penalty`, `frequency_penalty`,
  `metadata`, `tools`, `tool_choice`, `response_format` of type `json_object`, and `stop` given
  as a string (it arrives as a one-item list).
- Not forwarded: `top_p`, `user`, `logprobs`, `top_logprobs`, `logit_bias`,
  `parallel_tool_calls`, `reasoning_effort`, fields the API does not define, and `stop` given
  as a list.
- Content parts arrive intact: a user message with a `text` part and an `image_url` part
  reaches the upstream with both parts.
- Tool-calling loops work: a conversation with an assistant `tool_calls` message and a
  `"role":"tool"` result reaches the upstream intact, streaming or not, on `/v1` and `/ai/`.
- The provider key replaces the caller's `Authorization` header, and the caller's other headers
  are not forwarded.

## The pass-through endpoint

`/llm/call/{llm-slug}/...` forwards the caller's JSON body unchanged, with the caller's own
headers plus `X-Forwarded-For`, and returns the upstream's body and headers as they are (no
`X-Tyk-Served-*` headers). Sent with `top_p`, `user`, `logit_bias`, `reasoning_effort` and a
made-up `custom_vendor_field`, the upstream receives every field; content parts arrive
intact. Use it for sampling parameters and vendor fields the bridge drops, and for embeddings
(`/v1/embeddings` does not exist):

```bash
curl -s http://localhost:9091/llm/call/stub-alpha/v1/embeddings \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"alpha-fast","input":"hello world"}'
```

```json
{"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}], "model": "alpha-fast", "usage": {"prompt_tokens": 2, "total_tokens": 2}}
```

LLM failover does not apply on `/llm/call` (`routing-failover-budgets.md`). Guardrails and
budgets do (`guardrails-audit-analytics.md`).

## Which vendors

Any service that speaks the chat completions API is configured as `vendor: openai` with its own
`api_endpoint`; the stand-in backends are set up this way. `GET /api/v1/vendors/llm-drivers`
on the control plane lists the drivers of the running version.

How the gateway builds the upstream URL: it sends the request to the LLM's `api_endpoint` plus
the request path and drops the part they share, so `http://llm-alpha:8080/v1` plus
`/v1/chat/completions` reaches the backend as `POST /v1/chat/completions`.
