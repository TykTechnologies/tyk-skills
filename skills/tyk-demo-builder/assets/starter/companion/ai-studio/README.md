# Companion stack: Tyk AI Studio __AISTUDIO_VERSION__ Enterprise

> **This is a SEPARATE PRODUCT and a SEPARATE STACK from the Tyk Gateway demo
> bundle in the parent directory.** Tyk AI Studio has its own licence, its own
> images, its own compose project (`__SLUG__-aistudio`), its own Docker network
> and its own lifecycle. It does not share the bundle's `compose.yaml`, `.env`,
> APIs, upstreams or `scripts/verify-demo.sh`. The only link is optional and
> read-only: when the bundle also selected the `mcp` module,
> `AISTUDIO-TYKMCP-001` imports the Gateway bundle's MCP proxies from its
> Dashboard. Start, stop and reset the two stacks independently.

Images: `tykio/tyk-ai-studio-ent:__AISTUDIO_VERSION__` (control plane, admin UI,
AI Portal) and `tykio/tyk-microgateway-ent:__AISTUDIO_VERSION__` (edge, the data
plane), plus two stdlib Python stub LLM backends. The recipe is written for
AI Studio 2.3; the generator refuses anything from the 2.2 line or older.

## Prerequisite: a Tyk AI Studio 2.3 Enterprise licence

