---
name: tyk-troubleshooting
description: Find why a self-managed Tyk stack (Gateway, Dashboard, Redis, Pump, Developer Portal) fails, from symptom to cause to fix, with real error bodies, health output and log lines, and a script that collects a redacted diagnostic bundle. Use first when the cause is unknown - Tyk "does not work", an API returns 404, 403, 500 or 504, a Gateway loads no APIs, Redis is down, a licence looks invalid, analytics are missing - or for reading Tyk logs. Not for setting up definitions, authentication, limits or monitoring, which the `tyk-api-definitions-routing`, `tyk-authentication`, `tyk-rate-limits-keys-policies` and `tyk-observability` skills own.
---

# Tyk Troubleshooting

The front door for "Tyk is not working". Tyk's health endpoints and status codes mislead in
specific ways: the Gateway's `/hello` says `pass` while every API returns 404, the Dashboard's
and Pump's health endpoints have fixed answers, a valid key is refused as "non-existent" when
Redis is down, and an unlicensed Developer Portal answers its probes with `200`. This skill
carries what each failure really looks like on Gateway and Dashboard 5.16.0, Pump 1.18.0 and
Portal 1.20.1, and the check that tells the causes apart.
Once the cause sits in another skill's area (a definition, a limit, GitOps, MCP), it hands off.

What it needs: shell access to the stack (or its logs), the Gateway secret, and for a
Dashboard-managed stack a Dashboard user API key. Licensing questions go to the `tyk-facts`
skill.

## Ask first

Ask only what the report does not already say. Each question has a default.

| Question | Default |
| --- | --- |
| What exactly fails: which request, what status, body and headers? | Ask for one failing `curl -i`; you need it in step 1 |
| Open source Gateway alone, or managed by a Dashboard? Hybrid (MDCB) or Tyk Cloud? | Find out: a Dashboard-managed Gateway shows a `dashboard` check in `/hello` |
| How is it run: Docker Compose, Kubernetes, VMs? | Docker Compose; for Kubernetes use `kubectl logs` and pass saved logs with `--log-file` |
| Did it ever work? What changed (upgrade, licence, secret, network, new API)? | Assume a recent change and look for it |
| One Gateway or several? | Check every Gateway: they fail independently |
| Is this production? | Yes: no restarts, debug logging or config inspection without the owner's agreement |

## Workflow

Set these once; every command uses them. Keep secrets in variables, never in files.

```bash
SKILL=<this skill's folder>
GW=http://localhost:8080                     # each Gateway in turn
DASH=http://localhost:3000                   # when there is a Dashboard
PORTAL=http://localhost:3001                 # when there is a Developer Portal
export TYK_GW_SECRET=<gateway secret>        # "secret" in tyk.conf
export TYK_DASHBOARD_API_KEY=<Dashboard user API key>
SECRET=$TYK_GW_SECRET; DASHBOARD_API_KEY=$TYK_DASHBOARD_API_KEY
```

1. **Read the failing response, headers included.**

   ```bash
   curl -s -i "$GW/<listen path>/<path>" -H "Authorization: <key, if the API needs one>"
   ```

   `X-Generator: tyk.io` and a JSON `{"error": …}` body: the Gateway refused or failed the
   request; the body names the reason (`references/status-codes.md`). `404` with
   `Content-Type: text/plain` and body `Not Found`: no loaded API matched. The upstream's own
   headers and no `X-Generator`: the upstream answered. Look the body up in
   `references/decoder.md` before anything else.

2. **Check health, and what the Gateway has really loaded.**

   ```bash
   curl -s $GW/hello            # always 200: read "status" and each "details" entry
   curl -s -w ' %{http_code}\n' $GW/ready
   curl -s $GW/tyk/apis -H "x-tyk-authorization: $SECRET" | python3 -c \
     'import sys,json; d=json.load(sys.stdin); print(len(d), [a["api_id"] for a in d])'
   curl -s $DASH/api/system/nodes -H "authorization: $DASHBOARD_API_KEY"
   ```

   `/ready` 503 bodies: `Redis connection not available`; `A successful API reload did not
   happen` (the Gateway never loaded its configuration: secret, Dashboard URL, licence, node
   count); `Gateway is shutting down`. A Dashboard answering `402` is unlicensed;
   `nodes_remaining: 0` means another Gateway will get no APIs.
   `references/health-and-logs.md` says what each endpoint does and does not prove.

