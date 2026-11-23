# Choosing: Tyk Sync, Tyk Operator, or files

Last verified: 2026-11-17 against Tyk Sync 2.3.0, Tyk Operator 1.6.0, Tyk Dashboard 5.16.0, Tyk Gateway 5.16.0

The other reference files carry the details behind the tables. The licence statements are
quoted from the public documentation; for anything more about licences, packages or prices
use the `tyk-facts` skill.

## Three questions

1. **Is there a Tyk Dashboard?** Tyk Sync needs one. Files are for a Gateway without one.
2. **Is Kubernetes the place where configuration is applied?** The Operator runs only there.
3. **Is there an Operator licence?** The Operator needs its own key, in every mode.

| Situation | Use | Why |
| --- | --- | --- |
| Open source Gateway, no Dashboard, no Operator licence | Files in `app_path` and `policy_path`, delivered by CI, then a reload | The only option without a licence |
| Open source Gateway on Kubernetes, with an Operator licence | Tyk Operator in `ce` mode | Resources instead of hand-delivered files |
| Tyk Dashboard, configuration applied by a pipeline, on any platform | Tyk Sync | Works anywhere Docker runs; carries Templates and Fragments |
| Tyk Dashboard, and teams already ship Kubernetes manifests with Argo CD, Flux or `kubectl` | Tyk Operator in `pro` mode | APIs live beside the service's own manifests; keys as resources |
| Kubernetes Gateway API (`HTTPRoute`) wanted | Tyk Operator 1.6.0 or later with a Dashboard | Only the Operator implements it |
| API Fragments in use | Tyk Sync | The Operator has no Fragment resource; the Gateway alone does not resolve them |

## What each one needs

| | Files | Tyk Sync 2.3.0 | Tyk Operator 1.6.0 |
| --- | --- | --- | --- |
| Components | Gateway, Redis | Licensed Dashboard (with its Gateways, Redis, database) | Kubernetes cluster, cert-manager, and a Dashboard or an open source Gateway |
| Licence | None | No key of its own. "From Tyk Sync v2.0 ... we will only support use of Tyk Sync with licensed Tyk Dashboard" (https://tyk.io/docs/developer-support/release-notes/sync) | Its own key: "Starting from Tyk Operator v1.0, a license key is required to use the Tyk Operator" (https://tyk.io/docs/tyk-stack/tyk-operator/installing-tyk-operator) |
| Credential | Gateway API secret | Access key of a Dashboard user | Access key of a Dashboard user (or the Gateway API secret in `ce` mode), plus the licence key. The install page names the licensed mode "Self-managed or Tyk Cloud" |
| Runs as | Your CI job | A command your CI job runs | A controller that is always running |

## What each one manages

| Object | Files | Tyk Sync | Tyk Operator |
| --- | --- | --- | --- |
| Tyk OAS APIs | yes (a pair of files per API) | yes | yes (`TykOasApiDefinition`) |
| Tyk Classic APIs | yes | yes | yes (`ApiDefinition`) |
| MCP proxies (the `tyk-mcp-gateway` skill owns them) | | yes | yes (`TykMcpProxyDefinition`) |
| Policies | yes (files) | yes | yes (`SecurityPolicy`) |
| API Templates, API Fragments | no | yes | no |
| Identity Provider Registry records | no | yes | no |
| Access keys | no (Gateway API, stored in Redis) | no | yes (`TykSession`) |

The Operator also has resources for certificates, OAuth clients, webhooks, identity profiles,
user groups and governance rulesets (`tyk-operator.md`, "Custom resources in 1.6.0"). Tyk
Sync's four commands cover only the objects in its column.

## How each one behaves

| | Files | Tyk Sync | Tyk Operator |
| --- | --- | --- | --- |
| Applies a change | when the job copies files and calls reload | when the job runs | when a resource or its ConfigMap changes |
| Puts back a manual change | on the next delivery | on the next `sync` run | on the next change to that resource, when the Operator restarts, or when the controller's cache expires (about 10 hours per the docs, depending on configuration) |
| Tells you about a manual change | no | with `scripts/sync_drift.py` on a schedule | no |
| Deletes what Git no longer holds | when the job replaces the directory | `sync` yes; `sync --no-delete` no (API Templates still deleted) | when the resource is deleted |
| Reports a bad definition | Gateway log (`Spec not valid, skipped!`) | exit code 1 and an `Error:` line | `SYNCSTATUS Failed` and `status.latestTransaction.error` |
| Rollback | revert the commit, run the job | revert the commit, run the job | revert the commit, apply |
| Dry run | none | none | none |

## Using two at once

Possible, with one rule: every object has exactly one writer. A `tyk-sync sync` deleted an API
and a policy that the Operator had created in the same organisation, and the Operator's
resources went on showing `Successful`. Either give each tool its own organisation, or run
Tyk Sync with `--no-delete` and never list an Operator-managed object in the Tyk Sync
directory. Policies count too: two tools that own different APIs but edit one policy collide.

## Limitations to say plainly

- None of the three has a dry run or a diff.
- None of them moves keys between environments.
- Tyk Sync 2.x is not supported with the open source Gateway (it is supported only with a
  licensed Dashboard); the Operator works with it in `ce` mode, with a licence.
