---
name: tyk-poc-runbook
description: Plan, run and report a structured proof of concept or technical evaluation of Tyk (alone or against another API gateway, API management platform or AI gateway) with agreed acceptance criteria, executable tests and kept evidence - GitOps delivery and rollback, rate limits and quotas across replicas, Redis and control-plane resilience, latency and throughput under load, and a three-year cost frame. Use when someone wants a PoC, bake-off, evaluation plan, success criteria, scorecard or go/no-go report for Tyk rather than just a demo. Not for a single feature demonstration, which is the tyk-demo-builder skill.
---

# Tyk Proof of Concept Runbook

A demo shows a feature; a proof of concept proves agreed criteria with evidence someone can
check. This runbook turns requirements into a plan, builds the stack with the `tyk-demo-builder`
skill, runs each criterion, keeps the evidence and writes the report.

## Where deliverables go

Keep everything generated outside this plugin's folder, in
`${TYK_WORKSPACE:-~/tyk-workspace}/<project>/` (`demos/<slug>/`, `sizing/`, `docs/`), unless the
user names another location. The plan, report and per-criterion evidence go under `docs/`; the
stack goes under `demos/<slug>/`. Never write licence keys, quotes, credentials or personal
data into this repository.

## Dependencies

Resolve these before planning and write the result into the plan (section 3 of the template):
the path each one resolved to and the version of the plugin it came from.

| Dependency | Needed for | Where it comes from | If it is missing |
| --- | --- | --- | --- |
| `tyk-facts` skill | every licensing, edition and version statement | this plugin | stop; reinstall this plugin |
| `tyk-demo-builder` skill | the PoC stack and its module proofs (C1 to C4 prerequisites) | this plugin | stop; reinstall this plugin |
| `tyk-infra-sizing` skill | C4 hardware class and C5 infrastructure rows | this plugin | leave those rows `unknown` |
| `tyk-excalidraw-diagrams` skill | architecture drawings in the report | this plugin | optional; a plain list of components and connections is enough |
| `tyk-deployment-planner` and `tyk-deployment-runner` skills, with a checkout of the tyk-install repository | a production-like topology: Hybrid or MDCB for C3, the intended hardware for C4 | this plugin; the skills clone https://github.com/TykTechnologies/tyk-install where the user chooses | install it by hand from the public guides (https://tyk.io/docs/tyk-self-managed, https://tyk.io/docs/deployment-options/tyk-hybrid, https://tyk.io/docs/api-management/mdcb), or mark the dependent criteria `blocked (no topology)` |
| A Dashboard licence (trial) | every builder stack; scopes such as `streams` or `governance` for those features | the `tyk-facts` skill, `references/deployment-paths.md` | `blocked (licence)` |

Locate a sibling skill with
`python3 <tyk-demo-builder-dir>/scripts/resolve_skill_dir.py <name>`. It looks in the Claude Code
personal skills folder first, then the Codex one, then beside the builder, and prints the first
match. If the printed path is not inside the same plugin installation as this skill, an older
personal copy is shadowing the bundled one: say so in the plan, and remove or update that copy
before relying on it.

## Inputs

Collect before planning; ask for what is missing rather than assuming. Defaults in brackets:

1. **Requirements**: the capabilities to prove, with priority (mandatory or optional)
   [every criterion the user names is mandatory until they say otherwise].
2. **The gateway being replaced or compared**, if any, and its version; the features in use
   (the `tyk-migration-planner` skill inventories them) [none: Tyk alone].
3. **Constraints**: deployment model (Cloud, Hybrid, Self-Managed, air-gapped), Kubernetes or not,
   identity provider, network limits, data residency, timeline, who signs off [Self-Managed on
   Docker Compose for behaviour; the intended hardware for C4].
4. **Licences**: the builder needs a Dashboard licence; request a trial early (the `tyk-facts`
   skill, `references/deployment-paths.md`). Some features need a licence scope (Streams,
   governance); check `tyk-facts` `references/editions-and-features.md` before promising them.

## Workflow

1. **Establish facts.** Use the `tyk-facts` skill for every licensing, edition and version
   statement in the plan. Resolve component versions live; do not copy them from an old plan.
2. **Plan.** Copy `references/poc-plan-template.md` to
   `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/docs/poc-plan.md`. Pick criteria from
   `references/acceptance-criteria.md` (C1 to C5) and add the project's own. Write the agreed
   threshold for each before building. Mark each criterion mandatory or optional.
