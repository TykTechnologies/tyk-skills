# Tyk Operator: install, resources, reconciliation, drift, deletion, upgrades

Last verified: 2026-11-17 against Tyk Operator 1.6.0 (chart `tyk-helm/tyk-operator` 1.6.0, image `tykio/tyk-operator:v1.6.0`), Tyk Dashboard 5.16.0, Tyk Gateway 5.16.0, Kubernetes 1.34.0 (kind), cert-manager 1.15.3

Public pages: https://tyk.io/docs/api-management/automations/operator
(concepts), https://tyk.io/docs/tyk-stack/tyk-operator/installing-tyk-operator (install and
upgrade), https://tyk.io/docs/tyk-stack/tyk-operator/create-an-api (examples),
https://tyk.io/docs/product-stack/tyk-operator/crd-reference (fields),
https://tyk.io/docs/developer-support/release-notes/operator (release notes).

Tyk Operator is a Kubernetes controller. It reads Tyk custom resources and writes them to a
Tyk Dashboard (`TYK_MODE=pro`) or to an open source Gateway (`TYK_MODE=ce`) through their
APIs. The Dashboard and the Gateways do not have to run in Kubernetes; the Operator does.

## Resolve versions first

```bash
helm repo add tyk-helm https://helm.tyk.io/public/helm/charts/ && helm repo update tyk-helm
helm search repo tyk-helm/tyk-operator --versions | head -3
helm show values tyk-helm/tyk-operator --version 1.6.0 | grep -A3 '^image:'
```

