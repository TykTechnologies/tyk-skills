---
name: tyk-gitops-sync-operator
description: Put Tyk configuration (APIs, policies, templates) under version control and deliver it through CI with Tyk Sync, Tyk Operator, or plain files for the open source Gateway, and prove promotion, drift correction and rollback. Use when someone asks about GitOps, CI/CD or config as code for Tyk, tyk-sync dump or sync, Tyk Operator custom resources or Kubernetes Gateway API (HTTPRoute), promoting APIs between environments, or rolling back a change. Not for writing the API definitions themselves, which the tyk-api-definitions-routing skill owns.
---

# Tyk GitOps: Sync, Operator, Files

Tyk has three ways to keep API configuration in Git, and they are not interchangeable. Tyk
Sync is a command that a pipeline runs against a Tyk Dashboard. Tyk Operator is a Kubernetes
controller. The open source Gateway alone reads files. This skill picks the right one, sets up
delivery, and proves it on the running stack. It applies to Tyk Sync 2.3.0, Tyk Operator
1.6.0, Dashboard and Gateway 5.16.0; the reference files carry the commands and their output.

## Clarify first

Ask only what the request does not answer. Defaults in brackets.

1. Is there a Tyk Dashboard, or only the open source Gateway? [Dashboard]
2. Is Kubernetes where configuration is applied (Argo CD, Flux, `kubectl`)? [no: use Tyk Sync]
3. Is there a Tyk Operator licence key? [no; the Operator cannot run without one]
4. Does this repository own the whole organisation, or do other tools and people also write
   APIs and policies there? [owns it: `sync` may delete; otherwise `sync --no-delete`]
5. Which environments, and what differs between them? [dev and prod; upstream URLs and secrets]
6. Which CI system? [GitHub Actions]

## References

| File | Use it for |
| --- | --- |
| `references/choosing-a-tool.md` | The decision, what each tool needs and manages, licence statements quoted from the docs |
| `references/tyk-sync.md` | `dump`, `publish`, `update`, `sync`: flags, file formats, IDs, deletion, authentication, exit codes, the container image |
| `references/tyk-sync-ci.md` | The GitHub Actions workflow, the two scripts, promotion, drift and rollback with the workflow |
| `references/tyk-operator.md` | Install, the custom resources in 1.6.0, status fields, failures, drift, deletion, rollback, upgrades |
| `references/operator-gateway-api.md` | Kubernetes Gateway API with Operator 1.6: switching it on, `HTTPRoute` to API, securing a route |
| `references/oss-gateway-files.md` | Open source Gateway alone: the file pair, reload, policies, the delivery job |
| `references/environments.md` | The same definitions in several environments with different upstreams and secrets |
| `scripts/check_sync_repo.py`, `scripts/sync_drift.py` | Offline check of a Tyk Sync directory; comparison of Git with a fresh dump |
| `assets/` | `github-actions/tyk-sync.yml`, `operator/` (kustomize base and prod overlay), `oss/deliver.sh` |

## Workflow

1. **Resolve versions, do not recall them.**

   ```bash
   curl -s 'https://hub.docker.com/v2/repositories/tykio/tyk-sync/tags?page_size=100&name=v2.' \
     | python3 -c 'import json,sys,re; print(sorted(t["name"] for t in json.load(sys.stdin)["results"] if re.fullmatch(r"v\d+\.\d+\.\d+", t["name"])))'
   helm repo add tyk-helm https://helm.tyk.io/public/helm/charts/ && helm repo update tyk-helm
   helm search repo tyk-helm/tyk-operator --versions | head -3
   ```

   The `tyk-facts` skill has the full procedure. Pin the exact tag everywhere.

2. **Choose the tool** with `references/choosing-a-tool.md`. In short: no Dashboard means
   files (or the Operator in `ce` mode with a licence); a Dashboard and a pipeline means Tyk
   Sync; a Dashboard and Kubernetes-native delivery means the Operator. Say which licence each
   choice needs, in the docs' words, and send further licence questions to the `tyk-facts`
   skill.

