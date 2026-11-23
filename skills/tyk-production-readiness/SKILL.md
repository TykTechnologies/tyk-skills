---
name: tyk-production-readiness
description: Audit a Tyk configuration against the production, performance, security and OpenShift guidance the public tyk-install repository carries as marker comments, and report what is already right, what must change (with file:line) and what needs a decision - committed development secrets, single-instance Redis or PostgreSQL, an exposed control API, analytics load, TLS. Use when asked whether a Tyk deployment is production ready, to review a values.yaml or gateway env file before go-live, or to prepare an OpenShift overlay. Not for capacity numbers, which the `tyk-infra-sizing` skill owns, or for upgrades, which the `tyk-upgrades` skill owns.
---

# Tyk Production Readiness

Report what a Tyk configuration should change before it carries real traffic, and hand back the
edits. Works on any configuration the user already has, including one never deployed with the
other skills.

The guidance and the tool that reads it live in the public tyk-install repository
(https://github.com/TykTechnologies/tyk-install). This skill carries no copy of either.

## Get the tyk-install repository

Use the user's existing clone (confirm it with `git -C <dir> remote get-url origin`), or clone it
where the user chooses, suggesting `${TYK_WORKSPACE:-~/tyk-workspace}/tyk-install`:

```bash
git clone --depth 1 https://github.com/TykTechnologies/tyk-install <dir>
```

Never clone it inside a customer repository, another project, or this plugin's folder. Below,
`tools/profiles.py` and the `docker/`, `kubernetes/` and `baremetal/` paths are relative to that
checkout. The files under review can be anywhere; pass their paths.

## Where the guidance lives

Every tuning decision in tyk-install is a marker comment beside the setting it applies to:

```bash
TYK_GW_LOGLEVEL=info # PROD: Set to "error" to reduce log volume
TYK_GW_ENABLESEPERATEANALYTICSSTORE=false # PROD/PERFORMANCE: Set to true and use dedicated Redis Cluster for analytics
```

`tools/profiles.py` reads them. Use it instead of recalling production advice: the guidance stays
in the files engineers maintain, and every finding cites a `file:line` the user can open.

Marker families map to profiles: `# PROD:` to `prod`, `# PROD/PERFORMANCE:` to both `prod` and
`performance`, `# SECURITY:` to `security`, and the `# Required for deploying on RedHat
OpenShift` blocks to `openshift`.

## Workflow

1. Identify the files. On a Helm path that is **both** `kubernetes/<target>/values.yaml` **and**
   its sibling `.env.example` (or the user's filled-in `.env`): the credentials live there and the
   values file only references them, so auditing only the values file misses every secret. On
   Docker, `docker/<target>/confs/*.env`; on bare metal, `baremetal/confs/*.env`; or the files the
   user supplies.
2. `profiles.py list <files>` to see what guidance each file carries. A zero count for a profile
   means the file has no such markers, not that it has no such problems.
3. `profiles.py audit <files> --profile prod` (add `--profile performance`, `--profile security`).
   `audit` masks credential values, so it is the safe way to enumerate them; never read the file by
   hand and quote lines from it.
4. Work through `references/hardening.md` for what the markers do not cover, and
   `references/sizing.md` for the scale checks.
5. Report in the three buckets of `## Reporting`, worst first, led by the always-call-out items.
6. If asked to change things, hand back edits to a copy of the file. Do not generate a production
   overlay (`## Applying`).

```bash
python3 tools/profiles.py list  kubernetes/helm-self-managed/values.yaml kubernetes/helm-self-managed/.env.example
python3 tools/profiles.py audit kubernetes/helm-self-managed/values.yaml kubernetes/helm-self-managed/.env.example \
  --profile prod --profile security
```

## Reporting

Three buckets, because they need different actions:

- **Already correct**: the guidance is satisfied. Say so briefly.
- **Needs changing**: guidance the configuration does not satisfy. Give the setting, its
  `file:line` and the guidance text verbatim. Do not infer a value from a commented-out
  placeholder: many are the current default shown for illustration, so the commented value is
  often the opposite of the advice.
- **Needs a decision**: guidance whose recommendation is prose rather than a value
  (`# PROD: Set to a domain name`), or a number that needs sizing work (the `tyk-infra-sizing`
  skill). These are decisions the user owes.

Always call out, whatever the markers say:

- Committed development secrets (`references/hardening.md`, first table). Any deployment that is
  not a local throwaway replaces them.
- Single-instance Redis or PostgreSQL: a data-loss and availability risk, not a tuning gap.
- A Gateway control API that shares the port of API traffic.

## Applying

`apply` generates the OpenShift overlay only and refuses every other profile, for two reasons:

1. A commented-out setting does not reliably carry the recommended value. Activating
   `# TYK_GW_ENABLESEPERATEANALYTICSSTORE=false # PROD/PERFORMANCE: Set to true` would emit
   `false`, the opposite of its own advice, and nothing in the file tells an illustrative value
   from a recommended one.
2. On Helm, an overlay replaces a list instead of merging it. A generated `extraEnvs` deletes
   every entry the base file set, and `helm template` exits 0 either way.

So for production work: report findings and hand back edits to a copy of the values file that
keep its existing lists. Never present a generated production overlay as hardened.

The OpenShift overlay is the exception: those blocks are genuine "uncomment to use" settings that
graft new mappings onto the base file. `kubernetes/helm-self-managed/` and
`kubernetes/helm-hybrid/` already ship `values-openshift.yaml` (tyk-charts 5.4.0 or later); use
it. For the others, generate one outside tracked paths, validate it, and check the rendered
environment-variable count is unchanged:

```bash
python3 tools/profiles.py apply kubernetes/helm-oss/values.yaml \
  --profile openshift -o <out dir>/openshift-values.yaml
V=kubernetes/helm-oss/values.yaml
helm template check tyk-helm/tyk-oss -f $V                                  | grep -c '\- name: TYK'
helm template check tyk-helm/tyk-oss -f $V -f <out dir>/openshift-values.yaml | grep -c '\- name: TYK'
```

## References

- `references/hardening.md`: committed development defaults, data stores, network, availability,
  analytics and logging concerns that no single marker expresses.
- `references/sizing.md`: the scale checks to make, and where the numbers come from.
- `docs/topology-matrix.md` in the tyk-install checkout, when a finding implies a different
  topology rather than a different value (then the `tyk-deployment-planner` skill).

## Safety

- Never edit a tracked config file. Hand back edits for a copy, or generate the OpenShift
  overlay.
- Never print a secret's value, including one you flag as weak. Name the variable and cite its
  `file:line`.
- Do not call a configuration production ready. Report which guidance it satisfies and which it
  does not; the judgement belongs to the person who owns the service.
- Do not invent thresholds. If neither the markers nor `references/hardening.md` give a number,
  say it needs sizing work (the `tyk-infra-sizing` skill) and cite
  https://tyk.io/docs/planning-for-production.

## Hand-offs

- Gateway count, Redis memory, analytics store and cost: the `tyk-infra-sizing` skill.
- A different topology: the `tyk-deployment-planner` skill; applying the change: the
  `tyk-deployment-runner` skill.
- Backups, restores and version moves: the `tyk-upgrades` skill.
- Pump back ends, detailed recording and telemetry: the `tyk-observability` skill.
- Distributed rate limiter settings (`drl_threshold`): the `tyk-rate-limits-keys-policies` skill.

## Completion contract

Finish when every file in scope has been audited (including the sibling `.env.example` or `.env`
on a Helm path), findings are grouped into already correct, needs changing and needs a decision
with a `file:line` for each, committed development secrets and single-instance datastores are
called out explicitly, and any generated OpenShift overlay has been validated with
`helm template` and shown not to reduce the rendered environment-variable count.
