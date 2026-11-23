# The shape of a Tyk OAS definition

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`) and Tyk Dashboard 5.16.0

Field names come from the Gateway's public 5.16.0 schema. See
https://tyk.io/docs/api-management/gateway-config-tyk-oas

## Two parts in one document

A Tyk OAS definition is one JSON document:

1. An OpenAPI 3.0 or 3.1 description: `openapi`, `info`, `paths`, and optionally `components`,
   `security`, `servers`.
2. The Tyk extension, the top-level key `x-tyk-api-gateway`, which tells the Gateway how to
   serve it.

The extension has five sections:

| Section | Required | Holds |
| --- | --- | --- |
| `info` | yes | `id`, `name` (required), `state.active` (required), `state.internal`, `orgId`, `dbId`, `expiration`, `versioning` |
| `upstream` | yes | `url` or `loadBalancing`, plus `serviceDiscovery`, `authentication`, `mutualTLS`, `certificatePinning`, `tlsTransport`, `preserveHostHeader`, `enforceTimeout`, `rateLimit`, `uptimeTests`, `proxy` |
| `server` | yes | `listenPath.value` (required), `listenPath.strip`, `authentication`, `customDomain`, `clientCertificates`, `gatewayTags`, `detailedActivityLogs`, `ipAccessControl`, `eventHandlers`, `port`, `protocol` |
| `middleware` | no | `global` (applies to every request) and `operations` (keyed by `operationId`) |
| `errorOverrides` | no | custom error responses |

The classic format's names (`proxy.listen_path`, `target_url`, `use_keyless`, `version_data`,
`extended_paths`) do not exist in the extension.

## The smallest definition that loads

```json
{
  "openapi": "3.0.3",
  "info": {"title": "Orders", "version": "1.0.0"},
  "paths": {},
  "x-tyk-api-gateway": {
    "info": {"name": "orders", "state": {"active": true}},
    "upstream": {"url": "http://orders.example.com:8080"},
    "server": {"listenPath": {"value": "/orders/"}}
  }
}
```

`POST /tyk/apis/oas` with that body answers `200 {"key":"<generated id>","status":"ok","action":"added"}`.
Set `info.id` yourself to get a stable id; allowed characters are `a-z A-Z 0-9 . _ - ~`.
`paths` may be `{}`: the API then proxies every path under the listen path.

`listenPath.strip` is `false` when you leave it out, and the upstream then receives the listen
path too: `GET /orders/a/b` reaches the upstream as `/orders/a/b`. With `"strip": true` it
receives `/a/b`. Import sets `strip` to `true` for you; a hand-written definition must say so.

Read the definition back and the Gateway has added `components: {}` and a `servers` list with
the Gateway's own URL for the API. The Dashboard also adds `info.dbId` and `info.orgId`.

### The organisation id

On an open source Gateway, set `info.orgId` (the examples use `default`). The Gateway does not
fill it in: a definition sent without it reads back with no `orgId`. Keys and policies for the
API must then carry the same `org_id`; the `tyk-rate-limits-keys-policies` skill has the
outcome of each combination on Gateway 5.16.0. On Dashboard 5.16.0 the value you send is
ignored: a definition posted with `"orgId": "default"` is stored with the user's organisation id,
and served.

## What the Gateway rejects

All answers are `400` with `{"status":"error","message":"..."}` from the Gateway API, or
`{"Status":"Error","Message":"...","Meta":null}` from the Dashboard API. The messages are the
same on both unless noted.

| Mistake | Message |
| --- | --- |
| No `x-tyk-api-gateway` on create or PUT | `the payload should contain x-tyk-api-gateway` |
| Extension present on import | `the import payload should not contain x-tyk-api-gateway` |
| No `upstream` | `x-tyk-api-gateway: upstream is required` |
| `upstream: {}` | `x-tyk-api-gateway.upstream: Must validate at least one schema (anyOf)` and `x-tyk-api-gateway.upstream: url is required` |
| No `server` | `x-tyk-api-gateway: server is required` |
| No `listenPath.value` | `x-tyk-api-gateway.server.listenPath: value is required` |
| No `info.name` | `x-tyk-api-gateway.info: name is required` |
| No `info.state` | `x-tyk-api-gateway.info: state is required` |
| Wrong type, for example `"strip": "yes"` | `x-tyk-api-gateway.server.listenPath.strip: Invalid type. Expected: boolean, given: string` |
| `server.protocol: "tcp"` | `x-tyk-api-gateway.server.protocol: x-tyk-api-gateway.server.protocol must be one of the following: "http", "https", "h2c"` |
| No `paths` | `paths: Invalid type. Expected: object, given: null` |
| No `info.version` | `invalid info: value of version must be a non-empty string` |
| Swagger 2.0 | Gateway: `(root): Additional property swagger is not allowed` and `openapi: Does not match pattern '^3\.0\.\d(-.+)?$'`. Dashboard: `value of openapi must be a non-empty string` |
| `openapi: "3.2.0"` or `"2.0"` | `Schema not found for version "3.2.0"` |
| `info.id` with `/` or a space | `Invalid API ID: Allowed characters: a-z, A-Z, 0-9, ., _, -, ~ and must contain at least one letter or digit` |
| Body is not JSON | Gateway: `request malformed`. Dashboard: `couldn't be decoded: ...` |
| PUT where the id in the body differs from the URL | `Request APIID does not match that in Definition! For Update operations these must match.` |
| PUT, PATCH or DELETE of an unknown id | `404` `API not found` |
| OAS endpoint used on a classic API | `the requested API definition is in Tyk classic format, please use old api endpoint` (GET), `the supplied API definition is in OAS format, please use the Tyk classic format for this API` (PUT, PATCH) |
| Classic endpoint used to PUT an OAS API | `this API endpoint only supports Tyk Classic APIs; please use the appropriate Tyk OAS API endpoint` |
| Listen path without a leading slash, Dashboard only | `Listen path must start with a leading slash /` |
| Same `info.id` again, Dashboard only | `409` `Found API with the same id: <id>` |

