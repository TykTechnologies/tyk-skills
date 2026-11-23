---
name: tyk-deployment
description: Install Tyk for production, staging or anything long-lived from the public tyk-install repository, in three phases. Plan: edition (open source Gateway, Self-Managed, Hybrid, MDCB, AI Studio), platform (Docker Compose, Helm, OpenShift, bare metal), multi-site or multi-region, prerequisites, licences, pinned versions. Run: start, restart or bootstrap it with Compose, Helm or rpm/deb packages and prove it with a real request through the gateway, an idempotent rerun and a restart, or fix a fresh install that will not come up (gateway not starting, Dashboard licence, Redis or PostgreSQL connection, MDCB RPC login errors). Review: audit a values.yaml or gateway env file before go-live (committed development secrets, single-instance Redis or PostgreSQL, exposed control API, analytics load, TLS) and prepare the OpenShift overlay. Not for a time-boxed demo (`tyk-demo-builder`), capacity numbers (`tyk-infra-sizing`), a misbehaving established stack (`tyk-troubleshooting`) or upgrades (`tyk-upgrades`).
---

# Tyk Deployment

Plan, run and review a real Tyk installation. Everything works on a checkout of the public
tyk-install repository (https://github.com/TykTechnologies/tyk-install): its deployment
directories, the guides in `docs/` and the tools in `tools/`. This skill carries no copy of them.

## Pick the phase

| The user wants | Phase | Read |
| --- | --- | --- |
| To choose an edition, platform or topology; "how should we install Tyk"; anything multi-site, multi-region, MDCB or "either site can fail" | Plan | `references/planning.md`, and `references/multi-site.md` for more than one site |
| To start, bootstrap or prove an install; a fresh install that will not come up | Run | `references/running.md` |
| To know whether a configuration is ready for real traffic; a review of a `values.yaml` or env file before go-live | Review | `references/production-readiness.md`, `references/hardening.md`, `references/sizing.md` |
| An OpenShift overlay | Plan or Review | `references/openshift-overlay.md` |

Do only the phase the user asked for. A full installation goes through them in order: plan, run,
then review before real traffic. After writing a plan, stop: start the run phase only when the user
has approved the plan in this conversation, or, when there is no plan, has confirmed exactly one
row of `docs/topology-matrix.md`. A review stands on its own: it works on any configuration,
including one never deployed with this skill.

## Get the tyk-install repository

1. Use the checkout a plan names, or ask whether the user already has a clone. Confirm it is the
   right one (`git -C <dir> remote get-url origin`). Updating it (`git -C <dir> pull --ff-only`) is
   the user's call; say which commit you worked against (`git -C <dir> rev-parse --short HEAD`).
2. Otherwise clone it where the user chooses, suggesting
   `${TYK_WORKSPACE:-~/tyk-workspace}/tyk-install`:

   ```bash
   git clone --depth 1 https://github.com/TykTechnologies/tyk-install <dir>
   ```

   Never clone it inside a customer repository, another project, or this plugin's folder.

In the references, every tyk-install path is relative to that checkout, and every command runs
from its root. Files under review can live anywhere; pass their paths.

## The guidance in tyk-install

Production, performance, security and OpenShift guidance lives in tyk-install as marker comments
beside each setting (`# PROD:`, `# PROD/PERFORMANCE:`, `# SECURITY:`, `# Required for deploying on
RedHat OpenShift`). `tools/profiles.py` lists, audits and, for OpenShift only, applies them. Use it
instead of recalling production advice, so every finding cites a `file:line` the user can open
(`references/production-readiness.md`).

## Where generated files go

Plans, overlays and reviews are the user's files, not the repository's. In order of preference:

1. A path the user names.
2. The user's project workspace: `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/docs/`.
3. Inside the tyk-install checkout at `.tyk-deploy/`, which its `.gitignore` excludes; say so.

Never write into a path git tracks (check with `git check-ignore -v <path>`), and never into this
plugin's folder. Say where you put each file.

## Safety

- Planning and reviewing start nothing. Run `docker compose up`, `helm install`, `kubectl apply`,
  `oc apply` or a package manager only in the run phase, after the user has approved the plan and
  after echoing the Docker context, or the Kubernetes context and namespace, and what will be
  created.
- Never delete, upgrade or reconfigure resources the user did not ask you to touch. Ask before
  reusing a namespace, project or Compose stack that already exists.
- `docker compose down -v` destroys data. Confirm before running it, and never run it against a
  stack you did not create in this session.
- Planning and reviewing never edit a tracked config file: generate an overlay or hand back edits
  to a copy. In the run phase prefer an overlay; if a tracked file must change, say so, show the
  diff and get the user's agreement first.
- Never print, or write into a plan, log, evidence file or commit, a licence, token, password,
  generated credential or certificate, including one you flag as weak. Name the variable and cite its `file:line`.
- Never read licence values out of an unrelated deployment's `.env` to fill a gap. Ask the user.
- Do not use direct database access for bootstrap or credential recovery; use the APIs
  (`docs/bootstrap.md`, `docs/debugging.md`).
- The PostgreSQL and Redis passwords, `TYK_GW_SECRET` and `TYK_GW_NODESECRET` committed in
  tyk-install are development defaults (`references/hardening.md`). Never call a deployment that
  uses them production-ready, and say why.

## Hand-offs

- Gateway count, Redis memory, analytics store and cost: the `tyk-infra-sizing` skill.
- The topology drawn: the `tyk-excalidraw-diagrams` skill.
- Licences, editions, versions and trials: the `tyk-facts` skill.
- A time-boxed demo or proof of a capability: the `tyk-demo-builder` skill.
- An established stack that misbehaves: the `tyk-troubleshooting` skill.
- Upgrades, backups, restores and version moves: the `tyk-upgrades` skill.
- Pump back ends, detailed recording and telemetry: the `tyk-observability` skill.
- APIs, keys and policies beyond the bootstrap test API: the `tyk-api-definitions-routing` and
  `tyk-rate-limits-keys-policies` skills.
- Distributed rate limiter settings (`drl_threshold`): the `tyk-rate-limits-keys-policies` skill.

## Completion contract

A phase is finished when the "Done when" at the end of its reference is met. Whatever could not
be checked is named, with the reason. A full installation is finished when all three phases are.