3. **Tyk Sync path.** Details and output: `references/tyk-sync.md`, `references/tyk-sync-ci.md`.
   First copy `scripts/check_sync_repo.py` and `scripts/sync_drift.py` into the repository's
   `ci/`, and `assets/github-actions/tyk-sync.yml` into `.github/workflows/`. The Dashboard URLs
   must be reachable from inside the container: for a Dashboard on the same machine use
   `http://host.docker.internal:<port>` (`references/tyk-sync.md`, "Run it from the container
   image").

   ```bash
   export TYKGIT_DB_SECRET=<from your secret store>   # passed by name below, never printed
   # a. Export the source environment into the repository.
   mkdir -p tyk && docker run --rm --user "$(id -u):$(id -g)" -e TYKGIT_DB_SECRET \
     -v "$PWD/tyk:/work" tykio/tyk-sync:v2.3.0 dump -d "$DEV_DASHBOARD_URL" -t /work
   # b. Replace environment values with references (references/environments.md), then check.
   python3 ci/check_sync_repo.py tyk
   # c. Promote: make the target Dashboard match the directory.
   docker run --rm --user "$(id -u):$(id -g)" -e TYKGIT_DB_SECRET \
     -v "$PWD/tyk:/work:ro" tykio/tyk-sync:v2.3.0 sync -d "$PROD_DASHBOARD_URL" -p /work
   # d. Prove it: read back and compare.
   readback=$(mktemp -d) && docker run --rm --user "$(id -u):$(id -g)" -e TYKGIT_DB_SECRET \
     -v "$readback:/work" tykio/tyk-sync:v2.3.0 dump -d "$PROD_DASHBOARD_URL" -t /work
   python3 ci/sync_drift.py tyk "$readback"          # IN SYNC: N object(s) match
   ```

   `TYKGIT_DB_SECRET` is the access key of a Dashboard user kept for the pipeline, with
   `apis`, `policies` and `api_assets` write, `users` read, and `mcp` write when the
   directory holds MCP proxies. Before the first `sync` on a Dashboard that already holds
   objects, run step d against it: every `EXTRA` line is something `sync` would delete.

4. **Tyk Operator path.** Details: `references/tyk-operator.md`.

   ```bash
   kubectl create namespace tyk-operator-system
   kubectl -n tyk-operator-system create secret generic tyk-operator-conf --from-env-file=operator-conf.env
   helm install tyk-operator tyk-helm/tyk-operator --version 1.6.0 -n tyk-operator-system
   kubectl apply -k assets/operator/base
   kubectl get tykoas,tykpolicies,tyksessions          # SYNCSTATUS Successful on each
   ```

   cert-manager must be there first; `operator-conf.env` holds `TYK_OPERATOR_LICENSEKEY`,
   `TYK_MODE`, `TYK_URL`, `TYK_AUTH`, `TYK_ORG`. For `HTTPRoute`, read
   `references/operator-gateway-api.md`: it is off by default.

5. **Files path** (open source Gateway alone). Details: `references/oss-gateway-files.md`.
   Keep Tyk OAS documents and policy files in Git and run `assets/oss/deliver.sh`: it lets a
   throwaway Gateway write the file pairs, replaces the Gateways' directories and calls
   `/tyk/reload/group`.

6. **Environments.** Put `kv://env/<NAME>` references in the definitions and the values on
   each environment's Gateways as `TYK_SECRET_<NAME>`; promote the same files everywhere
   (`references/environments.md`).

7. **Prove the three behaviours** on the user's stack, and show the output:
   - *Promote*: a commit changes a value; after delivery the Dashboard (or Gateway) returns
     the new value and a request through the Gateway shows it.
   - *Drift*: change the same object by hand; show how it is detected (Tyk Sync:
     `sync_drift.py` exits 1 and names the field) and that the next delivery puts it back
     (Operator: after a restart of the Operator or a change to the resource; do not wait for
     the periodic resync).
   - *Rollback*: `git revert` the commit, deliver again, show the old value and that an
     existing key still works.

## Pitfalls

- **`tyk-sync sync` deletes.** Whatever the organisation holds and the directory does not is
  removed, including objects made by the Operator or by hand. `--no-delete` goes after the
  subcommand and still deletes API Templates.
- **Give every API an ID** (`x-tyk-api-gateway.info.id`). Without one, Tyk Sync creates the
  API under a new ID, deletes it on the next run and fails to recreate it in that run.
- **Wrap Tyk OAS documents** as `{"oas": {...}}` and list every file in `.tyk.json`. An
  unwrapped document becomes an empty classic API; an unlisted file is ignored. Both exit 0.
- **`publish` and `update` are not repeatable.** Each stops at the first object that already
  exists (or does not). Pipelines use `sync`.
- **A green `sync` proves nothing.** It rewrites policies and classic APIs on every run and
  does not compare. Read back and run `sync_drift.py`. A failed `sync` leaves what it had
  already written: fix the cause and run it again.
- **The Git branch default is `refs/heads/master`.** Use `-p` with a checkout, or `-b`.
- **Pass `--user "$(id -u):$(id -g)"`** to the Tyk Sync image, or `dump` fails with
  `permission denied` on Linux.
- **The Operator does not watch Tyk.** A manual change in the Dashboard stays, with
  `SYNCSTATUS Successful`, until the resource changes, the Operator restarts, or the
  controller's cache expires (the docs say about 10 hours, depending on configuration).
- **Helm does not upgrade the Operator's resource definitions.** After `helm upgrade`, run
  `helm show crds tyk-helm/tyk-operator --version <v> | kubectl apply --server-side --force-conflicts -f -`.
- **An Operator with a bad licence is silent in resource status.** The pod is in
  `CrashLoopBackOff` and the log says `License error`; resources keep their old `Successful`.
- **Delete order with the Operator**: session, policy, API, and the `OperatorContext` last. An
  API that a policy references, or whose context is gone, stays in `Terminating`.
- **The open source Gateway needs two files per Tyk OAS API**, written by a Gateway. A bare
  document is skipped; editing only the `-oas.json` file changes nothing.
- **Environment values travel with a dump.** An upstream URL from dev fails in prod with
  `Upstream host lookup failed`. Use references.
- **An API ID is also a path.** The Gateway serves each API under `/<api id>/` as well as its
  listen path, so after a listen path moves, the old one can still answer if it equals the ID.

## Limitations

- No tool here has a dry run or a diff. `sync_drift.py` is the closest thing, for Tyk Sync.
- Tyk Sync 2.x is supported only with a licensed Dashboard, not with the open source Gateway,
  and does not carry access keys, certificates or users.
- The Operator runs only in Kubernetes, needs its own licence key in every mode, has no
  resource for API Fragments, and corrects drift only when it reconciles (a resource change, a
  restart, or a cache resync about every 10 hours by the docs).
- Kubernetes Gateway API support needs Operator 1.6.0 or later with a Dashboard, Gateways in
  the cluster, and supports only part of `HTTPRoute` (`references/operator-gateway-api.md`).
- With files, nothing tells you that one Gateway missed a delivery. Verify each one.
- Keys are per environment. Only `TykSession` resources declare them, and each environment
  gets its own key value.

## Verification checklist

Run before reporting success, and show the output of each line you rely on:

- [ ] Image and chart versions were resolved live and pinned; the answer names them.
- [ ] `python3 ci/check_sync_repo.py tyk` exits 0 (Tyk Sync), or every resource shows
      `SYNCSTATUS Successful` with a fresh `status.latestTransaction.time` (Operator), or
      `GET /tyk/apis` lists every API on every Gateway (files).
- [ ] After delivery, the read-back matches Git: `sync_drift.py` prints `IN SYNC` (Tyk Sync);
      the Dashboard or Gateway API returns the value from the commit (Operator, files).
- [ ] One request per changed API through the Gateway returns the expected status, and a key
      issued before the change still works.
- [ ] Drift was created by hand, detected, and corrected by the documented trigger.
- [ ] `git revert` plus a delivery restored the previous behaviour.
- [ ] No secret, licence key or access key is in Git, in a command line or in the output.
- [ ] The deletion behaviour of the chosen command was told to the user before the first run.
- [ ] Every licence statement is a quotation with its URL, or was handed to `tyk-facts`.

## Hand-offs

- Installing Tyk, the Operator's production install, cert-manager, charts: the
  `tyk-deployment` skill.
- Writing the API definitions themselves: the `tyk-api-definitions-routing` skill.
- Policy content, rate limits, quotas and keys: the `tyk-rate-limits-keys-policies` skill.
- Licences, packages, versions and images: the `tyk-facts` skill.
- A disposable stack to try this on: the `tyk-demo-builder` skill. Acceptance criteria for a
  proof of concept: the `tyk-poc-runbook` skill. Coming from another gateway's declarative
  tool: the `tyk-migration-planner` skill.
