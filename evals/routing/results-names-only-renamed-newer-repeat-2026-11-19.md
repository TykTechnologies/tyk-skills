# Routing results: names-only (renamed-newer-repeat)

- Date: 2026-11-19; Claude Code 2.1.319 (Claude Code); model claude-opus-5-5
- Prompts: 36; correct 35 (positives 24/24, near-misses 11/12); errors 0
- Cost $2.76; wall time 53.6 s
- Renames: `tyk-api-definitions-routing` -> `tyk-api-definitions-routing`, `tyk-websockets-streams-webhooks` -> `tyk-websockets-streams-webhooks`

| Skill | Own prompts | Near-misses routed right | Near-misses pulled in | Chosen wrongly elsewhere |
| --- | --- | --- | --- | --- |
| tyk-api-definitions-routing (as tyk-api-definitions-routing) | 4/4 | 2/2 | 0 | 0 |
| tyk-facts | 4/4 | 2/2 | 0 | 0 |
| tyk-observability | 4/4 | 2/2 | 0 | 0 |
| tyk-websockets-streams-webhooks (as tyk-websockets-streams-webhooks) | 4/4 | 2/2 | 0 | 0 |
| tyk-troubleshooting | 4/4 | 2/2 | 0 | 0 |
| tyk-upgrades | 4/4 | 1/2 | 1 | 0 |

## Misrouted prompts

| Id | Expected | Chosen |
| --- | --- | --- |
| upgrades-n1 | tyk-facts | tyk-upgrades |
