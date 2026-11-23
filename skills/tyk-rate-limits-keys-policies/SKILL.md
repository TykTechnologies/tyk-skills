---
name: tyk-rate-limits-keys-policies
description: Give each API consumer (partner, team, plan) its own rate limit and quota on Tyk using keys and policies, on the open source Gateway alone or with a Dashboard, and explain how the limit is counted with one Gateway and with several. Use when asked for per-partner, per-key, per-plan or per-endpoint rate limits, quotas, API keys, security policies, tiers, 429, "Quota exceeded" or "Access to this API has been disallowed" responses, or why a rate-limit test gives odd counts. Not for writing the API definition itself, which is the `tyk-api-definitions-routing` skill.
---

# Tyk Rate Limits, Keys and Policies

A per-consumer limit on Tyk does not go in the API definition. It goes on the consumer's
**key**, usually through a **policy** the key names. Agents asked for "partner A 100 a second,
partner B 10" tend to write one limit into the API definition, which gives every caller the
same shared counter, or to miss the organisation id and the reload, which makes key creation
fail with an error that names neither. This skill carries the request bodies, the responses
and the limiter behaviour of Tyk Gateway 5.16.0 and Dashboard 5.16.0.

What it needs: the recipes on the Gateway API run on the open source Gateway with Redis and no
licence. The Dashboard API needs a licensed Dashboard. For what is open source and what is
licensed, use the `tyk-facts` skill.

## Ask first

Ask only what the request does not already answer. Each question has a default.

| Question | Default |
| --- | --- |
| Is there a Dashboard, or the open source Gateway alone? | Find out: a Gateway with `use_db_app_configs` on (or `POST /tyk/policies` answering "please use the Dashboard API") is Dashboard-managed |
| Who are the consumers, and what are the numbers (requests per window, quota per period)? | One policy per plan or partner; no quota unless asked |
| Is the limit per consumer, or one cap for everybody? | Per consumer. A cap for everybody goes in the API definition |
| Does any endpoint need its own limit, and for one consumer or for all? | None |
| How many Gateways share the Redis, and must the limit be exact across them? | Ask. It decides which limiter counts (step 5) |
| Should clients see rate-limit headers? | Leave the default (headers describe the quota) and say so |
| Is configuration kept in Git or Kubernetes? | No. If yes, hand the writing to the `tyk-gitops-sync-operator` skill and keep the object shapes from here |

## Workflow

Set these once; every command below uses them. Keep secrets in variables, never in files.

```bash
SKILL=<this skill's folder>          # holds scripts/ and assets/
GW=http://localhost:8080             # Gateway
SECRET=<gateway secret>              # "secret" in tyk.conf, or TYK_GW_SECRET
DASH=http://localhost:3000           # Dashboard, when there is one
DASHBOARD_API_KEY=<Dashboard user API key>
```

1. **Find the API id and the organisation id.** Open source Gateway: both are in the Tyk OAS
   definition (`x-tyk-api-gateway.info.id` and `.orgId`); the id is the map key you will use in
   `access_rights`, and the organisation id must be repeated on every policy and key. If the
   definition has no `orgId`, add `"orgId": "default"`, `PUT` it back and reload before any key
   exists, or leave `org_id` out of every policy and key; key creation is refused when the
   key's or its policy's `org_id` differs from the API's
   (`references/creating-keys-and-policies.md`). Dashboard: the API id is in the API's
   definition; the Dashboard fills in the organisation.
   A per-consumer limit belongs to a key, so the API must authenticate its callers.
   The recipes use an auth token in the `Authorization` header; other methods are listed in
   `references/pitfalls-and-limitations.md`, "Outside this skill".

