# Helm chart upgrades on Kubernetes

Last verified: 2026-11-19 against the `tyk-helm/tyk-oss` chart 5.4.1 and 5.5.0, Tyk Gateway (`docker.tyk.io/tyk-gateway/tyk-gateway`) 5.13.2, 5.13.5, 5.15.1 and 5.16.0, Redis 7.4, Helm 3.18, kind with Kubernetes 1.34.0

This file covers the Gateway in the open source umbrella chart. The `tyk-stack` chart 5.5.0
uses the same layout, one block per component chart (`tyk-gateway:`, `tyk-dashboard:`,
`tyk-pump:`, `tyk-dev-portal:`, ...), each with its own `image.tag`; the examples here use the
Gateway block. Installing and running the charts belongs to the `tyk-deployment-planner` and
`tyk-deployment-runner` skills.

Chart release notes: https://tyk.io/docs/developer-support/release-notes/helm-chart. Upgrade
command in the docs: `helm upgrade [RELEASE_NAME] tyk-helm/[CHART_NAME]`.

## Pin the image tag, at the right path

Chart 5.5.0 "installs the most recent Tyk LTS release 5.13.5" and "You must modify the charts to
install the desired version if not using LTS" (helm-chart release notes). Rendered defaults:

```bash
helm template t tyk-helm/tyk-oss --version 5.4.1 --set global.redis.addrs='{redis:6379}' \
  --set global.secrets.APISecret=x | grep 'image: "docker.tyk.io'
# image: "docker.tyk.io/tyk-gateway/tyk-gateway:v5.13.2"     (chart 5.5.0: v5.13.5)
```

In the umbrella chart the Gateway values sit under the subchart name:

```yaml
global:
  redis:
    addrs: ["redis.tyk.svc:6379"]
tyk-gateway:
  gateway:
    image:
      tag: v5.16.0
```

A values file with `gateway.image.tag: v5.15.1` at the top level (without `tyk-gateway:`)
installs with no error and runs `v5.13.2`, the chart 5.4.1 default. Read the image
back after every install and upgrade:

```bash
kubectl -n tyk get deploy gateway-tyk-tyk-gateway \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
```

## A chart upgrade can change the Tyk version

Starting from a release pinned to `v5.15.1` on chart 5.4.1:

| Command | Gateway image afterwards |
| --- | --- |
| `helm upgrade tyk tyk-helm/tyk-oss --version 5.5.0 --reuse-values` | `v5.15.1` (previous values kept) |
| `helm upgrade tyk tyk-helm/tyk-oss --version 5.5.0 -f values-without-tag.yaml` | `v5.13.5`: the new chart's default, a downgrade |
| `helm upgrade tyk tyk-helm/tyk-oss --version 5.5.0 -f values-5.16.0.yaml` | `v5.16.0` |

Keep one values file per environment in version control with every tag pinned, pass it with
`-f` on every upgrade, and upgrade the chart and the Tyk version as separate, deliberate
changes. `--reuse-values` carries the previous release's values forward; a values file in
version control is easier to review.

## API definitions inside the pod are lost on upgrade

The open source chart mounts an `emptyDir` at `/mnt/tyk-gateway` and points
`TYK_GW_APPPATH` at `/mnt/tyk-gateway/apps`. An API created through the Gateway API
(`POST /tyk/apis/oas`, then `/tyk/reload/group`) is written there. After a `helm upgrade`
replaces the pod, `GET /tyk/apis` returns `[]` and the API's listen path answers 404.

Keep the definitions outside the pod. One way: export the pair the Gateway uses in file mode
and mount it from a ConfigMap.

```bash
curl -s -H "x-tyk-authorization: $GW_SECRET" localhost:8080/tyk/apis/orders     > apps/orders.json
curl -s -H "x-tyk-authorization: $GW_SECRET" localhost:8080/tyk/apis/oas/orders > apps/orders-oas.json
kubectl -n tyk create configmap tyk-apis --from-file=apps/ --dry-run=client -o yaml | kubectl apply -f -
```

```yaml
tyk-gateway:
  gateway:
    extraVolumes:
      - name: tyk-apis
        configMap:
          name: tyk-apis
    extraVolumeMounts:
      - name: tyk-apis
        mountPath: /mnt/tyk-gateway/apps
        readOnly: true
```

After the upgrade to chart 5.5.0 and Gateway 5.16.0 with these values, `GET /tyk/apis`
returns `["orders"]` and the API answers 200. The `tyk-gitops-sync-operator` skill covers
file delivery and the Operator in full. With a Dashboard, definitions live in its database and
survive pod replacement.

## Readiness: use `/ready`

The chart's default probes, rendered from 5.4.1 and 5.5.0, use `path: /hello` for both
liveness and readiness. `/hello` answers 200 before the Gateway has loaded its APIs
(`order-and-mixed-versions.md`), so a new pod can take traffic and answer 404 for a moment.
Override readiness:

```yaml
tyk-gateway:
  gateway:
    readinessProbe:
      httpGet:
        scheme: HTTP
        path: /ready
        port: 8080
      initialDelaySeconds: 1
      periodSeconds: 5
```

On 5.16.0 the pod becomes Ready with this probe and `/ready` returns
`{"status":"pass","version":"5.16.0","description":"Tyk GW Ready",...}`.

The rendered Deployment uses `RollingUpdate` with `maxSurge: 2` and `maxUnavailable: 1`. With
one replica, `maxUnavailable: 1` lets Kubernetes stop the only old pod before a new one is
ready (https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#max-unavailable).
Run at least two Gateway replicas, or set `maxUnavailable: 0`, before an upgrade that must not
drop traffic. Chart 5.5.0 renders this override as written (`helm template`):

```yaml
tyk-gateway:
  gateway:
    strategy:
      type: RollingUpdate
      rollingUpdate:
        maxSurge: 1
        maxUnavailable: 0
```

## Rollback

`helm rollback <release> <revision>` restores that revision's chart and values. For example,
`helm rollback tyk 3` takes the Gateway from `v5.16.0` back to `v5.15.1` (history: `6 deployed
tyk-oss-5.5.0 Rollback to 3`). It also restores revision 3's probe (`/hello`) and drops the
ConfigMap mount, so the API from the ConfigMap is gone again. A rollback brings back
everything in that revision's values, not only the image tag.

## Tyk Operator

`helm upgrade` does not update CustomResourceDefinitions. The Operator upgrade, with the
command that applies the new CRDs, is in the `tyk-gitops-sync-operator` skill. The upgrade
guide says to deploy the new Operator only after the Dashboard and Gateway run the new version
when it manages MCP proxies (`order-and-mixed-versions.md`).
