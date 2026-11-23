# Per-operation middleware

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), exact path matching and strict routes on

The responses quoted below are what Gateway 5.16.0 returns for each block. Field names are from
the public 5.16.0 schema.

## How middleware is bound to an operation

Middleware for one operation lives under
`x-tyk-api-gateway.middleware.operations.<operationId>`. The key is the `operationId` of an
operation in `paths`, nothing else: not the path, not `GET /orders`.

```json
{
  "paths": {
    "/orders": {"get": {"operationId": "listOrders", "responses": {"200": {"description": "OK"}}}}
  },
  "x-tyk-api-gateway": {
    "middleware": {
      "global": {"contextVariables": {"enabled": true}},
      "operations": {
        "listOrders": {"allow": {"enabled": true}}
      }
    }
  }
}
```

- A key that matches no `operationId` is kept and never runs.
- Two operations cannot share an `operationId`; the definition is rejected.
- `middleware.global` holds settings that apply to every request of the API.
- Which requests count as "this operation" depends on the Gateway's matching mode: read
  `listen-paths-and-matching.md`.

## Access: allow, block, ignore authentication, internal

| Block | Behaviour |
| --- | --- |
| `"allow": {"enabled": true}` | As soon as one operation has it, every request that is not an allowed operation returns `403 {"error": "Requested endpoint is forbidden"}`: other paths, other methods on an allowed path, and declared operations without `allow` |
| `"block": {"enabled": true}` | That operation returns the same 403; everything else passes |
| `"ignoreAuthentication": {"enabled": true}` | That operation is served without credentials on an API that requires them |
| `"internal": {"enabled": true}` | A direct call returns the same 403; another operation reaches it with a URL rewrite to `tyk://self/<path>` |

`allow` and `block` take `"ignoreCase": true`. `"allow": {"enabled": false}` alone does not
create an allow list. Docs: https://tyk.io/docs/api-management/traffic-transformation/allow-list,
https://tyk.io/docs/api-management/traffic-transformation/block-list,
https://tyk.io/docs/api-management/traffic-transformation/ignore-authentication

## Request validation

```json
"createOrder": {"validateRequest": {"enabled": true, "errorResponseCode": 422}}
```

Checks each request against the OpenAPI description of that operation: its parameters, the
request body and the body's content type. `errorResponseCode` defaults to 422.

| Request | Response |
| --- | --- |
| Body `{"qty": 0}` against `qty: {minimum: 1}` | `422 {"error": "request validation error: request body has an error: doesn't match schema #/components/schemas/Order: Error at \"/qty\": number must be at least 1"}` |
| Body `{}` with two required properties | `... Error at \"/a\": property \"a\" is missing` (only the first failure is reported) |
| `Content-Type: text/plain` where the operation declares `application/json` | `422 ... header Content-Type has unexpected value \"text/plain\"` |
| `/orders/abc` where `id` is an integer | `422 {"error": "request validation error: parameter \"id\" in path has an error: value abc: an invalid integer: invalid syntax"}` |

With `"errorResponseCode": 400` the status is 400 and the body the same. Validation works the
same on an OpenAPI 3.1 document (`"type": ["string", "null"]`). Docs:
https://tyk.io/docs/api-management/traffic-transformation/request-validation

## Mock responses

```json
"mock1": {"mockResponse": {"enabled": true, "code": 202, "body": "{\"mock\": true}",
          "headers": [{"name": "Content-Type", "value": "application/json"}]}},
"mock2": {"mockResponse": {"enabled": true,
          "fromOASExamples": {"enabled": true, "code": 200, "contentType": "application/json", "exampleName": "second"}}}
```

- `mock1` answers `202 {"mock": true}` with the header, without calling the upstream.
- `mock2` returns the response example named `second` from the operation's `200` response.
- The caller can pick another documented example: header `X-Tyk-Accept-Example-Code: 404`
  returns the 404 example; `X-Tyk-Accept-Example-Name: first` returns the example `first`. A
  code with no example answers
  `500 {"error": "failed to mock response: mock: there is no example response for the code: 500"}`.
