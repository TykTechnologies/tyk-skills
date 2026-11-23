# Proof of concept plan: template

Last verified: 2026-11-19 against the tyk-demo-builder module list and proof IDs (`scripts/init_demo_bundle.py --help`, its `SKILL.md`) and the acceptance criteria in this skill

Copy this file to `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/docs/poc-plan.md` and fill the
`<...>` you can. Where a value is not known yet, write `unknown` and list it as an open point;
never invent one, and never write zero for an unknown cost.
Agree it with the people who will sign off before building anything. Keep commercial quotes and
licence keys out of this file; reference them by name.

---

# Proof of concept plan: <project>

- Version: <n>, date: <YYYY-MM-DD>
- Product under evaluation: Tyk <components>, versions resolved on <date> (see the `tyk-facts`
  skill, `references/versions-and-images.md`)
- Incumbent or alternative: <gateway being replaced or compared, version>

## 1. Scope

- In scope: <APIs, auth types, environments, protocols (REST, GraphQL, gRPC, MCP, streams)>
- Out of scope: <for example production cut-over, portal theming, load at full production scale>
- Constraints: <network, air-gapped, Kubernetes distribution, identity provider, data residency,
  licences available (trial key requested on <date>)>

## 2. Acceptance criteria

One row per criterion. Start from `acceptance-criteria.md` and add the project's own.

| ID | Criterion | Mandatory? | Threshold agreed | Builder modules / proof IDs (prerequisites) | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | GitOps delivery and rollback | <mandatory / optional> | <commit-to-live time, no hand edits, delivery path> | `tyk-sync` + `cicd` (`SYNC-001`, `CICD-001`) or `operator` (`OPERATOR-001`) | <name> | not started |
| C2 | Policy correctness across replicas | <mandatory / optional> | <limit, window, tolerance, limiter path> | `rate-limit`, `quota`, `gateway-sharding` (`SHARD-001`) | <name> | not started |
| C3 | Redis and control-plane resilience | <mandatory / optional> | <expected behaviour per combination and fault, recovery time> | `jwt`, `api-key`, `rate-limit`, `HEALTH-001` | <name> | not started |
| C4 | Latency and throughput | <mandatory / optional> | <offered rate, minimum achieved rate, load model (open or closed), p95 and p99 limits for the gateway run and for the difference from the baseline, error rate and what counts as an error, hardware> | `otel` + production auth module | <name> | not started |
| C5 | Three-year TCO frame | <decides the outcome? yes / no> | complete with sources | n/a (`tyk-infra-sizing`) | <name> | incomplete |
| C6 | <project-specific> | <mandatory / optional> | <...> | <...> | <name> | not started |

Status values for C1 to C4 and project criteria: `not started`, `running`, `pass`, `fail`,
`blocked (<reason>)`, `not run`, `not applicable`, `pass (waived)`. C5 is `complete` or
`incomplete`. A module
proof ID is a prerequisite for running a criterion; it never sets the criterion's status.

## 3. Environment

- Plugin and skills: `tyk-skills` plugin version <x.y.z>; resolved paths of `tyk-facts`,
  `tyk-demo-builder` and any other skill used (the output of `resolve_skill_dir.py <name>`); the
  tyk-install repository commit used by the `tyk-deployment` skill, or `not used` and the
  manual install guide used instead
- PoC stack: `tyk-demo-builder` bundle `<slug>` with modules `<list>`, at
  `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/demos/<slug>/`
- Component versions (strict tags, recorded by the builder in `demo.config.json`): Gateway
  <vX.Y.Z>, Dashboard <vX.Y.Z>, Pump <vX.Y.Z>, Portal <vX.Y.Z>, others <...>; the image digest
  of each (`docker image inspect --format '{{index .RepoDigests 0}}' <image:tag>`)
- Load-test environment for C4: <hardware class, region, network path>
- Production-like topology, if any part of the PoC needs one (Hybrid, MDCB, Kubernetes): the
  `tyk-deployment` skill's plan, tyk-install path <docker/... or kubernetes/...>
- Licences: <trial key requested / received date; scopes needed (for example `streams`,
  `governance`)>

## 4. Timeline

| Week | Activity | Exit check |
| --- | --- | --- |
| 1 | Plan agreed; trial licence in hand; stack built and rehearsed | `HEALTH-001` and all selected module proofs pass |
| 1-2 | C1 to C3 executed | evidence filed per criterion |
| 2 | C4 load tests; C5 inputs collected | numbers recorded with hardware |
| 2-3 | Report written and reviewed | sign-off meeting held |

## 5. Roles

| Role | Name | Responsibility |
| --- | --- | --- |
| Sponsor | <name> | Accepts the exit decision |
| Technical lead | <name> | Owns the plan and the report |
| Platform engineer | <name> | Builds and runs the stack |
| Security reviewer | <name> | Reviews auth, secrets, resilience findings |
| Tyk contact | <name> | Licences, quotes, questions on vendor behaviour |

## 6. Exit criteria

