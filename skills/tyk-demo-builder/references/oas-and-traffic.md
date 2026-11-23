# Traffic, Versioning And Custom-Domain Modules

Last verified: 2026-11-19 against Tyk Gateway and Dashboard 5.16.0.

This page covers what the traffic modules generate and what their proofs assert. For writing
Tyk OAS API definitions (listen paths, path matching, per-operation middleware, upstream,
versioning, timeouts), use the `tyk-api-definitions-routing` skill; for per-consumer rate limits and
quotas on keys and policies, the `tyk-rate-limits-keys-policies` skill.

## Core API Modules

`routing`, `transforms`, `validation`, `error-overrides`, `rate-limit`, `cache` and
`circuit-breaker` all add operation middleware to `<display name> Core API`
(`apis/<slug>-core.oas.json`, listen path `/demo/<slug>/core/`). `quota` sets the quota of the
API-key policy (`policies/<slug>-api-key-policy.json`). The bundled upstream
(`upstreams/http_api.py`) reflects method, path, headers and parsed body, and counts hits, so
every proof checks what reached the upstream, not only the status code.

| Proof | What it asserts |
| --- | --- |
| `ROUTE-001` | a request whose body carries two conflicting country fields is routed on the nested one ("InboundData country won conflicting route") |
| `TRANSFORM-001` | a Go-template body transform maps a scalar lookup and repeated parcel and item fields |
| `VALIDATE-001` | an invalid payload is rejected with 422 before the upstream |
| `ERROR-001` | 401, 422, 429 and 503 answers use the bundle's standard error envelope |
| `LIMIT-001` | a burst against the limited operation produces 429 |
| `QUOTA-001` | a fresh key exceeds the policy quota (`200 200 403 403`) |
| `CACHE-001` | a cached response leaves the upstream hit count unchanged |
| `CB-001` | after failures the circuit opens and the fourth call never reaches the upstream |

Keep upstream URLs bare (`http://host:port`) and put paths in the listen path or the operation:
the generated APIs follow that shape and their transform proofs pass with it.

## versioning (`VERSION-001`)

Adds `<display name> Versioned Services` (base, version `v1`) and its child
`<display name> Versioned Services v2`, selected by the `x-api-version` header. Any file in
`apis/` can be a child version: a top-level `x-demo-version-of` block (`base_api_id`,
`base_api_version_name`, `new_version_name`, `set_default`, named after the Dashboard's query
parameters) makes `scripts/import-apis.sh`:

- import every base first;
- create each child through
  `POST /api/apis/oas?base_api_id=<base>&base_api_version_name=v1&new_version_name=v2&set_default=false`;
- update versioned APIs carrying `info.dbId`, `info.orgId` and `info.versioning` from their
  Dashboard readback;
- never prune a child: a child goes with its base or by hand.

`VERSION-001` asserts: no version header is served by v1, `x-api-version: v2` by v2, an unknown
version returns 404, the base readback links v2 by the `x-api-version` header, and after
configuration has run twice there is exactly one Dashboard row per API id and no row without an
id.

The generator refuses `versioning` together with `tyk-sync` (or `cicd`): the version link is
created by the importer, which Tyk Sync does not read, so `SYNC-001` could not vouch for it.

## custom-domain (`DOMAIN-001`)

The "keep existing URLs during migration" recipe. `TYK_GW_ENABLECUSTOMDOMAINS=true` is set on
every gateway, and `<display name> Legacy Path` binds `server.customDomain` to
`api.legacy.<slug>.invalid` with the listen path `/api/v1/records/` and
`listenPath.strip: true`, so the upstream sees only the remainder. `DOMAIN-001` asserts, with
the Host `api.legacy.<slug>.invalid`, a 200 and the upstream path `/42?fields=name`; without
that Host a 404; with another Host under the same parent domain a 404.
Replace the `.invalid` host with your real one only in your own copy, and point DNS or the load
balancer at the gateway before a cut-over.