- With `fromOASExamples` and no example, the body is built from the response schema:
  `{"id":0,"name":"book"}` for an integer `id` and a string `name` whose schema has
  `example: book`.

Docs: https://tyk.io/docs/api-management/traffic-transformation/mock-response

## Headers

```json
"getOrder": {
  "transformRequestHeaders": {"enabled": true, "remove": ["X-Remove-Me"],
     "add": [{"name": "X-Req-Id", "value": "$tyk_context.request_id"}]},
  "transformResponseHeaders": {"enabled": true, "remove": ["X-Upstream-Name"],
     "add": [{"name": "X-Resp", "value": "r"}]}
}
```

The same two blocks under `middleware.global` apply to every operation. A `$tyk_context.`
value is an **empty string** unless `middleware.global.contextVariables.enabled` is `true`
(import sets it; a hand-written definition must). With it on, `$tyk_context.request_id` gives a
UUID and `$tyk_context.path` gives `/mwf/ctx`. Docs:
https://tyk.io/docs/api-management/traffic-transformation/request-headers,
https://tyk.io/docs/api-management/traffic-transformation/request-context-variables

## Bodies

```json
"createOrder": {"transformRequestBody": {"enabled": true, "format": "json",
                 "body": "<base64 of the Go template>"}},
"listOrders":  {"transformResponseBody": {"enabled": true, "format": "json",
                 "body": "<base64 of the Go template>"}}
```

- `body` is the template, base64-encoded. `format` is the format of the incoming body, `json`
  or `xml`.
- Template `{"wrapped": {"name": "{{.name}}", "n": {{.n}} }}` turns `{"name":"book","n":2}`
  into `{"wrapped": {"name": "book", "n": 2 }}` for the upstream.
- With `"format": "xml"` the template `{"item": "{{.order.item}}"}` turns
  `<order><item>book</item></order>` into `{"item": "book"}`.
- **A response body transform is not applied when `upstream.url` has a path.** With
  `http://orders.example.com:8080/base` the body comes back untouched and the Gateway logs
  `CheckSpecMatchesStatus not found. Transformation stopped.` Keep the upstream URL to scheme,
  host and port and put the path in a URL rewrite.

Docs: https://tyk.io/docs/api-management/traffic-transformation/request-body,
https://tyk.io/docs/api-management/traffic-transformation/response-body

## Method and URL

```json
"legacyGet": {"transformRequestMethod": {"enabled": true, "toMethod": "POST"}},
"getOrder":  {"urlRewrite": {"enabled": true, "pattern": "/orders/(\\d+)", "rewriteTo": "/items/$1/detail"}}
```

- `transformRequestMethod`: a `GET` reaches the upstream as `POST`.
- `urlRewrite.pattern` is a regular expression tested against the **whole request path, listen
  path included, followed by the query string**. On listen path `/shop/`, `^/shop/orders/(\d+)`
  matches `/shop/orders/42`; `^/orders/(\d+)$` does not, and the request goes upstream
  unrewritten. Leave the pattern unanchored or start it with the listen path.
- An end anchor fails when the request has a query: `^/shop/orders/(\d+)$` rewrites
  `/shop/orders/42` but not `/shop/orders/42?x=1`.
- A group that stops before the query drops the query: `/orders/(\d+)` rewrites
  `/shop/orders/42?x=1` to `/items/42/detail`. A group that runs to the end keeps it:
  `/q/(.*)` to `/target/$1` sends `/target/abc?x=1&y=2`.
- `rewriteTo` is a path on the upstream, or an absolute URL to send the request to another
  host.
- `triggers` choose a different target when rules match:

```json
"urlRewrite": {"enabled": true, "pattern": "/rwt", "rewriteTo": "/default", "triggers": [
  {"condition": "all", "rewriteTo": "/beta?from=trigger",
   "rules": [{"in": "header", "name": "X-Channel", "pattern": "beta"}]},
  {"condition": "any", "rewriteTo": "http://other.example.com:8080/other-host",
   "rules": [{"in": "query", "name": "host", "pattern": "b"}]}]}
```

  No header: `/default`. `X-Channel: beta`: `/beta?from=trigger`. `?host=b`: the other host.
  `in` is one of `query`, `path`, `header`, `sessionMetadata`, `requestContext`, `requestBody`.