3. **Read the logs at the right level.**

   ```bash
   docker logs --since 15m <gateway container> 2>&1 | grep -E 'level=(error|warning)' | sort | uniq -c | sort -rn | head -20
   docker logs --since 15m <gateway container> 2>&1 | grep 'prefix=dashboard'   # registration
   ```

   Look past the startup hints (`control_api_port`, `PIDFile`, `AnalyticsConfig … defaulting`,
   `session lifetime`). The status in `Response failed with code NNN; retrying in 5s` decides
   the cause: 403 node secret, 404 no licence slot. Upstream failures carry `mw=ReverseProxy`
   with the `api_id`. If nothing explains it, set `TYK_GW_LOGLEVEL=debug` (and check
   `TYK_LOGLEVEL`, which overrides it), reproduce once, and read the `Finished … error=` line.

4. **Collect the diagnostic bundle** when the cause is not obvious, or to hand to someone:

   ```bash
   python3 "$SKILL/scripts/collect_diagnostics.py" --gateway $GW --dashboard $DASH \
     --portal $PORTAL --redis <redis host>:6379 --probe "$GW/<listen path>/<path>" \
     --container <gateway> --container <dashboard> --container <pump> --container <portal> \
     --since 15m --out ./tyk-diagnostics
   ```

   It prints findings with the reference to read for each and writes `diagnostics.json` and
   `summary.md`, with secrets removed (`references/diagnostic-bundle.md`).

5. **Confirm the cause with the check in the decoder, then fix one thing at a time.** Use the
   reference for the area: Redis, Gateway-Dashboard link, licences, API not loaded, upstream,
   analytics, configuration precedence. After each change, repeat the failing request and
   steps 2 and 3.

6. **Prove the fix** with the checklist below, and tell the user the cause, the evidence
   (the response or log line before and after) and what you changed.

## References

| File | Read it for |
| --- | --- |
| `references/decoder.md` | Symptom to cause to check to fix, from the response or from a health or log line |
| `references/status-codes.md` | Who sent a response; 404, 400, 401, 403, 429, 500, 504 with exact bodies; JWT clock skew |
| `references/health-and-logs.md` | What each health endpoint proves; log levels, formats and what to grep; debug output |
| `references/redis.md` | Redis lost while running and at start: what keeps working, what fails, recovery |
| `references/control-plane.md` | Gateway cannot register (secret, URL, Dashboard down); Dashboard cannot reach the Gateway |
| `references/licences.md` | Invalid Dashboard or Portal licence, node limit, expiry date |
| `references/api-not-loaded.md` | No reload, listen-path clash, broken file, inactive API, segment tags |
| `references/upstream-errors.md` | Refused, DNS, TLS (CA, expiry, host name), timeouts, slow test backends |
| `references/analytics.md` | Missing analytics: `trafficLogs`, Pump down, records expiring from Redis |
| `references/configuration.md` | File versus environment variables; asking the Gateway what is in force |
| `references/diagnostic-bundle.md` | The collector script: options, findings, redaction, limits |

Script (Python standard library, with tests): `scripts/collect_diagnostics.py`.

## Pitfalls

Each holds on 5.16.0; the reference files hold the detail.

- Gateway `/hello` is HTTP 200 whatever happens and says `pass` for a Gateway that has loaded
  nothing. Use `/ready`, then count the loaded APIs: a segmented Gateway with no matching API
  is "ready" with zero.
- Dashboard `/hello` and Pump `/health` return fixed bodies: the Dashboard's stays `ok` with
  Redis down and with an invalid licence, Pump's with its database unreachable.
- **A valid credential while Redis is down** (`references/redis.md`). On Gateway 5.16.0 (open
  source or Dashboard-managed), while Redis is down, an API without authentication, and a JWT
  API whose policy has no quota, keep answering `200`; a JWT API whose policy has a quota
  answers `403 {"error": "Quota exceeded"}`, each after about 2 seconds; an auth-token key works
  only while the Gateway's local copy of its session lasts, at most 10 seconds after the
  Gateway last read it from Redis, then gets
  `403 {"error": "Access to this API has been disallowed"}`, and a key with a quota, or one not
  read in the last 10 seconds, is refused from the first request. Within 2 seconds of Redis
  returning, every key and token works again, with no restart. The log calls the refused key
  "non-existent".
