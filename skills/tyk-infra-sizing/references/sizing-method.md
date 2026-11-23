Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), Tyk Pump 1.18.0 (`tykio/tyk-pump-docker-pub:v1.18.0`), Redis 7.2, PostgreSQL 16, the public Tyk documentation and the published benchmark data

# Sizing method

Read this before explaining or defending a number. It gives every formula the engine uses,
every constant with the kind of evidence behind it, and the analytics facts behind them.
The generated report already words these points for its readers and cites public pages only.

## Contents

1. Kinds of evidence
2. Constants
3. Analytics records on Gateway 5.16.0
4. Traffic
5. Gateways
6. Redis
7. Pump
8. Control plane and analytics store
9. Data transfer
10. Cost mapping
11. Regression cases
12. Refreshing prices and benchmarks

## 1. Kinds of evidence

Every constant lives in `scripts/sizing_data.py` with an entry in `SOURCES` that names its kind:

- **published**: a figure on a public Tyk page; the URL is in the table below.
- **assumption**: a planning choice of this method. No Tyk page states it.
- **price**: an approximate list price from the snapshot dated in `PRICE_SNAPSHOT`.

A test fails when the engine or the report uses a constant with no `SOURCES` entry. KB and GB
are binary throughout (KiB, GiB), and a month is 30.4 days.

## 2. Constants

| Constant | Value | Kind | Source |
| --- | --- | --- | --- |
| `RPS_PER_VCPU` | AWS 5,050 / 4,260 / 3,930 / 3,420; GCP 5,420 / 4,190 / 3,960 / 3,330; Azure 3,820 / 3,430 / 3,330 / 2,990 (keyless / key auth / auth and quota / full middleware) | published | https://tyk.io/performance-benchmarks/ embeds the benchmark data. Each figure is the 16-vCPU machine (c5.4xlarge, c2-standard-16, Standard_F16s_v2) divided by 16 and rounded to 10. The 16-vCPU machine gives the lowest per-vCPU rate of the 4-, 8- and 16-vCPU runs, so smaller nodes are not overstated. The page states no Tyk version or test date |
| `RPS_PER_VCPU_BURSTABLE` | AWS 3,680 / 2,800 / 2,710 / 790; GCP 2,660 / 2,060 / 1,960 / 1,470; Azure 6,670 / 5,130 / 3,640 / 1,090 | published | Same data, 2-vCPU burstable machines (t2.medium, e2-medium, B2s) divided by 2. Used, when lower than the 16-vCPU rate, for non-production planes on the default burstable node |
| `API_KEY_KB` | 1 | published | https://tyk.io/docs/api-management/in-memory-database: "Session size is typically small (around 1KB)" |
| `ANALYTICS_BASE_KB` | 2 | published | Same page: "Basic analytics records are typically around 2KB". A minimal keyless record on Gateway 5.16.0 is 1,011 bytes (section 3), so 2 KB leaves room for keys, longer paths and tags |
| `BASE64_FACTOR`, `DETAILED_HEADERS_KB` | 4/3, 0.5 KB | published, assumption | Same page: detailed records hold the "full base64-encoded request and response body". No Tyk page sizes the headers in a detailed record; 0.5 KB raw is a planning allowance that matches typical request and response headers on Gateway 5.16.0 (section 3) |
| `NONAGG_RECORD_KB` | 2 | assumption | https://tyk.io/docs/api-management/persistent-database gives "roughly 1KB per record"; a basic record takes 546 bytes per PostgreSQL row with indexes (section 3). Planned at 2 KB |
| `API_DEF_KB`, `POLICY_KB` | 50, 2 | assumption, published | Persistent-database page: a Tyk OAS definition is "a few KB" to "100KB or more"; a policy "roughly 0.7-2KB" |
| `AGG_ITEM_KB` | 1 per active key or API per hour | assumption | An upper bound. The persistent-database page gives an average of "about ~40KB" per hourly aggregate record for a typical mix |
| `MONGO_LOG_INDEX_KB`, `MONGO_RAM_FACTOR` | 0.3, 2 | published | https://tyk.io/docs/tyk-configuration-reference/mongodb: log index "around 30%" of the ~1KB records; "Doubling the result is a reasonable rule of thumb" |
| `GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S` | 60 | published, measured | https://tyk.io/docs/tyk-oss-gateway/configuration, `analytics_config.storage_expiration_time`: "The default is 60 seconds". How the TTL behaves is in section 3 |
| `MIN_CONN_POOL` | 500 | published | https://tyk.io/docs/planning-for-production recommends `"max_idle_connections_per_host": 500`; the configuration page says 0 means the Go default of 2 |
| `PUMP_RECORDS_PER_S_PER_INSTANCE` | 5,000 | assumption | No public Tyk page gives a Pump throughput. One Pump writing to PostgreSQL ranges from about 2,700 to about 13,900 records per second with the host (section 3); 5,000 is a conservative planning rate inside that range |
| `PLANNING_UTIL`, `UTIL` | 40% gateways, 70% data stores | assumption | Planning headroom of this method |
| `CACHE_SHARE`, `CACHE_TTL_S` | 10%, 60 s | assumption | |
| `OUTAGE_CLASS_BUFFER_GB`, `REDIS_SINGLE_NODE_MAX_GB`, `AWS_REDIS_USABLE` | 10 GB, 270 GB, 85% | assumption | Buffers above 10 GB get three nodes; 270 GB is 85% of the largest listed ElastiCache node |
| `GATEWAY_RAM_GB_PER_VCPU`, `HPA_*`, `SELF_HOSTED_DISK_GB` | 2 GB, 60% CPU, 2x peak, 20 GB | assumption | Container requests, autoscaling bounds, VM disks |
| `CLOUDS`, `ATLAS_TIERS`, `AWS_REDIS_NODES`, `FARGATE_*`, `EU_FACTOR`, `PRIVATE_EGRESS_PER_GB`, `SMALL_DB_FACTOR` | see the file | price | List, on-demand, primary US region, snapshot of 8 July 2026. The ElastiCache node rows, EKS ($0.10 per cluster-hour) and Fargate ($0.04048 per vCPU-hour, $0.004445 per GB-hour) match the AWS public price list for us-east-1 as of 19 November 2026 (`PRICE_RECHECK`); the other rows are as of the 8 July snapshot |

