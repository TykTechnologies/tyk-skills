---
name: tyk-infra-sizing
description: Size self-managed Tyk infrastructure from stated requirements and produce a sizing pack - the calculation, a Markdown report and an Excalidraw topology per environment - covering gateways, Redis, Pump, the analytics store, MDCB, N-1 checks, cost and open questions. Use when asked to size, dimension or cost a Tyk deployment, how many gateways or how much Redis, EKS vs ECS vs VMs, multi-region or hybrid MDCB estates, from an RFP or notes. Not for a diagram without sizing, which is the `tyk-excalidraw-diagrams` skill.
---

# Tyk Infrastructure Sizing

Turn what you know about a planned self-managed Tyk deployment into a sizing you can defend:
every number comes from `scripts/sizing_engine.py` and a stated input, every constant is
labelled as a published Tyk figure or a planning assumption, and the same brief
produces the calculation, the diagrams and the report. Never type a sizing number into a
document by hand; change the brief and rerun.

What it needs: Python 3. The engine and the Markdown report use the standard
library only; the diagrams need Pillow (`requirements-diagrams.txt` at the plugin root) and the
`tyk-excalidraw-diagrams` skill, beside this one or installed. A DOCX is rendered only when the optional
`tyk-docx-theme` skill and `python-docx` are installed. Which Tyk components need a licence is
the `tyk-facts` skill's subject; this skill sizes infrastructure and states no price for Tyk.

## Ask first

Ask only what the material does not answer. Each unanswered input takes its default, is
labelled "Assumed" in the report and becomes an open question.

| Question | Default |
| --- | --- |
| Request volume (per year, month or day) or average RPS, and the peak RPS or peak-to-average ratio | Required. Peak = 3 x average |
| How long the peak lasts | Unknown: Pump is sized to keep up with the full peak |
| Regions, data centres or clouds that serve traffic, and the share each carries | One data plane; an even split |
| Where the control plane lives: beside one data plane, or on its own site | Beside the first data plane |
| Platform: Kubernetes, ECS, Fargate or VMs | Kubernetes |
| Environments (production, staging, development) and whether they share a control plane | One production environment |
| Gateway features on the hot path; custom plugins | Full middleware; no derating |
| Active keys or tokens, APIs, policies | 10,000 / 100 / 200 |
| Analytics on or off, log and aggregate retention, outage tolerance | On, 7 days, 90 days, 30 seconds |
| PostgreSQL or MongoDB for analytics | PostgreSQL |
| Share of responses leaving the cloud to the internet | All of them (worst case) |

## Workflow

1. **Extract inputs.** Read the supplied material (RFP, notes, monitoring figures, an older
   sizing to redo). Map it onto the brief with `references/intake-questions.md`. Recompute
   earlier sizings rather than copying them; when two sources disagree, bring both figures
   to the user. Anything not supplied stays unset.
2. **Write the brief** as JSON in the project's `sizing/` folder, outside this plugin:
   `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/sizing/brief.json`. Every field is in
   `references/brief-schema.md`; `examples/multi-region-hybrid.json` is a complete estate.
   The engine rejects unknown keys, wrong types and contradictory traffic figures with a
   one-line message, so a typo never becomes a silent default.
3. **Size and iterate.**
   ```bash
   python3 scripts/size_estate.py brief.json
   ```
   Check the traffic derivation, plane shares and environments against the requirements.
   Read every note: risks (an N-1 shortfall, a store no listed tier fits), decisions
   (`analytics_config.storage_expiration_time` for long outage tolerances) and limits. Add
   `scenarios` for the options being weighed; a scenario recomputes the whole estate. Share
   the summary and its questions with the user before building the pack when inputs are thin.
4. **Build the pack.**
   ```bash
   python3 scripts/build_sizing_pack.py brief.json            # or --out <folder>, --no-diagram
   ```
   Without `--out` it writes to `${TYK_WORKSPACE:-~/tyk-workspace}/<organisation>/sizing/`:
   `sizing-result.json`, `sizing-summary.md`, `sizing-content.json`, `sizing-report.md` (the
   production topology embedded), and one `.excalidraw` file and PNG per environment. It exits
   2 with `brief error:` and writes nothing when the brief is invalid.
5. **Look at the output.** Read each diagram PNG (and the DOCX preview pages, if rendered)
   with an image viewer or the Read tool: overlapping labels and clipped cells are invisible in
   JSON. Hand edits to a diagram follow the `tyk-excalidraw-diagrams` skill.
