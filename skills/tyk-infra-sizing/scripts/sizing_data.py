"""Constants for the Tyk infrastructure sizing engine.

Every constant has an entry in SOURCES (bottom of this file) that says what kind of figure it
is, so nothing here reads as stronger than its evidence:

* published:   a figure on a public Tyk page (the URL is given);
* assumption:  a planning choice of this method, not a Tyk figure;
* price:       a list-price approximation from the dated snapshot (PRICE_SNAPSHOT).

references/sizing-method.md explains each one. test_sizing_engine.py checks that every
constant the engine uses has a SOURCES entry.

KB and GB in this module are binary (KiB, GiB): KB converts KiB to GiB.
"""

ENGINE_VERSION = "2.0.0"
GUIDANCE_SNAPSHOT = "Tyk documentation and benchmark data as of 2026-11-19"
PRICE_SNAPSHOT = "8 Jul 2026"
PRICE_RECHECK = "AWS ElastiCache node, EKS and Fargate rows match the AWS public price list as of 19 Nov 2026"

# --- Gateway throughput (requests per second per vCPU) ---------------------------------
# Tyk's published benchmark data (https://tyk.io/performance-benchmarks/), the 16-vCPU machine
# of each family divided by 16 and rounded to 10. The 16-vCPU machine gives the lowest
# per-vCPU figure of the 4-, 8- and 16-vCPU runs in each family, so it is the conservative one.
RPS_PER_VCPU = {
    "aws": {"keyless": 5050, "api-key-auth": 4260, "auth-and-quota": 3930, "full-middleware": 3420},
    "gcp": {"keyless": 5420, "api-key-auth": 4190, "auth-and-quota": 3960, "full-middleware": 3330},
    "azure": {"keyless": 3820, "api-key-auth": 3430, "auth-and-quota": 3330, "full-middleware": 2990},
    # No published OCI run: AWS c5 rates are used and flagged in every output.
    "oci": {"keyless": 5050, "api-key-auth": 4260, "auth-and-quota": 3930, "full-middleware": 3420},
}
# The same data has a 2-vCPU burstable machine per cloud (AWS t2.medium, GCP e2-medium, Azure
# B2s), per vCPU rounded to 10. Non-production planes on the default burstable node use the
# lower of this and RPS_PER_VCPU. The AWS non-production node (t4g.medium) has no run of its own.
RPS_PER_VCPU_BURSTABLE = {
    "aws": {"keyless": 3680, "api-key-auth": 2800, "auth-and-quota": 2710, "full-middleware": 790},
    "gcp": {"keyless": 2660, "api-key-auth": 2060, "auth-and-quota": 1960, "full-middleware": 1470},
    "azure": {"keyless": 6670, "api-key-auth": 5130, "auth-and-quota": 3640, "full-middleware": 1090},
    "oci": {"keyless": 3680, "api-key-auth": 2800, "auth-and-quota": 2710, "full-middleware": 790},
}
# On-premises and private-cloud planes have no benchmark of their own: AWS rates, flagged.
BENCHMARK_PROXY_FOR_UNPUBLISHED = "aws"

AUTH_PROFILES = ("keyless", "api-key-auth", "auth-and-quota", "full-middleware")

