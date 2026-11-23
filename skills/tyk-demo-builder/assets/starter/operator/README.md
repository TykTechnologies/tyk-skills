# Neutral Tyk Operator Assets

These examples reconcile one `TykOasApiDefinition` and one `SecurityPolicy`
through an `OperatorContext`. Ownership is represented both as neutral
Kubernetes metadata (`demo.tyk.io/owner` and `demo.tyk.io/team`) and as Tyk
Dashboard user/user-group owner IDs supplied at runtime.

The bundle also deploys a neutral HTTP upstream. `apply-operator.sh` renders
the selected namespace consistently across the Secret, upstream, context, API,
policy, and cross-resource references in a protected runtime directory.

## Versions

- The generator writes the latest `tyk-operator` release to `OPERATOR_VERSION`
  in `.env`. This flow was proved on **v1.6.0** (chart `tyk-operator` 1.6.0,
  cert-manager `v1.15.3`) against Dashboard and Gateway `v5.16.0` in kind. The
  Operator release notes say "Only the most recent Tyk Operator release is
  supported" (https://tyk.io/docs/developer-support/release-notes/operator).
- Governance (`TykRuleset` and the admission webhook) needs v1.5.0 or later.
- The installer installs chart `tyk-operator` from
  `https://helm.tyk.io/public/helm/charts/` at `OPERATOR_VERSION` without the
  `v`, and always sets `image.tag` to `OPERATOR_VERSION` so the image matches the
  version you chose.

## Flow

Start, bootstrap, and configure the Compose stack first, then:

```bash
./operator/install-operator-kind.sh     # disposable kind cluster + cert-manager + operator
source operator/operator-env.sh         # exports connection and owner values (bash or zsh)
./operator/apply-operator.sh            # applies the resources and records OPERATOR-001
./operator/verify-governance.sh         # only when governance is selected; records GOV-003
./scripts/verify-demo.sh                # picks up both evidence files
```

### kubectl by hand: use ./operator/kubectl.sh

The installer never switches your current kubectl context. The kind cluster's
credentials go to a bundle-local kubeconfig, `.runtime/operator-kubeconfig`
(mode 0600), instead of `~/.kube/config`, and the context it installed into is
recorded in `.runtime/operator-kube-context`. A bare `kubectl` therefore talks
to whatever cluster your own kubeconfig selects (or fails), not to the demo
cluster: on stage that applies a manifest to the wrong cluster. Run every
command by hand through the wrapper, which reads both files the same way the
bundle scripts do:

```bash
./operator/kubectl.sh get tykoasapidefinitions,securitypolicies -A
./operator/kubectl.sh -n tyk-operator-system get pods
# with governance selected (files rendered by apply-operator.sh):
./operator/kubectl.sh get tykruleset -A
./operator/kubectl.sh apply -f .runtime/operator/governed-api-noncompliant.yaml
```

It is equivalent to
`KUBECONFIG=.runtime/operator-kubeconfig kubectl --context kind-<cluster> ...`
(the cluster is `${KIND_CLUSTER_NAME:-__SLUG__-operator}`). With
`ALLOW_NON_KIND_CONTEXT=true` there is no bundle kubeconfig, and the wrapper
uses your own kubeconfig with the recorded context.

### install-operator-kind.sh (opt-in)

