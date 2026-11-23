# Routing results: names-only

- Date: 2026-11-19; Claude Code 2.1.319 (Claude Code); model claude-opus-5-5
- Prompts: 120; correct 113 (positives 74/80, near-misses 39/40); errors 0
- Cost $8.83; wall time 184.0 s

| Skill | Own prompts | Near-misses routed right | Near-misses pulled in | Chosen wrongly elsewhere |
| --- | --- | --- | --- | --- |
| tyk-ai-gateway | 4/4 | 2/2 | 0 | 1 |
| tyk-api-definitions | 2/4 | 2/2 | 0 | 0 |
| tyk-api-governance | 4/4 | 2/2 | 0 | 0 |
| tyk-authentication | 3/4 | 2/2 | 0 | 0 |
| tyk-custom-plugins | 4/4 | 2/2 | 0 | 0 |
| tyk-demo-builder | 4/4 | 1/2 | 0 | 0 |
| tyk-developer-portal-branding | 4/4 | 2/2 | 0 | 0 |
| tyk-developer-portal-setup | 4/4 | 2/2 | 0 | 0 |
| tyk-excalidraw-diagrams | 4/4 | 2/2 | 0 | 0 |
| tyk-facts | 4/4 | 2/2 | 0 | 0 |
| tyk-gitops-sync-operator | 4/4 | 2/2 | 0 | 0 |
| tyk-infra-sizing | 4/4 | 2/2 | 0 | 1 |
| tyk-mcp-gateway | 4/4 | 2/2 | 0 | 0 |
| tyk-migration-planner | 3/4 | 2/2 | 0 | 0 |
| tyk-observability | 4/4 | 2/2 | 0 | 1 |
| tyk-poc-runbook | 4/4 | 2/2 | 0 | 0 |
| tyk-rate-limits-keys-policies | 4/4 | 2/2 | 0 | 0 |
| tyk-streams-events | 2/4 | 2/2 | 0 | 0 |
| tyk-troubleshooting | 4/4 | 2/2 | 0 | 4 |
| tyk-upgrades | 4/4 | 2/2 | 0 | 0 |

## Misrouted prompts

| Id | Expected | Chosen |
| --- | --- | --- |
| api-definitions-p1 | tyk-api-definitions | tyk-troubleshooting |
| api-definitions-p4 | tyk-api-definitions | tyk-troubleshooting |
| authentication-p2 | tyk-authentication | tyk-troubleshooting |
| demo-builder-n2 | none | tyk-infra-sizing |
| migration-planner-p4 | tyk-migration-planner | tyk-ai-gateway |
| streams-events-p2 | tyk-streams-events | tyk-troubleshooting |
| streams-events-p3 | tyk-streams-events | tyk-observability |
