# Sizing checks

Last verified: 2026-11-20 against the tyk-install configuration files (Gateway `v5.16.0`, tyk-charts `5.5.0`) and https://tyk.io/docs/planning-for-production

A readiness review checks that a configuration is set up to scale; it does not produce the
numbers. Gateway count, CPU and memory per Gateway, Redis memory, the analytics store and cost
come from the `tyk-infra-sizing` skill, which carries the method and its sources. Hand the user
there for any number, and record its output (or "not sized yet") in the report.

## What to check in the configuration

Each item is a finding for the report, in the bucket `production-readiness.md` describes.

| Check | Where | Bucket when it fails |
| --- | --- | --- |
| Two or more Gateways, load balanced ("Two or more Tyk Gateways, load balanced, each on a separate machine", https://tyk.io/docs/planning-for-production) | replica count in the values file, or the host list | needs changing |
| CPU and memory requests and limits set on Kubernetes | `resources:` blocks (`resources: {}` in the tyk-install `helm-oss` and `helm-mdcb` values sets none) | needs a decision: numbers from the `tyk-infra-sizing` skill |
| Autoscaling | `autoscaling.enabled` (`false` in tyk-install `helm-self-managed`, with commented bounds) | needs a decision, once a measured baseline exists |
| Analytics buffered in a separate Redis | `TYK_GW_ENABLESEPERATEANALYTICSSTORE` and its `# PROD/PERFORMANCE:` marker | needs changing for a busy Gateway |
| Detailed recording off | `TYK_GW_ANALYTICSCONFIG_ENABLEDETAILEDRECORDING` and its `# PROD:` marker | needs changing unless it is a deliberate, bounded choice |
| JavaScript middleware engine off when unused | `TYK_GW_ENABLEJSVM` and its `# PROD:` marker ("Set to false if not using JavaScript middlewares") | needs changing when no API uses JavaScript middleware |
| Redis connection pools | `TYK_GW_STORAGE_MAXACTIVE`, `TYK_GW_CACHESTORAGE_MAXACTIVE`, `TYK_GW_ANALYTICSSTORAGE_MAXACTIVE` (the cache pool carries a `# PROD/PERFORMANCE:` marker) | needs a decision: sized with the traffic |
| Keep-alive connections per host | `max_idle_connections_per_host`, which the production guide lists among its optimisation settings at `500` (https://tyk.io/docs/planning-for-production, "Use the optimization settings") | needs a decision |
| Distributed rate limiter | `TYK_GW_DRLTHRESHOLD` and its `# PROD/PERFORMANCE:` marker | needs a decision; how the limiters count is in the `tyk-rate-limits-keys-policies` skill |

## Measure, then size

Numbers from a benchmark are only as good as the test: measure with the real API definition, the
real authentication method and representative payloads, on the intended hardware class. The
`tyk-poc-runbook` skill has the load-test acceptance criterion and the evidence to keep; the
`tyk-infra-sizing` skill turns the measured traffic profile into a sizing pack.
