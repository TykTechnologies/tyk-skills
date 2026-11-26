# Production readiness review

Last verified: 2026-11-25 against the tyk-install repository (main), tyk-charts 5.5.0 and Gateway 5.16.0

Report what a Tyk configuration should change before it carries real traffic, and hand back the
edits. Works on any configuration the user already has, including one never deployed with this
skill. `tools/profiles.py` and the `docker/`, `kubernetes/` and `baremetal/` paths are relative to
the tyk-install checkout; the files under review can be anywhere, so pass their paths.

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
4. Work through `hardening.md` for what the markers do not cover, and `sizing.md` for the scale
   checks.
5. Report in the three buckets of `## Reporting`, worst first, led by the always-call-out items.
6. If asked to change things, hand back edits to a copy of the file. Do not generate a production
   overlay (`openshift-overlay.md` explains why only the OpenShift profile is applied).

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

- Committed development secrets (`hardening.md`, first table). Any deployment that is not a local
  throwaway replaces them.
- Single-instance Redis or PostgreSQL: a data-loss and availability risk, not a tuning gap.
- A Gateway control API that shares the port of API traffic.

When a finding implies a different topology rather than a different value, go back to
`planning.md` and `docs/topology-matrix.md`.

## Rules

- Do not call a configuration production ready. Report which guidance it satisfies and which it
  does not; the judgement belongs to the person who owns the service.
- Do not invent thresholds. If neither the markers nor `hardening.md` give a number, say it needs
  sizing work (the `tyk-infra-sizing` skill) and cite https://tyk.io/docs/planning-for-production.

## Done when

Every file in scope has been audited (including the sibling `.env.example` or `.env` on a Helm
path), findings are grouped into already correct, needs changing and needs a decision with a
`file:line` for each, committed development secrets and single-instance datastores are called out
explicitly, and any generated OpenShift overlay has been validated as `openshift-overlay.md`
describes.
