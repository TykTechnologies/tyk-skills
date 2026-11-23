Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0` and `tykio/tyk-gateway:v5.16.0`), Tyk Dashboard 5.16.0, Tyk Pump 1.18.0, Tyk Developer Portal 1.20.1

# Health endpoints and logs

## What each health endpoint proves

| Component | Endpoint | Healthy answer | What it does **not** tell you |
| --- | --- | --- | --- |
| Gateway | `GET /hello` | `200 {"status":"pass","version":"5.16.0","description":"Tyk GW","details":{"dashboard":{...},"redis":{...}}}` | Whether any API is loaded. It answers `pass` while the Gateway has a wrong node secret, is over the licence's node limit, or is unlicensed, and serves `404` for every API |
| Gateway | `GET /ready` | `200`, same body with `"description":"Tyk GW Ready"` | Whether **your** APIs are loaded: a segmented Gateway with no matching API is ready with 0 APIs |
| Dashboard | `GET /hello` | `200 {"status": "ok"}` | Anything: the body is fixed. It stays `ok` with Redis down and with an invalid licence |
| Portal | `GET /live`, `GET /ready` | `200 {"code":"OK","message":"Success"}`, `text/plain` | With an invalid licence both answer `200` with an **HTML page**: check the content type and body, not the status |
| Pump | `GET /health` on port 8083 | `200 {"status": "ok"}` | Anything: it stays `ok` with a pump whose database is unreachable |

All on the versions above.

### Gateway `/hello` and `/ready` in detail

- The checks behind both run every `liveness_check.check_duration` (10 seconds by default), so
  a failure shows up one interval late, plus the time the failing check takes (4 to 20 seconds
  after Redis stops; see `redis.md`); for the first interval after start `/hello`
  is `{"status":"pass",...}` with no `details`.
- `/hello` is always HTTP 200. Read `status`: `pass` when every check passes, `fail` when
  every check fails, `warn` when some fail
  ([`gateway/health_check.go`](https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/health_check.go#L227-L245)).
  An open source Gateway has only the `redis` check, so Redis down gives `"status":"fail"`; a
  Dashboard-managed Gateway also has the `dashboard` check, so Redis down alone gives
  `"status":"warn"`.
  A failing check has an `output`:
  - redis: `storage: Redis is either down or was not configured`, or the connection error itself
    (`dial tcp: lookup <redis host> …: no such host`)
  - dashboard: `dashboard is down? Heartbeat is failing`
- `/ready` returns 503 with one of these bodies:

  | Body | Seen when |
  | --- | --- |
  | `{"status":"error","message":"Redis connection not available"}` | Redis down |
  | `{"status":"error","message":"A successful API reload did not happen"}` | The Gateway has not loaded its configuration once since it started: wrong node secret, wrong Dashboard URL, Dashboard down at start, no licence slot, invalid Dashboard licence |
  | `{"status":"error","message":"Gateway is shutting down"}` | After SIGTERM, for `graceful_shutdown_delay_seconds` while `/hello` and APIs still answer 200 |

- Once a Gateway has loaded its APIs, losing the Dashboard does **not** make `/ready` fail:
  with the Dashboard stopped, `/ready` stays 200 and traffic flows.
- Point load balancers and Kubernetes readiness probes at `/ready`; use `/hello` for the
  per-component detail. Then still check the loaded API count (next section).

Docs: https://tyk.io/docs/planning-for-production/ensure-high-availability/health-check and
https://tyk.io/docs/planning-for-production/ensure-high-availability/graceful-shutdown

## The two reads that settle most cases

```bash
# What the Gateway has actually loaded (works on Dashboard-managed Gateways too)
curl -s $GW/tyk/apis -H "x-tyk-authorization: $SECRET" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(len(d), [a["api_id"] for a in d])'

