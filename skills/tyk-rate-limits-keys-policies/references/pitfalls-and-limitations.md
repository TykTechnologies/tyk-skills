Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), Tyk Dashboard 5.16.0 (`tykio/tyk-dashboard:v5.16.0`) and Redis 7.2

# Pitfalls and limitations

Each item holds on Gateway and Dashboard 5.16.0. The detail is in the file named at the end of
each item.

## Pitfalls: things that fail quietly

1. **Putting the consumer's limit in the API definition.** A `rateLimit` in the Tyk OAS
   definition (API level or on an operation) is one counter for all callers. Two partners with
   different limits need two policies (or two keys with their own limits), not two
   definitions. (`objects-and-merge-rules.md`)
2. **`org_id` missing or different (open source Gateway).** The policy's `org_id`, the key's
   `org_id` and the API's `orgId` must be equal. A policy without `org_id` is saved and listed;
   every key that names it fails with HTTP 500
   `Failed to create key, ensure security settings are correct.`, and only the Gateway log says
   `policy not found`. (`creating-keys-and-policies.md`)
3. **No reload after creating a policy (open source Gateway).** `POST /tyk/policies` answers
   `"status":"ok"` and writes a file; the policy is not in use until
   `GET /tyk/reload/group`. Until then keys that name it are refused with the same HTTP 500.
4. **A wrong API id in `access_rights`.** Accepted by policy and key creation; every request
   then gets HTTP 403 `Access to this API has been disallowed`.
5. **`per` in the wrong unit.** Seconds as a number in keys and policies (`"per": 60`); a
   duration string in the API definition (`"per": "1m"`). The definition refuses a number
   with HTTP 400.
6. **Limits in the key body together with `apply_policies`.** The policies win; the key's own
   `rate`, quota and `access_rights` are replaced.
7. **Expecting the strictest policy to win.** With several policies on a key the most generous
   rate and quota win. To cap a consumer, remove the generous policy; adding a strict one
   changes nothing. (`objects-and-merge-rules.md`)
8. **A changed policy rate that does not arrive (one open source Gateway, policy files).**
   The stored key shows the new rate and the Gateway keeps enforcing the old one for keys
   already in use, because a policy file carries no `last_updated`. Write
   `"last_updated": "<Unix seconds>"` into the policy on every change, or update the keys, or
   restart the Gateway. Policies saved through the Dashboard are stamped for you.
   (`objects-and-merge-rules.md`)
9. **Updating a key resets its quota.** `PUT` on a key, through either API, removes the quota
   counter unless `?suppress_reset=1` is added. (`quotas.md`)
10. **Dashboard `PUT` with the body from `GET`.** The stored `access_rights_array` overrides a
    changed `access_rights`; the call still answers `Data updated`. Remove
    `access_rights_array` first and read the policy back. (`creating-keys-and-policies.md`)
11. **Dashboard policy list cut at ten.** `GET /api/portal/policies/` returns the first 10
    policies only (10 of 13, for example); `?p=-1` returns all. A script that looks for an existing policy on the first
    page misses it, and its create call then fails on the unique name.
12. **Assuming one counter for all Gateways.** By default a limit above `drl_threshold` ×
    Gateways requests per second is split: each Gateway allows its share whatever the traffic
    split, so uneven traffic is limited early (60 of 120). Turn on
    `enable_redis_rolling_limiter` for one exact counter at any rate.
    (`limiters-and-counters.md`)
13. **Assuming the limit survives a restart.** With one Gateway the count is in memory: a
    restart hands every key a full allowance. Quotas survive.
14. **Retry loops against the Redis rolling window.** Rejected requests count, so a client
    that retries faster than its limit never gets through. Clients need back-off.
15. **Reading the headers as the rate limit.** By default `X-RateLimit-*` describe the quota
    and are absent on 429. Set `rate_limit_response_headers` to `rate_limits` if clients
    should see the rate limit. (`responses-and-headers.md`)
16. **Treating 403 as "not authorised" only.** A used-up quota is 403 `Quota exceeded`.
17. **A loose endpoint path.** `"path": "/anything/orders"` in a key's `endpoints` means
    different things per path matching mode. In wildcard mode (a `tyk.conf` without
    `http_server_options`) it also matched `/anything/orders/123` and
    `/anything/x/anything/orders`, on one shared counter; in prefix mode only the first of
    those; in exact mode (the Docker image and Helm chart setting) neither. Anchor it,
    `"^/anything/orders$"`, and it matches one path in every mode.
    (`objects-and-merge-rules.md`)
18. **A health check on the status code of `/hello`.** With Redis down it still answers
    HTTP 200, with `"status":"fail"` in the body, while keyed requests get 403.
19. **Keys kept only in Redis (open source Gateway).** When Redis data is lost the keys are
    gone; policies and APIs, being files, remain. Keep a way to recreate keys.
    (`limiters-and-counters.md`)
20. **Changes are not instant.** A Gateway caches a key for 10 seconds, so an updated or
    deleted key behaves as before for up to 10 seconds.

## Limitations: what the product does not do

- **No exact shared counter by default at higher rates.** The default limiter trades
  exactness for speed above the threshold. Exact counting at any rate costs Redis work on
  every request (`enable_redis_rolling_limiter` or `enable_fixed_window_rate_limiter`).
- **The limiter is a Gateway-wide setting.** It cannot be chosen per API or per key
  (see https://tyk.io/docs/api-management/rate-limit).
- **No `Retry-After` header.** No 429 or 403 carries one. The documentation gives error
  overrides as the way to add a fixed value (same page).
- **The headers describe the quota or the rate limit, not both**, and in `rate_limits` mode a
  429 from a limit in the API definition still shows the key's numbers.
- **The error body does not name the consumer, the limit or the time to wait.** It is
  `Rate Limit Exceeded`, `API Rate Limit Exceeded` or `Quota exceeded`.
- **Per-endpoint limits in a key or policy are rate limits only.** An `endpoints` entry holds
  `rate` and `per`; there is no per-endpoint quota there.
- **Fixed window allows a double burst at a boundary** (ten requests in 11 seconds against 5
  per 10 s), and the in-memory window refills in full when it ends.
- **The open source Gateway reports key and policy errors only in its log.** The API answer
  is the same HTTP 500 for a missing organisation, an unknown policy and a missing reload.
- **Policy management through the Gateway API needs `policy_path`.** With a single policy
  file (`policy_record_name`) `POST /tyk/policies` fails with HTTP 500
  `Failed to create file!`.
- **A Dashboard-managed Gateway refuses policy and API writes on the Gateway API** (HTTP 500
  `Due to enabled service policy source, please use the Dashboard API`).

## Outside this skill

- Authentication methods other than an auth token (JWT, OAuth 2.0, mutual TLS, Basic) and how
  they map a caller to a policy: https://tyk.io/docs/api-management/client-authentication.
  The limits, policies and merge rules here apply once the caller has a session.
- Request throttling, rate-limit smoothing and custom rate-limit keys shared by several
  credentials: https://tyk.io/docs/api-management/request-throttling and
  https://tyk.io/docs/api-management/rate-limit.
- Which components need a licence: the `tyk-facts` skill. Everything in this skill that uses
  the Gateway API works on the open source Gateway with Redis only; the Dashboard API needs a
  licensed Dashboard.
