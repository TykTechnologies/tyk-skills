# Plugin, Operator, Tyk Sync And CI/CD Modules

Last verified: 2026-11-19 against Tyk Gateway and Dashboard 5.16.0, Tyk Operator 1.6.0 and Tyk Sync 2.3.0.

This page covers what these modules generate and what their proofs assert. For writing, building
and deploying custom plugins in general, use the `tyk-custom-plugins` skill; for Tyk Sync, Tyk
Operator and GitOps delivery in general, the `tyk-gitops-sync-operator` skill.

## go-plugin (`PLUGIN-GO-001`)

`plugins/go` holds a native Go Post hook that adds `X-Demo-Post-Hook: native-go` to the request
before proxying. `start-demo-stack.sh` builds it with `plugins/go/build.sh`, passing the
bundle's `GATEWAY_VERSION` and the gateway image's architecture, so the plugin is compiled by
the `tykio/tyk-plugin-compiler` image of the same version. The output stays in the git-ignored
`plugins/go/build/`. `PLUGIN-GO-001` asserts that an authenticated request reaches the upstream
with the header. It passes with Gateway 5.16.0 on arm64.

## grpc-plugin (`PLUGIN-GRPC-001`)

`plugins/grpc` holds a gRPC server implementing the coprocess dispatcher, built into its own
container and reached at `tcp://grpc-plugin:5555`. Its Post hook adds `X-GRPC-Enriched: true` to
an accepted request, and answers a request carrying `X-Deny-Request: true` itself with a JSON
403 through `ReturnOverrides`, so that request never reaches the upstream. `PLUGIN-GRPC-001`
asserts the enrichment, that an invalid credential is refused by the gateway before the hook
runs, and that neither refusal changes the upstream hit counter. The server's `go.mod` takes the coprocess protocol types from the public `tyk`
module at the commit tagged v5.13.1; the gateway talks to the plugin over gRPC, and this server
passed against Gateway 5.16.0 as built.

## js-plugin (`PLUGIN-JS-001`)

Adds `<display name> Compatibility Services` with a pre-authentication JavaScript plugin,
`plugins/js/<slug>-credential-mapper.js`, loaded by file path from a `prePlugins` entry through
the read-only `./plugins` mount, and sets `TYK_GW_ENABLEJSVM=true` on every gateway. The plugin
copies a credential sent as `?user_key=` or in an `app_key` header into `Authorization` and
removes the original. The generator selects the goja engine (`pluginConfig.driver:
"javascript"`) when both the Gateway and the Dashboard are 5.15.0 or later, and `"otto"`
otherwise, saying so in the README and on stdout. The plugin uses `log()` and plain ES5 only.
`PLUGIN-JS-001` asserts: `?user_key=` accepted and removed before the upstream; the `app_key`
header accepted; no credential 401; an unknown key 403; and, for the `javascript` driver, the
gateway log line tagged `prefix=jsvm-goja`.

## Operator

Generate `TykOasApiDefinition`, `SecurityPolicy`, Secrets, and owner/team metadata from the same OAS and policy intent. Mark reconciliation live only when a disposable cluster and a compatible Operator are available. For Operator product behaviour beyond this bundle, use the `tyk-gitops-sync-operator` skill.

