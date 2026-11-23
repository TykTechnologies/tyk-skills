# Tyk Operator and the Kubernetes Gateway API

Last verified: 2026-11-17 against Tyk Operator 1.6.0 (chart 1.6.0), Kubernetes Gateway API v1.6.2 experimental channel, Tyk Dashboard 5.16.0, Tyk Gateway 5.16.0 (Enterprise Edition image, in the cluster), Kubernetes 1.34.0 (kind)

Public pages:
https://tyk.io/docs/product-stack/tyk-operator/gateway-api/overview,
https://tyk.io/docs/product-stack/tyk-operator/gateway-api/get-started,
https://tyk.io/docs/product-stack/tyk-operator/gateway-api/secure-and-extend-routes,
https://tyk.io/docs/product-stack/tyk-operator/gateway-api/reference.

From 1.6.0 the Operator turns `GatewayClass`, `Gateway` and `HTTPRoute` resources into Tyk OAS
APIs in the Tyk Dashboard. It "requires Tyk Dashboard, and is disabled by default"
(https://tyk.io/docs/developer-support/release-notes/operator, 1.6.0). Read `tyk-operator.md`
first.

## What it needs

- Tyk Operator 1.6.0 or later, connected to a Tyk Dashboard (`TYK_MODE=pro`).
- Tyk Gateways running in the cluster with a Kubernetes Service in front of them.
- The Gateway API definitions, experimental channel, v1.6.2: the version the Operator is
  tested with (get-started page).

## Switch it on, in this order

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.2/experimental-install.yaml
helm upgrade tyk-operator tyk-helm/tyk-operator --version 1.6.0 -n tyk-operator-system \
  --reuse-values --set enableGatewayAPI=true
```

The chart value `enableGatewayAPI` (default `false`) becomes the container argument
`--enable-gateway-api`. With it off the Operator logs
`Gateway API support is disabled; pass --enable-gateway-api to reconcile Gateway API resources`.

Definitions first. With the flag on and no Gateway API definitions in the cluster, the new
Operator pod goes into `CrashLoopBackOff`; the previous pod keeps running until it is replaced.
Installing the definitions and restarting the Deployment fixes it. `--server-side` is required
because the experimental definitions are too large for a client-side apply (get-started page).

## Gateways

Set on every Tyk Gateway that serves the routes (get-started page):

| Environment variable | Value |
| --- | --- |
| `TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED` | `true` |
| `TYK_GW_DBAPPCONFOPTIONS_TAGS` | the group's tag, for example `edge` |
| `TYK_GW_ENABLECUSTOMDOMAINS` | `true`, so requests are routed by hostname |
| `TYK_GW_HTTPSERVEROPTIONS_ENABLESTRICTROUTES` | `true`, so `/orders` does not match `/orders-archive` |

A segmented Gateway loads only APIs that carry one of its tags. If the same Gateways serve
other APIs, tag those too or use a separate group (get-started page).

## Resources

Two `OperatorContext` resources connect Kubernetes to Tyk, then the standard three:

```yaml
apiVersion: tyk.tyk.io/v1alpha1
kind: OperatorContext
metadata: {name: tyk-dashboard, namespace: tyk-operator-system}
spec:
  secretRef: {name: tyk-operator-conf, namespace: tyk-operator-system}
---
apiVersion: tyk.tyk.io/v1alpha1
kind: OperatorContext
metadata: {name: edge-data-plane, namespace: tyk}
spec:
  dataPlane:
    serviceRef: {name: tyk-gateway}     # the Service in front of the Gateways, same namespace
    tags: [edge]
---
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata: {name: tyk}
spec:
  controllerName: tyk.io/gateway-controller
  parametersRef: {group: tyk.tyk.io, kind: OperatorContext, name: tyk-dashboard, namespace: tyk-operator-system}
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata: {name: edge, namespace: tyk}
spec:
  gatewayClassName: tyk
  infrastructure:
    parametersRef: {group: tyk.tyk.io, kind: OperatorContext, name: edge-data-plane}
  listeners:
    - name: http
      protocol: HTTP
      port: 8080                        # a port of the tyk-gateway Service
      allowedRoutes: {namespaces: {from: All}}
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata: {name: orders, namespace: default}
spec:
  parentRefs: [{name: edge, namespace: tyk}]
  hostnames: [api.example.com]
  rules:
    - matches: [{path: {type: PathPrefix, value: /orders}}]
      filters:
        - type: URLRewrite
          urlRewrite: {path: {type: ReplacePrefixMatch, replacePrefixMatch: /}}
      backendRefs: [{name: orders, port: 80}]
```

After the apply:

| Check | Result |
| --- | --- |
| `kubectl get gatewayclass tyk -o jsonpath='{.status.conditions}'` | `Accepted` `True` |
| `kubectl -n tyk get gateway edge -o jsonpath='{.status}'` | `Accepted` `True`, `Programmed` `True`, `addresses` holds the Service's cluster IP |
| `kubectl get httproute orders -o jsonpath='{.status.parents}'` | `Accepted` `True`, `ResolvedRefs` `True` for parent `edge` |
| Dashboard | A Tyk OAS API named `gwapi-<hash>`, tag `edge`, custom domain `api.example.com`, listen path `/orders`, upstream `http://orders.default.svc.cluster.local:80` |
| `curl -H 'Host: api.example.com' http://tyk-gateway:8080/orders/get` (from a pod) | 200; the backend receives `/get` |
| Same request with `Host: other.example.com` | 404 |
| `/orders-archive` with the right host | 404 |

When a condition is `False`, its `reason` and `message` say why (reference page, "Status and
Troubleshooting").

## Securing a route

A route alone has no authentication. Attach a Tyk OAS document to the route with
`spec.targetRefs`, and give clients access with a policy that names the route:

```yaml
apiVersion: tyk.tyk.io/v1alpha1
kind: TykOasApiDefinition
metadata: {name: orders-security, namespace: default}    # the route's namespace
spec:
  tykOAS:
    configmapRef: {name: orders-security, namespace: default, keyName: orders-security.json}
  targetRefs:
    - {group: gateway.networking.k8s.io, kind: HTTPRoute, name: orders}
---
apiVersion: tyk.tyk.io/v1alpha1
kind: SecurityPolicy
metadata: {name: orders-route, namespace: default}
spec:
  name: Orders route
  state: active
  active: true
  rate: 60
  per: 60
  quota_max: -1
  access_rights_array:
    - {name: orders, namespace: default, kind: HTTPRoute, versions: [Default]}
---
apiVersion: tyk.tyk.io/v1alpha1
kind: TykSession
metadata: {name: orders-client, namespace: default}
spec:
  type: token
  policies: [{name: orders-route}]
```

The ConfigMap `orders-security` holds a Tyk OAS document with only the authentication section
(the example on the secure-and-extend-routes page works as written). After the apply:
no token 401, a wrong token 403, the token from Secret `orders-client-tyk-key` 200.

The attached `TykOasApiDefinition` creates no API of its own and shows no SYNCSTATUS; the route
sets listen path, domain, upstream and tags, the attached document sets the rest
(secure-and-extend-routes page).

## Limitations

From the reference page: path matches of type `Exact` and
`RegularExpression`, `headers` and `queryParams` matches, backends that are not a Service with
a port, `RequestMirror`, `CORS`, `ExternalAuth` and `ExtensionRef` filters, and `add` in header
modifiers are not supported. An unsupported part of a route is rejected and the reason is
reported in the route's status. Check that page for the current list before promising a route
feature.

The `Gateway` should always carry `infrastructure.parametersRef`: without it, every Tyk Gateway
that is not segmented loads its APIs (get-started page).