The Model Router, guardrails, budgets, the audit trail and the Tyk Dashboard MCP
integration are Enterprise features
(https://tyk.io/docs/ai-management/ai-studio/overview), so Community Edition
cannot run this demo. Both containers read the licence and each validates it at
startup.

Put the licence in `./secret.env`:

```
TYK_AI_LICENSE=<licence>
```

The generator writes two private files, both gitignored and mode 0600:

| File | Holds | Read by |
| --- | --- | --- |
| `secret.env` | the licence and the minted control-plane/edge secrets | both containers (`env_file`) and the host scripts |
| `admin.env` | `AISTUDIO_ADMIN_EMAIL`, `AISTUDIO_ADMIN_PASSWORD` | the host scripts only, never exported, never an `env_file` |

Keeping the admin login out of `secret.env` keeps it out of the containers'
environments and out of `docker inspect`. The scripts also send every request
body and bearer token to `curl` on stdin, so no credential appears in `ps`.
`secret.env.example` and `admin.env.example` are the committed, empty templates:
never paste a licence or password into them.

`./start.sh` refuses to start without it, and `configure-ai-studio.sh` stops at an
edition gate before creating anything. Licences expire: when `GET /auth/features`
reports `license_days_left`, `AISTUDIO-ENT-001` records it.

## What this proves

| Test ID | What it proves |
| --- | --- |
| `AISTUDIO-ENT-001` | The running build is Enterprise 2.3 or later; the evidence records the licence day count and every feature flag `/auth/features` reports. |
| `AISTUDIO-ROUTER-001` | `POST /router/__SLUG__-router/v1/chat/completions` (the Model Router alias): `gpt-4o` goes to backend alpha as `alpha-fast`, `claude-3-5-sonnet` to backend beta as `beta-deep`; a model no pool claims is `400`, and a router slug equal to an LLM's route slug is refused `400`. |
| `AISTUDIO-UNIFIED-001` | One OpenAI-compatible endpoint on the edge, `POST /v1/chat/completions`: `{llm-slug}/{model}` reaches each backend, `__SLUG__-router/{model}` goes through the router's pools, `GET /v1/models` lists all four ids for the App, the `X-Tyk-Served-LLM` / `X-Tyk-Served-Model` / `X-Tyk-Router` / `X-Tyk-Route` headers name what served it, and an LLM the App was never granted is `403`. |
| `AISTUDIO-FAILOVER-001` | The first LLM carries a failover waterfall to the second. Stop the `llm-alpha` backend: the same call returns `200` served by beta with `X-Tyk-Failover: true`. Start it again: served by alpha. |
| `AISTUDIO-BUDGET-001` | An App with `monthly_budget: 0` is refused with `403` and the OpenAI error envelope `code: budget_exceeded` on `/v1` and `/ai/`; the demo App has `monthly_budget: null` (no limit). |
| `AISTUDIO-GUARDRAIL-001` | Enterprise guardrail filter (built-in pattern library: IBAN, payment card, e-mail; action block) on the first LLM: a prompt with a valid IBAN is blocked with `400` before it reaches the provider, a clean prompt passes, and a `guardrail.pii.iban` compliance event is recorded without the matched text. |
| `AISTUDIO-AUDIT-001` | Enterprise audit trail (on by default): create events for both LLMs, the router, both Apps and the guardrail, with actor, time, IP, auth method and the LLM `api_key` stored as `[REDACTED]`. |
| `AISTUDIO-TYKMCP-001` | Enterprise, only with the `mcp` module: a read-only `catalogue` connection to the Gateway bundle's Tyk Dashboard imports its MCP proxies. `blocked`, with the reason, when `mcp` is not selected or that stack is not running. |
| `AISTUDIO-METRICS-001` | `/metrics` on the control plane and on the edge: a bearer scrape with `METRICS_AUTH_TOKEN` returns Prometheus text; no token and a wrong token are `401`. |

Results land in `evidence/results.json` and `evidence/validation.md`; request and
response bodies and headers in `evidence/bodies/`. Both files are removed at the
start of a run and written on exit, so they always describe the last run: a run
that stops early records every unfinished proof as `failed`, never an earlier
run's pass. `proofs/capture-screenshots.sh`
(optional, needs `agent-browser`) writes admin UI screenshots to
`evidence/screenshots/`.

## What the configuration contains

| Object | Name | Notes |
| --- | --- | --- |
| LLM | `__DISPLAY_NAME__ Alpha` | vendor `openai`, endpoint `http://llm-alpha:8080`, default model `alpha-fast`, guardrail attached, failover to Beta / `beta-deep` |
| LLM | `__DISPLAY_NAME__ Beta` | vendor `openai`, endpoint `http://llm-beta:8080`, default model `beta-deep` |
| Model Router | `__DISPLAY_NAME__ LLM Router` | slug `__SLUG__-router`; pool `openai-family` `gpt-*` -> Alpha (`gpt-4o` -> `alpha-fast`); pool `anthropic-family` `claude-*` -> Beta (`claude-3-5-sonnet` -> `beta-deep`) |
| Filter (guardrail) | `__DISPLAY_NAME__ Payment and Contact Data Guardrail` | provider `builtin`, category `pii` minus every pattern except `pii.iban`, `pii.payment_card`, `pii.email`; `on_detect: block`; request filter |
| LLM catalogue | `__DISPLAY_NAME__ LLM Catalogue` | both LLMs and the router, granted to the Default team (nothing shows in the AI Portal until a catalogue publishes it) |
| App | `__DISPLAY_NAME__ Assistant App` | both LLMs + the router (`model_router_ids`), `monthly_budget: null` |
| App | `__DISPLAY_NAME__ Zero Budget App` | Alpha only, `monthly_budget: 0` |
| Tyk connection | `__DISPLAY_NAME__ Tyk Dashboard` | only with `mcp`: `http://host.docker.internal:__DASHBOARD_PORT__`, `declared_mode: catalogue`, `allow_internal_host: true` |
| Admin user | `__DISPLAY_NAME__ Administrator` | `AISTUDIO_ADMIN_EMAIL` / `AISTUDIO_ADMIN_PASSWORD` in `admin.env` |

A fresh Enterprise database already holds two LLMs (`OpenAI`, `Anthropic`) and
five default guardrails, none attached to the bundle's LLMs. They are left as
shipped, which is why the bundle's ids do not start at 1; the LLM list is
searchable, so filter it for screenshots.

Every name, slug, pool pattern and model is an environment variable on
`configure-ai-studio.sh` (`LLM_A_NAME`, `ROUTER_SLUG`, `POOL_A_PATTERN`,
`POOL_A_SOURCE_MODEL`, `POOL_A_TARGET_MODEL`, `APP_NAME`, `BUDGET_APP_NAME`,
`GUARDRAIL_NAME`, ...); `verify-ai-studio.sh` reads the chosen values back from
`.runtime/ai-studio.env`, so the evidence follows the configuration. A re-run
converges: the LLMs, the guardrail, the Apps and the Model Router (found by
`ROUTER_SLUG`, then `PATCH`ed with its full name, pools and mappings) all take
the new values, and the script reads the router back to confirm. A changed
`ROUTER_SLUG` creates a second router; delete the old one in the admin UI. The
unified path is read from `GATEWAY_UNIFIED_ROUTER_PATH` in `confs/mgw-ent.env`,
which must match `UNIFIED_ROUTER_PATH` in `confs/ai-studio-ent.env`.

## Start / configure / verify / stop

```bash
cd companion/ai-studio

# the generator already wrote secret.env (fresh secrets) and admin.env (admin login); add the licence
$EDITOR secret.env

./start.sh                  # licence gate, state dirs, compose up, configure
./verify-ai-studio.sh       # all nine proofs; writes evidence/results.json and evidence/validation.md
./proofs/tyk-mcp-import.sh  # Tyk MCP import on its own; waits up to 15 min for the Dashboard

./configure-ai-studio.sh    # idempotent: converges the same objects, creates no duplicates
docker compose restart      # everything survives a restart; re-run ./verify-ai-studio.sh

docker compose down                              # stop, keep state
docker compose down -v && rm -rf data .runtime   # stop and wipe all state
```

For `AISTUDIO-TYKMCP-001`, bring the Gateway bundle up first (in the parent
directory: `scripts/start-demo-stack.sh`, `bootstrap-demo-stack.sh`,
`configure-demo.sh`), so its Dashboard holds the MCP proxies. The proof reads the
Dashboard user key and org id from the parent's `.runtime/runtime.env` without
printing them and only ever reads from the Dashboard.

One admin session at a time: every login replaces the user's session token, so a
script run logs a browser session out (and the other way round). Log in again in
the browser after running a script. A browser login during a verify run makes
the admin-backed proofs record `failed`; re-run `./verify-ai-studio.sh`.

On a first start the edge may exit once because the control plane is not
listening yet; compose restarts it and `configure-ai-studio.sh` waits until an
edge is registered before it pushes the configuration.

## URLs and ports (loopback only)

| Port | Service | What lives there |
| --- | --- | --- |
| `__AISTUDIO_ADMIN_PORT__` | `ai-studio` 8080 | Admin UI `http://localhost:__AISTUDIO_ADMIN_PORT__/admin`, AI Portal `/portal`, admin API `/api/v1/...`, `/auth/features`, `/metrics` (bearer) |
| `__AISTUDIO_PROXY_PORT__` | `ai-studio` 9090 | Control-plane embedded gateway (not used by the proofs; it does not route Model Routers) |
| `__AISTUDIO_ROUTER_PORT__` | `mgw` 9091 | **The data plane**: `POST /v1/chat/completions`, `GET /v1/models`, `/router/{slug}/v1/chat/completions`, `/ai/{slug}/v1/...`, `/llm/call/{slug}/...`, `/metrics` (bearer), `/health` |

The generator allocated these from the same checked-free block as the Gateway
bundle's ports, so the two stacks cannot collide. All three are published on
`127.0.0.1` only; change the bindings in `compose.yaml` deliberately if a remote
viewer needs them. The stub backends publish no host ports.

The optional Dashboard MCP import (`AISTUDIO-TYKMCP-001`) dials the Gateway bundle's Dashboard
through `host.docker.internal`. The bundle publishes the Dashboard on loopback
(`BIND_ADDRESS`, `127.0.0.1` by default). Docker Desktop and OrbStack forward that name to the
host's loopback, so the import works as is. On a Linux Docker Engine the name maps to the
bridge gateway, which a loopback bind does not answer: connect the AI Studio container to the
bundle's Compose network (`docker network connect <bundle-network> <ai-studio-container>`,
again after every recreate) and set `TYK_DASHBOARD_URL=http://tyk-dashboard:3000`. Do not widen
`BIND_ADDRESS` for this.