Versions: the generator resolves the newest `tyk-operator` release live, and the Operator release notes say "Only the most recent Tyk Operator release is supported" (https://tyk.io/docs/developer-support/release-notes/operator). The bundle flow works with Operator `v1.6.0` (chart `tyk-operator` 1.6.0, cert-manager `v1.15.3`) against Dashboard and Gateway `v5.16.0` in kind. The `TykRuleset` CRD and its admission webhook arrived in 1.5.0 (same release notes). The chart lives at `https://helm.tyk.io/public/helm/charts/` (chart `tyk-operator`); chart versions equal app versions without the `v`, and there are no charts for release-candidate tags. Chart 1.6.0 defaults to image `v1.6.0`; the installer passes `--set image.tag=<OPERATOR_VERSION>` anyway so the image never differs from the version you chose. The chart uses cert-manager for webhook TLS (`webhooks.tls.useCertManager: true`) and sets `webhooks.governanceFailurePolicy: Ignore`, so an unreachable Dashboard admits instead of blocking.

Bundle flow, after the Compose stack is bootstrapped:

1. `./operator/install-operator-kind.sh`: opt-in, disposable clusters only. Creates kind cluster `${KIND_CLUSTER_NAME:-<slug>-operator}` if missing, refuses a non-`kind-` context unless `ALLOW_NON_KIND_CONTEXT=true`, installs cert-manager when absent, writes the `tyk-operator-conf` Secret (`TYK_MODE=pro`, `TYK_URL`, `TYK_AUTH`, `TYK_ORG`, `TYK_OPERATOR_LICENSEKEY` from `TYK_LICENSE_KEY`) through a private env file, runs `helm upgrade --install` pinned to `OPERATOR_CHART_VERSION` (default: `OPERATOR_VERSION` without the `v`; a release-candidate tag fails with an explanation), waits for the rollout, and with governance asserts the `tykrulesets.tyk.tyk.io` CRD and the `vtykoasapidefinition.kb.io` webhook. With the bundle's Dashboard licence the Operator logs `License validated successfully`; re-running it over an existing cluster is safe.
2. `source operator/operator-env.sh`: exports `TYK_OPERATOR_AUTH`, `TYK_OPERATOR_ORG`, `TYK_OPERATOR_URL` (default `http://host.docker.internal:<DASHBOARD_PORT>`; an existing value is kept), `TYK_USER_OWNER_ID` (`DASHBOARD_USER_ID`), and `TYK_TEAM_OWNER_ID`, finding or creating the `<slug>-api-platform` Dashboard user group. Nothing else provisions the team owner.
3. `./operator/apply-operator.sh`: applies the resources and runs `verify-reconciliation.sh`, which writes `evidence/operator-reconciliation.json` for `OPERATOR-001`.
4. With governance selected, `./operator/verify-governance.sh` proves the admission hard block and writes `evidence/operator-governance.json` for `GOV-003` (see `references/governance.md`).

Networking: `http://host.docker.internal:<DASHBOARD_PORT>` reaches the Dashboard from kind pods on OrbStack. Where kind pods cannot resolve that name, run `docker network connect kind <dashboard-container>` and set `TYK_OPERATOR_URL=http://<dashboard-container>:3000` before the installer and `operator-env.sh`; both keep an existing value.

The installer never switches the user's kubectl context: it creates the cluster with `--kubeconfig .runtime/operator-kubeconfig` (0600; an existing cluster is exported into that file), and `operator/operator-lib.sh` points every later `kubectl` call at it, so the current context is the same before and after the install. By hand, use `./operator/kubectl.sh ...` (equivalent to `KUBECONFIG=.runtime/operator-kubeconfig kubectl --context kind-<name> ...`), and never document a bare `kubectl`: it reaches the presenter's own current context, not the demo cluster. Pipelines that test a listing end in `grep -Fx ... >/dev/null`, never `grep -q`: under `pipefail`, `grep -q` exits at the first match, the writer dies of SIGPIPE, and a context that exists is reported missing.

Cluster targeting: the installer addresses `kind-<name>` explicitly (or, with `ALLOW_NON_KIND_CONTEXT=true`, the current context without needing `kind`) and records it in `.runtime/operator-kube-context`; `apply-operator.sh`, `verify-reconciliation.sh`, and `verify-governance.sh` pass that context to every `kubectl` call, and without the record refuse a non-`kind-` current context unless `ALLOW_NON_KIND_CONTEXT=true`.

Evidence contract: each script writes a failed "run started but did not complete" marker before any other work, then the real outcome, always with `org_id` and `recorded_at`. `verify-demo.sh` accepts the evidence only when `org_id` equals the stack's `ORG_ID` and the named object still exists (`GET /api/apis/oas/<api.id>` or `GET /api/rulesets/<ruleset.id>` returns `200`); anything else, including unparsable JSON, is `blocked`, never `passed`. `reset-demo-stack.sh --purge` removes `evidence/operator-*.json`.

Freshness: `metadata.generation` is not a change signal. On v1.6.0, re-applying an identical rendered `SecurityPolicy` reports `configured` and bumps the generation every time while `latestCRDSpecHash` and `latestTykSpecHash` stay put, so waiting for a hash after a generation bump hangs forever. The bundle keeps its own desired-state digest per resource: `apply-operator.sh` hashes the sorted JSON of each rendered `.spec` (the ConfigMap's `.data`) with a client-side dry run (`create --dry-run=client -o json` through the bundle's kubeconfig) into `.runtime/operator/.pending-digests.json`. `verify-reconciliation.sh` requires a moved status hash only for a resource whose digest differs from the one in `.runtime/operator/.verified-digests.json` (0600), comparing with the status hashes recorded at that last successful verification (`latestCRDSpecHash` for the CRs, the API's `latestConfigMapHash` for the ConfigMap), and writes the new baseline only after the whole verification passes. `verify-governance.sh` uses the same digests for the `TykRuleset` (either `latestCRDSpecHash` or `latestSourceHash` may move), so an identical re-apply never waits (a second apply and governance proof take 3 to 4 seconds).

Two things to know when reading Operator status by hand on v1.6.0:

- `latestCRDSpecHash` hashes the desired CR spec and `latestTykSpecHash` the Dashboard read-back, so the two are never equal and a hash-match convergence check can never pass. Converged means ids assigned, both hashes populated, and `latestTransaction.status == Successful` on the API and the policy.
- `SecurityPolicy.spec.access_rights_array[].kind` defaults to `ApiDefinition` (`./operator/kubectl.sh explain securitypolicy.spec.access_rights_array.kind`). An entry for an OAS API must set `kind: TykOasApiDefinition`, or the policy never links.

## Tyk Sync And CI/CD (`SYNC-001`, `CICD-001`)

`tyk-sync` promotes the bundle's own `apis/` with Tyk Sync and needs no local binary:
`./ci/tyk-sync-local.sh promote apis` runs `ci/tyk-sync.sh` through the pinned
`tykio/tyk-sync` image (`ci/tyk-sync-docker.sh`, `TYK_SYNC_VERSION` in `.env`), reaching the
Dashboard as `http://host.docker.internal:<DASHBOARD_PORT>`. It works with
`tykio/tyk-sync:v2.3.0` against Dashboard 5.16.0. What `ci/tyk-sync.sh promote` does:

1. validates the JSON in `apis/` and freezes the owned definitions in a read-only snapshot
   (`.runtime/tyk-sync/snapshots/<id>`), refusing any definition the bundle does not own;
2. dumps the Dashboard, keeping the owned APIs as the rollback set
   (`.runtime/tyk-sync/rollback/<id>`);
3. sends each owned API to `tyk-sync update` when the dump holds it and to `tyk-sync publish`
   when it does not, wrapping the bare OAS documents in Tyk Sync's `{"oas": ...}` shape with a
   `.tyk.json` index first;
4. dumps the Dashboard again and checks that every owned API id is there, in the target
   organisation, and that each read-back matches what was sent on name and state, listen path,
   upstream, authentication, security scheme names, gateway tags, operations and the middleware
   set on each operation (the read-back is never byte-equal: the Dashboard adds server entries,
   `dbId` and `orgId` and drops default values);
5. records `.runtime/tyk-sync/latest-promotion.json` with the organisation, the API ids, the
   created and updated counts and a SHA-256 of every delivered definition.

`./ci/tyk-sync-local.sh rollback .runtime/tyk-sync/rollback/<id>` re-applies a rollback set the
same way. It does not delete an API that the promotion created.

`SYNC-001` passes only when the latest record belongs to this stack's organisation, its read-back
holds every API in `apis/`, each still answers 200 in the Dashboard, and the digest of every file
in `apis/` equals the digest recorded for it. So a promotion passes; editing an API's upstream
under the same id turns the row `blocked` ("does not match apis/"); a delivery whose update
does nothing fails the promotion ("Read-back content differs from what was delivered"); a
rollback leaves the row `blocked` until the next promotion.

`cicd` (`CICD-001`, "CI template checks and local promotion") checks the GitHub and GitLab
templates statically (they call `ci/tyk-sync.sh` with manual promote and rollback gates), runs
their validate step, and passes only once `SYNC-001` has passed. No CI runner runs: accepting a
real pipeline needs that pipeline's own run, change and rollback evidence.

The generator refuses `versioning` together with `tyk-sync` or `cicd`, because the link between
a base API and its child version is created by `scripts/import-apis.sh`, which Tyk Sync does not
read.