KB = 1 / (1024 * 1024)  # KiB -> GiB
API_KEY_KB = 1  # session size "around 1KB"
ANALYTICS_BASE_KB = 2  # basic analytics record in Redis, "typically around 2KB"
DETAILED_HEADERS_KB = 0.5  # request plus response headers captured by detailed recording
BASE64_FACTOR = 4 / 3  # detailed recording stores the raw request and response base64-encoded
NONAGG_RECORD_KB = 2  # stored per-request log row (published about 1KB; planned at 2)
API_DEF_KB = 50  # typical Tyk OAS API definition (published range: a few KB to 100KB+)
POLICY_KB = 2  # typical policy (published 0.7 to 2KB)
AGG_ITEM_KB = 1  # per active item per hourly aggregate: an upper bound
MONGO_LOG_INDEX_KB = 0.3  # index per log document: 30% of the published ~1KB record
MONGO_RAM_FACTOR = 2  # "Doubling the result is a reasonable rule of thumb"
UTIL = 0.7  # plan data stores at 70% utilisation
PLANNING_UTIL = 0.4  # plan gateways at 40% of benchmarked capacity
SMALL_DB_FACTOR = 0.4  # small burstable managed Postgres ~ 0.4 x the standard instance price
CACHE_SHARE = 0.1  # assume ~10% of average traffic is served from the response cache
CACHE_TTL_S = 60
PUMP_RECORDS_PER_S_PER_INSTANCE = 5000  # planning rate for sql + sql_aggregate into PostgreSQL
GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S = 60  # analytics_config.storage_expiration_time default
REDIS_SERVERLESS_FIT_RPS = 2000  # below this per-plane peak a serverless tier is cheaper
REDIS_SINGLE_NODE_MAX_GB = 270  # ~85% of the largest ElastiCache node in the ladder; beyond this, shard
OUTAGE_CLASS_BUFFER_GB = 10  # buffers above this are durability-critical: 3 nodes
MIN_CONN_POOL = 500  # max_idle_connections_per_host recommended by Tyk
DAYS_PER_MONTH = 30.4
EU_FACTOR = 1.12  # eu-west-1-class list premium over us-east-1 (approximation)
PRIVATE_EGRESS_PER_GB = 0.01  # same-cloud private endpoints (approximation)

