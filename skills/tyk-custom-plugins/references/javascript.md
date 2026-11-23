# JavaScript plugins and virtual endpoints

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source)

Docs: https://tyk.io/docs/api-management/plugins/javascript,
https://tyk.io/docs/api-management/traffic-transformation/virtual-endpoints

## Two engines, chosen per API

`enable_jsvm: true` (or `TYK_GW_ENABLEJSVM=true`) must be set on the Gateway. Each API then picks
its engine with `middleware.global.pluginConfig.driver`:

| | `javascript` (use this) | `otto` (older) |
| --- | --- | --- |
| Syntax | Modern (`const`, arrow functions, template strings run) | ES5 only: a file with `const` fails to load (`Unexpected reserved word`) and every request gets 500 |
| Inline `code` (base64) instead of a file | Yes | No: 500 and `ReferenceError: '<name>' is not defined` |
| `TykStorageGet/Set/SetNX/Del/TTL/Incr` | Yes | No: `ReferenceError: 'TykStorageIncr' is not defined` |
| `console`, underscore `_` | No: `ReferenceError: console is not defined` | Yes |
| `log()`, `rawlog()`, `b64enc`, `b64dec`, `TykMakeHttpRequest`, `TykBatchRequest`, `TykGetKeyData`, `TykSetKeyData` | Yes | Yes |
| Cost of a no-op post hook (median, `errors-and-performance.md`) | about +0.15 to +0.2 ms | about +1.7 to +1.8 ms |

No driver at all: every request gets 500 and the log says `Unsupported driver ''`. Use `log()` for
output: it works on both engines.

Virtual endpoints run on the `javascript` engine only when the API's driver is `javascript`.
With any other driver (`goplugin`, `grpc`, `python`, `otto`) or none, they run on `otto`. On
APIs with `driver: "goplugin"` and `driver: "grpc"`, a virtual endpoint reports
`typeof TykStorageIncr` as `undefined` and `typeof console` as `object` (the `otto` engine),
and one written with `const` and an arrow function answers
`500 {"error": "Error during virtual endpoint execution. Contact Administrator for more details."}`;
the same two endpoints on a `javascript` API report `function` and `undefined` and answer
`200`. So write virtual endpoints in ES5 unless the API's driver is `javascript`.

The docs say inline `code` with `otto` "loads nothing ... No error is raised"; on 5.16.0 the
request fails with 500 and the log line above. Treat it as an error.

## A middleware hook

```javascript
var jsDeny = new TykJS.TykMiddleware.NewMiddleware({});
jsDeny.NewProcessRequest(function(request, session, config) {
    if (request.Headers["X-Block"] !== undefined) {
        request.ReturnOverrides.ResponseCode = 403;
        request.ReturnOverrides.ResponseBody = '{"code":"PARTNER_BLOCKED"}';
        request.ReturnOverrides.ResponseHeaders = {"Content-Type": "application/json"};
        request.ReturnOverrides.OverrideError = true;
    }
    request.SetHeaders["X-Region"] = config.config_data.region || "";
    return jsDeny.ReturnData(request, session ? session.meta_data : {});
});
```

- `request.Headers["X"]` is a list: read `request.Headers["X"][0]`.
- `functionName` in the definition is the variable name (`jsDeny`). With `javascript` several hooks
  in one API can reuse a name; with `otto` they share one namespace and the last one wins (docs).
- Changing a `.js` file on disk and reloading the Gateway picked up the new code.

## Stopping a request: `ReturnOverrides`

| Set | Client gets |
| --- | --- |
| `ResponseCode = 403`, `ResponseError = "blocked by plugin"` | `403 {"error": "blocked by plugin"}` |
| `ResponseCode = 403`, `ResponseBody = '{"error":"blocked by plugin"}'` | `403 {"error": "{\"error\":\"blocked by plugin\"}"}`: the body is wrapped in Tyk's error template, headers you set are kept |
| The same plus `OverrideError = true` | `403 {"code":"PARTNER_BLOCKED"}` exactly, with your `Content-Type` |

Use `OverrideError: true` whenever the client must get your own body. The upstream is not called.

## A response hook

Supported on both engines since 5.15.0 (language table on
https://tyk.io/docs/api-management/plugins/overview). On `javascript`:

```javascript
var stamp = new TykJS.TykMiddleware.NewMiddleware({});
stamp.NewProcessResponse(function(response, request, session, config) {
    response.SetHeaders["X-Stage-Response"] = "status=" + response.StatusCode;
    return stamp.ReturnResponseData(response, session.meta_data);
});
```

Note the argument order (`response` first) and `ReturnResponseData`. The client gets
`X-Stage-Response: status=200`.

## Redis storage (`javascript` only)

`TykStorageIncr("order:pre", 600)` increments a counter, for example to count hook runs; the
docs say it returns the new value as a string. Keys land in the Gateway's Redis as
`jsvm-store:<key>` (read with `redis-cli get jsvm-store:order:pre`), shared by every API and every
Gateway on that Redis; atomicity holds per Redis instance (docs).

## Virtual endpoints

The operation is answered by your function; the upstream is not called.

```json
"paths": {"/aggregate": {"get": {"operationId": "aggregate", "responses": {"200": {"description": "OK"}}}}},
"x-tyk-api-gateway": {"middleware": {
  "global": {"pluginConfig": {"driver": "javascript", "data": {"enabled": true, "value": {"region": "eu"}}}},
  "operations": {"aggregate": {"virtualEndpoint": {"enabled": true, "functionName": "veAggregate",
                                                    "path": "/opt/tyk-gateway/plugins/js/virtual.js"}}}}}
```

```javascript
function veAggregate(request, session, config) {
    var raw = TykMakeHttpRequest(JSON.stringify({
        Method: "GET", Domain: "http://upstream:8080", Resource: "/uuid", Headers: {}, Body: ""}));
    var upstream = JSON.parse(raw);
    return TykJsResponse({
        Body: JSON.stringify({upstreamStatus: upstream.Code, upstreamBody: JSON.parse(upstream.Body)}),
        Code: 200, Headers: {"Content-Type": "application/json"}}, session.meta_data);
}
```

`GET /virtual/aggregate` answers `200 {"upstreamStatus":200,"upstreamBody":{"uuid":"8dc79432-..."}}`
on both engines. A virtual endpoint is a plain function, not a `NewMiddleware` object. Its
`request` has `Body`, `Headers`, `Params`, `Scheme`, `URL` and no `Method` (`request.Method` is
undefined). `body` (base64) can replace `path`.

## Failures

| What the plugin does | Client gets | Log |
| --- | --- | --- |
| `throw new Error("boom")` | 500 `Internal Server Error` | `Failed to run JS middleware ... error="Error: boom ... at <file>:4:11"` |
| Endless loop | 500 after 5.0 s | `JS middleware timed out after 5s` (`jsvm_timeout`, seconds) |
| Calls something the engine lacks | 500 | `ReferenceError: ...` |

A failing hook stops the request; it is never skipped. `scripts/check_plugins.py --map` finds the
engine mismatches in the file before you load it.
