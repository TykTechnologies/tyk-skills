# AI Studio module

Last verified: 2026-11-19 against Tyk AI Studio 2.3.0 (Enterprise) and Tyk Gateway and Dashboard 5.16.0

This file covers only what the `ai-studio` module generates, how to run it, what its nine
proofs assert, and the traps specific to it. For how Tyk AI Studio works (architecture, the
edge gateway, routers, guardrails, budgets) use the `tyk-ai-gateway` skill; for licensing and
editions use the `tyk-facts` skill.

Everything below describes a generated bundle running AI Studio 2.3.0 Enterprise beside a
Gateway and Dashboard 5.16.0 bundle, unless a public page is cited.

## What the module generates

AI Studio is a separate product, so the module adds nothing to the bundle's `compose.yaml` and
`scripts/verify-demo.sh` does not cover it. It writes its own Compose project,
`{slug}-aistudio`, under `companion/ai-studio/`:

```
companion/ai-studio/
  compose.yaml            ai-studio (control plane), mgw (edge), llm-alpha and llm-beta (stubs)
  secret.env              licence slot plus minted control-plane/edge secrets (0600, gitignored)
  admin.env               first-run admin login, read by the host scripts only (0600, gitignored)
  confs/                  ai-studio-ent.env, mgw-ent.env, mgw-analytics.yaml
  start.sh                checks secret.env and admin.env, starts the stack, runs configure
  configure-ai-studio.sh  idempotent configuration (below)
  verify-ai-studio.sh     the nine proofs; writes evidence/results.json and validation.md
  proofs/                 tyk-mcp-import.sh, capture-screenshots.sh
  lib/admin.sh, mock-llm/backend.py, keys/
```

- Images: `tykio/tyk-ai-studio-ent` and `tykio/tyk-microgateway-ent`, both at the bundle's
  AI Studio tag. The generator refuses anything from the 2.2 line or older.
  `--ai-studio-version <tag>` sets it explicitly. In latest mode, until a stable 2.3 tag is on
  Docker Hub for both images, the generator uses the newest 2.3 build both carry and prints a
  note naming the tag. `--versions pinned` writes `v2.3.0`; if that tag is not on Docker Hub
  yet, pass `--ai-studio-version` or use latest mode.
- Ports: `aistudio_admin` (admin UI, admin API, `/metrics`), `aistudio_proxy` (not used by the
  proofs) and `aistudio_router` (the edge: every runtime call), all on `127.0.0.1`, from the same
  free block as the Gateway ports.
