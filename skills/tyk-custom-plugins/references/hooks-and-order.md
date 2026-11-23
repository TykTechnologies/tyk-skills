# Hooks, where they go, and the order they run in

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source)

Docs: https://tyk.io/docs/api-management/plugins/plugin-types,
https://tyk.io/docs/api-management/plugins/overview,
https://tyk.io/docs/api-management/traffic-transformation (request middleware chain)

## Where each hook goes in a Tyk OAS definition

```json
"x-tyk-api-gateway": {
  "server": {"authentication": {"enabled": true,
    "custom": {"enabled": true, "config": {"enabled": true, "functionName": "PartnerAuth", "path": "<file>"}}}},
  "middleware": {
    "global": {
      "pluginConfig": {"driver": "javascript",
                       "data": {"enabled": true, "value": {"region": "eu"}},
                       "bundle": {"enabled": false, "path": "plugins-v1.zip"}},
      "prePlugins":                [{"enabled": true, "functionName": "...", "path": "<file>"}],
      "postAuthenticationPlugins": [{"enabled": true, "functionName": "...", "path": "<file>", "requireSession": true}],
      "postPlugins":               [{"enabled": true, "functionName": "...", "path": "<file>", "requireSession": true}],
      "responsePlugins":           [{"enabled": true, "functionName": "...", "path": "<file>", "requireSession": true}]
    },
    "operations": {
      "<operationId>": {
        "virtualEndpoint": {"enabled": true, "functionName": "...", "path": "<file.js>"},
        "postPlugins": [{"enabled": true, "functionName": "...", "path": "<file.so>"}]
      }
    }
  }
}
```

| Field | Notes (5.16.0) |
| --- | --- |
| `pluginConfig.driver` | `javascript`, `otto`, `goplugin`, `grpc`, `python` (also `lua`). Required for plugins: without it every request gets 500 |
| `pluginConfig.data.value` | Any JSON object. JavaScript reads it as `config.config_data`, Go with `ctx.GetOASConfigData(r)`, gRPC and Python as the JSON string `spec["config_data"]` |
| `pluginConfig.bundle` | Load the hooks from a bundle instead of `path` (`bundles.md`) |
| `functionName` | JavaScript: the middleware object's variable name. Go: the exported function. gRPC: the `hook_name` your server receives. Python: the `@Hook` function |
| `path` | Gateway filesystem path of the `.js` or `.so` file. Not used by gRPC; inside a bundle, a file name in the bundle |
| `code` | Base64 JavaScript instead of `path`; `javascript` driver only |
| `requireSession` | JavaScript post-auth, post and response hooks get the caller's session only with `true` |
| `server.authentication.custom` | Custom authentication: the plugin decides who the caller is (`custom-authentication.md`) |

The schema also has singular `prePlugin`, `postPlugin` and similar objects; use the plural lists
shown above, whose behaviour this file describes.

## Order

On an API with an auth token, a 2-per-minute key and a built-in request header transform that
adds `X-Builtin: yes`, the four JavaScript hook stages behave as follows:

| Stage | Runs | Sees the session | Sees the built-in header transform |
| --- | --- | --- | --- |
| Pre (`prePlugins`) | Before authentication, rate limits and quotas | No (`alias=none`) | No |
| Custom authentication | In place of built-in authentication | No: it creates the session | No |
| Post-auth (`postAuthenticationPlugins`) | After authentication, before the rate limit, quota and access checks | Yes (`alias=partner-a`) | No |
| Post (`postPlugins`) | After rate limits, quotas, validation and all request transforms, just before the upstream call | Yes | Yes |
| Response (`responsePlugins`) | After the upstream answers, and on a mock response | Yes (`alias=partner-a`) | n/a |

What runs for each outcome:

| Request | Pre | Post-auth | Post | Response | Client gets |
| --- | --- | --- | --- | --- | --- |
| Valid key, within limit | 1 | 1 | 1 | 1 | 200 |
| Valid key, third call in a minute | 1 | 1 | 0 | 0 | 429 `Rate Limit Exceeded` |
| Unknown key | 1 | 0 | 0 | 0 | 403 `Access to this API has been disallowed` |

So:

- A pre hook runs for every request, including ones the Gateway then refuses. Do not count, bill
  or log "accepted" requests there.
- Post-auth runs before the caller's rate limit: a request that is about to get 429 still reaches it.
- A response hook does not run for errors the Gateway generates itself (401, 403, 429); it runs
  when the upstream answers and on a mock response (below).
- Request transforms configured in the definition have already happened when Post runs; Post is
  the place to see the final request.
- Post is not the very last step. Mock responses, cache hits, virtual endpoints, request signing
  and an operation's own Go `postPlugins` come before the global Post hooks (see
  https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/api_loader.go). So a global Post hook does not run on a mock, a cached answer or a virtual
  endpoint, a header it adds is not covered by request signing, and an operation's Go post hook
  runs before it. On a mocked operation the Pre, Post-auth and Response hooks run and the
  Post hook does not; the client gets the mock body with `X-Stage-Response` set, so a response hook
  does run on a mock response.

## Session and request context

| Kind | Session | API definition and config data | Change the request |
| --- | --- | --- | --- |
| JavaScript | `session` argument (`requireSession: true` after auth); write only `meta_data` | `config.config_data`, `config.APIID` | `request.SetHeaders`, `DeleteHeaders`, `AddParams`, `DeleteParams`, `Body`, `URL` |
| Go | `ctx.GetSession(r)` | `ctx.GetOASDefinition(r)`, `ctx.GetOASConfigData(r)` | Change `r` directly (`r.Header.Set`) |
| gRPC | `object.session` | `object.spec` (`APIID`, `OrgID`, `config_data`) | `object.request.set_headers`, `delete_headers` |
| Python | `session` argument | `spec` dict | `request.add_header`, `delete_header` |

On 5.16.0 a Go post hook sees the caller's alias (`alias=partner-a`), config data such as region
`eu` from `GetOASConfigData` and the API id (`go-hooks`) from `GetOASDefinition`. On a Tyk OAS API
`ctx.GetDefinition(r)` (the classic definition) returns nil, and using it panics the plugin. A
JavaScript post hook without `requireSession` gets an empty session (`alias` undefined); on a
keyless API with `requireSession: true` it gets `null`, so guard `session` before reading `session.meta_data`.
Go docs for both calls: https://tyk.io/docs/api-management/plugins/golang