## How it is wired

```
POST /v1/chat/completions  {"model":"{llm-slug}/alpha-fast"}              (mgw, the EDGE)
POST /v1/chat/completions  {"model":"__SLUG__-router/gpt-4o"}
POST /router/__SLUG__-router/v1/chat/completions {"model":"gpt-4o"}        (alias)
        |  auth first; strip the prefix; a router slug resolves to pool + mapping
        v
    /ai/{llm-slug}/v1/chat/completions      (OpenAI bridge; failover waterfall walked here)
        |  loopback, one attempt per rung
        v
    /llm/call/{llm-slug}/v1/chat/completions   (grants, budget, guardrails/filters, analytics)
        |  reverse proxy: chunked body, stream: true
        v
    http://llm-alpha:8080  or  http://llm-beta:8080
```

An LLM's `{llm-slug}` is its name slugified (`__DISPLAY_NAME__ Alpha` becomes the
lower-case, hyphenated form); `configure-ai-studio.sh` prints both slugs.
Configuration is authored on the control plane and pushed to the edge over gRPC
(`GATEWAY_MODE=control` / `edge`, `CONTROL_ENDPOINT=ai-studio:9080`).
`configure-ai-studio.sh` calls `POST /api/v1/edges/reload-all` after every change:
without it the edge keeps serving its previous configuration. Zero budgets take
a short while to reach the edge, and guardrail compliance events reach the hub
on the analytics pulse (`confs/mgw-analytics.yaml`, every 10 s), which is why
the budget and guardrail proofs poll.

