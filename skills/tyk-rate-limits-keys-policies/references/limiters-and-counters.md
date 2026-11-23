Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`) with Redis 7.2

# Which limiter runs, and where the count lives

The limiter is chosen by Gateway configuration, for the whole Gateway, and with the default
configuration it is chosen again for every request from the size of the limit and the number
of Gateways. The same key can therefore be counted in memory at one moment and in Redis at
another. `scripts/which_limiter.py` applies the same rules to your numbers.

Public pages: https://tyk.io/docs/api-management/rate-limit,
https://tyk.io/docs/tyk-oss-gateway/configuration

## The decision

Checked in this order:

| # | Condition | Limiter | Counter |
| --- | --- | --- | --- |
| 1 | `enable_fixed_window_rate_limiter: true` | Fixed window | Redis string `rate-limit-<key hash>/<window start>` |
| 2 | `enable_sentinel_rate_limiter: true` | Redis rolling window with a block key | Redis sorted set `rate-limit-<key hash>` and `rate-limit-<key hash>.BLOCKED` |
| 3 | `enable_redis_rolling_limiter: true` | Redis rolling window | Redis sorted set `rate-limit-<key hash>` |
| 4 | None of those (the default), one Gateway | In-memory | That Gateway's memory |
| 5 | The default, G Gateways on one Redis, limit **above** `drl_threshold` × G requests per second | In-memory, split | Each Gateway's memory, each with 1/G of the limit |
| 6 | The default, G Gateways, limit **at or below** `drl_threshold` × G requests per second | Redis rolling window, or the sentinel variant when `drl_enable_sentinel_rate_limiter: true` | Redis sorted set `rate-limit-<key hash>` (plus `rate-limit-<key hash>.BLOCKED` with the sentinel variant) |

`drl_threshold` defaults to 5. "Requests per second" is `rate / per`. With two Gateways the
boundary is 10 per second: 100 per 10 s goes through Redis, 120 per 10 s is split in memory.

The Gateway logs its mode at start:

| Setting | Log line |
| --- | --- |
| Default | `[RATELIMIT] DRL with Redis Rate Limiter enabled (using transactions)` |
| `enable_redis_rolling_limiter` | `[RATELIMIT] Redis Rate Limiter enabled (using transactions)` |
| `enable_sentinel_rate_limiter` | `[RATELIMIT] Redis Sentinel Rate Limiter enabled (using transactions)` |
| `enable_fixed_window_rate_limiter` | `[RATELIMIT] Fixed Window Rate Limiter enabled` |
| `drl_enable_sentinel_rate_limiter` | `[RATELIMIT] DRL with Redis Sentinel Rate Limiter enabled (using transactions)` |

The text in brackets changes with other rate-limit settings: `(using pipeline)` with
`enable_non_transactional_rate_limiter`, and `, with smoothing` is added with
`enable_rate_limit_smoothing`. Search
the log for `[RATELIMIT]`, not for the whole line.

## Settings

| `tyk.conf` key | Environment variable | Default |
| --- | --- | --- |
| `enable_redis_rolling_limiter` | `TYK_GW_ENABLEREDISROLLINGLIMITER` | `false` |
| `enable_sentinel_rate_limiter` | `TYK_GW_ENABLESENTINELRATELIMITER` | `false` |
| `enable_fixed_window_rate_limiter` | `TYK_GW_ENABLEFIXEDWINDOWRATELIMITER` | `false` |
| `drl_threshold` | `TYK_GW_DRLTHRESHOLD` | 5 (0 means 5) |
| `drl_enable_sentinel_rate_limiter` | `TYK_GW_DRLENABLESENTINELRATELIMITER` | `false` |
| `rate_limit_response_headers` | `TYK_GW_RATELIMITRESPONSEHEADERS` | `quotas` (see `responses-and-headers.md`) |

These are top-level keys in `tyk.conf`. Set the same values on every Gateway that shares a
Redis.

## What each limiter does

### In-memory (the default with one Gateway, or with high limits)

- Nothing is written to Redis: after a burst, Redis holds only the key itself.
- The window starts with the first request and lasts `per` seconds. When it ends the full
  allowance comes back at once.
- A Gateway restart clears the count: a key at its limit of 5 per 60 s gets 5 more straight
  after a restart. The key itself survives, because keys are in Redis.
- With G Gateways each one enforces its own share, `rate / G`. With two Gateways and 120 per
  10 s, each allows 60. Sending every request to one Gateway gets 60, not 120, in every
  window. The share is fixed by the number of Gateways, not by where the traffic goes.
- A Gateway learns about the others through Redis. When the second Gateway stops, the first
  goes on using the Redis counter for about 5 seconds, then returns to memory with a fresh
  count; during that hand-over a fresh key limited to 5 per 10 s can get only 2. When a second
  Gateway starts, a key limited to 5 per minute can get 10 at the moment it comes up (each
  Gateway still counting alone) and exactly 5 from about two seconds later.

### Redis rolling window (`enable_redis_rolling_limiter`, or the default fallback for low limits)

- One counter for every Gateway. A limit of 10 per 60 s sent alternately through two Gateways
  gives exactly ten 200s.
- The counter is a sorted set with one member per request, expiring after `per` seconds.
  Rejected requests are recorded too: after 14 requests against a limit of 10 the set holds 14
  members.
- Because rejected requests count, a client that keeps calling above its limit stays blocked.
  With 5 per 10 s, a client sending one request per second gets 429 for as long as it keeps
  that pace. When it slows to one request every 3 seconds it gets three more 429s, then
  200s. Clients must back off below the limit to recover.
- The count survives a Gateway restart.
- With the setting on, one Gateway also uses Redis, and high limits are exact: 120 of 150
  pass for 120 per 10 s.

### Sentinel (`enable_sentinel_rate_limiter`)

- As the rolling window, plus a key `rate-limit-<key hash>.BLOCKED` that lives for `per`
  seconds once the limit is passed. While it exists every request is rejected.
- With 5 per 10 s: a burst of 10 gives five 200s and five 429s; then one request per second
  gives ten 429s, one 200, and 429s again.

### Sentinel as the fallback (`drl_enable_sentinel_rate_limiter`)

- Changes only row 6 of the decision table: low limits on several Gateways use the sentinel
  variant instead of the plain rolling window. One Gateway, and high limits, still count in
  memory.
- On Gateway 5.16.0 with 5 per 60 s on two Gateways, a fresh key gets four 200s and then
  429s, whether the burst goes through both Gateways or through one; Redis holds
  `rate-limit-<key hash>` and `rate-limit-<key hash>.BLOCKED`. The same burst through one
  Gateway with `enable_sentinel_rate_limiter` instead gives five 200s. Test with the real
  setting before quoting a number.

### Fixed window (`enable_fixed_window_rate_limiter`)

- One Redis counter per window: `rate-limit-<key hash>/<window start in nanoseconds>`, a plain
  number with a time to live. Windows are aligned to the clock, not to the first request: with
  `per` 10 a burst at second 4 falls in the window 0 to 10, and its counter expires with the
  window rather than 10 seconds after the burst.
- The full allowance returns at each boundary, even to a client that keeps calling. With 5 per
  10 s and one request per second: six 429s, then five 200s, then 429s.
- A burst can therefore straddle a boundary: ten requests can pass within 11 seconds against
  a limit of 5 per 10 s.

## Counters for the other limits

- The API-level limit and the per-endpoint limit in the API definition use the same limiter
  as key limits, with a counter per API or per endpoint instead of per key. With two Gateways
  a 3-per-minute endpoint cap gives exactly three 200s across both, through a Redis entry.
- A per-endpoint limit in a key or policy has its own counter per key and endpoint.
- Quotas are always counted in Redis (see `quotas.md`).

## Restarts and outages

| Event | Keys | Rate counters | Quota counters |
| --- | --- | --- | --- |
| Gateway restart | Kept (Redis) | In-memory: reset. Redis: kept | Kept (Redis) |
| A Gateway joins or leaves | Kept | The limiter can switch between memory and Redis within about 5 seconds; the count starts again in the new place | Kept |
| Redis stopped | Not readable once the Gateway's 10-second local copy expires | See below | Not readable: a key or JWT policy with a quota is refused |
| Redis data lost (`FLUSHALL`, or a Redis without persistence restarted) | **Gone** on the open source Gateway: keys exist only in Redis | Gone | Gone |

Redis stopped, one Gateway, in the same words as the `tyk-troubleshooting` skill:
On Gateway 5.16.0 (open source or Dashboard-managed), while Redis is down, an API without
authentication, and a JWT API whose policy has no quota, keep answering `200`; a JWT API whose
policy has a quota answers `403 {"error": "Quota exceeded"}`, each after about 2 seconds; an
auth-token key works only while the Gateway's local copy of its session lasts, at most 10
seconds after the Gateway last read it from Redis, then gets
`403 {"error": "Access to this API has been disallowed"}`, and a key with a quota, or one not
read in the last 10 seconds, is refused from the first request. Within 2 seconds of Redis
returning, every key and token works again, with no restart.

Redis stopped, two open source Gateways sharing a limit low enough to be counted in Redis:

- A key used just before the outage gets one 429 (after about 2 s) while its limit is being
  counted in Redis, a 200 about five seconds later, and 403 after that.
- Any other key gets HTTP 403 `{"error": "Access to this API has been disallowed"}` at once.
- `GET /hello` still answers HTTP 200, with
  `"status":"fail"` and `"redis":{"status":"fail","output":"storage: Redis is either down or was not configured"}`
  in the body. A health check that reads only the status code does not see the outage.
- Creating a key returns HTTP 500 `Failed to create key, ensure security settings are correct.`
- When Redis comes back, the same keys work again without any other action.

After `FLUSHALL` an existing key keeps working for under 11 seconds (the Gateway's key cache),
then gets 403. Policies and API definitions are unaffected: on the open source Gateway they
are files.

What Tyk documents about staying available without Redis, and the conditions, is in the
`tyk-facts` skill; testing it for a decision is the `tyk-poc-runbook` skill.
