Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`)

# Responses and headers

What a caller receives on Gateway 5.16.0. The examples use the auth token scheme with the key
in the `Authorization` header. The open source Gateway and a Dashboard-managed Gateway return
the same bodies.

Every error body is JSON with one `error` field, served with `Content-Type: application/json`
and `X-Generator: tyk.io`, in this exact layout:

```text
{
    "error": "Rate Limit Exceeded"
}
```

## Status and body

| Situation | Status | `error` value |
| --- | --- | --- |
| Rate limit of the key or its policy, including a per-API or per-endpoint limit in the key or policy | 429 | `Rate Limit Exceeded` |
| Rate limit in the API definition: API level, or an operation's `rateLimit` | 429 | `API Rate Limit Exceeded` |
| Quota used up | 403 | `Quota exceeded` |
| No key sent | 401 | `Authorization field missing` |
| Unknown key | 403 | `Access to this API has been disallowed` |
| Valid key without access to this API | 403 | `Access to this API has been disallowed` |
| Key whose policy was deleted | 403 | `Access to this API has been disallowed` |
| Key or its policy inactive (`is_inactive: true`) | 403 | `Key is inactive, please renew` |
| Key past its `expires` time | 401 | `Key has expired, please renew` |

Two things follow for clients and tests:

- **The body tells which limit fired.** `Rate Limit Exceeded` is the consumer's own limit;
  `API Rate Limit Exceeded` is a limit in the definition that all callers share. A partner who
  reports 429s while well inside their plan may be hitting the shared one.
- **A used-up quota is 403**, the same status as an unknown key. Clients that retry on 429 and
  give up on 403 will treat a quota as an authorisation failure unless they read the body.

To change these bodies or statuses, use error overrides or error templates in the API
definition (https://tyk.io/docs/api-management/custom-error-responses); that belongs to the
`tyk-api-definitions-routing` skill.

## Rate-limit headers

`rate_limit_response_headers` (`TYK_GW_RATELIMITRESPONSEHEADERS`) chooses what the
`X-RateLimit-*` headers describe (available from Tyk 5.13). See
https://tyk.io/docs/api-management/rate-limit.

### `quotas` (the default)

The headers carry the **quota**, on successful responses only:

```text
HTTP/1.1 200 OK
X-Ratelimit-Limit: 3
X-Ratelimit-Remaining: 2
X-Ratelimit-Reset: 1794924814
```

- `Limit` is `quota_max`, `Remaining` what is left of the quota, `Reset` the Unix time the
  quota period ends.
- A key with no quota still gets the headers, with `X-Ratelimit-Limit: -1`,
  `X-Ratelimit-Remaining: 0`, `X-Ratelimit-Reset: 0`.
- A 429 and a 403 `Quota exceeded` carry no `X-RateLimit-*` headers.

In this mode the headers say nothing about the rate limit. A client reading
`X-Ratelimit-Remaining: 0` on a key without a quota is not about to be limited.

### `rate_limits`

The headers carry the **rate limit** of the key, on successful responses and on 429s:

```text
HTTP/1.1 200 OK                  X-Ratelimit-Limit: 5   X-Ratelimit-Remaining: 4   X-Ratelimit-Reset: 1794925096
…
HTTP/1.1 200 OK                  X-Ratelimit-Limit: 5   X-Ratelimit-Remaining: 0   X-Ratelimit-Reset: 1794925106
HTTP/1.1 429 Too Many Requests   X-Ratelimit-Limit: 5   X-Ratelimit-Remaining: 0   X-Ratelimit-Reset: 1794925106
```

- A key with no rate limit gets no headers.
- When a limit in the API definition rejects the request, the headers still describe the
  key: a 429 `API Rate Limit Exceeded` can arrive with `X-Ratelimit-Limit: 100` and
  `X-Ratelimit-Remaining: 95`.
- With the in-memory limiter on several Gateways, `Remaining` is that Gateway's share, not
  the total (https://tyk.io/docs/api-management/rate-limit).

### Both modes

- Go writes the header names as `X-Ratelimit-Limit`; HTTP header names are not case
  sensitive, so clients must not match on case.
- `X-Ratelimit-Reset` is a Unix time in seconds.
- No `Retry-After` header is sent on any 429 or 403. The documentation gives error overrides
  as the way to add a fixed one (https://tyk.io/docs/api-management/rate-limit).

## Seeing the stored state of a key

`GET /tyk/keys/<key>` (open source Gateway) or `GET /api/keys/<key_id>` (Dashboard, under
`data`) returns the session with the limits its policies gave it:

```json
{"rate": 100, "per": 1, "quota_max": 3, "quota_renews": 1794924814, "quota_renewal_rate": 60,
 "apply_policies": ["trial"], "last_updated": "1794924753",
 "access_rights": {"orders": {"versions": ["Default"],
   "limit": {"rate": 100, "per": 1, "quota_max": 3, "quota_renews": 1794924814, "quota_renewal_rate": 60}}}}
```

The stored session shows what the key is entitled to, which is not always what a Gateway is
enforcing at that moment (see "When a change reaches traffic" in
`objects-and-merge-rules.md`). Prove a limit with a burst, not with a readback.