Docs: https://tyk.io/docs/transform-traffic/url-rewriting,
https://tyk.io/docs/api-management/traffic-transformation/request-method

## Cache

```json
"middleware": {
  "global": {"cache": {"enabled": true, "timeout": 5, "cacheAllSafeRequests": false}},
  "operations": {"listOrders": {"cache": {"enabled": true, "timeout": 5, "cacheResponseCodes": [200]}}}
}
```

An operation cache does nothing unless `middleware.global.cache.enabled` is `true`: without the
global block three calls reach the upstream three times. With it, the second and third calls
are served from cache and carry `X-Tyk-Cached-Response: 1`. `cacheAllSafeRequests: false`
keeps other operations uncached. Docs: https://tyk.io/docs/api-management/response-caching

## Timeouts and circuit breaker

```json
"slowReport": {"enforceTimeout": {"enabled": true, "value": 3}},
"flaky": {"circuitBreaker": {"enabled": true, "threshold": 0.5, "sampleSize": 4,
          "coolDownPeriod": 3, "halfOpenStateEnabled": false}}
```

- `enforceTimeout.value` is in seconds. A slower upstream gets
  `504 {"error": "Upstream service reached hard timeout."}` at that second.
- For the whole API use `upstream.enforceTimeout: {"enabled": true, "duration": "1s"}`. An
  operation's own `enforceTimeout` wins over it: API 1 s and operation 3 s lets a 2-second call
  succeed.
- The value may exceed 30 seconds: with `"value": 40`, a call the upstream takes 35 seconds to
  answer returns 200.
- `circuitBreaker` needs all five fields. With the values above, four `500` responses open
  it; the next calls get `503 {"error": "Service temporarily unavailable."}` without reaching
  the upstream, and it closes again after the 3-second cool-down.

Docs: https://tyk.io/docs/planning-for-production/ensure-high-availability/enforced-timeouts,
https://tyk.io/docs/planning-for-production/ensure-high-availability/circuit-breakers

## Limits

```json
"search": {"rateLimit": {"enabled": true, "rate": 3, "per": "10s"}},
"upload": {"requestSizeLimit": {"enabled": true, "value": 20}}
```

- An operation `rateLimit` counts all callers together. `per` is a duration (`10s`, `1m`). The
  fourth call inside ten seconds gets `429 {"error": "API Rate Limit Exceeded"}`.
- The same shape under `upstream.rateLimit` limits the whole API, with the same 429 body.
- `requestSizeLimit.value` is in bytes: a 50-byte body against a limit of 20 gets
  `400 {"error": "Request is too large"}`.

Limits per consumer, quotas and policies belong to the `tyk-rate-limits-keys-policies` skill.
Docs: https://tyk.io/docs/api-management/rate-limit,
https://tyk.io/docs/api-management/traffic-transformation/request-size-limits

## Virtual endpoint

```json
"hello": {"virtualEndpoint": {"enabled": true, "functionName": "hello",
          "body": "<base64 of the JavaScript function>"}}
```

Answered from the JavaScript function without an upstream call, on a Gateway with
`enable_jsvm: true`. Docs:
https://tyk.io/docs/api-management/traffic-transformation/virtual-endpoints

## Other operation fields

The schema also defines `trackEndpoint`, `doNotTrackEndpoint`, `postPlugins`, `scopeCheck` and
`exchange` for an operation, and under `middleware.global`: `cors`, `pluginConfig`,
`prePlugins`, `postAuthenticationPlugins`, `postPlugins`, `responsePlugins`, `trafficLogs`,
`requestSizeLimit`, `ignoreCase`, `skipRateLimit`, `skipQuota`. Their behaviour is described in
the Tyk OAS reference: https://tyk.io/docs/api-management/gateway-config-tyk-oas
