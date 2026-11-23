# Routing results: names-only

- Date: 2026-11-23; Claude Code 2.1.319 (Claude Code); model claude-opus-5-5
- Prompts: 138; correct 133 (positives 88/92, near-misses 45/46); errors 0
- Cost $10.03; wall time 217.5 s

| Skill | Own prompts | Near-misses routed right | Near-misses pulled in | Chosen wrongly elsewhere |
| --- | --- | --- | --- | --- |
| tyk-ai-gateway | 4/4 | 2/2 | 0 | 1 |
| tyk-api-definitions-routing | 4/4 | 2/2 | 0 | 0 |
| tyk-api-governance | 4/4 | 2/2 | 0 | 0 |
| tyk-authentication | 4/4 | 2/2 | 0 | 0 |
| tyk-custom-plugins | 4/4 | 2/2 | 0 | 0 |
| tyk-demo-builder | 3/4 | 1/2 | 0 | 0 |
| tyk-deployment-planner | 4/4 | 2/2 | 0 | 0 |
| tyk-deployment-runner | 3/4 | 2/2 | 0 | 2 |
| tyk-developer-portal-branding | 4/4 | 2/2 | 0 | 0 |
| tyk-developer-portal-setup | 4/4 | 2/2 | 0 | 0 |
| tyk-excalidraw-diagrams | 4/4 | 2/2 | 0 | 0 |
| tyk-facts | 4/4 | 2/2 | 0 | 0 |
| tyk-gitops-sync-operator | 4/4 | 2/2 | 0 | 0 |
| tyk-infra-sizing | 4/4 | 2/2 | 0 | 0 |
| tyk-mcp-gateway | 4/4 | 2/2 | 0 | 0 |
| tyk-migration-planner | 3/4 | 2/2 | 0 | 0 |
| tyk-observability | 4/4 | 2/2 | 0 | 0 |
| tyk-poc-runbook | 4/4 | 2/2 | 0 | 1 |
| tyk-production-readiness | 3/4 | 2/2 | 0 | 0 |
| tyk-rate-limits-keys-policies | 4/4 | 2/2 | 0 | 0 |
| tyk-troubleshooting | 4/4 | 2/2 | 0 | 1 |
| tyk-upgrades | 4/4 | 2/2 | 0 | 0 |
| tyk-websockets-streams-webhooks | 4/4 | 2/2 | 0 | 0 |

## Misrouted prompts

| Id | Expected | Chosen |
| --- | --- | --- |
| demo-builder-p3 | tyk-demo-builder | tyk-poc-runbook |
| demo-builder-n2 | none | tyk-deployment-runner |
| migration-planner-p4 | tyk-migration-planner | tyk-ai-gateway |
| deployment-runner-p3 | tyk-deployment-runner | tyk-troubleshooting |
| production-readiness-p3 | tyk-production-readiness | tyk-deployment-runner |
