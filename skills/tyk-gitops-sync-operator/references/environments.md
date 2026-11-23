# Environments: the same definitions with different upstreams and secrets

Last verified: 2026-11-17 against Tyk Gateway 5.16.0, Tyk Dashboard 5.16.0, Tyk Sync 2.3.0, Tyk Operator 1.6.0

The examples use two separate environments, each with its own Dashboard, Gateway, Redis and
database. Public pages:
https://tyk.io/docs/tyk-configuration-reference/kv-store (secrets management),
https://tyk.io/docs/tyk-configuration-reference/kv-store-legacy (older notation),
https://tyk.io/docs/api-management/multiple-environments (moving between environments).

## The problem

A definition dumped from one environment carries that environment's upstream URL. Promoted
unchanged to another, it fails:

```text
$ curl http://<prod-gateway>/orders/list
{"error": "Upstream host lookup failed"}        HTTP 500
```

None of the three tools rewrites values per environment. Tyk Sync replaces only the
organisation and database IDs. So keep environment values out of the definitions.

## The pattern: references that each Gateway resolves

Put a reference in the definition; give each environment's Gateways the value.

```json
"x-tyk-api-gateway": {
  "upstream": {"url": "kv://env/ORDERS_UPSTREAM"},
  "middleware": {"global": {"transformRequestHeaders": {"enabled": true,
    "add": [{"name": "X-Upstream-Token", "value": "$kv{env:UPSTREAM_TOKEN}"}]}}}
}
```

| Environment | Gateway environment variables | Result of `GET /orders/list` |
| --- | --- | --- |
| dev | `TYK_SECRET_ORDERS_UPSTREAM=http://orders-dev:8080`, `TYK_SECRET_UPSTREAM_TOKEN=dev-token-value` | upstream `orders-dev` receives `x-upstream-token: dev-token-value` |
| prod | `TYK_SECRET_ORDERS_UPSTREAM=http://orders-prod:8080`, `TYK_SECRET_UPSTREAM_TOKEN=prod-token-value` | upstream `orders-prod` receives `x-upstream-token: prod-token-value` |

The same files go to both Dashboards with `tyk-sync sync`, and nothing in Git differs between
the two.

- `kv://<store>/<path>` is a whole-value reference; `$kv{<store>:<path>}` is the form inside
  a longer string. Both are available from Gateway and Dashboard 5.15.0 (see the secrets
  management page above).
- The built-in store `env` reads the environment variable `TYK_SECRET_<NAME>`, upper case
  (https://tyk.io/docs/tyk-configuration-reference/kv-store-providers).
- These references are resolved when the Gateway loads the API, not per request
  (https://tyk.io/docs/tyk-configuration-reference/kv-store-legacy). After changing a variable, restart or reload the
  Gateways.
- The Dashboard stores the reference as text: `GET /api/apis/oas/<id>` returns
  `"url": "kv://env/ORDERS_UPSTREAM"`. The secret value never enters Git or the Dashboard.
- Other stores (Vault, Consul, cloud secret managers) are configured on the Gateway; the
  secrets management page lists them and which need the Enterprise Edition. Quote that page,
  or the `tyk-facts` skill, for what each edition includes.
- An unresolved reference can reach an upstream as plain text and reveal store names
  (https://tyk.io/docs/tyk-configuration-reference/kv-store, "Failed References"). Test each environment
  after the first promotion.

## With Tyk Sync

One directory in Git, one Dashboard per environment, one pipeline job per environment that
differs only in `TYK_DASHBOARD_URL` and the secret (`tyk-sync-ci.md`). What stays the same
across environments: API IDs, policy IDs, Template and Fragment IDs. What Tyk Sync changes on
the way in: the organisation ID and the database IDs.

Promotion order is the Git flow you choose: for example a `dev` job on every merge and a
`prod` job behind an approval on the same commit. `tyk-sync dump` from dev is how a change
made in the dev Dashboard gets into Git in the first place.

## With Tyk Operator

One base, one overlay per environment (`assets/operator/`). The prod overlay adds an
`OperatorContext` and points every resource at it:

```yaml
# overlays/prod/context.yaml
apiVersion: tyk.tyk.io/v1alpha1
kind: OperatorContext
metadata:
  name: prod
spec:
  secretRef:
    name: tyk-prod-conf        # TYK_MODE, TYK_URL, TYK_AUTH, TYK_ORG of the prod Dashboard
    namespace: apis-prod
```

```yaml
# overlays/prod/kustomization.yaml (extract)
namespace: apis-prod
resources: [../../base, context.yaml]
patches:
  - target: {group: tyk.tyk.io, kind: TykOasApiDefinition}
    patch: |-
      - op: add
        path: /spec/contextRef
        value: {name: prod, namespace: apis-prod}
  - target: {group: tyk.tyk.io, kind: SecurityPolicy}
    patch: |-
      - op: add
        path: /spec/contextRef
        value: {name: prod, namespace: apis-prod}
      - op: replace
        path: /spec/rate
        value: 600
```

After `kubectl apply -k overlays/prod` the prod Dashboard holds the API and a policy with rate
600 (the base says 60), the key from the Secret in `apis-prod` returns 200 through the prod
Gateway, and the upstream is `orders-prod`.

- One Operator can serve both Dashboards: resources without `contextRef` use the connection in
  `tyk-operator-conf`, resources with it use the context's Secret. A cluster per environment,
  each with its own Operator, needs no context at all.
- The API ID includes the namespace (`apis-prod/catalog` in base64), so IDs differ between
  environments unless the document sets `x-tyk-api-gateway.info.id`.
- Create the context's Secret outside Git (or with a sealed or external secret tool); it holds
  a Dashboard access key.
- Delete order matters: resources first, the context last (`tyk-operator.md`, "Deletion").

## With files and the open source Gateway

The same documents and policy files go to every environment's Gateways; the Gateways carry
the `TYK_SECRET_*` variables. `kv://env/ORDERS_UPSTREAM` resolves the same way on the open
source Gateway 5.16.0 (`oss-gateway-files.md`).

## Limitations

- Listen paths, policy limits and other plain values are the same everywhere unless a tool
  outside Tyk changes them: a kustomize patch (Operator), or a step that edits the files
  before `tyk-sync` runs. Keep such edits small and visible in the pipeline.
- Keys are per environment and are not promoted by any of the three approaches, except
  `TykSession` resources, which create a new key in each environment.
