# Routing results: names-only (renamed-newer)

- Date: 2026-11-19; Claude Code 2.1.319 (Claude Code); model claude-opus-5-5
- Prompts: 120; correct 116 (positives 78/80, near-misses 38/40); errors 0
- Cost $8.89; wall time 188.5 s
- Renames: `tyk-api-definitions-routing` -> `tyk-api-definitions-routing`, `tyk-websockets-streams-webhooks` -> `tyk-websockets-streams-webhooks`

| Skill | Own prompts | Near-misses routed right | Near-misses pulled in | Chosen wrongly elsewhere |
| --- | --- | --- | --- | --- |
| tyk-ai-gateway | 4/4 | 2/2 | 0 | 1 |
| tyk-api-definitions-routing (as tyk-api-definitions-routing) | 4/4 | 2/2 | 0 | 0 |
| tyk-api-governance | 4/4 | 2/2 | 0 | 0 |
| tyk-authentication | 4/4 | 2/2 | 0 | 0 |
| tyk-custom-plugins | 4/4 | 2/2 | 0 | 0 |
| tyk-demo-builder | 3/4 | 1/2 | 0 | 0 |
| tyk-developer-portal-branding | 4/4 | 2/2 | 0 | 0 |
| tyk-developer-portal-setup | 4/4 | 2/2 | 0 | 0 |
| tyk-excalidraw-diagrams | 4/4 | 2/2 | 0 | 0 |
| tyk-facts | 4/4 | 2/2 | 0 | 0 |
| tyk-gitops-sync-operator | 4/4 | 2/2 | 0 | 0 |
| tyk-infra-sizing | 4/4 | 2/2 | 0 | 1 |
| tyk-mcp-gateway | 4/4 | 2/2 | 0 | 0 |
| tyk-migration-planner | 3/4 | 2/2 | 0 | 0 |
| tyk-observability | 4/4 | 2/2 | 0 | 0 |
| tyk-poc-runbook | 4/4 | 2/2 | 0 | 1 |
| tyk-rate-limits-keys-policies | 4/4 | 2/2 | 0 | 0 |
| tyk-websockets-streams-webhooks (as tyk-websockets-streams-webhooks) | 4/4 | 2/2 | 0 | 0 |
| tyk-troubleshooting | 4/4 | 2/2 | 0 | 0 |
| tyk-upgrades | 4/4 | 1/2 | 1 | 0 |

## Misrouted prompts

| Id | Expected | Chosen |
| --- | --- | --- |
| demo-builder-p3 | tyk-demo-builder | tyk-poc-runbook |
| demo-builder-n2 | none | tyk-infra-sizing |
| migration-planner-p4 | tyk-migration-planner | tyk-ai-gateway |
| upgrades-n1 | tyk-facts | tyk-upgrades |