Calls to internal addresses are blocked by default, on the control plane and on
the edge. The confs name only the two stub backends in
`LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS`; add any other in-network host you point
an LLM at, or the call fails with `502` "upstream endpoint not permitted".
Usage telemetry is switched off with `TELEMETRY_ENABLED=false`
(https://tyk.io/docs/ai-management/ai-studio/telemetry).

## Point the LLMs at Azure OpenAI or Bedrock (environment variables only)

`configure-ai-studio.sh` reads every provider setting from the environment (or
from `secret.env`, which it sources) and re-applies it on each run. Point the
LLMs at real providers without editing any file:

```bash
# LLM A -> an Azure OpenAI deployment (OpenAI v1-compatible endpoint; the model is the deployment name)
export LLM_A_VENDOR=openai
export LLM_A_ENDPOINT="https://<resource>.openai.azure.com/openai/v1"
# The key itself, or an AI Studio secret reference such as $SECRET/AZURE_OPENAI_KEY
export LLM_A_API_KEY='<azure-openai-key>'
export POOL_A_TARGET_MODEL=<deployment-name>          # also LLM A's default model and the router mapping target

# LLM B -> Amazon Bedrock (region as the endpoint; AWS credentials live in metadata)
export LLM_B_VENDOR=bedrock
export LLM_B_ENDPOINT=eu-central-1
export LLM_B_API_KEY=unused
export LLM_B_METADATA_JSON='{"aws_access_key_id":"$SECRET/AWS_ACCESS_KEY_ID","aws_secret_access_key":"$SECRET/AWS_SECRET_ACCESS_KEY"}'
export POOL_B_TARGET_MODEL=<bedrock-model-id>         # also the failover rung's model

./configure-ai-studio.sh
```

Keep real keys in `secret.env` or in AI Studio secrets, never in a tracked file.
Check the provider settings against
https://tyk.io/docs/ai-management/ai-studio/llm-management and your own
provider account. With real providers the
`SERVED-BY=...` content assertions in `verify-ai-studio.sh` no longer apply (only
the stubs write that): read the `X-Tyk-Served-LLM` / `X-Tyk-Served-Model` headers
instead, and fail the primary for `AISTUDIO-FAILOVER-001` some other way than
`docker compose stop llm-alpha`.

## Why stub backends

Each stub (`mock-llm/backend.py`, stdlib Python) names itself in the completion
text, the `model` field and an `x-backend-name` header, so routing and failover
decisions are visible in every answer. AI Studio calls the upstream with
`stream: true` even when the caller did not ask for streaming (the stub logs
`stream=True`), so the stub answers with an SSE stream; it also reads chunked
request bodies.

## Further reading

- Release notes: https://tyk.io/docs/developer-support/release-notes/ai-studio
- Model Router: https://tyk.io/docs/ai-management/ai-studio/model-router
- Filters and guardrails: https://tyk.io/docs/ai-management/ai-studio/filters
- Compliance events: https://tyk.io/docs/ai-management/ai-studio/compliance-events
- Budgets: https://tyk.io/docs/ai-management/ai-studio/budgeting
- Edge Gateway: https://tyk.io/docs/ai-management/ai-studio/proxy
- LLM management: https://tyk.io/docs/ai-management/ai-studio/llm-management
- Telemetry: https://tyk.io/docs/ai-management/ai-studio/telemetry