Chart 1.6.0 ships the image `tykio/tyk-operator:v1.6.0` by default. The release notes for
1.6.0 (https://tyk.io/docs/developer-support/release-notes/operator) list Kubernetes 1.35.x as
tested and 1.34.x and 1.35.x as compatible, and Tyk Charts 5.5.0 as the umbrella chart that
ships it. Chart 5.5.0 is in the Helm repository (`helm search repo tyk-helm/tyk-stack`) before
the public tyk-charts Git repository carries a 5.5.0 tag, so resolve the chart from the Helm
repository. The same release notes say: "Only the most recent Tyk Operator release is
supported".

## Install

Needs: a Kubernetes cluster, cluster-admin rights for the install (the chart creates
cluster-wide resource definitions), cert-manager for the webhook certificate, a Tyk Operator
licence key, and the URL, access key and organisation ID of a Dashboard user for the Operator.

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.15.3/cert-manager.yaml
kubectl -n cert-manager wait --for=condition=Available deployment --all --timeout=240s

kubectl create namespace tyk-operator-system
kubectl -n tyk-operator-system create secret generic tyk-operator-conf \
  --from-env-file=operator-conf.env        # a private file, mode 600, never in Git
helm install tyk-operator tyk-helm/tyk-operator --version 1.6.0 -n tyk-operator-system
kubectl -n tyk-operator-system rollout status deploy/tyk-operator-controller-manager
```

`operator-conf.env` holds the five keys the Operator reads at start:

```text
TYK_OPERATOR_LICENSEKEY=<Tyk Operator licence key>
TYK_MODE=pro
TYK_URL=http://<dashboard-host>:3000
TYK_AUTH=<Dashboard user access key>
TYK_ORG=<organisation ID>
```

Optional keys, from the install page: `TYK_TLS_INSECURE_SKIP_VERIFY`,
`WATCH_NAMESPACE` (comma-separated; empty watches every namespace), `WATCH_INGRESS_CLASS`.
The umbrella charts (`tyk-stack`, `tyk-control-plane`, `tyk-oss`) install the Operator too and
take the licence as `global.license.operator` (install page); installing Tyk itself is the
`tyk-deployment-planner` skill's job.

A healthy start logs `License validated successfully. Valid till: <date>`. Licensing itself
(who needs a key, what a key covers) is the `tyk-facts` skill's subject.

## Custom resources in 1.6.0

`kubectl get crd | grep tyk.tyk.io` lists 18 definitions, all in group `tyk.tyk.io`, version
`v1alpha1`:

| Kind (short name) | What it manages |
| --- | --- |
| `TykOasApiDefinition` (`tykoas`) | A Tyk OAS API. The document lives in a ConfigMap that the resource names |
| `SecurityPolicy` (`tykpolicies`) | A policy. `access_rights_array` links APIs by resource name |
| `TykSession` (`tyksessions`) | An access key. The Operator writes the credential to a Secret |
| `TykStreamsApiDefinition` (`tykstreams`) | A Tyk Streams API |
| `TykMcpProxyDefinition` (`tykmcp`) | An MCP proxy, document in a ConfigMap |
| `ApiDefinition` (`tykapis`) | A Tyk Classic API. Use it only for what Tyk OAS cannot express, such as TCP, GraphQL and Universal Data Graph (concepts page) |
| `OperatorContext` | A connection to one Dashboard organisation or Gateway, or a data plane for the Gateway API |
| `SubGraph`, `SuperGraph` | GraphQL federation |
| `TykCertificate` (`tykcerts`), `TykOAuthClient`, `TykWebhook`, `TykIdentityProfile`, `TykUserGroup`, `TykRuleset` (`tykrs`) | Certificates, OAuth clients, webhooks, identity profiles, user groups and governance rulesets |
| `APIDescription`, `PortalAPICatalogue` (`tykcatalogues`), `PortalConfig` | The classic Developer Portal |

API Fragments have no resource: "Tyk Operator doesn't support Fragments"
(https://tyk.io/docs/platform-management/api-fragments).

## A Tyk OAS API, a policy and a key

`assets/operator/base/` holds this set. The API document is plain Tyk OAS JSON in
`catalog-oas.json`; kustomize turns it into the ConfigMap:

```yaml
apiVersion: tyk.tyk.io/v1alpha1
kind: TykOasApiDefinition
metadata:
  name: catalog
spec:
  tykOAS:
    configmapRef:
      name: catalog-oas
      keyName: catalog-oas.json
---
apiVersion: tyk.tyk.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: catalog-standard
spec:
  name: Catalog standard
  state: active
  active: true
  rate: 60
  per: 60
  quota_max: -1
  access_rights_array:
    - name: catalog
      kind: TykOasApiDefinition      # the default kind is ApiDefinition (classic)
      versions: [Default]
---
apiVersion: tyk.tyk.io/v1alpha1
kind: TykSession
metadata:
  name: partner-a
spec:
  type: token
  alias: partner-a
  policies:
    - name: catalog-standard
```

```text
$ kubectl apply -k assets/operator/base
$ kubectl get tykoas,tykpolicies,tyksessions
NAME      DOMAIN   LISTENPATH   PROXY.TARGETURL            ENABLED   SYNCSTATUS
catalog            /catalog/    kv://env/ORDERS_UPSTREAM   true      Successful
NAME               SYNCSTATUS
catalog-standard   Successful
NAME        TYPE    SYNCSTATUS   SECRET
partner-a   token   Successful   partner-a-tyk-key
```

- The Secret `partner-a-tyk-key` holds `token`, `keyID` and `keyHash`. Without the token the
  Gateway answers 401, with it 200.
- IDs are derived from the namespace and name: the API ID is `ZGVmYXVsdC9jYXRhbG9n`, which is
  `default/catalog` in base64. The same manifest in namespace `apis-prod` gives another ID.
  Moving a resource to another namespace therefore creates a new API. To take over an API
  that already exists in the Dashboard, set its ID in `x-tyk-api-gateway.info.id`
  (concepts page, "Updating an existing API").
- A `TykSession` can reference only policies in its own namespace (resource description in
  the 1.6.0 definition).

## Status fields

```bash
kubectl get tykoas catalog -o jsonpath='{.status.latestTransaction}'
```

| Field | Meaning |
| --- | --- |
| `status.latestTransaction.status` | `Successful` or `Failed`, shown as the SYNCSTATUS column |
| `status.latestTransaction.time` | when the Operator last wrote this object to Tyk |
| `status.latestTransaction.error` | the reason, when `Failed` |
| `status.id` (API), `status.pol_id` (policy) | the object's ID in Tyk |
| `status.linkedByPolicies` (API), `status.linked_apis` (policy) | the links the Operator resolved |
| `status.latestCRDSpecHash`, `status.latestTykSpecHash`, `status.latestConfigMapHash` | hashes the Operator uses to decide whether to write. The first two are different values on a healthy object; do not wait for them to match |

## Failures: status first, then the log

A document without a listen path gives:

```text
$ kubectl get tykoas catalog
NAME      DOMAIN   LISTENPATH   PROXY.TARGETURL            ENABLED   SYNCSTATUS
catalog                         kv://env/ORDERS_UPSTREAM   true      Failed
$ kubectl get tykoas catalog -o jsonpath='{.status.latestTransaction.error}'
Failed api call: HTTP 400: x-tyk-api-gateway.server: listenPath is required Status: Error HTTP 400
```

The controller log carries the same message
(`kubectl -n tyk-operator-system logs deploy/tyk-operator-controller-manager -c manager`).
While the resource is `Failed`, the Gateway keeps serving the last good version (200).

The status shows nothing in two cases, so also check the pods and the time:

- **No running Operator.** With an expired or malformed licence the new pod never starts:
  `kubectl -n tyk-operator-system get pods` shows `CrashLoopBackOff` and the log says
  `License error:` with `Token is expired` (or `token contains an invalid number of
  segments`) and `failed to validate license`. A resource changed in that state keeps
  `SYNCSTATUS Successful` with its old `latestTransaction.time`, and the Dashboard keeps the old
  value. During a rolling restart the previous pod keeps working, which hides the problem
  until that pod is gone. After a licence change, update the Secret and restart the
  Deployment: the Secret reaches the pod as environment variables, which are set at start.
- **Someone else changed or deleted the object in Tyk.** See "Drift".

## Reconciliation and drift

The Operator writes to Tyk when a resource's spec or its ConfigMap changes, and when the
Operator starts. It does not watch Tyk: "Reconciliation will NOT be triggered when there is an
update or modification on Tyk's side" (concepts page,
https://tyk.io/docs/api-management/automations/operator). The same page adds that
reconciliation also happens "when the cache used by the controller expires", typically after
about 10 hours, depending on the Operator's configuration. Do not rely on that resync for drift
correction.

| What happens | Result |
| --- | --- |
| API upstream and policy rate changed in the Dashboard | Still changed 90 seconds later. `SYNCSTATUS` stays `Successful` |
| `kubectl apply` of the same manifests | The policy is put back (`securitypolicy ... configured`); the API is not (`unchanged`) |
| An annotation added to the API resource | Nothing |
| Operator restarted (`kubectl -n tyk-operator-system rollout restart deploy/tyk-operator-controller-manager`) | The API is put back within 20 seconds |
| API deleted in the Dashboard by hand | Stays deleted, resource still `Successful`; recreated with the same ID after an Operator restart, and the key works again |

So drift correction with the Operator means: restart the Operator on a schedule or after an
incident, and take write access to APIs and policies away from people in the Dashboard. The
install page says the same: "It is the recommended practice to turn off write access for other
users".

Every `kubectl apply` reports `securitypolicy ... configured`, even with no change, because
the Operator writes `_id`, `id`, `org_id` and `access_rights` into the policy's spec. A GitOps
controller sees that as a difference on every comparison; tell it to ignore those four fields
of `SecurityPolicy`.

## Changing and rolling back

A change is a commit and an apply. The ConfigMap change alone triggers the API
(`configmap/catalog-oas configured`, `tykoasapidefinition ... unchanged`), and the new listen
path answers within 10 seconds while the old one returns 404.

Rollback is `git revert` and the same apply: after it the old listen path answers 200 again,
the new one 404, and the policy rate is back. With Argo CD or Flux the apply is theirs.

## Deletion

- Deleting a resource deletes the object in Tyk. Deleting a `TykSession` removes the key and
  its Secret.
- An API that a policy references cannot be deleted first. The resource stays in
  `Terminating` with
  `Unable to delete TykOAS API as it's referenced by security policy: default/catalog-standard`
  in `latestTransaction.error`, and the API stays in the Dashboard. Delete the session and the
  policy; the API deletion then completes by itself.
- Do not delete an `OperatorContext` together with the resources that use it.
  `kubectl delete -k` on an overlay that holds both leaves the API resource stuck in
  `Terminating` (log: `OperatorContext.tyk.tyk.io "prod" not found`) and the API still in the
  Dashboard. Applying the context again lets the deletion finish. Delete resources first, the
  context last.
- `helm uninstall tyk-operator` leaves the 18 definitions and every resource in place.

## Upgrades and resource definitions

Helm installs the definitions on first install and never updates them. After
`helm upgrade` from chart 1.5.0 to 1.6.0 the pod runs `tykio/tyk-operator:v1.6.0`, but a
manifest using a 1.6 field is refused:

```text
OperatorContext in version "v1alpha1" cannot be handled as a OperatorContext:
strict decoding error: unknown field "spec.dataPlane"
```

Apply the definitions of the new version with every upgrade:

```bash
helm upgrade tyk-operator tyk-helm/tyk-operator --version 1.6.0 -n tyk-operator-system --reuse-values
helm show crds tyk-helm/tyk-operator --version 1.6.0 | kubectl apply --server-side --force-conflicts -f -
```

The install page gives the same step with a file per version,
`https://raw.githubusercontent.com/TykTechnologies/tyk-charts/refs/heads/main/tyk-operator-crds/crd-v1.6.0.yaml`.

## The open source Gateway (`ce` mode)

An `OperatorContext` whose Secret holds `TYK_MODE=ce`, the Gateway URL as `TYK_URL`, the
Gateway API secret as `TYK_AUTH` and an organisation ID as `TYK_ORG` lets the same
`TykOasApiDefinition` and `SecurityPolicy` resources reach an open source Gateway 5.16.0:
both show `Successful`, the Gateway writes the API pair and the policy file into its own
`app_path` and `policy_path`, and the API answers 200. This needs both directories writable
and `policies.policy_source` set to `file` with a `policy_path` on the Gateway. The Operator
still needs its licence key in this mode (install page).

Deleting works for the API (its files are removed) but not for the policy: the
`SecurityPolicy` stays in `Terminating` with
`latestTransaction.error: TODO: This feature is not implemented yet`, and its file stays in
`policy_path`. Clear the resource with
`kubectl patch tykpolicies stock-standard --type merge -p '{"metadata":{"finalizers":[]}}'`;
the API deletion then finishes after a restart of the Operator. Remove the policy file
with the delivery job (`oss-gateway-files.md`).

## Limitations

- Kubernetes only, and it needs cluster-wide rights to install.
- No drift detection against Tyk (above); a manual change can last until the next resource
  change, restart or cache resync.
- No API Fragments. No dry run; an invalid document is reported after the apply, in the status.
- One Dashboard organisation per `OperatorContext`; resources without a `contextRef` use the
  connection in `tyk-operator-conf`.
- Kubernetes Gateway API support is separate and off by default: `operator-gateway-api.md`.
