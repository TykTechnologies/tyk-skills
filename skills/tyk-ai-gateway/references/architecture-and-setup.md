# Architecture and setup

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`)

The examples use the evaluation stack in `assets/eval-stack/`.

See [overview](https://tyk.io/docs/ai-management/ai-studio/overview),
[installation](https://tyk.io/docs/ai-management/ai-studio/installation/overview),
[quickstart](https://tyk.io/docs/ai-management/ai-studio/quickstart),
[control plane variables](https://tyk.io/docs/ai-management/ai-studio/ai-studio-env),
[edge gateway variables](https://tyk.io/docs/ai-management/ai-studio/edge-gateway-env),
[release notes](https://tyk.io/docs/developer-support/release-notes/ai-studio).

## What it is made of

Tyk AI Studio is a separate product from Tyk Gateway and Tyk Dashboard. It has its own images,
its own database and its own API. The evaluation stack runs it with no Redis, no Tyk Gateway and
no Tyk Dashboard.

| Part | Image | What it does | Container ports |
| --- | --- | --- | --- |
| Control plane ("AI Studio", the hub) | `tykio/tyk-ai-studio` (Community), `tykio/tyk-ai-studio-ent` (Enterprise) | Admin UI, admin API, AI Portal and chat, analytics store, and the gRPC server that edges register with | `8080` UI and API, `9090` embedded gateway, gRPC on `GRPC_PORT` (the stack sets `9080`) |
| Edge gateway ("microgateway" in file and variable names) | `tykio/tyk-microgateway`, `tykio/tyk-microgateway-ent` | The data plane: authenticates the App, applies filters, budgets and routers, proxies to the LLM, fails over, records analytics | `PORT` (the stack sets `9091`) |
| Database | SQLite file (default) or PostgreSQL for the control plane; a local SQLite file on each edge | Control plane state; each edge keeps the configuration it received | none published |

Both Tyk images run at the same tag. This skill is written for AI Studio 2.3. Step 1 of
`SKILL.md` looks the tag up on Docker Hub (the highest 2.3 tag by version order, skipping
unstable builds) and writes it to `.env` as `AISTUDIO_TAG`; as of 2026-11-19 all four images
carry the same 2.3 tag. Compose refuses to start the stack without it:
`required variable AISTUDIO_TAG is missing a value: look up the AI Studio 2.3 tag on Docker Hub (SKILL.md step 1) and set AISTUDIO_TAG in .env`.

Two deployment modes (see [installation](https://tyk.io/docs/ai-management/ai-studio/installation/overview)):

- **Standalone**: one control plane container and nothing else. `GATEWAY_MODE` unset means
  `standalone`. Run this way with a SQLite path, the container logs
  `Server listening on :8080` and `Starting proxy server on port 9090`, and the embedded
  gateway on `9090` serves a new LLM and a new App at once, with no push. There is nothing to
  push to: `POST /api/v1/edges/reload-all` answers
  `503 {"errors":[{"detail":"configuration pushes are not available: the edge control server is not running","title":"Service Unavailable"}]}`.
- **Hub and spoke**: the control plane runs with `GATEWAY_MODE=control`, and one or more edge
  gateways run with `GATEWAY_MODE=edge` and `CONTROL_ENDPOINT=<host>:<grpc port>` (or
  `CONTROL_ENDPOINTS`, a list; see `hub-and-edge.md`). The embedded gateway on `9090` still
  answers and applies a guardrail block and a budget block like the edge, but it does not
  route through Model Routers or Semantic Routers. Give applications the edge address.

The images are distroless and run as uid `65532`. Their working directories
(`/opt/tyk-ai-studio/`, `/opt/tyk-microgateway/`) are not writable by that user, so every
writable path must point at a volume the user owns. The evaluation stack does this with an init
container that runs `chown -R 65532:65532` on two named volumes.

## The evaluation stack

`assets/eval-stack/` holds a Compose project for a workstation: control plane, one edge and two
stand-in LLM backends. Copy it out of the skill folder before use.

```bash
DEST="${TYK_WORKSPACE:-$HOME/tyk-workspace}/<project>/demos/ai-gateway"
mkdir -p "$(dirname "$DEST")" && cp -R <skill-dir>/assets/eval-stack "$DEST" && cd "$DEST"
./make_secrets.sh                        # writes secret.env (mode 0600) with fresh random values
# put AISTUDIO_TAG in .env with the Docker Hub lookup in SKILL.md step 1
docker compose up -d
curl -s http://localhost:8080/auth/features
```

`GET /auth/features` needs no authentication. Use `edition` and `version` to check what is
running before configuring anything. The Community answer starts
`{"build_hash":"...","build_time":"...","edition":"community","features":{"chat_ui_v2":true,...,"feature_agent_tracing":false,...}`
and ends with the `version`. `editions.md` lists the feature flags of each edition.

| File | Purpose |
| --- | --- |
| `compose.yaml` | The stack. Image names, the tag and host ports are variables |
| `compose.postgres.yaml` | Optional override that moves the control plane to PostgreSQL 16 |
| `studio.env`, `edge.env` | Non-secret settings for the control plane and the edge |
| `make_secrets.sh` | Writes `secret.env`, and `POSTGRES_PASSWORD` into `.env` when it is missing; reads an Enterprise licence from `TYK_AI_LICENSE` if set |
| `stub_llm.py` | Stand-in LLM backend (chat completions with streaming, embeddings, models) |

Host ports come from variables, so put overrides in `.env` beside `compose.yaml`:
`ADMIN_PORT` (8080), `EMBEDDED_GATEWAY_PORT` (9090), `EDGE_PORT` (9091), `STUB_ALPHA_PORT`
(9101), `STUB_BETA_PORT` (9102). Everything is published on `127.0.0.1` only.

PostgreSQL instead of SQLite (the password is the `POSTGRES_PASSWORD` that `make_secrets.sh`
put in `.env`; without it Compose stops with `run ./make_secrets.sh first`):

```bash
docker compose -f compose.yaml -f compose.postgres.yaml up -d
docker compose logs studio | grep -i database
# Testing database connectivity (type: postgres)...
# ✅ Database connectivity test passed (postgres)
```

PostgreSQL is what several control plane replicas share (`hub-and-edge.md`).

Enterprise images: set the licence, then choose the images. `read -rs` takes the licence
without showing it and keeps it out of the shell history and the process list (bash and zsh).

```bash
read -rs TYK_AI_LICENSE && export TYK_AI_LICENSE   # paste the licence, then Enter
./make_secrets.sh --force && unset TYK_AI_LICENSE
STUDIO_IMAGE=tykio/tyk-ai-studio-ent EDGE_IMAGE=tykio/tyk-microgateway-ent docker compose up -d
```

`--force` writes the licence and keeps every other value in `secret.env`, because changing
`TYK_AI_SECRET_KEY` on a used database makes the stored keys unreadable (see "Settings that
matter"). AI Studio 2.3 needs a licence issued for 2.3; the start-up messages for a missing or
refused licence are in `editions.md`. How to obtain a licence, and what happens when one lapses,
belong to the `tyk-facts` skill.

## Settings that matter

Control plane (`studio.env` and `secret.env`):

| Variable | Value in the stack | Notes |
| --- | --- | --- |
| `DATABASE_TYPE`, `DATABASE_URL` | `sqlite`, `file:/data/studio.db` | `postgres` with a `postgres://user:pass@host:5432/db?sslmode=disable` URL also works |
| `EXPORT_STORAGE_PATH`, `BRANDING_STORAGE_PATH`, `MARKETPLACE_CACHE_DIR` | under `/data` | Defaults are relative to the read-only working directory; the log warns `is not writable` for each one left at its default |
| `GATEWAY_MODE` | `control` | Unset means `standalone` |
| `GRPC_PORT`, `GRPC_TLS_INSECURE` | `9080`, `true` | Plain-text gRPC, for a private network on a workstation only |
| `GRPC_AUTH_TOKEN` | random | Must equal the edge's `EDGE_AUTH_TOKEN` |
| `MICROGATEWAY_ENCRYPTION_KEY` | random, 32 characters | Must equal the edge's `ENCRYPTION_KEY` |
| `TYK_AI_SECRET_KEY` | random | Encrypts stored secrets, LLM keys and App keys. Keep it for the life of the database: with a new value the App key gets `401 Invalid or expired bearer token`; with the original value back it gets `200` again |
| `LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS` | `llm-alpha,llm-beta` | See "Internal addresses" |
| `SITE_URL`, `PROXY_URL` | host URLs | `PROXY_URL` is the edge address the UI and the API print for Apps and tools |
| `ALLOW_REGISTRATIONS` | `true` | Needed to register the first admin; see below |
| `METRICS_AUTH_TOKEN` | random | Bearer token for `/metrics` on both sides |
| `TELEMETRY_ENABLED`, `LICENSE_DISABLE_TELEMETRY`, `MARKETPLACE_ENABLED` | `false`, `true`, `false` | See "Telemetry" |
| `DOCS_URL_OVERRIDE` | public docs URL | Help links in the UI |

Edge (`edge.env` and `secret.env`):

| Variable | Value in the stack | Notes |
| --- | --- | --- |
| `PORT` | `9091` | Listener |
| `DATABASE_TYPE`, `DATABASE_DSN` | `sqlite`, `file:/data/edge.db?mode=rwc` | Local copy of the configuration |
| `GATEWAY_MODE`, `CONTROL_ENDPOINT`, `EDGE_ID`, `EDGE_NAMESPACE` | `edge`, `studio:9080`, `edge-01`, `default` | The gRPC address, not the UI port |
| `EDGE_TLS_ENABLED`, `EDGE_ALLOW_INSECURE` | `false`, `true` | Match `GRPC_TLS_INSECURE=true` on the hub |
| `EDGE_AUTH_TOKEN`, `ENCRYPTION_KEY` | the hub's pair | `ENCRYPTION_KEY` must be exactly 32 characters |
| `LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS` | `llm-alpha,llm-beta` | Each process applies its own setting, so set it on every edge too |
| `ENABLE_METRICS`, `METRICS_AUTH_TOKEN` | `true`, random | See `guardrails-audit-analytics.md` |

The edge image already points `PLUGINS_CONFIG_PATH` at its bundled analytics pulse file, which is
what sends edge usage to the control plane. Leave it set.

### Internal addresses

AI Studio 2.3 refuses to call an LLM on a private or loopback address unless the host is
allowed. Without the allow list the stand-ins fail on the edge:

```text
502 {"error":{"code":"api_error","message":"failed to make upstream request for streaming: connection to internal address 10.245.1.4 blocked by LLM_UPSTREAM_BLOCK_INTERNAL","type":"api_error"}}
```

The stack names the two stand-ins in `LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS` on the control
plane and the edge; the calls then answer `200`. Add the host name of any LLM, tool or
guardrail service you run on your own network to that list. LLMs and tools also carry an
`allow_internal_host` field in the admin API. `ALLOW_INTERNAL_NETWORK_ACCESS=true` lifts the
block for every host; keep it out of anything shared.

## First admin and API token

The first account registered on an empty database becomes the administrator
(see [quickstart](https://tyk.io/docs/ai-management/ai-studio/quickstart)). Registration is
refused while `ALLOW_REGISTRATIONS` is not `true`.

Passwords need 8 or more characters with an uppercase letter, a lowercase letter, a digit and
a special character. Refusals:

```text
POST /auth/register  ->  400 {"errors":[{"title":"Bad Request","detail":"password must be at least 8 characters long"}]}
POST /auth/register  ->  400 {"errors":[{"title":"Bad Request","detail":"password must contain at least one uppercase letter, one lowercase letter, one number, and one special character"}]}
```

Use the helper script. It keeps the password and the token off the command line.

```bash
export AISTUDIO_URL=http://localhost:8080
export AISTUDIO_ADMIN_PASSWORD='<a compliant password>'
python3 <skill-dir>/scripts/aistudio_admin.py bootstrap --email admin@example.com --name "Example Admin"
# registered admin@example.com
# API token written to .aistudio-token (any earlier token of this user stopped working)
```

What the script does, for callers that cannot run it:

1. `GET /csrf-token`. The token is in the `X-CSRF-Token` response header and a `_gorilla_csrf`
   cookie comes with it. Send both back on every unauthenticated or cookie-authenticated `POST`.
   Without them: `403` with the plain-text body `Forbidden - CSRF token not found in request`.
2. `POST /auth/register` with
   `{"data":{"type":"users","attributes":{"email":"admin@example.com","name":"Example Admin","password":"...","with_portal":true,"with_chat":true}}}`
   returns `201 {"message":"User registered successfully"}`.
3. `POST /auth/login` with `{"data":{"type":"users","attributes":{"email":"...","password":"..."}}}`
   returns `200` with `"is_admin":true` and a `session` cookie.
4. `POST /common/me/api-key/roll` with the cookies and the CSRF header returns
   `200 {"data":{"api_key":"<token>"}}`.

From then on send `Authorization: Bearer <token>` to `/api/v1/...`. A request that carries an
`Authorization` header is not subject to the CSRF check.

After the admin exists, close registration: set `ALLOW_REGISTRATIONS=false` and recreate the
control plane container. A later sign-up then gets
`400 {"errors":[{"title":"Bad Request","detail":"registration is currently disabled"}]}`. While
it is open, anyone who can reach the UI can register an ordinary (non-admin) account; a second
registration answers `201`. The API token keeps working after the container is recreated.

## Health

| Endpoint | Control plane (`8080`) | Edge |
| --- | --- | --- |
| `GET /health` | `200 {"fips":{"enabled":false,...},"status":"ok"}` | `200 {"fips":{...},"service":"microgateway","status":"ok"}` |
| `GET /ready` | `200 {"status":"ready"}` | `200` with `"status":"ready"`, `config_source`, `control_connected`, `control_endpoint` and a `plugin_health` block |

The edge's `/ready` stays `200` while the control plane is down; read `control_connected`
(`false` then) to see whether it reaches one.

## Telemetry

The control plane sends usage statistics to `https://telemetry.tyk.technology` by default
([Telemetry](https://tyk.io/docs/ai-management/ai-studio/telemetry): "enabled by default").
Started with default settings and no network, the Community control plane logs, within a second
of starting:

```text
Warning: Failed to send telemetry data: failed to send telemetry request: Post "https://telemetry.tyk.technology": dial tcp: lookup telemetry.tyk.technology ...
```

Switch it off with `TELEMETRY_ENABLED=false`. The log then says `Telemetry is disabled`.

Two more settings keep an evaluation stack quiet:

- `MARKETPLACE_ENABLED=false` stops the plugin marketplace index sync. The log says
  `Marketplace is disabled via MARKETPLACE_ENABLED=false`. With the marketplace off,
  `GET /api/v1/marketplace/plugins` is not registered and returns the admin UI page.
- `LICENSE_DISABLE_TELEMETRY=true` is the Enterprise licence telemetry switch
  (see [control plane variables](https://tyk.io/docs/ai-management/ai-studio/ai-studio-env)).

## Installing for real

This stack is for evaluation: plain-text gRPC, loopback ports, stand-in backends. For
production or anything long-lived use the `tyk-deployment-planner` skill. For a full demo with proofs and
a Tyk Gateway beside it, use the `tyk-demo-builder` skill.