# --- Cloud price snapshot (USD per month, list, on-demand) ------------------------------
# redis_per_gb is REPLICATED $/GB-month: managed standard tiers include the replica.
# node_ram_gb / dev_node_ram_gb: memory of the purchased node shape.
CLOUDS = {
    "aws": {
        "name": "AWS", "k8s": "EKS", "k8s_fee": 73,
        "node": 124, "node_shape": "c5.xlarge (4 vCPU / 8 GB, the benchmark family)", "node_ram_gb": 8,
        "dev_node": 25, "dev_node_shape": "t4g.medium (2 vCPU / 4 GB, burstable)", "dev_node_ram_gb": 4,
        "redis_svc": "Amazon ElastiCache (Valkey or Redis OSS)", "redis_per_gb": 34,
        "redis_serverless": "ElastiCache Serverless",
        "pg_svc": "Amazon RDS for PostgreSQL db.m5.large", "dev_pg_svc": "Amazon RDS for PostgreSQL, small burstable instance",
        "pg_node": 125, "stor_per_gb": 0.115,
        "mongo_note": "MongoDB Atlas on AWS. Tyk's MongoDB page lists Amazon DocumentDB as a drop-in replacement with limits (no capped collections), so validate your workload.",
        "egress_free_gb": 100, "egress_tiers": [(10240, 0.09), (40960, 0.085), (102400, 0.07), (float("inf"), 0.05)],
        "inter_region_per_gb": 0.02, "calc": "https://calculator.aws",
    },
    "gcp": {
        "name": "Google Cloud", "k8s": "GKE", "k8s_fee": 73,
        "node": 152, "node_shape": "c2-standard-4 (4 vCPU / 16 GB, the benchmark family)", "node_ram_gb": 16,
        "dev_node": 25, "dev_node_shape": "e2-medium (2 vCPU / 4 GB, shared-core)", "dev_node_ram_gb": 4,
        "redis_svc": "Memorystore (standard tier, replicated)", "redis_per_gb": 44,
        "redis_serverless": "Memorystore, smallest standard tier",
        "pg_svc": "Cloud SQL db-n2-standard-2 (2 vCPU / 8 GB)", "dev_pg_svc": "Cloud SQL, small burstable instance",
        "pg_node": 113, "stor_per_gb": 0.17,
        "mongo_note": "MongoDB Atlas on Google Cloud.",
        "egress_free_gb": 0, "egress_tiers": [(1024, 0.12), (10240, 0.11), (float("inf"), 0.08)],
        "inter_region_per_gb": 0.02, "calc": "https://cloud.google.com/products/calculator",
    },
    "azure": {
        "name": "Azure", "k8s": "AKS", "k8s_fee": 0,
        "node": 123, "node_shape": "F4s v2 (4 vCPU / 8 GB, the benchmark family)", "node_ram_gb": 8,
        "dev_node": 30, "dev_node_shape": "B2s (2 vCPU / 4 GB, burstable)", "dev_node_ram_gb": 4,
        "redis_svc": "Azure Cache for Redis (standard, replicated)", "redis_per_gb": 27,
        "redis_serverless": "Azure Managed Redis, smallest tier",
        "pg_svc": "Azure Database for PostgreSQL Flexible Server (2 vCPU / 8 GB)", "dev_pg_svc": "Azure Database for PostgreSQL Flexible Server, small burstable instance",
        "pg_node": 130, "stor_per_gb": 0.115,
        "mongo_note": "MongoDB Atlas on Azure. Tyk's MongoDB page lists Azure Cosmos DB for MongoDB as a drop-in replacement with limits (no TTL index on expireAt), so validate your workload.",
        "egress_free_gb": 100, "egress_tiers": [(10240, 0.087), (40960, 0.083), (102400, 0.07), (float("inf"), 0.05)],
        "inter_region_per_gb": 0.02, "calc": "https://azure.microsoft.com/pricing/calculator",
    },
    "oci": {
        "name": "Oracle Cloud (OCI)", "k8s": "OKE", "k8s_fee": 73,
        "node": 55, "node_shape": "VM.Standard.E5.Flex (4 vCPU / 8 GB)", "node_ram_gb": 8,
        "dev_node": 28, "dev_node_shape": "VM.Standard.E5.Flex (2 vCPU / 4 GB)", "dev_node_ram_gb": 4,
        "redis_svc": "OCI Cache (Redis-compatible)", "redis_per_gb": 28,
        "redis_serverless": "OCI Cache, smallest cluster",
        "pg_svc": "OCI Database with PostgreSQL (2 vCPU)", "dev_pg_svc": "OCI Database with PostgreSQL, small burstable instance",
        "pg_node": 72, "stor_per_gb": 0.0425,
        "mongo_note": "MongoDB Atlas is not offered on OCI. Self-manage MongoDB on compute, or validate a MongoDB-compatible service against Tyk's MongoDB requirements.",
        "egress_free_gb": 10240, "egress_tiers": [(float("inf"), 0.0085)],
        "inter_region_per_gb": 0.0085, "calc": "https://www.oracle.com/cloud/costestimator.html",
    },
}
CLOUD_KEYS = tuple(CLOUDS)
# Planes run on your own hardware or private cloud. Sized, never priced.
SELF_HOSTED_CLOUDS = ("on-prem", "private-cloud")

# MongoDB Atlas ladder: (tier, RAM GB, included storage GB, USD per month).
ATLAS_TIERS = [
    ("M10", 2, 10, 57), ("M20", 4, 20, 140), ("M30", 8, 40, 390),
    ("M40", 16, 80, 760), ("M50", 32, 160, 1450), ("M60", 64, 320, 2850),
]
ATLAS_EXTRA_STORAGE_PER_GB = 0.25  # storage beyond a tier's included amount (approximation)

# ElastiCache node ladder (us-east-1, on-demand): (node GiB, USD per month per node, name).
# Usable capacity ~0.85 x node GiB, which assumes reserved-memory tuned to ~15%.
AWS_REDIS_NODES = [
    (6.38, 109, "cache.m6g.large"), (13.07, 160, "cache.r7g.large"),
    (26.32, 319, "cache.r7g.xlarge"), (52.82, 637, "cache.r7g.2xlarge"),
    (105.81, 1274, "cache.r7g.4xlarge"), (209.55, 2548, "cache.r7g.8xlarge"),
    (317.77, 3822, "cache.r7g.12xlarge"),
]
# Burstable tiers: non-production posture only.
AWS_REDIS_DEV_NODES = [(1.37, 23, "cache.t4g.small"), (3.09, 47, "cache.t4g.medium")]
AWS_REDIS_USABLE = 0.85

