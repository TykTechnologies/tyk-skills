Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`) and Tyk Dashboard 5.16.0

# Objects and merge rules

Behaviour described here is that of Gateway and Dashboard 5.16.0.

Public pages: https://tyk.io/docs/api-management/policies,
https://tyk.io/docs/api-management/rate-limit,
https://tyk.io/docs/api-management/access-control/sessions-and-keys/access-rights

## The objects

| Object | What it is | Where it lives |
| --- | --- | --- |
| API definition (Tyk OAS) | The API. It can carry limits that apply to **all callers together** | Files (open source) or the Dashboard database |
| Key | A credential plus its session: who the caller is, which APIs it may call, its limits | Redis |
| Policy | A reusable set of limits and access rights. A key names policies in `apply_policies` | Files (open source) or the Dashboard database |
| Access rights | A map inside a key or policy, one entry per API id the caller may use. An entry can carry a per-API `limit` and per-endpoint `endpoints` limits | Inside the key or policy |

A key is a session object. The fields this skill uses:

```json
{
  "org_id": "default",
  "alias": "partner-a",
  "apply_policies": ["partner-a"],
  "rate": 5, "per": 60,
  "quota_max": 1000, "quota_renewal_rate": 86400,
  "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}},
  "expires": 0,
  "is_inactive": false
}
```

- `rate` requests per `per` seconds. `rate: 0` means no rate limit (30 of 30 requests passed).
- `quota_max` requests per `quota_renewal_rate` seconds. `-1`, `0` or absent means no quota.
- When `apply_policies` is set, the policies supply `rate`, `per`, the quota and
  `access_rights`; values for those fields in the key body are replaced. A key created with
  `rate: 1000` and access to another API plus a 5-per-10 policy got 5 per 10 and a 403 on the
  other API.
- A policy has the same limit fields plus `id`, `name`, `org_id`, `partitions` and
  `is_inactive`.

## Where a limit can be set, and who shares it

| Limit | Set in | Shared by | 429 body |
| --- | --- | --- | --- |
| API level | Tyk OAS `x-tyk-api-gateway.upstream.rateLimit` | every caller of the API | `API Rate Limit Exceeded` |
| Endpoint, for everyone | Tyk OAS `x-tyk-api-gateway.middleware.operations.<operationId>.rateLimit` | every caller of that endpoint | `API Rate Limit Exceeded` |
| Endpoint, per consumer | `access_rights.<api id>.endpoints` in a policy or key | one key | `Rate Limit Exceeded` |
| API, per consumer | `access_rights.<api id>.limit` in a policy (`partitions.per_api`) or key | one key, that API only | `Rate Limit Exceeded` |
| Consumer | `rate` and `per` in a policy or key | one key, across the APIs it covers | `Rate Limit Exceeded` |

The two definition limits take a duration string for `per` (`"1m"`, `"30s"`, `"1h30m"`); the
key and policy limits take seconds as a number. A number or a string without a unit in the
definition is refused with HTTP 400 (`Invalid type. Expected: string, given: integer`, or
`Does not match pattern '^(\d+h)?(\d+m)?(\d+s)?$'`).

Tyk checks them in this order: endpoint limit in the definition, API limit in the definition,
then the key's endpoint limit, per-API limit and global limit (see
https://tyk.io/docs/api-management/rate-limit). For one request by one key, the key side works
like this:

1. If an `endpoints` entry matches the method and path, that limit applies, with its own
   counter. The request does not use up the key's `rate`: a key with `rate: 3` per 10 s and an
   endpoint limit of 5 per 10 s can make 5 endpoint calls and still have 3 calls on another API.
2. Otherwise the per-API `limit` for that API applies, if there is one.
3. Otherwise the key's `rate` and `per` apply.

Endpoint paths in `endpoints` are matched as patterns against the path after the listen path,
and the Gateway's path matching mode (`http_server_options`) changes what an unanchored path
covers. On Gateway 5.16.0, with a key limit on `"/anything/orders"`:

| Mode | `enable_path_prefix_matching` / `enable_path_suffix_matching` | `/anything/orders/123` | `/anything/x/anything/orders` |
| --- | --- | --- | --- |
| Exact (the Docker image's `tyk.conf` and the Helm chart; the lab) | `true` / `true` | not limited | not limited |
| Prefix | `true` / `false` | limited | not limited |
| Wildcard (the code default: a hand-made `tyk.conf` without `http_server_options`) | `false` / `false` | limited, on the same counter as `/anything/orders` | limited, same counter |

`"^/anything/orders$"` matches only `/anything/orders` in every mode, so anchor the path and the
result does not depend on the mode. The method must match: a `GET` rule does not limit `POST`.

## Several policies on one key

On Gateway 5.16.0:

| Policies on the key | Result |
| --- | --- |
| Two plain policies granting the same API (5 per 10 s with quota 10; 20 per 10 s with quota 100) | The more generous value of each wins: 20 per 10 s, quota 100. "More generous" for the rate is the higher requests per second, not the bigger `rate` number |
| Two plain policies granting different APIs (5 per 10 s on `orders`; 8 per 10 s on `payments`) | Each API keeps its own policy's limits and its own counter (5 on one, 8 on the other) |
| One policy with `"partitions": {"per_api": true}` and a `limit` inside each access rights entry | Each API has its own limit and counter (3 per 10 s and 6 per 10 s) |
| Partitioned policies: one with `"partitions": {"acl": true}` and access rights, one with `{"rate_limit": true}` and `rate`/`per`, one with `{"quota": true}` and the quota | The key gets the access rights of the first, the rate of the second, the quota of the third |
| The same, without the quota policy | Access and rate apply; there is no quota |
| A rate-only or quota-only partition without any policy that grants access | The key is refused. Dashboard: HTTP 403 `key has no valid policies to be applied`. Gateway API: HTTP 500 `Failed to create key, ensure security settings are correct.` |
| A `per_api` policy together with a partitioned policy | The key is refused. Gateway log: `cannot apply multiple policies when some have per_api set and some are partitioned` |

A partitioned policy carries only its own part:

```json
{"id": "acl-orders", "name": "ACL orders", "org_id": "default", "active": true,
 "partitions": {"acl": true},
 "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}}
```

```json
{"id": "rate-5", "name": "Rate 5 per 10", "org_id": "default", "active": true,
 "partitions": {"rate_limit": true}, "rate": 5, "per": 10}
```

```json
{"id": "quota-3", "name": "Quota 3 per minute", "org_id": "default", "active": true,
 "partitions": {"quota": true}, "quota_max": 3, "quota_renewal_rate": 60}
```

A key with all three (`"apply_policies": ["acl-orders", "rate-5", "quota-3"]`) answered
`200 200 200 403 403 429 429` to seven quick requests: three inside the quota, two refused by
the quota, then the rate limit of five (requests refused by the quota still count towards the
rate).

A `per_api` policy:

```json
{"id": "per-api", "name": "Per API", "org_id": "default", "active": true,
 "partitions": {"per_api": true},
 "access_rights": {
   "orders":   {"api_id": "orders",   "api_name": "Orders",   "versions": ["Default"],
                "limit": {"rate": 3, "per": 10, "quota_max": -1}},
   "payments": {"api_id": "payments", "api_name": "Payments", "versions": ["Default"],
                "limit": {"rate": 6, "per": 10, "quota_max": -1}}}}
```

For endpoint limits that appear in more than one policy, the more generous limit for each
endpoint wins and endpoints missing from one policy are added.

## When a change reaches traffic

- **Policy changed.** Policies are applied to the key on every request, so new access rights,
  quotas and flags follow the policy. On the open source Gateway the policy file must be
  reloaded first (`GET /tyk/reload/group`).
- **Rate of a policy changed, one Gateway.** The in-memory counter is tied to the key's
  `last_updated` value. The new rate reaches a key that is already in use only when the
  policy's `last_updated` (Unix seconds, as a string) is newer than the key's. The Dashboard
  stamps `last_updated` on every policy save, and the new rate applies within about 12
  seconds. A policy file has no `last_updated` unless you write one: without it the stored key
  shows the new rate while the Gateway keeps enforcing the old one (3 per 10 s is still enforced
  more than 30 seconds after the policy says 6). Set `"last_updated": "<now>"` in the policy file on every
  change, or update each key (`PUT /tyk/keys/<key>`), or restart the Gateway.
- **Rate of a policy changed, counter in Redis.** The new rate applies without touching the key.
- **Key changed.** A Gateway caches each key for 10 seconds: a changed or deleted key keeps its
  old behaviour for up to 10 seconds.
- **Policy made inactive** (`"is_inactive": true`): its keys get HTTP 403
  `Key is inactive, please renew`.
- **Policy deleted while keys still name it:** those keys get HTTP 403
  `Access to this API has been disallowed`, and the Gateway logs `policy not found`.
