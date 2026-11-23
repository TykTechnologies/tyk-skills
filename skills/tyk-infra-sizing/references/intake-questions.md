Last verified: 2026-11-19 against the sizing engine in `scripts/sizing_engine.py` (engine version 2.0.0)

# Intake questions

Use these to pull inputs out of requirements, RFPs and meeting notes, and to draft the
questions that settle what is still open. Ask only what the material does not answer, in
this priority order. Anything left unanswered takes the default, is labelled "Assumed" in
the report and appears in its open questions.

## 1. Traffic (drives everything)

| Ask | Brief field | If unknown |
| --- | --- | --- |
| Request volume per year, month or day, or average RPS | `traffic.*_requests` / `avg_rps` | Required: nothing can be sized without it |
| Peak RPS, or the peak-to-average ratio, and known peak events | `peak_rps` / `peak_multiplier` | 3x, flagged |
| How long the peak lasts | `workload.peak_duration_seconds` | Pump sized to keep up with the full peak |
| How traffic splits across regions or sites | `data_planes[].traffic_share` | Even split, flagged |
| Typical request and response sizes | `avg_request_kb`, `avg_response_kb` | 2 KB / 8 KB |
| Typical upstream response time | `upstream_latency_ms` | 50 ms |

## 2. Topology

| Ask | Brief field |
| --- | --- |
| Which regions, data centres or clouds must serve traffic, and why (residency, latency, isolation) | `environments[].data_planes` |
| Where the control plane should live: beside one data plane, or on its own site (then every plane is an MDCB worker) | `control_plane`, `hosts_control_plane` |
| Deployment platform: Kubernetes (which), ECS or Fargate, VMs, Podman | `target` |
| Which environments exist, whether they share a control plane, and what load each carries | `environments[]` |
| Whether any plane is on-premises or private cloud | `cloud: on-prem` |

## 3. Workload

| Ask | Brief field | Default |
| --- | --- | --- |
| Gateway features on the hot path: auth only, quotas and rate limits, transformations | `auth_profile` | full middleware |
| Custom plugins (gRPC, Go, JavaScript), JWT signing, heavy transformation | `throughput_factor` | 1.0: ask for a load-test figure before derating |
| Number of active API keys, tokens or sessions | `active_api_keys` | 10,000: always confirm for per-consumer tokens |
| Number of APIs and policies | `api_definitions`, `policies` | 100 / 200 |
| Response caching and on which APIs | `response_caching` | on |
| Developer Portal: none, internal, partner-facing | `developer_portal` | none |

## 4. Analytics

| Ask | Brief field | Default |
| --- | --- | --- |
| Is Dashboard analytics wanted, or does an external observability platform take over | `analytics` | enabled |
| Days of per-request logs that must stay queryable | `request_log_retention_days` | 7 |
| Days of aggregated reporting | `aggregate_retention_days` | 90 |
| Whether full request and response bodies must be recorded | `detailed_analytics` | false |
| How long a Pump or database outage must be survived without losing records | `analytics_buffer_seconds` | 30 s (above 60 s, `analytics_config.storage_expiration_time` must be raised on the gateways) |
| Multi-plane: must raw logs be browsable centrally | `multi_dc_analytics` | hybrid-aggregate |
| PostgreSQL or MongoDB | `storage_engine` | PostgreSQL |
| Database instance class or limits already chosen, and who load-tests it | not modelled | The engine sizes storage (and MongoDB memory), not database CPU or IOPS: record it as an open question |

## 5. Cost inputs

| Ask | Brief field | Default |
| --- | --- | --- |
| Share of responses that leave the cloud to the public internet, versus private links or same-cloud consumers | `internet_egress_share` | 1.0 (worst case) |
| Price region (US or EU basis) | `region` | us |

## Turning gaps into open questions

Group the unanswered items into a short list that the owners of each figure (application
teams, network, operations, finance) can answer quickly, most impactful first: traffic peak
and distribution, active keys, analytics retention and outage tolerance, egress share.
Rerun the sizing as answers arrive; each answer replaces an assumption.