OCI, on-premises and private-cloud planes have no benchmark run: they use the AWS rates, and
every output says so.

## 3. Analytics records on Gateway 5.16.0

Facts behind the analytics constants, for `tykio/tyk-gateway:v5.16.0` with
`tykio/tyk-pump-docker-pub:v1.18.0`, Redis 7.2 and PostgreSQL 16:

- A Tyk OAS API created through the Gateway API (`POST /tyk/apis/oas`) writes no analytics
  unless `middleware.global.trafficLogs.enabled` is `true`; with it set and a reload, records
  appear.
- A basic record for a keyless `GET /basic/v1/orders/N?include=items` with a browser
  User-Agent is 1,011 bytes in the Redis list. Redis holds about 1,150 bytes per record
  (`MEMORY USAGE` 1,146 bytes; `used_memory` grows 1,159 bytes per record).
- A detailed record for a `POST` with a 2,048-byte body and an 8,192-byte response is 15,387
  bytes; the bodies alone base64 to 13,656 bytes. A detailed `GET` with a 2-byte response is
  1,680 bytes, 669 more than the basic record, which is the headers (about 0.5 KB raw).
- In PostgreSQL the basic record takes 546 bytes per row, indexes included.
- One Pump with the `sql` and `sql_aggregate` pumps (`purge_delay` 1, default batch size)
  writes anywhere from about 2,700 to about 13,900 records per second into PostgreSQL: the
  sink and the host decide.
- With Pump stopped and the default `analytics_config.storage_expiration_time`, records
  written at 2 s (TTL 58) and 33 s (TTL 23) are all gone at 63 s. With the setting at 600,
  records written at 2 s (TTL 598) are still there at 72 s (TTL 528).

The TTL is set once, when the first unprocessed record is written, and is not extended by
later writes: the setting must cover the whole outage tolerance.

## 4. Traffic

| Input | Rule |
| --- | --- |
| `annual_requests` | average RPS = requests / (365 x 86,400) |
| `monthly_requests` | average RPS = requests / (30.4 x 86,400) |
| `daily_requests` | average RPS = requests / 86,400 |
| `peak_multiplier` | peak = average x multiplier; default 3, flagged |
| only `peak_rps` | average = peak / `peak_multiplier` when supplied, else peak / 3, flagged |

