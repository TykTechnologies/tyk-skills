# Hardening

Last verified: 2026-11-20 against the tyk-install `docker/`, `kubernetes/` and `baremetal/` configuration files (Gateway and Dashboard `v5.16.0`, tyk-charts `5.5.0`), https://tyk.io/docs/planning-for-production and https://tyk.io/docs/api-management/in-memory-database

What `profiles.py` cannot tell you. The marker comments cover per-setting guidance; this covers
what no single marker expresses. Run the audit first, then work through this list.

## Committed development defaults: always flag

These are correct for a laptop and wrong for anything else, and the markers alone do not catch
every one, because the value in the file is the intended development value.

On a Helm path, audit the sibling `.env.example` (or the user's `.env`) as well as `values.yaml`:
the credentials a Helm deployment boots with live there, and the values file only references them
(`useSecretName`, `connectionStringSecret`). Pass both paths to `profiles.py audit`, which masks
the values.

| What | Where in tyk-install | Why it matters |
| --- | --- | --- |
| `TYK_GW_SECRET` and `TYK_GW_NODESECRET` share one committed value | `docker/self-managed/`, `docker/hybrid/` and `docker/mdcb/*/` `confs/tyk.env` | `TYK_GW_SECRET` authorises the Gateway API (https://tyk.io/docs/tyk-oss-gateway/configuration). Generate separate values, for example `openssl rand -hex 32`. |
| `TYK_ADMIN_SECRET`, `TYK_API_SECRET`, `POSTGRES_PASSWORD` | `kubernetes/helm-self-managed/.env.example` (and the other Helm `.env.example` files) | Their own `# SECURITY:` markers say "Committed development default"; the admin secret "grants full Dashboard admin access". |
| `TYK_DB_ADMINSECRET` | `docker/*/confs/tyk_analytics.env` | The Dashboard admin secret (https://tyk.io/docs/api-management/dashboard-configuration). |
| The PostgreSQL user and password | `docker/self-managed/`, `docker/mdcb/control-plane/` and `docker/ai-studio/` `docker-compose.yml` | Committed in plain text in a public repository. |
| Redis without a password | the Compose stacks | Redis holds client session data, OAuth tokens and rate-limit counters (https://tyk.io/docs/api-management/in-memory-database). |
| `TYK_GW_ALLOWINSECURECONFIGS=true`, or `allowInsecureConfigs: true` on Helm | `docker/*/confs/tyk.env`, `baremetal/confs/gateway.env`, `kubernetes/helm-self-managed/values.yaml` | Carries no marker in these files, so `audit` does not report it. Check it by hand every time. |

Name the variable and cite its `file:line`. Never print the value, even to show it is weak.

## Data stores

Single-instance Redis or PostgreSQL is a data-loss and availability risk; rank it above any
`# PROD:` finding.

- **Redis.** In the control plane it holds "API client Session data (the source of truth for the
  deployment)", and the Tyk page says to deploy it "with persistence enabled and a
  high-availability topology such as Redis Sentinel or Redis Cluster"
  (https://tyk.io/docs/api-management/in-memory-database). Connection settings, Sentinel and
  Cluster: https://tyk.io/docs/tyk-configuration-reference/redis-cluster-sentinel. Turn on TLS to
  Redis when it crosses a network boundary; the `# PROD:` markers on `*_USESSL` cover the
  settings.
- **Separate analytics Redis.** Traffic logs are buffered in Redis before Pump reads them, and
  "Under high traffic, this can place significant pressure on your primary Redis";
  `enable_separate_analytics_store` moves that buffering to its own instance (same page,
  "Separate analytics storage"). The tyk-install marker on
  `TYK_GW_ENABLESEPERATEANALYTICSSTORE` says the same.
- **Dedicated infrastructure.** The production guide: "Keep Redis and your persistent database
  (MongoDB or PostgreSQL) on dedicated infrastructure", and recommends "A separate MongoDB or
  PostgreSQL cluster" (https://tyk.io/docs/planning-for-production, "Recommended Topology").
- **PostgreSQL** holds API definitions, policies and users. It needs backups with a restore that
  has been run, not only replication: replication protects against host loss, not against a bad
  change. Backups and restores: the `tyk-upgrades` skill.

## Network

- **Isolate the control API.** The Gateway API shares the traffic port unless
  `control_api_port` is set; the guide: "This allows you to run the Gateway Control API on a
  separate port, and protect it behind a firewall if needed"
  (https://tyk.io/docs/planning-for-production#change-your-control-port). The tyk-install marker
  is on `TYK_GW_CONTROLAPIPORT`.
- **TLS** at the edge, and to Redis and PostgreSQL where they cross a network boundary.
- **Certificate validation stays on.** Every `SSLINSECURESKIPVERIFY` setting carries a `# PROD:`
  marker to keep or set it `false`; the development defaults set it `true` for the Gateway and
  Pump of the Hybrid and MDCB data planes (`docker/hybrid/confs/`, `docker/mdcb/data-plane/confs/`). List each one that is `true`.
- **Exposure is a decision.** Which of the Gateway, Dashboard, Developer Portal and MDCB are
  reachable from the internet goes in the "needs a decision" bucket; record the answer.

## Availability

- The guide recommends "Two or more Tyk Gateways, load balanced, each on a separate machine"
  (https://tyk.io/docs/planning-for-production, "Recommended Topology"). Report the replica count
  each file sets.
- On Kubernetes, report whether CPU and memory requests and limits are set (`resources: {}` in
  the tyk-install `helm-oss` and `helm-mdcb` values means none), whether autoscaling is on
  (`autoscaling.enabled` in `helm-self-managed`), and whether replicas can land on the same node.
  The numbers come from the `tyk-infra-sizing` skill; see `sizing.md`.
- MDCB control planes: more than one MDCB instance; see `multi-site.md`.

## Analytics and logging

- **Detailed recording.** `TYK_GW_ANALYTICSCONFIG_ENABLEDETAILEDRECORDING=true` in tyk-install's
  `docker/self-managed/confs/tyk.env` carries `# PROD: Set to false - increases storage and
  resource usage (use for debugging only)`. Detailed records hold full request and response
  bodies, so it is also a data-protection question. Its cost and the per-API setting: the
  `tyk-observability` skill.
- **Pump keeps up or records expire.** Records wait in Redis for Pump and expire after
  `storage_expiration_time`, so a Pump that falls behind loses them; details and the Elasticsearch
  version limits are in the `tyk-observability` skill.
- **Log level.** The marker on `TYK_GW_LOGLEVEL` says to set it to `error` in production; the
  guide says "It is recommended to set to debug only for the duration of troubleshooting"
  (https://tyk.io/docs/planning-for-production#selecting-the-appropriate-log-level).

## Version control and upgrades

- Keep API definitions and policies in version control (Tyk Sync, Tyk Operator or files for the
  open source Gateway), so the configuration can be rebuilt without a database restore: the
  `tyk-gitops-sync-operator` skill.
- Upgrade order, mixed-version windows and rollback: the `tyk-upgrades` skill.

## What not to do

Do not invent thresholds. If the markers give no number and nothing here does, say the number
needs sizing work and point at https://tyk.io/docs/planning-for-production and the
`tyk-infra-sizing` skill.

Do not report a configuration as production ready. Report which guidance it satisfies and which
it does not.