Apply these in order; the first that matches is the outcome. Every state falls under exactly
one.

1. No-go: any mandatory criterion is `fail` after one agreed retry.
2. Go: every mandatory criterion is `pass` or `pass (waived)`, and C5 is `complete` if the plan
   says cost decides the outcome.
3. Extend: anything else. That covers a mandatory criterion that is `blocked`, `not run`,
   `not started`, `running` or `fail` awaiting its retry, and an `incomplete` C5 when cost
   decides. Name the owner and the date for each.

Optional criteria that are `blocked`, `not run` or `fail` are listed in the report with an
owner; they do not change the outcome. A mandatory criterion can be waived only in writing, by
the sponsor, with the reason recorded next to it; its status becomes `pass (waived)`.
`not applicable` is for optional criteria only: a mandatory criterion that turns out not to
apply is first made optional, in writing, by the sponsor.

## 7. Evidence layout and run manifest

Every execution of a criterion is a run with its own ID (`<criterion>-<UTC date>-<n>`, for
example `C2-20261119-1`) and its own folder. A finished run's folder is never changed; a
re-run gets a new ID. The report names the run each verdict rests on.

```text
${TYK_WORKSPACE:-~/tyk-workspace}/<project>/
  demos/<slug>/                 # tyk-demo-builder bundle; its own evidence/ and notes/
  docs/
    poc-plan.md                 # this file, filled
    poc-report.md               # results, per criterion, each naming its run ID
    poc-evidence/
      C1-gitops/<run-id>/       # manifest.json, git log, pipeline output, request captures
      C2-replicas/<run-id>/     # manifest.json, limiter config, per-request log, counts
      C3-resilience/<run-id>/   # manifest.json, traffic log per fault, timestamps, gateway logs
      C4-performance/<run-id>/  # manifest.json, load profile, raw output and histograms, hardware
      C5-tco/                   # filled TCO table (quotes referenced, not copied)
  sizing/                       # tyk-infra-sizing pack, if produced
```

`manifest.json` for each run, written before the first request and completed after the last:

| Field | Content |
| --- | --- |
| `run_id`, `criterion` | as above |
| `started_utc`, `finished_utc` | ISO 8601 timestamps |
| `operator` | who ran it |
| `stack` | bundle slug or topology, and the commit of the configuration repository |
| `images` | every image with its tag and digest |
| `config` | gateway settings that decide the result (limiter, analytics, TLS) |
| `commands` | the exact commands, and the load profile file for C4 |
| `tools` | tool names and versions (k6, curl, the CI runner) |
| `evidence_kind` | `runtime observation`, `documentation`, `static check`, `vendor quote` or `estimate` |
| `verdict` | `pass`, `fail`, `blocked (<reason>)` or `not run`, with one sentence of why |
| `limits` | what this run does not show (topology, scale, duration) |
| `redaction` | how credentials and personal data were removed from the captures |

Never store licence keys, private keys, personal data or vendor quotes in a public
repository; keep the `.env` files the builder generates private.

## 8. TCO table (C5)

Currency: <...>. Figures as of: <date>. Quantities assumed: <data planes, environments, API
count, support level>.

| Cost line | One-off or recurring | Year 1 | Year 2 | Year 3 | Source |
| --- | --- | --- | --- | --- | --- |
| Tyk: licence (plan: <Core / Enterprise>) | recurring | <from quote> | | | <quote reference> |
| Tyk: infrastructure (gateways, Redis, control plane, analytics store) | recurring | | | | <sizing pack> |
| Tyk: operations effort (FTE x rate) | recurring | | | | <estimate by> |
| Tyk: migration effort | one-off | | | | <migration plan> |
| Tyk: training and enablement | one-off | | | | <estimate by> |
| Tyk: total | | | | | only if no cell above is open |
| Alternative: licence | recurring | | | | <quote reference> |
| Alternative: infrastructure | recurring | | | | <estimate by> |
| Alternative: operations effort (FTE x rate) | recurring | | | | <estimate by> |
| Alternative: migration effort | one-off | | | | <estimate by; zero only if it is the incumbent> |
| Alternative: training and enablement | one-off | | | | <estimate by> |
| Alternative: total | | | | | only if no cell above is open |

Leave a cell empty rather than guess: an empty cell is unknown, not zero, and a column with an
empty cell has no total. Tyk publishes no list prices. If the incumbent keeps running during
the migration, add its cost for that period to the Tyk side.

## 9. Report outline (`docs/poc-report.md`)

1. Summary: decision recommended, one line per criterion with status.
2. Environment as built: versions, modules, topology, deviations from the plan.
3. Per criterion: procedure followed, result, the run ID and evidence path it rests on, and
   the evidence kind (runtime observation, documentation, static check, vendor quote or
   estimate). Only a runtime observation can pass C1 to C4.
4. Findings and open points, each with an owner.
5. Next steps: production topology (`tyk-deployment`), capacity (`tyk-infra-sizing`), migration
   (`tyk-migration-planner`).