# AWS Fargate, Linux/x86, us-east-1 list: $0.04048 per vCPU-hour, $0.004445 per GB-hour.
FARGATE_VCPU_MONTH = 0.04048 * 730
FARGATE_GB_MONTH = 0.004445 * 730
GATEWAY_RAM_GB_PER_VCPU = 2  # container or VM memory request per gateway vCPU
HPA_TARGET_CPU = 60  # scale out before the 40% planning headroom is consumed
HPA_BURST_FACTOR = 2  # HPA ceiling absorbs twice the design peak
SELF_HOSTED_DISK_GB = {"gateway": 20, "pump": 20, "dashboard": 20, "portal": 20, "mdcb": 20}
CONTROL_PLANE_NODE_VCPU, CONTROL_PLANE_NODE_RAM_GB = 4, 8

DEPLOYMENT_TARGETS = ("kubernetes", "ecs", "fargate", "vms")
AWS_ONLY_TARGETS = ("ecs", "fargate")
TARGET_ALIASES = {"eks": "kubernetes", "gke": "kubernetes", "aks": "kubernetes", "oke": "kubernetes",
                  "k8s": "kubernetes", "openshift": "kubernetes", "podman": "vms", "docker": "vms", "vm": "vms"}

# --- Provenance ------------------------------------------------------------------------
BENCHMARKS_URL = "https://tyk.io/performance-benchmarks/"
REDIS_DOCS = "https://tyk.io/docs/api-management/in-memory-database"
PERSISTENT_DB_DOCS = "https://tyk.io/docs/api-management/persistent-database"
MONGO_DOCS = "https://tyk.io/docs/tyk-configuration-reference/mongodb"
PLANNING_DOCS = "https://tyk.io/docs/planning-for-production"
GATEWAY_CONFIG_DOCS = "https://tyk.io/docs/tyk-oss-gateway/configuration"

