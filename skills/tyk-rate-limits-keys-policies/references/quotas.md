Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`) and Tyk Dashboard 5.16.0

# Quotas

A quota is a number of requests per long period (a day, a month) for one key. A rate limit
protects the service second by second; a quota caps total use. Behaviour described here is
that of Gateway 5.16.0.

Public page: https://tyk.io/docs/api-management/request-quotas

## Fields

On a policy or a key:

| Field | Meaning |
| --- | --- |
| `quota_max` | Requests allowed per period. `-1`, `0` or absent: no quota |
| `quota_renewal_rate` | Length of the period in seconds (86400 a day, 2592000 thirty days) |
| `quota_remaining`, `quota_renews` | Written by Tyk on the stored key: what is left and when the period ends (Unix time) |

```json
{"id": "trial", "name": "Trial", "org_id": "default", "active": true,
 "rate": 100, "per": 1, "quota_max": 3, "quota_renewal_rate": 60,
 "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}}
```

A key on this policy:

```text
HTTP/1.1 200 OK        X-Ratelimit-Limit: 3   X-Ratelimit-Remaining: 2   X-Ratelimit-Reset: 1794924814
HTTP/1.1 200 OK        X-Ratelimit-Limit: 3   X-Ratelimit-Remaining: 1   X-Ratelimit-Reset: 1794924814
HTTP/1.1 200 OK        X-Ratelimit-Limit: 3   X-Ratelimit-Remaining: 0   X-Ratelimit-Reset: 1794924814
HTTP/1.1 403 Forbidden {"error": "Quota exceeded"}
```

A used-up quota is HTTP 403, not 429. The `X-RateLimit-*` headers describe the quota by
default (see `responses-and-headers.md`).

## How it is counted

- **Always in Redis**, whatever the rate limiter setting: a plain counter
  `quota-<key hash>` whose time to live is the renewal period. When several policies or
  per-API limits give a key separate quotas, each has its own counter
  (a `per_api` policy produces `quota-orders-<key hash>`, the API id being the scope).
- **Shared by every Gateway** on that Redis, and **kept across a Gateway restart**: a key over
  its quota still gets 403 after a restart, and the counter keeps its remaining time to live.
- **The period starts with the first request**, not when the key is created: a key created at
  second 790 and first used at second 798 with a 20-second period is told the quota resets at
  second 818.
- **The period renews by itself** when it ends: the next request starts a new period.
- **Requests refused by the quota are counted too**: after three allowed requests and one 403
  the counter reads 4. This does not change the result, because the counter is discarded when
  the period ends. Requests refused by the rate limit do not use quota: after two 200s and
  four 429s the counter reads 2.
- **The rate limit is checked first.** A key with 5 per 10 s and a quota of 4 answers
  `200 200 200 200 403 429`: the fifth request is inside the rate and outside the quota, the
  sixth is over the rate. Requests refused by the quota count towards the rate limit.

## Resetting a quota

| Action | Effect |
| --- | --- |
| `PUT /tyk/keys/<key>` (Gateway API) or `PUT /api/keys/<key_id>` (Dashboard API) with the key's body | Quota counter removed; the key has its full quota again |
| The same with `?suppress_reset=1` | Key updated, quota counter kept |
| Wait for the period to end | New period on the next request |
| Delete `quota-<key hash>` in Redis | Full quota again. Use only in a test stack |

The update resets the quota every time, wanted or not. Scripts that update keys for another
reason (a new alias, a new policy) must add `?suppress_reset=1` or they hand every key a fresh
quota.

Changing a key's policy with a `PUT` (a plan upgrade) therefore also resets its quota unless
`suppress_reset=1` is set.

## What quotas do not do

- A quota is per key. One quota shared by several keys of the same customer needs the custom
  rate-limit key feature (`rate_limit_pattern` in policy metadata), described at
  https://tyk.io/docs/api-management/rate-limit.
- There is no per-endpoint quota in the endpoint limits of a key or policy: an `endpoints`
  entry holds `rate` and `per` only.