- The module needs a Tyk AI Studio 2.3 Enterprise licence in `secret.env` as `TYK_AI_LICENSE`.
  Both containers read it and each validates it at startup. The Model Router, guardrail filters,
  budgets, the audit trail and the Tyk Dashboard MCP integration are Enterprise features; LLM
  failover is in both editions
  ([feature comparison](https://tyk.io/docs/ai-management/ai-studio/overview)).

## How to run it

```bash
# 1. Optional, only for AISTUDIO-TYKMCP-001: the Gateway bundle with the mcp module
scripts/start-demo-stack.sh && scripts/bootstrap-demo-stack.sh && scripts/configure-demo.sh

# 2. The companion
cd companion/ai-studio
$EDITOR secret.env               # TYK_AI_LICENSE=<licence>
./start.sh                       # compose up, then configure-ai-studio.sh
./verify-ai-studio.sh            # nine AISTUDIO-* proofs
./configure-ai-studio.sh         # re-run: converges, creates no duplicates
./verify-ai-studio.sh

docker compose down -v && rm -rf data .runtime evidence   # wipe
```

`configure-ai-studio.sh` does, in order: register the first admin (`{Display Name} Administrator`,
`admin@{slug}.invalid`); stop unless `GET /auth/features` reports `edition: enterprise` and
`feature_model_router: true`; create or update two LLMs (`{Display Name} Alpha` and `Beta`,
vendor `openai`, pointing at the stubs); create or update the Model Router `{slug}-router` with
two pools (`gpt-*` to Alpha, renaming `gpt-4o` to `alpha-fast`; `claude-*` to Beta, renaming
`claude-3-5-sonnet` to `beta-deep`) and read the pools back; create a guardrail filter and attach
it to Alpha; give Alpha a failover waterfall to Beta; publish both LLMs and the router in a
catalogue granted to the Default team; create two Apps (the Assistant App with both LLMs, the
router grant and `monthly_budget: null`; the Zero Budget App with Alpha and `monthly_budget: 0`);
wait for a registered edge, then `POST /api/v1/edges/reload-all`; poll until the edge serves the
router and `/v1`; with `mcp`, run the Tyk Dashboard import. App credentials go to
`.runtime/ai-studio.env` (0600).

Every name, slug, pool pattern and model is an environment variable (`LLM_A_NAME`,
`ROUTER_SLUG`, `POOL_A_PATTERN`, `POOL_A_SOURCE_MODEL`, ...). `LLM_A_VENDOR`, `LLM_A_ENDPOINT`,
`LLM_A_API_KEY`, `LLM_A_METADATA_JSON` (and `LLM_B_*`) point the LLMs at real providers with no
file edits; see [LLM management](https://tyk.io/docs/ai-management/ai-studio/llm-management) for
each vendor's fields. With real providers the stub content checks in verify no longer apply.

## The nine proofs

`verify-ai-studio.sh` records `{test_id, status, detail}` for each, with bodies and headers in
`evidence/bodies/`. The results file is deleted at the start and written on exit, so a run that
stops early records every unfinished proof as `failed`. `blocked` never fails the run.

| Proof | Passes when |
| --- | --- |
| `AISTUDIO-ENT-001` | `GET /auth/features` reports `edition: enterprise`, `feature_model_router: true` and a version of 2.3 or later; every flag that is on and `license_days_left` (when reported) are recorded. |
| `AISTUDIO-ROUTER-001` | `POST /router/{slug}-router/v1/chat/completions` sends `gpt-4o` to Alpha as `alpha-fast` and `claude-3-5-sonnet` to Beta as `beta-deep` (content, body `model` and `X-Tyk-Router` checked); `llama-3` is `400`; a router whose slug equals an LLM's route slug is refused `400` with "route name is already used by another LLM or router". |
| `AISTUDIO-UNIFIED-001` | `POST /v1/chat/completions` with `example-corp-alpha/alpha-fast`, `example-corp-beta/beta-deep`, `{slug}-router/gpt-4o` and `{slug}-router/claude-3-5-sonnet` each reach the right stub, with `X-Tyk-Served-LLM`, `X-Tyk-Served-Model`, `X-Tyk-Router`, `X-Tyk-Route` and `X-Tyk-Route-Reason` set; `GET /v1/models` lists all four ids; the Zero Budget App asking for Beta (never granted) gets `403`. |
| `AISTUDIO-BUDGET-001` | The Zero Budget App gets `403` with `"code":"budget_exceeded"` on `/v1` and on `/ai/{llm-slug}/v1/chat/completions` (polled up to 45 s); the stored budgets read back as `0` and `null`. |
| `AISTUDIO-GUARDRAIL-001` | A prompt with an IBAN is refused `400` naming the filter; a clean prompt is served by Alpha; a new `guardrail.pii.iban` compliance event arrives (polled up to 60 s) and does not contain the IBAN. |
| `AISTUDIO-AUDIT-001` | `GET /api/v1/audit/records`, filtered per action (`Create LLM`, `Create Model Router`, `Create App`, `Create Filter`) with `status_class=2xx&sort=asc`, holds a create record for each object by name, by the admin, with a timestamp; the LLM `api_key` diff is `[REDACTED]` and the stub key appears nowhere. |
| `AISTUDIO-METRICS-001` | `/metrics` on the control plane and on the edge returns Prometheus text with `Authorization: Bearer $METRICS_AUTH_TOKEN`, and `401` with no token or a wrong one. |
| `AISTUDIO-TYKMCP-001` | Only with `mcp`: a `catalogue` (read-only) Tyk connection to the bundle's Dashboard is `active`, its last sync is `ok`, and it lists the Dashboard's MCP proxies. `blocked`, with the reason, without `mcp` or when the Dashboard is down. |
| `AISTUDIO-FAILOVER-001` | With `llm-alpha` up, `example-corp-alpha/alpha-fast` is served by Alpha with no failover header; after `docker compose stop llm-alpha` the same call is `200` from Beta as `beta-deep` with `X-Tyk-Failover: true`; after `start` Alpha serves again. An exit trap restarts the stub if the run stops. |

Responses on AI Studio 2.3.0:

| Case | Status and body |
| --- | --- |
| Model no pool claims | `400 {"error":{"code":"invalid_request_error","message":"router '{slug}-router' cannot route model 'llama-3': no route matches the requested model: no pool matches the requested model",...}}` |
| Router slug equal to an LLM's route slug | `400 {"errors":[{"title":"Error","detail":"route name is already used by another LLM or router: the LLM \"Example Corp Alpha\" answers to \"example-corp-alpha\""}]}` |
| LLM not granted to the App | `403 {"status":403,"message":"vendor 'example-corp-beta' not found or not supported by your access rights"}` |
| Zero budget | `403 {"error":{"code":"budget_exceeded","message":"Budget limit exceeded: app monthly budget is 0","type":"permission_error"}}` |
| Guardrail block | `400`, `invalid_request_error`, message starting `[ERROR] msg: policy_violation err: Policy error: <filter name> - <block message>` |
| `/metrics` without or with a wrong bearer | `401 {"error":"unauthorized"}` with `WWW-Authenticate: Bearer` |

## Traps specific to this module

**The edge only changes when the configuration is pushed.** Every runtime call is served by the
`mgw` container. After an admin API change the edge keeps its previous configuration
(`GET /api/v1/edges` shows `sync_status: pending`; a changed LLM endpoint was still in use 60 s
later) until `POST /api/v1/edges/reload-all` answers `202`; seconds later the edge is `in_sync`.
Configure always pushes.

**On a first start the edge may not be registered yet.** The edge exits when it cannot reach the
control plane's gRPC port ("Failed to connect to control server - edge cannot start without
configuration") and Compose restarts it. Until it registers, `reload-all` answers
`409 {"errors":[{"detail":"no edges to push to: no edges are registered in any namespace",...}]}`,
so configure waits for `GET /api/v1/edges` to list an edge before pushing.

**Calls to internal addresses are blocked by default.** The stubs sit on the private Compose
network, so both confs set `LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS=llm-alpha,llm-beta` and nothing
wider. An LLM pointed at any other internal host (here, the stub's own IP) fails with
`502 {"error":{"code":"api_error","message":"upstream endpoint not permitted: upstream host \"10.252.60.3\" is an internal address — blocked by LLM_UPSTREAM_BLOCK_INTERNAL",...}}`.
Add the host name to that list on both confs. The Tyk Dashboard connection is separate: without
`allow_internal_host: true` it is refused `422` ("url is not allowed by the Tyk Dashboard URL
policy: host.docker.internal is an internal hostname ..."); `proofs/tyk-mcp-import.sh` sets it.

**Grant the router, and do not trust a `200`.** An App without the router in
`model_router_ids` still gets `200` from the router, served from the App's own LLMs; the edge
logs "App called a Model Router it has not been granted ... (deprecated; grant the router to the
app)" and `GET /v1/models` leaves the router's ids out. `AISTUDIO-UNIFIED-001` checks the model
list for that reason. The router grant is documented on the
[Model Router page](https://tyk.io/docs/ai-management/ai-studio/model-router).

**Failover is walked on `/v1` and `/ai/`, not on `/llm/call/`.** With `llm-alpha` stopped,
`/ai/example-corp-alpha/v1/chat/completions` returned `200` from Beta with
`X-Tyk-Failover: true`; `/llm/call/example-corp-alpha/v1/chat/completions` returned
`502 {"status":502,"message":"failed to make upstream request",...}`.

**The stubs must answer streaming requests.** AI Studio calls the upstream with
`stream: true` even when the caller did not ask for streaming (the stub logs `stream=True` for
every proof call). `mock-llm/backend.py` answers with SSE and also reads chunked request bodies.
Each stub names itself in the completion (`SERVED-BY=alpha MODEL=alpha-fast`), the body `model`
and an `x-backend-name` header, which is what the routing and failover proofs read.

**Guardrail detectors are categories.** The built-in provider takes `pii`, not `pii.iban`:
`400 {"errors":[{"title":"Invalid filter","detail":"guardrail config: detector \"pii.iban\" is not offered by provider \"builtin\""}]}`.
The module enables `pii` and excludes every pattern except IBAN, payment card and e-mail
([filters](https://tyk.io/docs/ai-management/ai-studio/filters),
[compliance events](https://tyk.io/docs/ai-management/ai-studio/compliance-events)).

**Admin API calls need the cookie and an `Authorization` header.** `lib/admin.sh` sends the
session cookie plus `Authorization: session`. A cookie-only POST answers
`403 Forbidden - CSRF token not found in request`.

**One session per user.** A second login makes the first session cookie answer
`401 {"error":"Unauthorized"}`. Each script keeps its own cookie jar and verify logs in again
after the Tyk MCP import. A browser login during a verify run fails the admin-backed proofs;
run verify again.

**Container user and state directories.** Both images run as uid 65532 with working directories
`/opt/tyk-ai-studio/` and `/opt/tyk-microgateway/`, so relative paths in the confs resolve
there. `start.sh` makes `./data` writable before `docker compose up`.

**A fresh database is not empty.** It already holds two LLMs (`OpenAI`, `Anthropic`) and five
default guardrails, none attached to the module's LLMs, so the module's ids start at 3 (LLMs)
and 6 (the guardrail). Filter the lists for screenshots.

**Admin password rule.** Registration refuses a password without an uppercase letter, a
lowercase letter, a number and a special character (`400`, "password must contain at least one
uppercase letter, one lowercase letter, one number, and one special character"). The generator
mints a compliant one into `admin.env`.

**Telemetry is off in the bundle.** AI Studio sends anonymous usage statistics unless
`TELEMETRY_ENABLED=false` ([telemetry](https://tyk.io/docs/ai-management/ai-studio/telemetry));
`confs/ai-studio-ent.env` sets it and the control plane logs "Telemetry is disabled".

**Secrets stay out of `ps` and `docker inspect`.** `secret.env` is an `env_file` of both
containers; the admin login is in `admin.env`, which only the host scripts source and never
export. Request bodies and bearer tokens reach `curl` on stdin. `start.sh` checks that
`GRPC_AUTH_TOKEN` equals `EDGE_AUTH_TOKEN`, that `MICROGATEWAY_ENCRYPTION_KEY` equals
`ENCRYPTION_KEY` (32 characters) and that `METRICS_AUTH_TOKEN` is set. Remove `secret.env`,
`admin.env`, `.runtime/`, `data/` and `evidence/` before sharing a bundle.

## Limits

- The proofs cover the unified `/v1` ingress, the Model Router with a grant, LLM failover, a zero
  budget, one built-in guardrail with its compliance event, the audit trail, token-protected
  `/metrics` and a read-only Tyk Dashboard MCP import. Nothing else in AI Studio is exercised or
  evidenced by this module; for the rest use the `tyk-ai-gateway` skill.
- The Tyk Dashboard import runs in `catalogue` mode only, which reads from the Dashboard.
- The LLMs are stubs. Real providers need their own credentials, and the failover proof stops a
  stub container, which a real provider does not allow.
- Failover is not applied on `/llm/call/`.

## Public pages

- Overview and edition comparison: https://tyk.io/docs/ai-management/ai-studio/overview
- Model Router: https://tyk.io/docs/ai-management/ai-studio/model-router
- Edge gateway: https://tyk.io/docs/ai-management/ai-studio/proxy
- Filters and guardrails: https://tyk.io/docs/ai-management/ai-studio/filters
- Compliance events: https://tyk.io/docs/ai-management/ai-studio/compliance-events
- Budgets: https://tyk.io/docs/ai-management/ai-studio/budgeting
- LLM management: https://tyk.io/docs/ai-management/ai-studio/llm-management
- Telemetry: https://tyk.io/docs/ai-management/ai-studio/telemetry
- Release notes: https://tyk.io/docs/developer-support/release-notes/ai-studio
