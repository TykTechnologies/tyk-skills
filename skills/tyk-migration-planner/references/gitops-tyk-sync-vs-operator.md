# GitOps: Tyk Sync or Tyk Operator

Last verified: 2026-11-19 against Tyk Sync 2.3.0, Tyk Operator 1.6.0 and Tyk Dashboard 5.16.0

Commands, output and behaviour for both tools are in the `tyk-gitops-sync-operator` skill; this
file keeps the mapping from Kong's tools.

Tyk has two declarative tools. Tyk Sync is the closest equivalent of Kong's decK; Tyk Operator is
the Kubernetes-native one (the equivalent of Kong Ingress Controller's custom resources). Tyk
Sync works through the Tyk Dashboard. Tyk Operator usually does too, but it can also manage an
open source Gateway directly (`TYK_MODE=ce`); its Kubernetes Gateway API support needs the
Dashboard.

## Licensing, exactly as documented

- **Tyk Operator**: "Tyk Operator is a licensed, supported Tyk product (not open source): it
  needs a license key" (https://tyk.io/docs/tyk-stack/tyk-operator/installing-tyk-operator). The
  key is `TYK_OPERATOR_LICENSEKEY` or `global.license.operator` in the Helm charts. It can target
  the open source Gateway (`TYK_MODE=ce`), but "a valid license key is required for running Tyk
  Operator" (same page).
- **Tyk Sync**: "From Tyk Sync v2.0, Tyk Sync will be closed source and we will only support use
  of Tyk Sync with licensed Tyk Dashboard" (2.0 section,
  https://tyk.io/docs/developer-support/release-notes/sync). "Tyk Sync is no longer usable with
  the Open Source (OSS) version of the Tyk Gateway"
  (https://tyk.io/docs/api-management/automations/sync). The documentation describes no Sync
  licence key of its own: it authenticates to the Dashboard with the Dashboard URL and an API
  secret.
- So both need a licence: Sync needs a licensed Dashboard, and Operator needs its own licence
  key even when it targets the open source Gateway (the Gateway itself stays open source). The
  Sync statement is Tyk's support policy; describe Sync with the open source Gateway as not
  supported. With only the open source Gateway and no Operator licence, GitOps means
  managing API definition files in the Gateway's apps folder (`app_path`,
  https://tyk.io/docs/tyk-oss-gateway/configuration) or calling the Gateway API from a pipeline,
  or Operator in `ce` mode with an Operator licence.

## Decision guide

| Question | Points to Tyk Sync | Points to Tyk Operator |
| --- | --- | --- |
| Where does the platform run? | VMs, Docker, any cloud, or Kubernetes without a GitOps controller | Kubernetes, with Argo CD, Flux or `kubectl apply` already in use |
| How is configuration authored? | Files exported from a Dashboard (`dump`), edited in Git, promoted by a CI pipeline | Kubernetes manifests (`TykOasApiDefinition`, `SecurityPolicy`, `TykStreamsApiDefinition`, `TykMcpProxyDefinition`) next to the application's own manifests |
| Who triggers changes? | A pipeline step runs `tyk-sync sync` (or `publish` and `update`) | A change to a Kubernetes resource (`kubectl apply`, Argo CD, Flux). The Operator reconciles on resource changes, on its own restart and on a periodic cache resync; it does not react to changes made in Tyk (https://tyk.io/docs/api-management/automations/operator) |
| Moving between environments | `dump` from one Dashboard, `publish`/`update`/`sync` to the next (https://tyk.io/docs/api-management/multiple-environments) | One set of manifests per environment or cluster (overlays) |
| Objects covered | API definitions (Tyk OAS, Classic, Streams), MCP proxies, policies, API Templates, API Fragments and identity provider records on Sync 2.3.0 (https://tyk.io/docs/api-management/automations/sync; a dump of Dashboard 5.16.0 includes MCP proxies and identity provider records) | APIs, policies and the other CRDs listed in https://tyk.io/docs/api-management/automations/operator |
| Kubernetes Gateway API | Not applicable | In Operator 1.6.0, off by default and needs a Dashboard (https://tyk.io/docs/developer-support/release-notes/operator); setup in the `tyk-gitops-sync-operator` skill |

Both can coexist: some teams author in Kubernetes for cluster-local APIs and use Sync for the
rest. One owner per API is not enough. Give every managed object exactly one writer: APIs,
policies, API Templates, API Fragments and assets. Two tools that own different APIs but share
a policy still collide. `tyk-sync sync` deletes what is in the Dashboard but not in its source,
so in an organisation that another tool also writes to, use `sync --no-delete`, unless the
whole organisation belongs to that one repository. `publish` and `update` each stop at the
first object that already exists (or does not), so a pipeline cannot run them repeatedly.
On Sync 2.3.0, `--no-delete` still deletes API Templates that are not in the source, so keep every Template in the one repository that runs `sync`. Before the
first destructive `sync`, list what it would remove: Sync has no dry run, so `tyk-sync dump` the
organisation into a temporary directory and compare it with the source directory (the
`tyk-gitops-sync-operator` skill has a script for this).

## decK and Tyk Sync commands

| decK (Kong) | What it does | Tyk Sync | Notes |
| --- | --- | --- | --- |
| `deck gateway dump` | Export the running configuration to a file | `tyk-sync dump -d <dashboard-url> -s <secret> -t <dir>` | Exports from the Dashboard only: "There is no equivalent flag or functionality to dump configurations directly from a standalone Gateway" (https://tyk.io/docs/api-management/automations/sync) |
| `deck gateway sync` | Make the gateway match the file, including deletes | `tyk-sync sync -d <dashboard-url> -s <secret> -p <dir>` (or a Git repository URL) | "Deletes APIs, policies, and Assets that exist in the Dashboard but not in the source (unless `--no-delete` flag is used)" |
| `deck gateway apply` | Create or update without deleting | `tyk-sync publish` (create only; "Stops if it detects a collision") plus `tyk-sync update` (update only; "Will not create new items") | Run `update` for existing objects and `publish` for new ones; each stops at the first collision, so pipelines use `sync --no-delete` |
| `deck gateway diff` | Preview changes | No equivalent command documented | Run `tyk-sync dump` into a temporary directory and `git diff --no-index` it against the repository |
| `deck file ...` (convert, validate) | Offline file operations | No equivalent command documented | Validate Tyk OAS documents against the published schema (https://tyk.io/docs/api-management/gateway-config-managing-oas); API governance rulesets can gate CI (Enterprise, licence claim; https://tyk.io/docs/tyk-governance/overview) |

The quoted command descriptions in the table are from
https://tyk.io/docs/api-management/automations/sync. Command reference:
https://tyk.io/docs/product-stack/tyk-sync/commands. Sync also has
`tyk-sync examples` to load sample APIs. Since Sync 1.5.0, failures "return non-zero exit codes",
so a pipeline can gate on them (https://tyk.io/docs/developer-support/release-notes/sync).

## Proving it

The `tyk-demo-builder` skill has both: `tyk-sync` plus `cicd` (GitHub and GitLab pipeline
templates; proof `SYNC-001`, `CICD-001`) and `operator` (a `TykOasApiDefinition` and a
`SecurityPolicy` reconciled from a kind cluster; proof `OPERATOR-001`). The `tyk-poc-runbook`
skill's criterion C1 tests delivery and rollback with either. The builder proofs show the
mechanism on the bundle: `SYNC-001` checks that the promoted API ids are present in the
Dashboard, not that their content matches the commit; the builder's promote step skips
policies and never deletes; `CICD-001` checks the pipeline templates statically and does not
run a CI system. C1 on the project's own pipeline is the evidence.
