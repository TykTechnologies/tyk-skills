Last verified: 2026-11-19 against Tyk Gateway 5.16.0, Tyk Dashboard 5.16.0, Tyk Pump 1.18.0, Tyk Developer Portal 1.20.1, Redis 7.2

# The diagnostic bundle script

`scripts/collect_diagnostics.py` (Python 3 standard library) gathers in one run what the other
reference files check one by one, removes secrets, and prints the likely causes with the
reference file to read for each.

## Run it

Secrets go in environment variables; the script takes the variable **names**:

```bash
export TYK_GW_SECRET=<gateway secret>             # or name another variable with --gateway-secret-env
export TYK_DASHBOARD_API_KEY=<Dashboard user API key>
python3 <skill-dir>/scripts/collect_diagnostics.py \
  --gateway http://localhost:8080 --dashboard http://localhost:3000 --portal http://localhost:3001 \
  --redis localhost:6379 \
  --probe http://localhost:8080/<listen path>/<path> \
  --container tyk-gateway --container tyk-dashboard --container tyk-pump --container tyk-portal \
  --since 15m --out ./tyk-diagnostics
```

Every option is optional, but name at least one target. Repeat `--gateway`, `--redis`,
`--probe` and `--container` as needed. `--log-file` scans a saved log (from `kubectl logs`,
`journalctl` or a support ticket) instead of a container. `--redis-password-env` names the
variable holding the Redis password. `--extra-secret-env` names more variables whose values
must never appear in the output (licences, database passwords).

Redis must be reachable from where the script runs. When it is only on a container network,
publish a port for the session or run the script from a container on that network.

## What it collects

| Target | Reads |
| --- | --- |
| Each Gateway | `/hello` (status, version, per-component checks), `/ready`, and with the secret `/tyk/apis` (count and ids of loaded APIs) |
| Dashboard | `/hello`, and with the API key `/api/system/nodes` (nodes used and available, licence expiry date) and `/api/apis?p=-1` (APIs and how many are active) |
| Portal | `/live` and `/ready`, with status and content type |
| Pump | The health URL you pass (it only shows the process is up) |
| Redis | `PING` (after `AUTH` when a password variable is named) and `LLEN analytics-tyk-system-analytics` twice, `--backlog-gap` seconds apart |
| Probes | Status, content type, `X-Generator` and body of each URL |
| Containers | Image, state and restart count from `docker inspect`; error and warning lines from `docker logs --since`, timestamps removed, counted, most frequent first |

It writes `diagnostics.json` and `summary.md` to `--out` and prints the summary. Exit status:
0 with no finding, 1 with at least one, 2 on a usage error.

## Findings it raises

Each matches a real failure signature: a `/ready` message (Redis, no reload,
shutting down), a failing `/hello` check, a Gateway with no APIs or fewer than the Dashboard
lists as active, a refused Gateway secret, Dashboard `402` or `401`, no node slot left, a
licence ending within 30 days, a Portal answering HTML on `/ready`, Redis unreachable, an
analytics backlog that is not shrinking, a probe answered by the no-API `404`, a container
that restarted, and these log signatures:

| Log text | Finding |
| --- | --- |
| `Response failed with code 403` | Node secret mismatch |
| `Response failed with code 404` | No licence slot |
| `/register/node` | Gateway cannot reach the Dashboard URL |
| `Registration deferred (409` | Dashboard cannot use Redis |
| `Redis is either down or was not configured` | A component cannot reach Redis |
| `Is Temporal Storage down?` | Pump cannot reach Redis |
| `Pump init error (skipping)` | A pump's store is unreachable |
| `License key is invalid or has expired` | Dashboard licence |
| `License is invalid` | Portal licence |
| `Listen path collision` | Two APIs on one listen path |
| `Couldn't unmarshal read file` | Broken definition file |
| `Attempted administrative access with invalid or missing key` | Wrong Gateway secret used by a caller |
| `x509:` | Upstream TLS verification |
| `proxy error: dial tcp` | Upstream unreachable |
| `timeout awaiting response headers` | Upstream slower than the timeout |

A finding is a lead, not a verdict: confirm it with the check in `decoder.md`.

## Redaction

Removed from every string before anything is written or printed: the values of the variables
named by `--gateway-secret-env`, `--dashboard-key-env`, `--redis-password-env` and
`--extra-secret-env`; JWTs and licence strings; `Authorization`, `x-tyk-authorization` and
cookie values; `password=`, `secret=`, `token=`-style pairs; credentials inside URLs; and any
unbroken run of 32 or more letters and digits (keys, hashes). Node ids (UUIDs) and log text
stay readable. A 32-character API id is redacted too; read ids from the Gateway directly if
you need them.

The written bundle contains none of the stack's secret values (Gateway and admin secrets,
Dashboard API key, database password, licences, Portal token, consumer keys). Still read
`summary.md` before you attach it anywhere.

## Limits

- Redis over TLS, Sentinel and Cluster are not supported; it speaks plain RESP to one address.
- Reading container logs needs the `docker` command; for Kubernetes save `kubectl logs` output
  and pass it with `--log-file`.
- The Pump and Dashboard health endpoints have fixed answers, so the script reports them but
  never trusts them.