SOURCES = {
    "RPS_PER_VCPU": ("published", f"{BENCHMARKS_URL} (16-vCPU machine of each family / 16, rounded to 10)"),
    "RPS_PER_VCPU_BURSTABLE": ("published", f"{BENCHMARKS_URL} (2-vCPU burstable machine / 2, rounded to 10)"),
    "BENCHMARK_PROXY_FOR_UNPUBLISHED": ("assumption", "no published run for OCI, on-premises or private-cloud hardware"),
    "API_KEY_KB": ("published", f"{REDIS_DOCS} (session size around 1KB)"),
    "ANALYTICS_BASE_KB": ("published", f"{REDIS_DOCS} (basic analytics records typically around 2KB); a minimal keyless record is 1,011 bytes on Tyk Gateway 5.16.0"),
    "DETAILED_HEADERS_KB": ("assumption", "planning allowance for request and response headers in a detailed record; on Tyk Gateway 5.16.0 typical headers add 669 base64 bytes, about 0.5 KB raw"),
    "BASE64_FACTOR": ("published", f"{REDIS_DOCS} (detailed records hold the full base64-encoded request and response); on Tyk Gateway 5.16.0 a detailed record for a 2 KB request and 8 KB response is 15,387 bytes"),
    "NONAGG_RECORD_KB": ("assumption", f"published about 1KB per stored record ({PERSISTENT_DB_DOCS}); a basic record takes 546 bytes per PostgreSQL 16 row with indexes; planned at 2 KB"),
    "API_DEF_KB": ("assumption", f"within the published range of a few KB to 100KB+ ({PERSISTENT_DB_DOCS})"),
    "POLICY_KB": ("published", f"{PERSISTENT_DB_DOCS} (policy roughly 0.7-2KB)"),
    "AGG_ITEM_KB": ("assumption", f"upper bound: every active key and API has a row every hour; the published average is about 40KB per hourly aggregate record ({PERSISTENT_DB_DOCS})"),
    "MONGO_LOG_INDEX_KB": ("published", f"{MONGO_DOCS} (log index around 30% of the collection; 1KB records per {PERSISTENT_DB_DOCS})"),
    "MONGO_RAM_FACTOR": ("published", f"{MONGO_DOCS} (doubling the index and aggregate working set)"),
    "UTIL": ("assumption", "data stores planned at 70% utilisation"),
    "PLANNING_UTIL": ("assumption", "gateways planned at 40% of benchmarked throughput"),
    "SMALL_DB_FACTOR": ("price", "small burstable managed PostgreSQL approximated at 0.4 x the standard instance"),
    "CACHE_SHARE": ("assumption", "10% of average traffic served from the response cache"),
    "CACHE_TTL_S": ("assumption", "60 s cache TTL"),
    "PUMP_RECORDS_PER_S_PER_INSTANCE": ("assumption", "conservative planning rate: one Pump 1.18.0 with sql + sql_aggregate writes about 2,700 to 13,900 records/s into PostgreSQL depending on the host; no public Tyk page gives a Pump throughput"),
    "GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S": ("published", f"{GATEWAY_CONFIG_DOCS} (analytics_config.storage_expiration_time, default 60 seconds); the TTL is set when the first unprocessed record is written, so unprocessed records are deleted 60 s after the first one"),
    "REDIS_SERVERLESS_FIT_RPS": ("assumption", "below 2,000 RPS per plane a serverless Redis tier is suggested"),
    "REDIS_SINGLE_NODE_MAX_GB": ("assumption", "85% of the largest ElastiCache node in AWS_REDIS_NODES"),
    "OUTAGE_CLASS_BUFFER_GB": ("assumption", "buffers above 10 GB get a primary and two replicas"),
    "MIN_CONN_POOL": ("published", f"{PLANNING_DOCS} and {GATEWAY_CONFIG_DOCS} (max_idle_connections_per_host: Tyk recommends 500; unset means the Go default of 2)"),
    "DAYS_PER_MONTH": ("assumption", "30.4 days per month"),
    "EU_FACTOR": ("price", "EU list premium over US approximated at 12%"),
    "PRIVATE_EGRESS_PER_GB": ("price", "same-cloud private transfer approximated at $0.01/GB"),
    "CLOUDS": ("price", f"list on-demand snapshot of {PRICE_SNAPSHOT}; {PRICE_RECHECK}"),
    "ATLAS_TIERS": ("price", f"MongoDB Atlas list snapshot of {PRICE_SNAPSHOT}"),
    "ATLAS_EXTRA_STORAGE_PER_GB": ("price", f"MongoDB Atlas extra storage approximation, snapshot of {PRICE_SNAPSHOT}"),
    "AWS_REDIS_NODES": ("price", f"ElastiCache node prices, us-east-1; {PRICE_RECHECK}"),
    "AWS_REDIS_DEV_NODES": ("price", f"ElastiCache burstable node prices, us-east-1; {PRICE_RECHECK}"),
    "AWS_REDIS_USABLE": ("assumption", "85% of node memory usable with reserved memory tuned to about 15%"),
    "FARGATE_VCPU_MONTH": ("price", f"AWS Fargate us-east-1 list; {PRICE_RECHECK}"),
    "FARGATE_GB_MONTH": ("price", f"AWS Fargate us-east-1 list; {PRICE_RECHECK}"),
    "GATEWAY_RAM_GB_PER_VCPU": ("assumption", "2 GB of memory requested per gateway vCPU"),
    "HPA_TARGET_CPU": ("assumption", "autoscale at 60% CPU"),
    "HPA_BURST_FACTOR": ("assumption", "autoscaling ceiling absorbs twice the design peak"),
    "SELF_HOSTED_DISK_GB": ("assumption", "20 GB system disks"),
    "CONTROL_PLANE_NODE_VCPU": ("assumption", "control-plane node of 4 vCPU / 8 GB"),
}