6. **Deliver** `sizing-report.md` (or the DOCX), the `.excalidraw` files and PNGs. Keep
   `sizing-result.json` and `sizing-summary.md` as the working record. Turn the open questions
   into a short list for whoever owns each answer, and rerun as answers arrive.

## References

| Need | Read |
| --- | --- |
| Every formula, every constant and the kind of evidence behind it, the analytics record facts, how to refresh prices | `references/sizing-method.md` |
| Every brief field, default and validation rule | `references/brief-schema.md` |
| What to ask, in priority order, and how gaps become questions | `references/intake-questions.md` |
| A neutral four-plane, three-environment estate | `examples/multi-region-hybrid.json` |
| Redis memory, Valkey, compression, separate stores | https://tyk.io/docs/api-management/in-memory-database |
| Analytics forwarding between planes | https://tyk.io/docs/api-management/dashboard-analytics/data-plane-pump |
| MDCB (port 9091 by default for self-managed) | https://tyk.io/docs/api-management/mdcb |
| Published benchmark runs | https://tyk.io/performance-benchmarks/ |

## Pitfalls

- **Gateways delete unprocessed analytics after 60 seconds by default.** The key that buffers
  analytics in Redis gets a TTL of `analytics_config.storage_expiration_time` (default 60)
  when the first unprocessed record is written. On Gateway 5.16.0, with Pump stopped, records
  written at 2 and 33 seconds are all gone at 63 seconds; with the setting at 600 they are
  still there at 72 seconds. An outage tolerance above 60 seconds needs this setting
  raised on every gateway, or the Redis buffer it pays for never fills.
- **A Tyk OAS API created through the Gateway API records no analytics** on Gateway 5.16.0
  unless `x-tyk-api-gateway.middleware.global.trafficLogs.enabled` is `true`.
  A load test that should prove the analytics sizing must turn it on.
- **Detailed analytics is base64.** The raw request and response, headers included, are stored
  base64-encoded, a third larger than the payload: 15,387 bytes per record for a 2 KB request
  and an 8 KB response on 5.16.0. Enable it per API, not globally.
- **Non-production inherits the estate workload.** Override active keys, retention and portal
  in the environment's `workload`, or a staging estate is sized like production storage.
- **A control plane placed in another cloud, region or site is remote.** Every data plane then
  becomes an MDCB worker and ships analytics across; say so in the brief with
  `control_plane.cloud`, `region` or `label` and no `hosts_control_plane` flag.
- **Earlier sizing documents are inputs, not baselines.** Recompute every number they give.

## Limitations

- PostgreSQL and MongoDB compute (CPU, IOPS) is not modelled beyond MongoDB memory. The
  database shown is a starting shape; the report gives the write rate to load-test it at.
- Pump throughput is a planning rate for a PostgreSQL sink; no figure exists for MongoDB
  sinks or the Hybrid Pump to MDCB, and no public Tyk page gives one.
- Aggregate storage assumes every active key and API has a row every hour: an upper bound.
- The N-1 check is arithmetic on the planning rate, not proof of failover, zone placement or
  autoscaling speed.
- Prices are an approximate list-price snapshot, US or EU basis only. Load balancers, NAT,
  snapshots, observability storage, support and Tyk licensing are excluded; on-premises,
  private-cloud and edge-local analytics sinks are sized or listed but never priced. When no
  listed MongoDB Atlas tier fits, the store is reported as unpriced rather than forced into one.
- Gateway rates for OCI, on-premises and private-cloud hardware borrow the AWS figures; the
  AWS burstable non-production node borrows the published t2.medium run.

## Before reporting success

- [ ] Traffic, shares and environments in `sizing-summary.md` match the supplied figures.
- [ ] Every "Assumed" input is in the open questions, and the user has seen them.
- [ ] No risk note is left unaddressed (N-1 shortfall, sharded Redis, an unpriced store).
- [ ] Outage tolerance above 60 seconds: the report tells the reader to raise
      `analytics_config.storage_expiration_time`.
- [ ] Each diagram PNG was opened and read; no label overlaps or runs off its zone.
- [ ] The report cites only public pages, and no figure was typed by hand.
- [ ] Nothing was written inside the plugin folder.

## Hand-offs

- Drawing or restyling an architecture diagram without sizing: the `tyk-excalidraw-diagrams` skill.
- Licensing, packages and which components need a licence: the `tyk-facts` skill.
- Building a demo or proof-of-concept stack to load-test the sizing: the `tyk-demo-builder`
  skill, with acceptance criteria from the `tyk-poc-runbook` skill.
- Migration planning from another gateway: the `tyk-migration-planner` skill.
- Installing Tyk on the chosen platform: the `tyk-deployment-planner` skill.
