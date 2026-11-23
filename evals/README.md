# Evals

## Routing

`routing/` measures whether Claude Code picks the right skill from this plugin for a request, and
how much of that depends on the skill name alone.

Claude Code always lists every skill name to the model, but when the listing is over its budget
(about 1% of the context window) it drops the descriptions of the least-used skills first. A newly
installed skill can therefore be seen by name only, so the names have to route on their own.

### What is measured

- `routing/prompts.jsonl` holds 138 prompts, six per skill: four that should load the skill
  (`"kind": "positive"`) and two near-misses that look like it but belong to a named sibling or to
  no skill in this plugin (`"kind": "near_miss"`, `"expected"` is the sibling or `null`). The prompts
  are written as a developer would type them and never contain a skill name.
- `routing/run_routing.py` sends each prompt once to `claude -p` with this repository loaded as a
  plugin from its local path, reads the `stream-json` output and records the first `Skill` tool call.
  A prompt is correct when that call names the expected skill, or, for `null`, when no skill from
  this plugin is called.
- Two modes:
  - `descriptions`: the normal listing, every name with its description.
  - `names-only`: `SLASH_COMMAND_TOOL_CHAR_BUDGET=1` shrinks the listing budget, so Claude Code keeps
    every skill name and drops every description from this plugin. (The `skillOverrides` setting has
    a `name-only` state, but it does not apply to plugin skills.)
- Per skill the summary reports: its own positives routed right (of 4), its near-misses routed right
  (of 2), its near-misses wrongly pulled into it, and how often it was chosen for another skill's
  prompt.

Each run uses only documented flags and leaves the user's settings alone: `--setting-sources project`
from an empty temporary folder (so user settings, user plugins and personal skills are not loaded),
`--strict-mcp-config`, `--no-session-persistence`, `--tools Skill`, `--permission-mode dontAsk`, the CLI's
default model, and an appended system prompt that tells the model to load the right skill and stop,
or to answer `NONE`. At most four prompts run at once, with a per-prompt timeout.

### Run it

Requires the `claude` CLI, logged in. From the repository root:

```bash
python3 evals/routing/run_routing.py --mode descriptions
python3 evals/routing/run_routing.py --mode names-only
```

Each run writes `evals/routing/results-<mode>[-<label>]-<date>.json` (every prompt, the skill chosen,
cost and time) and a Markdown table next to it. Useful options:

- `--skills tyk-facts,tyk-upgrades` or `--ids facts-p1,facts-n1` to run a subset.
- `--rename old=new` (repeatable) with `--label <name>` to try a candidate name. The runner copies
  the plugin manifests and every `SKILL.md` to a temporary folder, renames there and runs against the
  copy; results stay keyed by the current names. The working tree is never changed.

A full 120-prompt run takes about three minutes. The CLI reported $8.80 to $8.90 for a names-only run
and $12.95 for a descriptions run on 2026-11-19.

### Latest results (2026-11-19)

Claude Code 2.1.319, default model `claude-opus-5-5`, 20 skills. Each cell is: own prompts right,
near-misses right, times chosen for another skill's prompt.

| Skill | Descriptions | Names only, run 1 | Names only, run 2 |
| --- | --- | --- | --- |
| `tyk-ai-gateway` | 4/4, 2/2, 0 | 4/4, 2/2, 1 | 4/4, 2/2, 1 |
| `tyk-api-definitions` (now `tyk-api-definitions-routing`) | 4/4, 2/2, 0 | 2/4, 2/2, 0 | 2/4, 2/2, 0 |
| `tyk-api-governance` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-authentication` | 4/4, 2/2, 0 | 3/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-custom-plugins` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-demo-builder` | 4/4, 2/2, 0 | 4/4, 1/2, 0 | 3/4, 2/2, 0 |
| `tyk-developer-portal-branding` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-developer-portal-setup` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-excalidraw-diagrams` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-facts` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-gitops-sync-operator` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-infra-sizing` | 4/4, 2/2, 0 | 4/4, 2/2, 1 | 4/4, 2/2, 0 |
| `tyk-mcp-gateway` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-migration-planner` | 4/4, 2/2, 0 | 3/4, 2/2, 0 | 3/4, 2/2, 0 |
| `tyk-observability` | 4/4, 2/2, 0 | 4/4, 2/2, 1 | 4/4, 2/2, 1 |
| `tyk-poc-runbook` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 1 |
| `tyk-rate-limits-keys-policies` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| `tyk-streams-events` (now `tyk-websockets-streams-webhooks`) | 4/4, 2/2, 0 | 2/4, 2/2, 0 | 2/4, 2/2, 0 |
| `tyk-troubleshooting` | 4/4, 2/2, 0 | 4/4, 2/2, 4 | 4/4, 2/2, 3 |
| `tyk-upgrades` | 4/4, 2/2, 0 | 4/4, 2/2, 0 | 4/4, 2/2, 0 |
| **Total** | **120/120** | **113/120** | **114/120** |

Candidate names, names-only mode:

| Current name | Candidate | Current (two runs) | Candidate | Verdict |
| --- | --- | --- | --- | --- |
| `tyk-facts` | `tyk-licensing-pricing-versions` | 4/4, 2/2 twice | 4/4, 2/2 | Tie, keep |
| `tyk-demo-builder` | `tyk-poc-builder` | 4/4, 1/2 and 3/4, 2/2 | 4/4, 1/2 | Tie, keep |
| `tyk-poc-runbook` | `tyk-poc-acceptance-tests` | 4/4, 2/2 twice | 4/4, 2/2 | Tie, keep |
| `tyk-migration-planner` | `tyk-gateway-migration` | 3/4, 2/2 twice | 3/4, 2/2 | Tie, keep |
| `tyk-excalidraw-diagrams` | `tyk-architecture-diagrams` | 4/4, 2/2 twice | 4/4, 2/2 | Tie, keep |
| `tyk-api-definitions` | `tyk-api-definitions-routing` | 2/4, 2/2 twice | 4/4, 2/2 twice | Rename wins, applied |
| `tyk-streams-events` | `tyk-websockets-streams-webhooks` | 2/4, 2/2 twice | 4/4, 2/2 twice | Rename wins, applied |

With the two winning candidates, `tyk-troubleshooting` was no longer chosen for other skills'
prompts (3 to 4 times before, 0 after) and the descriptions mode stayed at 36/36 on the affected
skills. One near-miss in a different pair moved the wrong way in both candidate runs: a question about
which version to standardise on went to `tyk-upgrades` instead of `tyk-facts`.

### Limits

- Every prompt runs once per mode, so a single prompt flipping is within noise. A rename counts only
  when it wins clearly and repeats; ties keep the current name.
- The appended system prompt asks the model to choose deliberately, which routes better than an
  unprompted session. The runs load only this plugin and Claude Code's built-in skills; a machine with
  many other skills installed has a longer, more crowded listing.
- In names-only mode the plugin prefix `tyk-skills:` stays visible, which helps the model tell Tyk
  requests from others.