2. **Create one policy per consumer or plan.** `rate` requests per `per` seconds, and
   optionally `quota_max` requests per `quota_renewal_rate` seconds (`-1` for no quota).

   Open source Gateway (`x-tyk-authorization` is the Gateway secret):

   ```bash
   curl -s -X POST "$GW/tyk/policies" -H "x-tyk-authorization: $SECRET" \
     -H 'Content-Type: application/json' -d '{
       "id": "partner-a", "name": "Partner A", "org_id": "default", "active": true,
       "rate": 5, "per": 60, "quota_max": -1,
       "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}}'
   # {"key":"partner-a","status":"ok","action":"added"}
   curl -s "$GW/tyk/reload/group" -H "x-tyk-authorization: $SECRET"
   # {"status":"ok","message":""}
   ```

   The reload is required: until it runs the policy is a file the Gateway has not read.

   Dashboard (`authorization` is a Dashboard user's API key; no `org_id`, no reload):

   ```bash
   curl -s -X POST "$DASH/api/portal/policies/" -H "authorization: $DASHBOARD_API_KEY" \
     -H 'Content-Type: application/json' -d '{
       "id": "partner-a", "name": "Partner A", "active": true,
       "rate": 5, "per": 60, "quota_max": -1,
       "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders", "versions": ["Default"]}}}'
   # {"Status":"OK","Message":"<database id>","Meta":null}
   ```

3. **Create one key per consumer, naming its policy.**

   ```bash
   # open source Gateway
   curl -s -X POST "$GW/tyk/keys" -H "x-tyk-authorization: $SECRET" \
     -H 'Content-Type: application/json' \
     -d '{"org_id": "default", "alias": "partner-a", "apply_policies": ["partner-a"]}'
   # {"key":"default68b0…","status":"ok","action":"added","key_hash":"f8d4d6e4"}

   # Dashboard
   curl -s -X POST "$DASH/api/keys" -H "authorization: $DASHBOARD_API_KEY" \
     -H 'Content-Type: application/json' -d '{"alias": "partner-a", "apply_policies": ["partner-a"]}'
   # {"api_model":{},"key_id":"eyJvcmciOi…","data":{…},"key_hash":"59ce…"}
   ```

   The client sends `key` (or `key_id`) in the `Authorization` header. If the open source
   Gateway answers `Failed to create key, ensure security settings are correct.`, the cause is
   in the Gateway log; the table in `references/creating-keys-and-policies.md` maps each log
   line to its fix (most often a missing `org_id` or a missing reload).

4. **Add endpoint limits only where asked.** For one consumer: an `endpoints` list inside that
   policy's `access_rights` entry, with an anchored path (`"^/anything/orders$"`): an
   unanchored one covers more or fewer paths depending on the Gateway's path matching mode. For every caller together: a
   `rateLimit` on the operation in the Tyk OAS definition, where `per` is a duration string
   (`"1m"`). Both are in `references/recipes.md`, recipe 3.

5. **Work out which limiter will count it.**

   ```bash
   python3 "$SKILL/scripts/which_limiter.py" --rate 5 --per 60 --gateways 2
   ```

   Pass the Gateway's limiter settings too (`--redis-rolling`, `--sentinel`, `--fixed-window`,
   `--drl-sentinel`, `--drl-threshold`) when its configuration sets them.

   One Gateway counts in its own memory (reset by a restart). Several Gateways count a low
   limit in Redis, exactly, and split a high one between them in memory; the boundary is
   `drl_threshold` (default 5) × Gateways requests per second. If the user needs one exact
   counter at any rate, set `enable_redis_rolling_limiter: true` on every Gateway and tell them
   it adds Redis work to every request. Quotas are always in Redis. Read
   `references/limiters-and-counters.md` before answering any question that starts "what
   happens to the limit when".

6. **Prove it with a burst**, through every Gateway, with a fresh key, at least 10 seconds
   after the last change:

   ```bash
   export KEY_A=<the key>
   python3 "$SKILL/scripts/burst_test.py" --url "$GW/orders/anything/orders" \
     --header-env Authorization=KEY_A --count 12 --window 60 --expect-ok 5 --expect-reject 429
   # sequence: 200 200 200 200 200 429 429 429 429 429 429 429
   # first rejection: request 6, HTTP 429, body { "error": "Rate Limit Exceeded" }
   # PASS
   ```

   Repeat for a second consumer with a different limit. `references/testing-limits.md` lists
   the fifteen ways such a test gives a wrong answer.

7. **Tell the user what callers will see**: 429 `Rate Limit Exceeded` for the consumer's own
   limit, 429 `API Rate Limit Exceeded` for a limit in the definition, 403 `Quota exceeded`
   for a quota, no `Retry-After`, and headers that describe the quota unless configured
   otherwise (`references/responses-and-headers.md`).

