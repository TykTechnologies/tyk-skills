# Choose the kind of extension

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`)

A plugin is code you own, build, deploy to every Gateway and upgrade with every Gateway release.
Reach for one only when configuration cannot do the job.

## First: is configuration enough?

These are built into the Tyk OAS definition and need no code. Shapes and real responses: the
`tyk-api-definitions-routing` skill (`references/operation-middleware.md`). Docs:
https://tyk.io/docs/api-management/traffic-transformation

| The user asks for | Built-in feature |
| --- | --- |
| Add, remove or rename request or response headers, static or from the request context | Request and response header transforms (`$tyk_context.*`, `$tyk_meta.*` values) |
| Reshape a JSON or XML body | Body transforms (Go templates) |
| Change the path, route by a header or query value | URL rewrite |
| Refuse a body or parameters that break the OpenAPI document | Request validation |
| A canned answer without an upstream | Mock response |
| Allow or block paths and methods | Allow, block and ignore-authentication lists |
| Limits per caller, per API, per operation | Rate limits and quotas (the `tyk-rate-limits-keys-policies` skill) |
| A standard credential (keys, JWT, OAuth, mTLS, HMAC, Basic) | Built-in authentication (the `tyk-authentication` skill) |
| Caching, timeouts, circuit breaker | Operation middleware |

If one of these fits, stop here.

## Then: which plugin kind

| Need | Kind | Why |
| --- | --- | --- |
| Small request or response logic, a lookup with `TykMakeHttpRequest`, a counter in Redis | JavaScript, `driver: "javascript"` | No build step; edit the file and reload. A no-op hook adds about 0.15 to 0.2 ms median |
| An endpoint answered entirely by code (aggregation, a shim in front of another API) | Virtual endpoint (JavaScript) | Runs instead of the upstream for one operation |
| Highest throughput, Go libraries, full access to the request context and API definition | Go plugin | Runs in the Gateway process; no measurable cost for a no-op hook. Must be rebuilt for every Gateway release |
| Logic in a language other than Go or JavaScript, or owned by another team and deployed on its own | gRPC plugin | Your server, any language. About +0.8 ms median per hook with a Python server on the same host; while it is down, requests to its APIs fail |
| Existing Python code | Python plugin | Embedded interpreter; needs a Gateway image with Python added |
| A credential format no built-in method understands | Custom authentication in any of the kinds above | The plugin returns the session (identity, limits, rights) |

Avoid `driver: "otto"` for anything new: it is the older JavaScript engine, ES5 only, without
inline code or `TykStorage*`, and adds about 1.7 to 1.8 ms median per no-op hook. Lua exists as a driver
and is not covered here.

## What each kind needs

All five kinds run on the **open source Gateway** (MPL 2.0, `tykio/tyk-gateway`) with Redis and no
licence; every example in this skill runs there. The `tyk-facts` skill lists custom plugins in Go,
gRPC (any language), Python and JavaScript in the Community Edition
(https://tyk.io/docs/api-management/plugins/overview). gRPC plugins are marked unavailable on Tyk
Cloud; check that edition row with the `tyk-facts` skill before promising one there.

| Kind | Extra component |
| --- | --- |
| JavaScript, virtual endpoints | `enable_jsvm: true` in the Gateway configuration |
| Go | The plugin compiler image whose tag equals the Gateway's version |
| gRPC | Your gRPC server; `coprocess_options.enable_coprocess` and `coprocess_grpc_server` |
| Python | A Gateway image with Python added (the stock image has none); `enable_coprocess` |
| Bundles (any kind) | A web server the Gateways can reach; `enable_bundle_downloader`, `bundle_base_url` |

A Dashboard (licensed) adds a UI and API over the same definitions; the plugin files still live on,
or are downloaded by, each Gateway. Paid deployments run the Enterprise Edition Gateway image
(`tykio/tyk-gateway-ee`); its Go plugins are built with `tykio/tyk-plugin-compiler-ee`
(`go-plugins.md`). Editions, trials and anything about licences: the `tyk-facts` skill.

## One driver per API

`pluginConfig.driver` applies to every hook of the API: Pre, custom authentication, Post-auth,
Post and Response are all JavaScript, or all Go, or all gRPC, or all Python. Docs:
https://tyk.io/docs/api-management/plugins/overview ("not possible to mix the implementation
language"). Two exceptions:

- Virtual endpoints are always JavaScript, whatever the driver. They run on the `javascript`
  engine only when the driver is `javascript`; with any other driver, or none, they run on `otto`
  (ES5, no `TykStorage*`; `javascript.md`).
- An operation's own `postPlugins` list is Go. On an API with `driver: "javascript"`, a Go
  `postPlugins` entry on operation `echo` runs on that operation only, after the JavaScript pre hook,
  and not on other paths.