Two figures for the same quantity (an average and a volume, two volumes, or a peak, an average
and a multiplier) must agree within 5%, or the brief is rejected. A data plane carries the
environment's figures times its `traffic_share`; shares default to an even split, flagged.

## 5. Gateways

- `rate = benchmark x throughput_factor`; the factor defaults to 1.0.
- `base = ceil(peak / (rate x 0.4) / vCPU)`; HA adds one instance; `min_gateways` raises the floor.
- Instance size: 4 vCPU in production, 2 vCPU in non-production; `gateway_vcpu` overrides it
  and the node price scales linearly. The result keeps the purchased node's memory
  (`node_ram_gb`, 16 GB for GCP c2-standard-4) apart from the 2 GB per vCPU each gateway
  requests (`ram_gb`).
- N-1: `(count - 1) x vCPU x rate x 0.4 >= plane peak`. It is arithmetic on the planning rate.
- Connection pool: one upstream connection serves `1000 / latency_ms` requests per second
  (planning page); `max_idle_connections_per_host = max(500, ceil(peak per gateway / (1000 / latency)))`.
- Autoscaling (assumption): minimum is the planned count, maximum absorbs twice the peak, 60% CPU.

## 6. Redis

Per data plane, in GB:

| Term | Formula |
| --- | --- |
| Keys | active keys x 1 KB (every plane caches the full key set: an upper bound) |
| Cache | average RPS x 10% x response KB x 60 s (when caching is on) |
| Analytics buffer | plane peak x record KB x `analytics_buffer_seconds`; record = 2 KB, plus 4/3 x (request KB + response KB + 0.5 KB) with detailed analytics |
| Config backup | MDCB workers only: API definitions x 50 KB + policies x 2 KB |

`planned = max(1, ceil((keys + cache + config) / 0.7 + buffer))`. One function turns a
requirement into a topology and a price, and serves the planned Redis, the 30 s / 5 min / 1 h
outage sensitivity and the control-plane Redis alike, so the same memory always gets the
same shards, nodes and cost:

- Above 270 GB the requirement splits into `ceil(GB / 270)` equal shards (cluster mode).
- Nodes: three when the buffer exceeds 10 GB, otherwise two with HA, one without.
- AWS maps each shard onto the ElastiCache node ladder at 85% usable memory. Production never
  uses burstable nodes (`cache.m6g.large` is the floor); non-production may.
- Other clouds price replicated capacity per GB-month.
- Single-plane Redis is persistent and shared with the Dashboard. MDCB workers have a
  recoverable Redis; the control plane gets its own persistent Redis of
  `ceil((keys x 1 KB + 0.2 GB) / 0.7)`, counting the keys of environments that share it.
- An outage tolerance above 60 seconds produces a decision note: raise
  `analytics_config.storage_expiration_time` to at least that many seconds (section 3).

## 7. Pump

Pump is sized by records per second at 5,000 per instance (assumption), and its capacity
always exceeds the average so a backlog drains.

- No `peak_duration_seconds`: `instances = max(floor(avg / 5,000) + 1, ceil(peak / 5,000))`,
  so Pump keeps up with the full peak.
- With `peak_duration_seconds`: the fewest instances for which
  `max(0, peak - capacity) x duration <= peak x analytics_buffer_seconds`, so the backlog a
  peak builds fits the Redis buffer. The report gives the backlog and
  `drain time = backlog / (capacity - average)`. That backlog uses the same buffer an outage
  would, which the note says.

The rate is for a PostgreSQL sink. Notes flag MongoDB sinks and the Hybrid Pump to MDCB as
having no throughput figure. On Kubernetes Pump shares the gateway node pool and is not priced;
elsewhere each instance is a 2 vCPU / 4 GB task or VM.

## 8. Control plane and analytics store

- Placement: beside the plane flagged `hosts_control_plane`, or with no flag the first plane
  matching the `control_plane` `cloud`, `region` and `label` (default the first plane). When
  no plane matches, it is remote: every plane
  is an MDCB worker and ships analytics across. A flagged host with a conflicting override is
  rejected.