# Licence nodes and expiry as the Dashboard sees them
curl -s $DASH/api/system/nodes -H "authorization: $DASHBOARD_API_KEY"
# {"data":{"nodes":[{"id":"092b68ba-…","hostname":"gateway-1"}],"active_node_count":1,
#  "nodes_available":10,"nodes_remaining":9,"valid_until":1796428799},"pages":1}
```

`valid_until` is the licence expiry as a Unix time. The node list shows Gateways by hostname,
so give each Gateway a readable hostname.

## Reading logs

Every component writes its application log to standard error, so `docker logs <container>`,
`kubectl logs` or the service manager's journal holds it.

| Component | Level variable | Format variable | Line shape |
| --- | --- | --- | --- |
| Gateway | `TYK_GW_LOGLEVEL` | `TYK_GW_LOGFORMAT` (`text`, `json`, `legacy`) | `time="…" level=error message="…" prefix=main` |
| Dashboard | `TYK_DB_LOGLEVEL` | `TYK_DB_LOGFORMAT` | `time="…" level=warning message="…"` |
| Pump | `TYK_PMP_LOGLEVEL` | `TYK_PMP_LOGFORMAT` | `time="…" level=error message="…" prefix=SQL-pump` |
| Developer Portal | `PORTAL_LOGLEVEL` | `PORTAL_LOGFORMAT` | With `PORTAL_LOGFORMAT=dev`, tab separated: `2026-11-19T13:41:21.141Z	error	…	License is invalid:` |
| All but the Portal | `TYK_LOGLEVEL` | `TYK_LOGFORMAT` | Overrides the per-component level |

Levels are `error`, `warn`, `info` (default) and `debug`. Setting `debug` on the Gateway,
Dashboard and Portal produces debug lines.

Docs: https://tyk.io/docs/api-management/logs

### What to grep for

- Gateway errors: `level=error`. Startup prints a batch of `level=warning` lines that are
  configuration hints, not the fault (`The control_api_port should be changed for production`,
  `Failed to write PIDFile`, `AnalyticsConfig.… defaulting`, `Tyk has not detected any setting
  for session lifetime`). Look past them.
- Registration with the Dashboard: lines with `prefix=dashboard`.
- Upstream failures: `mw=ReverseProxy`, each with `api_id` and `server_name`.
- Rejected keys at the default level: `level=info message="Attempted access with non-existent key."`
  with `mw=AuthKey`. JWT rejections write nothing at `info`.

### At debug level

With `TYK_GW_LOGLEVEL=debug` the Gateway writes a `Started` and a `Finished` line per
middleware per request; the failing one carries the status and the error:

```text
level=debug message=Finished api_id=jwt api_name=jwt code=401 error="Key not authorized: token used before issued" mw=JWTMiddleware ns=827543 org_id=default origin=10.250.50.1 path=/jwt/x type=request
```

For an unknown key it adds `Querying local cache`, `Querying keystore` and
`Could not get session detail, key not found`. Debug output is large; switch it back after
the reproduction.

### Requests that match no API

They are not logged by default. `TYK_GW_TRACK404LOGS=true` writes one `error` line each:

```text
{"host":"localhost:50021","level":"error","message":"Not Found","origin":"10.250.50.1:39618","request":"GET /nothing/here HTTP/1.1","time":"2026-11-19T13:47:39Z"}
```

Docs: https://tyk.io/docs/api-management/logs/application-logs

## Pitfalls proved here

- `TYK_LOGLEVEL=warn` silenced the Gateway's `info` and `debug` output even with
  `TYK_GW_LOGLEVEL=debug`, while `GET /config?field=log_level` still reported `debug`.
- At `warn` or `error` the Gateway's access log and the `Attempted access with non-existent
  key` lines disappear, because they are written at `info`.
- The open source Gateway image `tykio/tyk-gateway` has no shell; `tykio/tyk-gateway-ee` has
  `sh` and `wget` (no `curl`). Probe the open source image from another container.
