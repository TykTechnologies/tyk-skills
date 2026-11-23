# Hub and edge: pushes, key checks, outages and replicas

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`)

Pushes, key checks, outages and replicas work the same on the Community and Enterprise images;
full credential sync needs the Enterprise images with a licence.

See [Edge Gateway](https://tyk.io/docs/ai-management/ai-studio/proxy),
[managing edge gateways](https://tyk.io/docs/ai-management/ai-studio/manage-edge-gateway),
[edge gateway variables](https://tyk.io/docs/ai-management/ai-studio/edge-gateway-env),
[installation](https://tyk.io/docs/ai-management/ai-studio/installation/overview).

## How configuration reaches an edge

The edge registers with the control plane over gRPC (`CONTROL_ENDPOINT`), authenticated by the
shared token (`EDGE_AUTH_TOKEN` on the edge, `GRPC_AUTH_TOKEN` on the hub). It receives a
snapshot of the configuration when it starts and again when an administrator pushes.

**A change does not reach an edge until it is pushed.** A new LLM granted to an App answers
`403 vendor 'stub-gamma' not found or not supported by your access rights` on the edge until a
push, and `200` straight after one.

```bash
python3 <skill-dir>/scripts/aistudio_admin.py push
# push accepted (202) Push recorded for 1 edge(s).
# edge edge-01: connected, in_sync
```

The helper pushes, then waits until nothing is pending and every connected edge reports
`in_sync`. It fails when no edge is connected within `--wait` seconds (default 60).

The API underneath:

| Call | Answer |
| --- | --- |
| `GET /api/v1/sync/status` | `{"data":[{"namespace":"default","expected_checksum":"...","pending_count":...,"total_edges":1,...}],"has_pending":...}` |
| `GET /api/v1/sync/pending-changes` | `{"data":{"namespace":"default",...,"total":1,"changes":[{"type":"model_price","id":3,"name":"openai/beta-deep","change":"created",...}],...}}` |
| `POST /api/v1/edges/reload-all` | `202`, `"message":"Push recorded for 1 edge(s)."` |
| `GET /api/v1/edges` | One row per edge: `edge_id`, `namespace`, `version`, `status` (`connected` or `disconnected`), `sync_status` (`in_sync`), `last_heartbeat` |

What needs a push and what does not:

| Change | Needs a push |
| --- | --- |
| A new or changed LLM (endpoint, key, allowed models, failover, filters, active flag), a deleted LLM | Yes. A deleted LLM keeps answering on the edge until the push |
| A new model price | Yes. It is listed in `pending-changes`; calls before the push are recorded at cost `0` |
| A changed LLM grant on an App (`llm_ids`) | Yes. It is listed in `pending-changes`. After a push, an added grant answers `200` and a removed one `403` at once. Without a push, an added grant takes from 21 seconds to about 4 minutes to arrive, and a removed one keeps working |
| A new App with an activated credential, for an LLM the edge already has | No: it is served before any push |
| Deactivating an App or its credential | No: refused within about 2 seconds (below) |

Push after every batch of changes anyway. In standalone mode (one control plane, no edge) there
is no push: see `architecture-and-setup.md`.

## App keys: checked against the hub, cached, revoked at once

An edge checks an App key with the control plane the first time it sees it and keeps the answer
in memory.

| Setting (edge) | Effect |
| --- | --- |
| `EDGE_TOKEN_CACHE_TTL` | How long a checked key is trusted while the hub cannot be reached (`1m` in the examples below) |
| `EDGE_TOKEN_CACHE_STALE_GRACE` | How much longer an expired entry may be used when the hub cannot be reached (unset: none, as the outage table below shows) |

On 2.3 the control plane tells connected edges when a key or an App changes:

- `POST /api/v1/apps/{id}/deactivate-credential`: the key can still answer `200` one second
  later, and answers `401 {"status":401,"message":"Invalid or expired bearer token"}` two seconds
  later.
- `POST /api/v1/apps/{id}/deactivate`: `403 {"status":403,"message":"app is inactive"}` within
  one second.
- Activating them again restores `200` within two seconds.

## When the control plane is unreachable

With the control plane stopped while edges are serving:

| Situation | What the caller gets |
| --- | --- |
| A key the edge checked recently | `200`. The edge keeps serving from its loaded configuration |
| LLM failover | Still works: `200` with `X-Tyk-Failover: true` |
| A key this edge has not seen, or an unknown key | `503` with `Retry-After: 5` and `{"error":{"code":"credential_check_unavailable","message":"The gateway cannot check credentials right now; retry shortly.","type":"api_error"}}` |
| A cached key after its lifetime ran out (`EDGE_TOKEN_CACHE_TTL=1m`, grace `0`, 75 seconds into the outage) | The same `503` |
| The same key on an edge with `EDGE_TOKEN_CACHE_TTL=1m` and `EDGE_TOKEN_CACHE_STALE_GRACE=10m` | `200` |
| `GET /ready` on the edge | `200`, with `"control_connected":false` |
| An edge that restarts during the outage, default settings | It exits: `FTL Failed to connect to control server - edge cannot start without configuration`, and restarts in a loop |
| The control plane comes back | Running edges answer `200` again for keys they had not seen |

### Starting without a control plane

With `EDGE_START_FROM_LAST_KNOWN_GOOD=true` (and `EDGE_STARTUP_CONTROL_TIMEOUT`, `30s` by
default; `5s` is enough for a quick start) an edge restarted during an outage starts from the last
configuration it applied, which it keeps in its own database. It logs
`No control plane answered: serving the last-known-good configuration, a snapshot of 17s ...
Changes made on the control plane since, revoked keys included, do not apply until this edge
reconnects`, `/ready` shows `"config_source":"last_known_good"`, and the metrics show
`microgateway_config_source{source="last_known_good"} 1` and `microgateway_config_age_seconds`.
When the control plane comes back it reports `"config_source":"control"` again.

On Community Edition such an edge validates no key at all until a control plane answers:
every key, including one it served a minute earlier, gets the `503` above. The edge needs a
volume that survives the restart.

### Full credential sync (Enterprise Edition)

With `EDGE_CREDENTIAL_SYNC=full` an Enterprise edge keeps a hashed list of the App keys of its
namespace and validates from it. With Enterprise images on both sides:

- While connected, the edge reports `microgateway_credentials_synced` (the number of keys held)
  and sync calls on `registration` and `heartbeat`.
- With the control plane stopped and the edge restarted (last-known-good on), an App key created
  before the outage and never used on that edge answers `200`; an unknown key gets the `503`.
- `microgateway_credential_validations_total{source="local"}` counts the local validations.

## More than one edge

A second edge is the same image with the same shared secrets, its own `EDGE_ID` and its own
data volume. Both edges show `connected` and `in_sync` in `GET /api/v1/edges`, one push
reaches both (`Push recorded for 2 edge(s).`), and both serve the same Apps. Each edge keeps
its own key cache and its own metrics.

`EDGE_NAMESPACE` groups edges. On Community Edition `GET /api/v1/namespaces` answers
`402 {"errors":[{"detail":"Multi-tenant namespaces require Enterprise Edition","title":"Payment Required"}]}`.
On Enterprise Edition it lists the namespaces (`global` and `default` on a new installation)
with their edge, LLM and App counts.

## More than one control plane replica

Two control plane replicas can run against one PostgreSQL database, on the Community images
too (the `compose.postgres.yaml` override plus a second `studio` service with the same
`secret.env` and the same `CSRF_KEY`), each with its own `CLUSTER_NODE_LABEL`:

- `GET /api/v1/cluster/status` lists both nodes with their labels, which one is the `leader`,
  and how many edges each holds.
- An LLM created on one replica can be read at once on the other; an App created and a push made
  on the second replica reach the edge.
- Give the edge both addresses with `CONTROL_ENDPOINTS=studio:9080,studio2:9080` (instead of
  `CONTROL_ENDPOINT`). It logs
  `Connected to control plane control_endpoint=studio:9080 endpoint_count=2 endpoint_position=1`.
- When the replica holding the edge stops, `/ready` shows
  `"control_connected":true,"control_endpoint":"studio2:9080"` about two seconds later, calls
  keep answering `200`, and an App created on the remaining replica during the outage is served.
- An admin API token minted on one replica works on the other.

Production topology belongs to the `tyk-deployment` skill, sizing to the
`tyk-infra-sizing` skill; the database's own failover is the user's database design.

## Transport security

The evaluation stack uses plain-text gRPC (`GRPC_TLS_INSECURE=true` on the hub,
`EDGE_TLS_ENABLED=false` and `EDGE_ALLOW_INSECURE=true` on the edge). On the control plane
`GRPC_TLS_INSECURE` defaults to `false`, so its gRPC server expects TLS unless told otherwise
(see [control plane variables](https://tyk.io/docs/ai-management/ai-studio/ai-studio-env)). Certificates and production topology are the `tyk-deployment` skill's job.