- Nodes: 2 x 4 vCPU / 8 GB with HA, 1 without, priced on the control plane's own target
  (Fargate tasks, or the cloud's standard node). A control plane on Kubernetes that is not
  beside a Kubernetes data plane pays its own cluster fee.
- Developer Portal: internal = one 2 vCPU / 4 GB instance; partner-facing = two 4 vCPU / 8 GB
  instances plus a small PostgreSQL HA pair.
- Per-request logs reach the store with one data plane, or with the `control-plane`
  multi-plane mode: `average RPS x 86,400 x (2 KB + detail KB) x retention days`.
- Aggregates (not with `edge-local`): `(keys + APIs x 2) x 1 KB x 24 x retention days`.
- Storage = `max(10, ceil((logs + aggregates + config + guests' logs and aggregates) / 0.7))` GB.
- MongoDB RAM = `max(4, ceil(2 x (log documents x 0.3 KB + aggregates)))` GB, guests included.
  The smallest Atlas tier with that RAM is chosen; storage beyond its included amount is
  priced as extra storage. When no listed tier has the RAM, the store is reported as not
  priced, with a risk note, rather than forced into the largest tier.
- PostgreSQL: the managed instance plus storage. Compute is not modelled; a note gives the
  write rate to load-test.
- More than 20 GB of raw logs a day triggers a recommendation to shorten queryable retention.
- Shared control planes: a guest's planes are MDCB workers; the host stores the guest's
  analytics, counts its log documents in MongoDB RAM and its keys in control-plane Redis. A
  single-plane host stays one install: its shared Redis holds the guests' keys.

## 9. Data transfer

- Client egress = monthly requests x response KB. `internet_egress_share` of it pays the
  cloud's tiered internet rate after its free allowance, per cloud per environment; the rest
  pays about $0.01/GB. Ingress is free.
- Analytics between planes, for every plane not beside the control plane: `control-plane`
  mode ships every record (record KB as in section 6); `hybrid-aggregate` ships
  `active items x 24 x 30.4 x plane share` KB; `edge-local` ships nothing, and its local sinks
  are listed as not priced.
- Priced at the plane cloud's inter-region rate when the control plane is in the same cloud,
  and at its first internet tier when it is not. Self-hosted planes are not priced.

## 10. Cost mapping

List, on-demand, primary US region; EU multiplies infrastructure by 1.12 (approximation),
egress is not adjusted. The total is the sum of the displayed rows. Excluded: load balancers,
NAT gateways, snapshots, observability storage, support and Tyk licensing. On-premises and
private-cloud planes, an unfit MongoDB store and edge-local sinks are listed as not priced.

## 11. Regression cases

`scripts/fixtures/regression_cases.json` holds twelve single-environment cases and every
number the engine produced for them. `test_sizing_engine.py` replays them exactly. They are a
compatibility check, not an oracle: the formula, boundary and finding tests in the same file
check the method itself.

The cases were recaptured on 19 November 2026 after these reviewed changes: the published
2 KB basic record (was 1 KB), base64 detailed records, the 500 connection-pool floor (was 100),
the published MongoDB memory rule (was 160 MB of index per million documents, which no public
page states), Pump at 5,000 records per second sized for the peak (was 15,000 on the
average), the ElastiCache ladder for AWS control-plane Redis (was per GB), one Redis function
for base and sensitivity (the 1-hour sensitivity had stopped at the largest single node), the
corrected `cache.t4g.small` price, and an unpriced MongoDB store when no tier fits. Recapture
again only after checking each changed number against its source, and record why.

## 12. Refreshing prices and benchmarks

Prices and benchmark rates live only in `scripts/sizing_data.py`. To refresh, update the
constants, their `SOURCES` entries and `PRICE_SNAPSHOT` or `PRICE_RECHECK` together, run
`python3 -m unittest discover -s scripts -p 'test_*.py'`, and recapture the regression cases
only after checking each changed figure. The AWS public price list
(`https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<service>/current/us-east-1/index.json`
for `AmazonElastiCache`, `AmazonEKS`, `AmazonECS`) is a primary source for AWS rows; use each
provider's calculator or price list for the others.