## References

| File | Read it for |
| --- | --- |
| `references/objects-and-merge-rules.md` | Keys, policies, access rights; which limit applies to a request; several policies on one key; partitioned and per-API policies; when a change reaches traffic |
| `references/creating-keys-and-policies.md` | Every call on the Gateway API and the Dashboard API with its response; policy files; required fields; what each mistake returns |
| `references/limiters-and-counters.md` | Which limiter runs with one and several Gateways; the settings; Redis key names; restarts; a Redis outage |
| `references/quotas.md` | Quota fields, where it is counted, renewal, reset, what resets it by accident |
| `references/responses-and-headers.md` | Exact status, body and headers for every outcome; the two header modes |
| `references/recipes.md` | Lab; two partners; two Gateways; per-endpoint limits; Free and Pro plans; the Dashboard variant |
| `references/testing-limits.md` | The burst tool, a good test, and the mistakes that make a test lie |
| `references/pitfalls-and-limitations.md` | Twenty pitfalls, what the product does not do, what is outside this skill |

Scripts (Python standard library): `scripts/burst_test.py`, `scripts/which_limiter.py`.
Assets: `assets/limits-lab/` (Compose file, `tyk.conf`, a Tyk OAS API) for the lab.

## Pitfalls

The ones that cost the most; the full list is in `references/pitfalls-and-limitations.md`.

- A limit in the API definition is shared by all callers. Per-consumer limits are policies.
- Open source Gateway: `org_id` on the policy and the key must equal the API's `orgId`, and a
  new or changed policy needs `GET /tyk/reload/group`. Both failures look the same.
- `per` is seconds in keys and policies and a duration string in the API definition.
- Several policies on one key: the most generous rate and quota win.
- With the default limiter, several Gateways do not share one counter above the threshold,
  and one Gateway loses its counts on restart.
- With the Redis rolling window, rejected requests count: clients must back off.
- A used-up quota is 403, and updating a key resets its quota unless `?suppress_reset=1`.
- A changed policy rate on one open source Gateway needs a new `last_updated` in the policy.
- Dashboard `PUT` of a policy: remove `access_rights_array` from the body first.

## Limitations

Say these plainly when they matter to the request:

- The limiter is one setting for the whole Gateway, not per API or per key.
- By default there is no exact shared counter at rates above the threshold; exactness costs
  Redis work on every request.
- Tyk sends no `Retry-After`, and the error body does not say which limit or how long to wait.
- Per-endpoint limits inside a key or policy are rate limits only, with no quota.
- The open source Gateway reports key and policy errors only in its log.

## Before reporting success

- [ ] Every consumer has its own key, and each key's policy carries that consumer's numbers.
- [ ] A burst through **every** Gateway passed with the expected count, status and body, for
      at least two consumers with different limits.
- [ ] The burst used a fresh key, fitted inside one window, and ran at least 10 seconds after
      the last change to a key, a policy or the number of Gateways.
- [ ] You ran `which_limiter.py` with the real number of Gateways and told the user where the
      count lives and what a restart does to it.
- [ ] Any quota was exhausted once in the test and returned 403 `Quota exceeded`.
- [ ] A changed policy was proved with traffic, not with a readback of the key.
- [ ] No key value, Gateway secret or Dashboard API key is written into a file you leave
      behind or into the report.
- [ ] The report says which Gateway and Dashboard versions the test ran on.

## Hand-offs

- **Writing or changing the API definition** (authentication, paths, the definition-level
  limits, error overrides): the `tyk-api-definitions-routing` skill.
- **Policies and keys in Git or as Kubernetes resources**: the `tyk-gitops-sync-operator`
  skill.
- **A Dashboard-managed stack to try this on**: the `tyk-demo-builder` skill.
- **Installing Tyk for real, including the open source Gateway alone**: the
  `tyk-deployment` skill.
- **Limits under load, with acceptance criteria and evidence**: the `tyk-poc-runbook` skill.
- **Licensing, editions and versions**: the `tyk-facts` skill.
- **How many Gateways and how much Redis**: the `tyk-infra-sizing` skill.
- **MCP proxies and AI traffic**: the `tyk-mcp-gateway` skill and the `tyk-ai-gateway` skill.
