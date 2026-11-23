# Routing results: names-only (merged)

- Date: 2026-11-23; Claude Code 2.1.319 (Claude Code); model claude-opus-5-5
- Prompts: 138; correct 133 (positives 88/92, near-misses 45/46); errors 0
- Cost $9.91; wall time 228.4 s

| Skill | Own prompts | Near-misses routed right | Near-misses pulled in | Chosen wrongly elsewhere |
| --- | --- | --- | --- | --- |
| tyk-ai-gateway | 4/4 | 2/2 | 0 | 1 |
| tyk-api-definitions-routing | 4/4 | 2/2 | 0 | 0 |
| tyk-api-governance | 4/4 | 2/2 | 0 | 0 |
| tyk-authentication | 4/4 | 2/2 | 0 | 0 |
| tyk-custom-plugins | 4/4 | 2/2 | 0 | 0 |
| tyk-demo-builder | 3/4 | 2/2 | 0 | 0 |
| tyk-deployment | 11/12 | 6/6 | 0 | 1 |
| tyk-developer-portal-branding | 4/4 | 2/2 | 0 | 0 |
| tyk-developer-portal-setup | 4/4 | 2/2 | 0 | 0 |
| tyk-excalidraw-diagrams | 4/4 | 2/2 | 0 | 0 |
| tyk-facts | 3/4 | 2/2 | 0 | 0 |
| tyk-gitops-sync-operator | 4/4 | 2/2 | 0 | 0 |
| tyk-infra-sizing | 4/4 | 2/2 | 0 | 0 |
| tyk-mcp-gateway | 4/4 | 2/2 | 0 | 0 |
| tyk-migration-planner | 3/4 | 2/2 | 0 | 0 |
| tyk-observability | 4/4 | 2/2 | 0 | 0 |
| tyk-poc-runbook | 4/4 | 2/2 | 0 | 1 |
| tyk-rate-limits-keys-policies | 4/4 | 2/2 | 0 | 0 |
| tyk-troubleshooting | 4/4 | 2/2 | 0 | 1 |
| tyk-upgrades | 4/4 | 1/2 | 1 | 0 |
| tyk-websockets-streams-webhooks | 4/4 | 2/2 | 0 | 0 |

## Misrouted prompts

| Id | Expected | Chosen |
| --- | --- | --- |
| demo-builder-p3 | tyk-demo-builder | tyk-poc-runbook |
| facts-p4 | tyk-facts | tyk-deployment |
| migration-planner-p4 | tyk-migration-planner | tyk-ai-gateway |
| upgrades-n1 | tyk-facts | tyk-upgrades |
| deployment-runner-p3 | tyk-deployment | tyk-troubleshooting |
