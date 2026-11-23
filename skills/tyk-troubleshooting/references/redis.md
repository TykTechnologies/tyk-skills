Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`), Tyk Dashboard 5.16.0, Tyk Pump 1.18.0, Redis 7.2

# When Redis is unreachable

## What a valid credential gets while Redis is down

The answer, in the same words as the `tyk-facts`, `tyk-poc-runbook` and
`tyk-rate-limits-keys-policies` skills:

> On Gateway 5.16.0 (open source or Dashboard-managed), while Redis is down, an API without
> authentication, and a JWT API whose policy has no quota, keep answering `200`; a JWT API
> whose policy has a quota answers `403 {"error": "Quota exceeded"}`, each after about 2
> seconds; an auth-token key works only while the Gateway's local copy of its session lasts, at
> most 10 seconds after the Gateway last read it from Redis, then gets
> `403 {"error": "Access to this API has been disallowed"}`, and a key with a quota, or one not
> read in the last 10 seconds, is refused from the first request. Within 2 seconds of Redis
> returning, every key and token works again, with no restart.

The table below holds for one Gateway with hashed keys and the default rate limiter, whether
it is an open source Gateway with the image's own `tyk.conf` or a Gateway managed by Dashboard
5.16.0 with keys created through the Dashboard API. The APIs are Tyk OAS APIs with no
authentication, an auth token, and JWT signed with a shared secret (`signingMethod: hmac`) with
a policy taken from a `pol` claim. Each credential is called once a second; "warm" credentials
were used in the 6 seconds before the outage, "cold" ones (never used) only from the moment
Redis stops.

| Credential | During the outage | First requests | After Redis returns |
| --- | --- | --- | --- |
| None (API without authentication) | `200` throughout | normal | `200` |
| JWT, policy without a quota, token seen before | `200` throughout | normal | `200` |
| JWT, policy without a quota, new subject | `200` throughout | first three take 2.2 to 4.5 s | `200` |
| JWT, policy with a quota (warm or new) | `403 {"error": "Quota exceeded"}` on every request | every response takes about 2.2 s, the first up to 6.9 s | `200` on the first request |
| Auth token, no quota, used every second before | `200` for 4 s, then `403 {"error": "Access to this API has been disallowed"}` | normal | `200` from the second request (1 s) |
| Auth token, no quota, never used | `403 … disallowed` from the first request | first three take 2.2 to 4.5 s, then a few ms | `200` from the second request |
| Auth token with a quota, used every second before | `403 … disallowed` from the first request | first three take 2.2 to 4.5 s | `200` from the second request |
| Auth token with a quota, never used | `403 … disallowed` from the first request | as above | `200` from the second request |

The Gateway logs each refused token as
`level=info message="Attempted access with non-existent key." mw=AuthKey`, and each JWT quota
refusal as `level=error message="error getting key TTL, blocking"` followed by
`Key quota limit exceeded.`

Why:

- The Gateway keeps a key it reads from Redis in a local cache for 10 seconds
  ([`gateway/server.go` line 381](https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/server.go#L381)).
  Using the key does not extend that, which is why a key in use every second still fails
  10 seconds after it was first read. The only setting is
  `local_session_cache.disable_cached_session_state` (`true` turns the cache off). A request
  that changes the session, such as one that counts a quota, writes it back to Redis and drops
  the cached copy ([`gateway/auth_manager.go` line 132](https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/auth_manager.go#L132)),
  so a key with a quota has no cached copy to fall back on.
- A JWT API builds the session from the policy the Gateway holds in memory when Redis has no
  session for the subject
  ([`gateway/mw_jwt.go` line 933](https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/mw_jwt.go#L933)),
  so it needs Redis only for counters.
- A quota check that cannot reach Redis blocks the request
  ([`gateway/session_manager.go` line 492](https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/session_manager.go#L492)).

Two settings change the table. With `hash_key_function: murmur32` instead of the image's
`murmur64`, the warm token key with a quota gets `403 {"error": "Quota exceeded"}` for about 4
seconds before `Access to this API has been disallowed`: still refused from the first request.
A rate limit counted in Redis (several Gateways sharing a low limit, or the Redis rate limiter
switched on) also refuses: the `tyk-rate-limits-keys-policies` skill describes the two-Gateway
case, in which a key gets `429` first.

The trap: a valid key is reported to the caller and in the log as unknown. Before you look for
a missing or revoked key, check the Gateway's `redis` health.

What Tyk documents about running through a Redis outage, and how to test it for a decision:
the `tyk-facts` skill and the `tyk-poc-runbook` skill.

## Health and other components while Redis is down

Same conditions:

| What | Result |
| --- | --- |
| Gateway `/hello` | `pass` for 4 to 20 s, then HTTP 200 with `"redis":{"status":"fail","output":"storage: Redis is either down or was not configured"}` (the output can also be the connection error itself, for example `dial tcp: lookup <redis host> on 127.0.0.11:53: no such host`); overall `"status":"fail"` on the open source Gateway (Redis is its only check), `"status":"warn"` on a Dashboard-managed one (its `dashboard` check still passes) |
| Gateway `/ready` | `503 {"status":"error","message":"Redis connection not available"}` from the same moment |
| Gateway log | `level=error message="storage: Redis is either down or was not configured"` (hundreds per minute), `Connection to Redis failed, reconnect in 10s` prefix=pub-sub, `Redis health check failed` |
| Dashboard `/hello` | `200 {"status": "ok"}`, unchanged |
| Dashboard API with a valid user key | `401 {"Status":"Error","Message":"Not authorised","Meta":null}` |
| Pump log | `level=error message="Error on Purge Loop. Is Temporal Storage down?: dial tcp: …"` |

The delay before `/hello` and `/ready` change is the health check interval
(`liveness_check.check_duration`, 10 seconds by default) plus the time the failing check takes.
After Redis returns, `/hello` and `/ready` go back to `pass` and `200` within one check
interval (2 to 10 s).

## Gateway started while Redis is unreachable

| What | Result |
| --- | --- |
| Every API, with or without authentication | `404 Not Found` (`text/plain`): nothing is loaded |
| `/ready` | `503 Redis connection not available` |
| Gateway log | `level=warning message="Registration deferred (409 with status: Error); retrying in 5s" prefix=dashboard` |

The Dashboard refuses to register a Gateway while it cannot use Redis itself. When Redis came
back, the Gateway registered, loaded its APIs and served them within 6 seconds.

## Checks

```bash
curl -s $GW/hello                         # redis: pass or fail, with output
curl -s -o /dev/null -w '%{http_code}\n' $GW/ready
# from a container on the Gateway's network; the Gateway images have no redis-cli
docker run --rm --network <gateway network> redis:7.2-alpine redis-cli -h <redis host> -p 6379 ping   # PONG
```

The collector script (`scripts/collect_diagnostics.py --redis host:port`) sends `PING` (with
`AUTH` when you name a password variable) and reads the analytics backlog.

Docs: https://tyk.io/docs/planning-for-production/ensure-high-availability/health-check

## Scope

This file covers one standalone Redis. For Redis Sentinel or Cluster,
TLS to Redis and Redis settings, use
https://tyk.io/docs/tyk-configuration-reference/redis-cluster-sentinel and the
`tyk-production-readiness` skill.