3. **Map criteria to modules.** For each criterion, list the `tyk-demo-builder` modules and
   proof IDs in the plan table. Select the smallest module set that covers every criterion:

   | Criterion | Modules | Skill with the detail |
   | --- | --- | --- |
   | C1 GitOps delivery and rollback | `tyk-sync` + `cicd`, or `operator` | `tyk-gitops-sync-operator` |
   | C2 Policy correctness across replicas | `rate-limit`, `quota`, `gateway-sharding` | `tyk-rate-limits-keys-policies` |
   | C3 Redis and control-plane resilience | `jwt`, `api-key`, `rate-limit` | `tyk-authentication` |
   | C4 Latency and throughput | `otel` plus the production auth module | `tyk-observability` |
   | Migration-specific (keep URLs, keep keys, legacy credentials) | `custom-domain`, `key-import`, `js-plugin` | `tyk-migration-planner` |

   Other requirements map to the builder's other modules (Portal, MCP, governance, Streams,
   AI Studio and so on); read that skill's `SKILL.md`. API definitions for the PoC are written
   with the `tyk-api-definitions-routing` skill; MCP with the `tyk-mcp-gateway` skill; AI Studio with
   the `tyk-ai-gateway` skill; the Developer Portal with the `tyk-developer-portal-setup` skill.
4. **Build.** Run the `tyk-demo-builder` skill with the module list, output under
   `demos/<slug>/`. Let it validate, start, configure, re-run for idempotency, restart and
   rehearse. Every bundle runs `HEALTH-001`; do not start criteria until it and every selected
   module proof pass.
5. **Run each criterion.** Follow the procedure in `references/acceptance-criteria.md`. Give
   every run its own ID and folder, `docs/poc-evidence/<criterion>/<run-id>/`, with a
   `manifest.json` written before the first request (fields in the template, section 7). Never
   add to or change a finished run's folder; a re-run is a new run ID. A criterion passes only
   on evidence from a run on the running stack; a documentation statement never passes a
   criterion by itself.
6. **Handle blockers honestly.** If a criterion cannot run (licence scope, missing cluster,
   hardware), mark it `blocked (<reason>)` with an owner. Do not substitute a talk track. A
   newer run that is partial or failed replaces the verdict of an older passing run; it never
   inherits it.
7. **Load testing.** A single-host Compose stack proves behaviour, not capacity. Run C4 on the
   intended hardware class; deploy that topology with the `tyk-deployment-planner` and `tyk-deployment-runner` skills.
8. **Report.** Write `docs/poc-report.md` from the outline in the template: one line per
   criterion with status, the run ID it rests on and its evidence kind, findings, open points,
   next steps. Quote licensing and pricing only through `tyk-facts`.
9. **Hand off.** Production topology: the `tyk-deployment-planner` skill (Docker Compose, Helm,
   OpenShift, bare metal; Self-Managed, Hybrid, MDCB, AI Studio), then the
   `tyk-deployment-runner` and `tyk-production-readiness` skills. Capacity and cost inputs: the
   `tyk-infra-sizing` skill. Moving traffic from the incumbent: the `tyk-migration-planner`
   skill. Architecture drawings: the `tyk-excalidraw-diagrams` skill.

## Rules

- Agree thresholds before running; never move them after seeing results without recording why.
- Prefer Tyk OAS API definitions for every API in the PoC.
- Keep vendor reference numbers (benchmarks, ratings) as context only; the project's own runs
  are the evidence.
- No invented prices. Tyk publishes no list prices; the TCO frame uses a written quote or leaves
  the cell empty.
- Where Tyk documentation and observed behaviour differ, record both, label each, and raise the
  difference as a finding.
- Keep the verdict and the evidence kind apart: a verdict (`pass`, `fail`, ...) rests on one
  named run; the kind of evidence is one of runtime observation, documentation, static check,
  vendor quote or estimate.
- Re-runs must be reproducible: keep the module list, image tags and digests, commands and load
  profiles in the run manifest so someone else can repeat the result.

## Verification before reporting

- Every mandatory criterion has a status from the template's list and, unless `blocked` or
  `not run`, a run ID whose folder holds a manifest and the raw output.
- Every number in the report names its run ID and hardware.
- Every licensing, packaging or version statement came through the `tyk-facts` skill, with its
  date.
- The dependency table in the plan names the resolved path and plugin version of each skill
  used.

## References

| File | Contents |
| --- | --- |
| `references/acceptance-criteria.md` | C1 to C5: purpose, mechanism with sources, modules and proof IDs, procedure, pass/fail, evidence |
| `references/poc-plan-template.md` | Fill-in plan, run manifest, evidence folder layout, TCO table, report outline |
