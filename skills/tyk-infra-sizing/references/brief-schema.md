Last verified: 2026-11-19 against the sizing engine in `scripts/sizing_engine.py` (engine version 2.0.0)

# Sizing brief schema

A brief is one JSON object (YAML also works when PyYAML is installed). Only a traffic figure
and a cloud are required; everything else has a default that the engine records as an
assumption and turns into an open question. `examples/multi-region-hybrid.json` is a complete
example.

The engine validates the whole brief before sizing anything and stops with one line naming
the field when:

- a key is not one listed on this page, at any level (a typo never becomes a silent default);
- a value has the wrong type: numbers must be JSON numbers, flags JSON booleans, objects
  objects (`"workload": []` and `"3000"` are rejected, not defaulted);
- two figures contradict each other: an average and a volume, two volumes, or a peak, an
  average and `peak_multiplier` that differ by more than 5%, or the same payload size given
  under both `traffic` and `workload` with different values;
- a target does not exist on the cloud: `ecs` and `fargate` are AWS only.

## Top level

| Field | Required | Meaning |
| --- | --- | --- |
| `organisation` | no | Your organisation's display name for titles and diagrams; also names the default `<project>` folder. Never commit a real one. |
| `title` | no | Report title. Default "Infrastructure Sizing". |
| `prepared_by` | no | Report author. Default "Tyk". |
| `date` | no | ISO date. Default today. |
| `context.summary` | no | One or two sentences on the use case, shown under Scope. |
| `context.drivers` | no | List of requirements the sizing reflects. |
| `traffic` | yes | See below. |
| `workload` | no | Estate-wide defaults, see below. |
| `cloud` | yes, unless every plane sets one | `aws`, `gcp`, `azure`, `oci`, `on-prem`, `private-cloud`. |
| `region` | no | `us` or `eu` (price basis). Default `us`. |
| `target` | no | `kubernetes` (aliases eks, gke, aks, oke, openshift, k8s), `ecs` and `fargate` (AWS only), `vms` (aliases podman, docker, vm). Default `kubernetes`. |
| `environments` | no | List of environments, at least one. Left out: one production environment with one data plane. |
| `scenarios` | no | Alternatives to compare, see below. |

## `traffic`

Give at least one of `peak_rps`, `avg_rps`, `annual_requests`, `monthly_requests`,
`daily_requests`. Several are accepted only when they agree within 5%.

| Field | Default | Meaning |
| --- | --- | --- |
| `peak_multiplier` | 3 | Peak = average x multiplier when peak is not given; average = peak / multiplier when only the peak is given. |
| `avg_request_kb`, `avg_response_kb` | 2, 8 | Payload sizes (also accepted under `workload`). |
| `upstream_latency_ms` | 50 | Upstream response time (also accepted under `workload`). |

## `workload`

| Field | Default | Values |
| --- | --- | --- |
| `auth_profile` | `full-middleware` | `keyless`, `api-key-auth`, `auth-and-quota`, `full-middleware` |
| `throughput_factor` | 1.0 | 0.05 to 1. Derate for custom plugins or heavy transformations. |
| `analytics` | `enabled` | `enabled`, `disabled` |
| `detailed_analytics` | false | Record full request and response bodies. |
| `request_log_retention_days` | 7 | 1 to 90 |
| `aggregate_retention_days` | 90 | 7 to 365 |
| `analytics_buffer_seconds` | 30 | 10 to 86,400: how long a Pump or database outage is survived. Above 60, gateways need `analytics_config.storage_expiration_time` raised to match |
| `peak_duration_seconds` | not set | 60 to 86,400: how long the peak lasts. Not set: Pump keeps up with the full peak. Set: Pump may fall behind during the peak as long as the backlog fits the analytics buffer |
| `response_caching` | true | |
| `active_api_keys` | 10,000 | Keys, tokens or sessions live at once |
| `api_definitions`, `policies` | 100, 200 | |
| `developer_portal` | `none` | `none`, `internal`, `partner-facing` |
| `storage_engine` | `postgresql` | `postgresql`, `mongodb` |
| `multi_dc_analytics` | `hybrid-aggregate` | `control-plane`, `hybrid-aggregate`, `edge-local` |
| `internet_egress_share` | 1.0 | 0 to 1 |

## `environments[]`

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | "Environment N" | Unique. |
| `posture` | first `production`, others `non-production` | Non-production allows burstable tiers. |
| `resilience` | `ha` for production, `single-node` otherwise | |
| `description` | | Shown in the non-production section. |
| `traffic` | production: estate traffic | `{"fraction": 0.1}` of estate traffic (nothing else beside it), or its own `peak_rps`, `avg_rps`, `peak_multiplier` or volume. Non-production without traffic: 5% of estate peak, 10 RPS floor, flagged. |
| `workload` | | Overrides of estate workload fields for this environment. |
| `cloud`, `region`, `target`, `gateway_vcpu` | estate values | Defaults for this environment's planes. |
| `data_planes` | 1 | A count (even split) or a list of planes. |
| `control_plane` | dedicated, beside the plane that hosts it | `{"mode": "shared", "shared_with": "<env>"}` to reuse another environment's control plane (no placement keys then). Or place it: `target` alone keeps it beside the host plane; `cloud`, `region` or `label` put it beside the first plane they match, and when no plane matches it is remote, so every data plane becomes an MDCB worker. A remote control plane cannot be combined with `hosts_control_plane: true` on a plane. |

## `data_planes[]`

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | DPn | Unique within the environment. |
| `label` | `name` | Display name in tables and diagrams. |
| `cloud`, `region`, `target` | environment values | |
| `traffic_share` | even split | Shares must sum to 1; missing shares split the remainder. |
| `hosts_control_plane` | first plane | At most one plane hosts the control plane; none when it is remote. |
| `gateway_vcpu` | 4 production, 2 otherwise | vCPU per gateway instance (Fargate: 1, 2, 4, 8 or 16). |
| `min_gateways` | 0 | Floor on the gateway count. |

## `scenarios[]`

```json
{"name": "Analytics disabled", "environment": "Production",
 "overrides": {"analytics": "disabled"},
 "description": "No Pump and no Dashboard analytics."}
```

`overrides` takes any `workload` field (at least one). The whole estate is recomputed with the
override, so a change in an environment that shares a control plane also moves the host's
analytics store and cost. The report compares gateways, Pump, Redis, the analytics store that
holds the environment's data, the environment's monthly cost and the estate's. Common scenarios:
analytics disabled, a longer outage tolerance, detailed analytics on, a different storage
engine, or a lower `internet_egress_share` once private connectivity is agreed.