Document mistakes that are also rejected are listed in `limits-and-classic.md`.

## What is accepted and then goes wrong

These load with `200`. Nothing in the response warns you.

- **A field that is not in the extension.** `server.listenPath.stripPath`, a top-level `proxy`
  block, a `graphql` block: the definition is accepted and the unknown field is dropped. Reading
  the API back shows it gone. This is the usual result of guessing a field name, on the Gateway
  API and the Dashboard API alike. Run `scripts/check_tyk_oas.py` to catch it.
- **Middleware under an `operationId` that no operation has.** Kept in the definition, never runs.
- **An upstream URL without a scheme** (`orders.example.com:8080`, `/just/path`, `""`). Every
  request then fails with `500 {"error": "There was a problem proxying the request"}` and the
  Gateway logs `unsupported protocol scheme`.
- **A listen path with a space.** The Gateway skips the API at load and logs
  `Listen path contains spaces, is invalid` and `Spec not valid, skipped!`. Calls return 404.
- **A listen path another API already uses.** Both are accepted. At load the Gateway keeps the
  first and moves the other to `<listen path>-<api id>`, logging
  `Listen path collision, changed to /orders/-orders-2`. The Dashboard's readback still shows the
  path you asked for.
- **`POST` with an id that exists, on the Gateway API.** It answers `200 ... "action":"added"`
  and overwrites the stored definition. The Dashboard refuses with `409`.
- **`info.state.active: false`.** A Dashboard-managed Gateway does not load the API (404). A
  Gateway that reads its definitions from files still serves it.
- **OpenAPI `security` without `server.authentication` in the extension.** The API is open. See
  `versioning-and-authentication.md`.

## Check before loading

```bash
python3 <skill-dir>/scripts/check_tyk_oas.py orders.oas.json
```

It reports unknown and missing fields (from the 5.16.0 schema), middleware bound to an unknown
`operationId`, and the accepted-then-wrong cases in this skill. `--plain` checks a plain OpenAPI
document that you mean to import. Exit status `1` means at least one error. Rebuild its field
map for another Gateway release with `scripts/build_extension_fields.py` and the schema file
from that release tag.
