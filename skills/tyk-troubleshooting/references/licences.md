Last verified: 2026-11-19 against Tyk Gateway 5.16.0, Tyk Dashboard 5.16.0, Tyk Developer Portal 1.20.1

# Licence problems, by component

This file covers what a licence problem **looks like** on a running stack. Which components
need a licence, what a licence includes and what the vendor says happens when one lapses
belong to the `tyk-facts` skill; quote it, not this file, for those questions.

| Component | Reads a Tyk licence? | Variable |
| --- | --- | --- |
| Gateway (open source and Enterprise Edition images) | Does not read a licence key at runtime; a Dashboard-managed Gateway depends on the Dashboard's licence for its node slot. Licensing terms: the `tyk-facts` skill | none (`newrelic.license_key`, `TYK_GW_NEWRELIC_LICENSEKEY`, is the New Relic key: [`config/config.go`](https://github.com/TykTechnologies/tyk/blob/v5.16.0/config/config.go#L836-L843)) |
| Dashboard | Yes | `TYK_DB_LICENSEKEY` |
| Developer Portal | Yes | `PORTAL_LICENSEKEY` |
| Pump | No | none |

The invalid-licence results below are for a malformed value (`not-a-licence`). To tell an
expired licence from a malformed one, read its expiry date with `valid_until` (below).

## Dashboard licence invalid

| What | Result (Dashboard 5.16.0) |
| --- | --- |
| `GET $DASH/hello` | `200 {"status": "ok"}`: no signal |
| Dashboard API with a valid user key (`/api/apis`, `/api/system/nodes`) | `402 {"Status":"Error","Message":"Not authorised","Meta":null}` |
| Dashboard UI | Page title `Unlicensed Dashboard` |
| Dashboard log | `level=error message="License error:token is malformed: token contains an invalid number of segments"`, `level=warning message="Licensing: license is invalid"`, `level=warning message="License key is invalid or has expired, please renew"` |
| A Gateway already running | Keeps serving its loaded APIs; `/ready` 200 |
| A Gateway (re)started now | `/hello` `pass`, `/ready` `503 A successful API reload did not happen`, every API 404, log `Response failed with code 404; retrying in 5s` prefix=dashboard |

With a valid licence back in `TYK_DB_LICENSEKEY` and the Dashboard restarted, the waiting
Gateway loads its APIs without a restart.

## Licence node limit reached

The licence allows a number of Gateway nodes (`nodes_available`). With 11 Dashboard-managed
Gateways on a 10-node licence:

```bash
curl -s $DASH/api/system/nodes -H "authorization: $DASHBOARD_API_KEY"
# {"data":{"nodes":[… 10 entries …],"active_node_count":10,"nodes_available":10,"nodes_remaining":0,"valid_until":1796428799},"pages":1}
```

The eleventh Gateway: `/hello` `pass` (dashboard `pass`), `/ready`
`503 {"status":"error","message":"A successful API reload did not happen"}`, every API 404,
log `level=error message="Response failed with code 404; retrying in 5s" prefix=dashboard`.
The Dashboard logs nothing about it. Stopping another Gateway normally frees a slot and the
waiting one loads within about 5 seconds; a Gateway killed without shutdown holds its slot for
about 30 seconds.

Fix: stop Gateways that should not count (old replicas, test containers pointed at the same
Dashboard), or obtain a licence for more nodes (the `tyk-facts` skill has the vendor's
wording; numbers are a commercial question for Tyk).

## Licence expiry date

`valid_until` in `GET /api/system/nodes` is the Dashboard licence's expiry as a Unix time:

```bash
curl -s $DASH/api/system/nodes -H "authorization: $DASHBOARD_API_KEY" | python3 -c \
 'import sys,json,datetime; v=json.load(sys.stdin)["data"]["valid_until"]; print(datetime.datetime.fromtimestamp(v, datetime.timezone.utc).date())'
```

`scripts/collect_diagnostics.py` prints it and raises a finding when fewer than 30 days remain.
Every copy of a licence must be renewed: the Dashboard's, the Portal's, and any other place a
deployment keeps one (for Tyk Operator, the `tyk-gitops-sync-operator` skill).

## Developer Portal licence invalid

| What | Result (Portal 1.20.1) |
| --- | --- |
| Container | Stays up |
| `GET /live`, `GET /ready` | `200` with an **HTML** page (`text/html`), not `{"code":"OK","message":"Success"}` |
| Portal admin API (`/portal-api/...`) with a valid token | `200` with the same HTML page instead of JSON |
| Portal log | At start: `Initialisig license: license error: token contains an invalid number of segments`; on each request: `error … License is invalid:` with a stack trace |

A probe that checks only the status code reports this Portal healthy. Check the content type
or the body:

```bash
curl -s -i $PORTAL/ready | grep -i '^content-type'   # text/plain when healthy, text/html when unlicensed
```

Docs (licensing and packaging): the `tyk-facts` skill; deployment of the licence values:
https://tyk.io/docs/tyk-dashboard/configuration
