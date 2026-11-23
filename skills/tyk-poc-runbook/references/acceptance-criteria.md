# Acceptance criteria

Last verified: 2026-11-20 against the Tyk documentation for Gateway and Dashboard 5.16 and Sync 2.3, https://tyk.io/pricing/, the tyk-demo-builder proofs, and `tykio/tyk-gateway:v5.16.0` (open source)

Five criteria evaluators commonly set for an API gateway proof of concept. Each has the same
shape: purpose, the Tyk mechanism (with its source), the `tyk-demo-builder` modules and proof
IDs that exercise it, a test procedure, pass/fail, and the evidence to keep. Adapt thresholds to
the project's own requirements; write the agreed numbers into the plan before running anything.

Module names and proof IDs are those of the `tyk-demo-builder` skill (`--modules` of
`scripts/init_demo_bundle.py`; read that skill's `SKILL.md` for the current list). A builder
stack is a single-host Docker Compose rehearsal: good evidence for behaviour, not for capacity.

---

## C1. GitOps delivery and rollback

**Purpose.** Show that API configuration is delivered from version control by a pipeline, with
no hand edits in a UI, and that a bad change can be rolled back the same way.

**Mechanism.**
- Tyk Sync 2.x: `dump` exports APIs, policies and templates from the Dashboard to files;
  `publish` creates, `update` updates, `sync` creates, updates and deletes so the Dashboard
  matches the source "unless `--no-delete` flag is used"
  (https://tyk.io/docs/api-management/automations/sync,
  https://tyk.io/docs/product-stack/tyk-sync/commands). Sync 2.x needs a licensed Dashboard.
- Tyk Operator: Kubernetes custom resources such as `TykOasApiDefinition` and `SecurityPolicy`,
  reconciled into the Dashboard (https://tyk.io/docs/api-management/automations/operator).
  Operator is licensed.
- Which to use, and how to run either one: the `tyk-gitops-sync-operator` skill (it also has a
  script that compares a Dashboard dump with the repository, for step 3 below). Mapping from
  Kong's decK and Ingress Controller: the `tyk-migration-planner` skill.

**Builder modules.** `tyk-sync` (`SYNC-001`: after the latest delivery run, the Dashboard
read-back holds every API id in `apis/` and each still answers in the Dashboard) plus `cicd`
(`CICD-001`: GitHub and GitLab pipeline templates checked statically, passing once `SYNC-001`
passes; no CI system is run). Or `operator` (`OPERATOR-001`: a `TykOasApiDefinition` and a
`SecurityPolicy` reconciled from a kind cluster).

These proofs are prerequisites, not the criterion. Know their limits before relying on them:

- `SYNC-001` shows the APIs exist in the Dashboard. It does not compare their content with the
  commit, and it sends no traffic through a gateway. An API with the right id and stale content
  passes it.
- The builder's promote step delivers API definitions only. It does not promote policies. If
  the change under test is in a policy, deliver policies with a path that includes them (Tyk
  Sync run on a directory that holds the policies, or Operator `SecurityPolicy` resources) and
  say which.
- The promote step uses `update` and `publish`, so it never deletes; a rollback re-applies
  earlier definitions and leaves in place any API the change created. Test `sync` deletion
  separately if the project needs it.

C1 passes on the procedure below, run for this project, never on a module row being `live`.

**Procedure.**
1. Commit the baseline API definitions (Tyk OAS) and policies; promote; record a request through
   the gateway that shows baseline behaviour (status, a header or body field).
2. Change one observable thing in Git on an existing API (a response header transform, a rate
   limit); promote through the pipeline; repeat the request and record the change. A change
   that adds a new API or endpoint needs step 4 as well, because re-applying older files does
   not remove it.
3. After each delivery, compare what the Dashboard holds with what was committed for every
   changed object (dump it and diff it against the commit), not only that its id exists.
4. Roll back with `git revert`; promote again; repeat the request and record baseline behaviour.
   If the change created something new, state beforehand whether rollback deletes it, disables
   it or leaves it, and show that outcome.
5. Optional: delete an API from Git and run `tyk-sync sync` (without `--no-delete`). Name the
   organisation and the set of objects that run owns, list what the Dashboard holds before and
   after, and confirm the API is gone from the Dashboard and returns 404 at the gateway. `sync`
   removes everything absent from its source, not one API, and the `tyk-gitops-sync-operator`
   skill records that `sync --no-delete` still removes API Templates absent from the source.
6. Time each delivery from commit to observable change, and say whether time spent waiting for
   a manual approval counts.

**Pass/fail.** Pass when change and rollback each take effect at the gateway with no hand
edits, the Dashboard content matches Git after each step, and commit-to-live time is within the
agreed limit. Fail on any hand edit of configuration (in a UI or by an ad hoc API call), drift
between Git and the Dashboard, or a rollback that does not restore the baseline response. A
manual approval gate in the pipeline is not a hand edit.

**Evidence.** Git log of the three commits with their SHAs; the pipeline run for each delivery
(CI system, run ID, the commit it ran); the content comparison from step 3; the three
request/response captures with timestamps; Dashboard read-back (`SYNC-001` or `OPERATOR-001`
record). If no CI system is available and Tyk Sync is run by hand from a workstation, say so.
C1 is then `pass` only if the delivery path agreed in the plan was local; against a threshold
that names a pipeline it is `not run`.

---

## C2. Policy correctness across replicas

**Purpose.** Show that a rate limit and a quota set once in a policy hold across every gateway
replica, not per node.

**Mechanism.**
- The Redis Rate Limiter enforces one cluster-wide limit across all Gateways, and quotas are
  tracked in Redis across all Gateway nodes, on the open source Gateway too
  (https://tyk.io/docs/api-management/rate-limit).
- The default limiter is different. "The Distributed Rate Limiter (DRL) is the default rate
  limiting mechanism in Tyk Gateway. It is implemented using a token bucket implementation that
  does not use Redis", it "divides the configured rate limit between the number of addressable
  gateway instances" and "will yield approximate results". The Redis Rate Limiter "can be enabled
  via the `enable_redis_rolling_limiter` configuration option"; `drl_threshold` switches between
  them by rate (https://tyk.io/docs/api-management/rate-limit). Despite the "token bucket"
  wording, on Gateway 5.16.0 the in-memory limiter counts in a window that starts at the first
  request and refills in full when it ends, so a burst at a window boundary can pass up to twice
  the rate (the `tyk-rate-limits-keys-policies` skill has the detail).
- Record which limiter the gateways run before testing; the result depends on it. The setting
  alone does not tell you. With the default limiter and more than one gateway, a limit at or
  below `drl_threshold` requests per second per gateway (5 by default) is enforced through Redis
  anyway (https://tyk.io/docs/api-management/rate-limit, and the `drl_threshold` entry of
  https://tyk.io/docs/tyk-oss-gateway/configuration: "So you have 2 Gateways, and your threshold
  is set to 5, if a user rate limit is larger than 10, it will use the distributed rate limiter
  algorithm"). With two gateways that means any limit of 10 requests per
  second or fewer. A limit of 10 requests per 60 seconds therefore exercises the
  Redis path whichever limiter is configured, and comparing "both limiters" at that rate
  compares Redis with Redis. To observe the Distributed Rate Limiter, test at a rate above the
  threshold.

**Builder modules.** `rate-limit`, `quota` (with `api-key`), and `gateway-sharding`
(`SHARD-001`) for a second gateway, `tyk-gateway-external`. That second gateway is segmented and
"loads only APIs tagged `external`", so test against an API tagged `external` (the module
generates one) or both gateways will not serve it. Both gateways share Redis. The Dashboard
licence must allow two gateway nodes. How each limiter counts, with real counts per case, is in
the `tyk-rate-limits-keys-policies` skill.

**Procedure.**
1. Create two fresh keys: one on a policy with a small rate limit and no quota (for example
   10 requests per 60 seconds), one on a policy with a small quota and a generous rate limit.
   Testing both on one key lets one limit hide the other.
2. Record the limiter in effect on each gateway: the limiter settings
   (`enable_redis_rolling_limiter`, `enable_sentinel_rate_limiter`, `drl_threshold`), the number
   of gateways (count every gateway that shares this Redis), and the policy's rate and period.
   State which path that selects.
3. Positive control first: one request with the rate-limit key through each gateway must return
   2xx. A key that never succeeds cannot show that a limit works.
4. Wait for a clean window. Send requests with the rate-limit key to both gateways at the same
   time, faster than the limit, then repeat with an uneven split (most requests to one
   gateway). Log gateway, timestamp, status code and the `X-RateLimit-*` headers for every
   request.
5. Count every status class across both gateways, not only 2xx and 429. Any 401, 403 or 5xx
   means the run is invalid until explained.
6. Keep sending past the end of the window and record the first request that succeeds again.
7. Repeat steps 3 to 6 at a rate above the threshold if the project needs to compare limiters.
8. With the quota key, exhaust the quota through one gateway and confirm the other gateway
   refuses the same key; record the response the consumer sees.

**Pass/fail.** State the window you are judging (for a rolling limiter: any period of the
policy's length). With the Redis path: pass when the allowed requests across both gateways are
more than zero and no more than the policy limit in any such window, the excess is answered
429, no other status appears, requests succeed again after the window, and the quota is
exhausted cluster-wide. With DRL: record the observed spread and judge it against the agreed
tolerance; do not claim exact enforcement.

**Evidence.** The limiter settings and the path they select on each gateway; the per-request
log with timestamps and gateway; the counted summary per window with every status class; both
policy JSON documents.

---

## C3. Redis and control-plane resilience

**Purpose.** Know what keeps working when Redis or the control plane is unavailable, and how
the platform recovers.

**Mechanism.** Tyk requires Redis in production and recommends running it highly available
(Redis Sentinel or Redis Cluster). In Tyk Hybrid, Gateways keep their last working configuration
and keep serving traffic while the control plane is unavailable
(https://tyk.io/docs/deployment-options/tyk-hybrid). On Tyk Cloud, Tyk runs Redis. What a
gateway does while Redis is away depends on configuration:

- **Quotas and auth-token keys need Redis.** In the same words as the `tyk-troubleshooting`
  skill:
  On Gateway 5.16.0 (open source or Dashboard-managed), while Redis is down, an API without
  authentication, and a JWT API whose policy has no quota, keep answering `200`; a JWT API
  whose policy has a quota answers `403 {"error": "Quota exceeded"}`, each after about 2
  seconds; an auth-token key works only while the Gateway's local copy of its session lasts, at
  most 10 seconds after the Gateway last read it from Redis, then gets
  `403 {"error": "Access to this API has been disallowed"}`, and a key with a quota, or one not
  read in the last 10 seconds, is refused from the first request. Within 2 seconds of Redis
  returning, every key and token works again, with no restart.
- **Rate limits depend on the limiter.** The Distributed Rate Limiter keeps its counters
  in each gateway's memory; the Redis path does not survive without Redis. Which one is in
  effect depends on the settings and on the rate (see C2).
- **Cache state matters.** An auth-token key the gateway read from Redis in the last 10
  seconds behaves differently from one it meets for the first time during the fault; so can a
  signing key it has cached from a JWKS.

So write the expected result per combination before the run: auth type, whether the policy has
a quota, the limiter in effect, warm or cold credentials, and which fault.

**Builder modules.** `jwt` and `api-key` (two APIs, one per auth type), `rate-limit`. Every
bundle runs `HEALTH-001`, which checks each gateway's `/hello` including its `redis` and
`dashboard` components; use it before and after each fault. The gateway refreshes `/hello` on an
interval: on Gateway 5.16.0 it still reports Redis `pass` one second after Redis stops, and
`fail` with "storage: Redis is either down or was not configured" ten seconds later. Wait
for the next refresh before reading it.

**Procedure.**
1. Before any fault, confirm the rate limit is being enforced on the route under test (send
   enough to see a 429). A limit that was never enforced cannot be seen to stop.
2. Start steady traffic (for example 5 requests per second) to the JWT API and the API-key API,
   with valid credentials and, on a separate client, invalid ones; log status per request with
   timestamps. Rejections must stay rejections during a fault.
3. Stop the Redis container (`docker compose stop <redis service>`); keep traffic running for a
   fixed period; note what each API returns for valid and invalid credentials and whether rate
   limits and quotas apply. During the fault, send one request with a credential the gateway
   has not seen before.
4. Start Redis; measure the time until every API answers as it did before the fault; run
   `HEALTH-001`. A healthy check after recovery does not show that traffic kept flowing.
5. Stop the Dashboard container; keep traffic running; note gateway behaviour; try an API change
   (it should not reach the gateway); start the Dashboard; note recovery.
6. For Hybrid or MDCB evaluations, repeat step 5 by cutting the data plane's connection to the
   control plane instead (the builder does not deploy MDCB; use the tyk-install repository's
   `docker/mdcb/` or `docker/hybrid/` deployment through the `tyk-deployment-planner` skill).

**Pass/fail.** Agree the expected behaviour per combination and per fault before the run (the
list above), and the longest acceptable recovery time. Pass when observed behaviour matches the
agreed expectation, invalid credentials stay rejected throughout, and the stack recovers within
the agreed time without manual repair. Any difference from the vendor statement is a finding to
raise with Tyk, not a pass or fail to assume. If the evaluation depends on analytics surviving
the fault, test that separately and say what was lost.

**Evidence.** The traffic log across each fault window; container stop/start timestamps;
gateway logs for the window; `HEALTH-001` before and after.

---

## C4. Latency and throughput

**Purpose.** Measure the latency the gateway adds and the throughput it sustains at the
project's own load, with its own policies switched on.

**Mechanism.** Tyk load-tests releases and publishes the results at
https://tyk.io/performance-benchmarks/ (AWS, GCP and Azure, four machine classes each). The test
methodology is public: https://github.com/TykTechnologies/tyk-ansible-performance-testing.
Vendor figures are context; the project's own run is the evidence.

**Builder modules.** `otel` for OpenTelemetry traces through the gateway (pair with the
tyk-install repository's observability overlay, planned with the `tyk-deployment-planner` skill, for metrics on a real topology); the auth module
the project will use in production (`api-key`, `jwt`, `oauth`, `mtls`); `rate-limit` set high
enough not to interfere, or off. The Gateway exports its own metrics and traces over OTLP
("Tyk Gateway produces all three signals. It exports metrics and traces through the
OpenTelemetry Protocol (OTLP)", https://tyk.io/docs/api-management/observability; the
`tyk-observability` skill sets it up). The builder's `OTEL-001` checks a trace, a request
metric derived from spans and a correlated log line; it does not check the Gateway's own
metrics, so confirm those separately if C4 reads them.

**Load profile.** Write it into the plan before running, as a file kept in the repository (a k6
script, or the equivalent for the chosen tool), with:

- the load model: open (a fixed arrival rate, for example k6 `constant-arrival-rate`) or closed
  (a fixed number of virtual users, as `hey -c` does). With a closed model a slow gateway lowers
  the request rate instead of showing higher latency, so judge throughput only with the achieved
  rate beside the offered one (k6 describes both models:
  https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/);
- the offered rate, warm-up, duration (at least 5 minutes after warm-up; that is a smoke test,
  not evidence of memory stability, autoscaling or failover behaviour) and number of repeats;
- the request mix: methods, paths, payload sizes and the auth type, matching production;
- connection settings: keep-alive, TLS on or off, timeouts in the client;
- what counts as an error: every non-2xx status, every client timeout and every connection
  error, counted against all requests sent.

**Procedure.**
1. Run the load generator on a separate machine from the gateway and the upstream, and record
   its CPU during the run; a saturated generator invalidates the run.
2. Run the profile directly against the upstream (baseline), then through the gateway, with the
   same profile and the same upstream. Record the upstream's own health and latency in both.
3. For each run keep: offered and achieved rate, the full latency histogram (or raw per-request
   timings), p50, p95 and p99, the error count by class and the error rate, and gateway CPU and
   memory.
4. Repeat each run at least three times; report the median and the worst run.
5. Report "p95 through the gateway minus p95 direct" as a difference between two distributions.
   It is not the gateway's own per-request overhead; for that, use the gateway's spans or its
   own latency metrics for the same requests.
6. For capacity numbers, run on the hardware class intended for production, not on a laptop
   demo stack; use the `tyk-infra-sizing` skill to plan that hardware.

**Pass/fail.** Pass when, at the agreed offered rate, the achieved rate is at least the agreed
minimum, the error rate (as defined above) is within the threshold, and p95 and p99 through the
gateway, and their differences from the baseline, are within the agreed limits in the median run
and the worst run stays within the agreed tolerance. State the hardware and the load model with
every number.

**Evidence.** The load profile file; raw output and histograms for every run; generator,
gateway and upstream resource figures; hardware description; gateway configuration; a trace
sample from the OpenTelemetry backend; the run manifest.

---

## C5. Three-year total cost of ownership frame

**Purpose.** Compare the cost of running Tyk with the alternative over three years, using real
inputs and no invented numbers.

**Mechanism.** Tyk publishes no list prices; quotes come from Tyk sales
(https://tyk.io/contact/). The active packages are Core (usage-based, priced by the number of
APIs managed with unlimited API calls, not by request volume) and Enterprise ("Custom",
"Contact Tyk", with premium support and custom SLAs) (https://tyk.io/pricing/; the `tyk-facts`
skill, `references/licensing-and-packaging.md`, "Packages and pricing"). Build the table from the written quote for the plan actually offered.

**Inputs.**
- Infrastructure: from the `tyk-infra-sizing` skill (gateways, Redis, control plane, analytics
  store, per environment and region).
- Licence: a written quote from Tyk for the chosen plan and topology. Leave the cell empty until
  it exists.
- Operations: people time to run the platform (upgrades, on-call, pipeline maintenance).
- Migration: one-off effort from the `tyk-migration-planner` skill's plan.
- The alternative: the same rows for the incumbent or other candidate, from its own quote,
  including its migration, training and operations lines. A comparison with rows on one side
  only is not a comparison.
- For every figure: currency, the date of the source, whether it is one-off or recurring, and
  the quantities it assumes (data planes, environments, API count, support level).

**Procedure.** Fill the table in `poc-plan-template.md` (TCO section) per year; mark every cell
with its source (quote reference, sizing pack, estimate by whom); leave unknowns blank and list
them as open points.

**Pass/fail.** Not a pass/fail test. Its status is `complete` when every cell has a source or is
marked as not affecting the decision, and `incomplete` otherwise. Show totals only for columns with
no open cells; an empty cell is unknown, not zero. If cost decides the outcome, the plan must
say so, and an `incomplete` C5 then blocks a Go.

**Evidence.** The sizing pack, the quotes (stored outside any public repository), and the
filled table with sources.