- A Gateway that starts while Redis or the Dashboard is unreachable serves 404 for every API
  until both are back; a running Gateway keeps its APIs through a Dashboard outage.
- An unlicensed Developer Portal answers `/live` and `/ready` with 200 and an HTML page.
- A refused, unresolvable or failed-TLS upstream gives `500`, not `502`; a timeout gives `504`.
- A Tyk OAS definition created without `middleware.global.trafficLogs` records no analytics;
  imported ones do.
- Records waiting in Redis are deleted about `storage_expiration_time` seconds (default 60)
  after the list gets its expiry, whether or not traffic continues; set `-1` to keep them until
  Pump drains them (watch Redis memory). Details: the `tyk-observability` skill.
- A misspelt environment variable is ignored silently, and `TYK_LOGLEVEL` overrides
  `TYK_GW_LOGLEVEL`.
- A Python `http.server` test backend with Nagle on adds about 40 ms per call, recorded as
  Gateway latency.
- A JWT issued by a clock 2 seconds ahead is refused with `token used before issued`.

## Limitations

Say these plainly when they matter:

- No single endpoint reports overall health: the Gateway's checks cover Redis and its link to
  the Dashboard (or MDCB), not loaded APIs, upstreams, Pump or the analytics store.
- On the open source Gateway reading files, `state.active: false` does not stop an API, and
  segmentation tags did not filter which files were loaded.
- Many causes appear only in logs, several only at `debug`; JWT rejections are not logged at
  the default level.
- The Dashboard logs nothing at `info` when it refuses a Gateway for the wrong node secret or
  the node limit; the Gateway's log is the only place.
- The failures here are those of a deployment with one Redis and Gateways reading files or
  managed by a Dashboard. For MDCB or hybrid links, Redis Sentinel, Cluster or TLS, Tyk Cloud
  and Kubernetes, work from the public documentation and the `tyk-deployment` skill, and tell the
  user which answers come from the documentation and which from their stack.

## Before reporting success

- [ ] The original failing request now gives the expected status and body, shown with headers.
- [ ] `/ready` is 200 on **every** Gateway, and each has loaded the expected number of APIs.
- [ ] On a Dashboard stack, `/api/system/nodes` lists every Gateway and `nodes_remaining` is
      not 0 unless intended; the Dashboard API answers 200, not 401 or 402.
- [ ] No new `level=error` lines in the last 5 minutes of the Gateway, Dashboard and Pump logs.
- [ ] If analytics were the problem, new requests reached the store (rows or the Dashboard's
      log browser), not just Redis.
- [ ] `collect_diagnostics.py` raises no finding you cannot explain.
- [ ] Debug logging and config inspection are switched back off; no secret, key or licence
      value is in a file you leave behind or in the report.
- [ ] The report names the cause, the evidence before and after, the change, and the versions.

## Hand-offs

- Listen paths, path matching, middleware, error overrides, upstream mutual TLS: the
  `tyk-api-definitions-routing` skill.
- 429s, quotas, keys and policies: the `tyk-rate-limits-keys-policies` skill.
- Authentication setup (JWT, OAuth, mutual TLS): the `tyk-authentication` skill.
- MCP proxies: the `tyk-mcp-gateway` skill. AI Studio: the `tyk-ai-gateway` skill.
- Configuration delivered by Tyk Sync or Tyk Operator, including an Operator with a stale
  licence: the `tyk-gitops-sync-operator` skill.
- Metrics, traces and dashboards: the `tyk-observability` skill. Portal configuration: the
  `tyk-developer-portal-setup` skill. Version upgrades: the `tyk-upgrades` skill.
- Licensing, editions and what a lapsed licence means: the `tyk-facts` skill. Installing:
  the `tyk-deployment` skill. A stack to reproduce on: the `tyk-demo-builder` skill. Capacity:
  the `tyk-infra-sizing` skill.