For disposable clusters only. It creates kind cluster
`${KIND_CLUSTER_NAME:-__SLUG__-operator}` if missing and addresses it
explicitly as `kind-<name>`, whatever context is current.
`ALLOW_NON_KIND_CONTEXT=true` instead uses the current context as-is, creates no
cluster, and does not need `kind`. The context used is recorded in
`.runtime/operator-kube-context` (mode 0600); `apply-operator.sh`, both
verify scripts and `kubectl.sh` target it, and without it they refuse a non-`kind-` current
context unless `ALLOW_NON_KIND_CONTEXT=true`. The installer applies cert-manager
only when its deployments are absent (the chart's webhook TLS depends on it),
writes the `tyk-operator-conf` Secret in `tyk-operator-system`, installs the
chart with `helm upgrade --install`, and waits for the rollout. Re-running it
over an existing release is safe. When governance is selected it also asserts the
`tykrulesets.tyk.tyk.io` CRD and the `vtykoasapidefinition.kb.io` validating
webhook exist.

- **Licence**: the Operator needs `TYK_OPERATOR_LICENSEKEY`. The installer takes
  it from `TYK_LICENSE_KEY` in the private `.env`; with a Dashboard licence the
  Operator logs `License validated successfully`.
- **Dashboard address**: the default `http://host.docker.internal:__DASHBOARD_PORT__`
  reached the Dashboard from kind pods on OrbStack. Where kind pods cannot resolve
  `host.docker.internal`, attach the Dashboard container to the kind network and
  use its name and container port instead, before running the installer and
  `operator-env.sh` (both keep an existing `TYK_OPERATOR_URL`):

  ```bash
  docker network connect kind <dashboard-container>   # docker compose ps shows the name
  export TYK_OPERATOR_URL=http://<dashboard-container>:3000
  ```
- An rc `OPERATOR_VERSION` fails unless `OPERATOR_CHART_VERSION` names a
  published chart, because there is no chart for rc tags.
- Requires `kind`, `kubectl`, `helm`, and `jq`. No secret is printed.

### operator-env.sh (source it)

`apply-operator.sh` needs these values; the connection Secret is never stored in
source:

- `TYK_OPERATOR_AUTH` — the bootstrap Dashboard API key
- `TYK_OPERATOR_ORG` — the bootstrap organisation id
- `TYK_OPERATOR_URL` — defaults to `http://host.docker.internal:__DASHBOARD_PORT__`;
  an existing value is kept
- `TYK_USER_OWNER_ID` — the bootstrap admin user (`DASHBOARD_USER_ID`)
- `TYK_TEAM_OWNER_ID` — the Dashboard user group `__SLUG__-api-platform`

Nothing else in the bundle provisions the owner group, so `operator-env.sh`
finds it by name or creates it (`POST /api/usergroups` with `apis: write`). It
is idempotent, prints no secret, keeps strict-mode options out of your shell,
and returns non-zero (without closing the shell) when bootstrap has not run.
You can still export the five values by hand instead.

## Verification

`verify-reconciliation.sh` (run by `apply-operator.sh`) waits for populated
reconciliation status IDs, both spec hashes recorded, a `Successful` latest
transaction on the API and the policy, and policy linkage to the OAS API. It
then checks the owner/team metadata.

Freshness uses a desired-state digest the bundle owns, not `metadata.generation`:
on tyk-operator v1.6.0 re-applying an identical rendered `SecurityPolicy` reports
`configured` and bumps the generation every time, while its status hashes stay put. `apply-operator.sh` digests each rendered
resource (`.spec`, or `.data` for the ConfigMap) into
`.runtime/operator/.pending-digests.json`. When a digest differs from the one in
`.runtime/operator/.verified-digests.json`, the verification waits for that
resource's status hash (`latestCRDSpecHash`, or the API's `latestConfigMapHash`)
to move past the value recorded at the last successful verification. The new
digests become the baseline only after a fully successful run, so a failed run
detects the same change again next time. Delete `.verified-digests.json` to
forget the baseline.

Evidence is written on every outcome. `apply-operator.sh` and each verify script
first write a `status: failed` marker ("run started but did not complete"), so
an interrupted run can never leave an earlier pass behind; the verify script
then replaces it with `status: passed`, or `status: failed` and a reason (with
the Operator's `latestTransaction.error` on timeout). Every evidence file carries
`org_id` and `recorded_at`. `scripts/verify-demo.sh` records `OPERATOR-001` (and
`GOV-003`) only when `org_id` matches this stack's `ORG_ID` and the named API
(or ruleset) still answers `200` from this Dashboard; otherwise the row is
`blocked` as evidence from a different stack. `scripts/reset-demo-stack.sh
--purge` deletes `evidence/operator-*.json`.

Two things worth knowing when you read Operator status by hand on v1.6.0:

- `latestCRDSpecHash` hashes the desired CR spec and `latestTykSpecHash` hashes
  the Dashboard read-back, so they are never equal; convergence is the
  `latestTransaction.status` of `Successful`, not a hash match.
- A `SecurityPolicy` `access_rights_array` entry defaults `kind` to the classic
  `ApiDefinition`, so an OAS API entry must say `kind: TykOasApiDefinition` or
  the policy never links.

The scripts do not mutate a database. Use only a disposable cluster.
