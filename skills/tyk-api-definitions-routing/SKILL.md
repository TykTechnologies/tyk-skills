---
name: tyk-api-definitions-routing
description: Write, import and change Tyk OAS API definitions that load and behave as intended on Tyk Gateway 5.16 (extension fields, listen paths, path matching, per-operation middleware, upstream, versioning) through the Gateway API, files or the Dashboard API. Use when asked to create, fix or review a Tyk API definition, import an OpenAPI or Swagger file into Tyk, or explain a 404, a 403 from an allow or block list, or an ignored setting. Not for keys, policies and per-consumer limits, which belong to the tyk-rate-limits-keys-policies skill.
---

# Tyk API Definitions and Routing

Agents asked for a Tyk API reach for the classic format, guess field names in the Tyk
extension, and assume path matching works like their last gateway. Tyk Gateway 5.16.0 accepts
most of those guesses without an error and then ignores them. This skill gives the shapes,
commands and responses of Gateway and Dashboard 5.16.0, and a script
that catches the mistakes before the definition is loaded. Use the Tyk OAS format for every REST
API; the classic format is needed only for GraphQL and TCP (`references/limits-and-classic.md`).

## What you need

- **Open source Gateway** (plus Redis): write through the Gateway API, which stores two files
  per API. No licence.
- **Dashboard-managed Gateway**: write through the Dashboard API. The Dashboard needs a licence;
  the `tyk-facts` skill has the detail and the trial path. Upstream authentication also needs
  the Enterprise Edition Gateway.
- No stack yet: the `tyk-demo-builder` skill builds a licensed one, the `tyk-deployment`
  skill installs the open source Gateway.

## Clarify first

Ask only what the request leaves open. Defaults in brackets.

1. **Where do definitions go: a Dashboard, or the open source Gateway?** [Find out: step 1.]
2. **Is there an OpenAPI document already?** [Yes: import it. No: write the definition by hand.]
3. **API id, listen path and upstream URL.** [id from the API name in lower-case with hyphens;
   listen path `/<id>/` with `strip: true`; the upstream URL must come from the user.]
4. **Should paths that are not in the document be refused?** [Yes: allow list on every
   operation and request validation where the document has parameters or a body.]
5. **Which path matching mode does the Gateway run?** [Check, never assume: step 2.]
6. **Authentication.** [None unless asked; where it goes: `references/versioning-and-authentication.md`.]
7. **Gateway version.** [5.16.0, the version this skill describes; else resolve it with `tyk-facts`, re-run step 6.]

## Workflow

Set these once; every command below uses them. `<skill-dir>` is this skill's folder.

```bash
GW=http://localhost:8080                 # Gateway
SECRET=<gateway secret>                  # "secret" in tyk.conf, or TYK_GW_SECRET
DASH=http://localhost:3000               # Dashboard, when there is one
DASHBOARD_API_KEY=<Dashboard user API key>
UPSTREAM=http://orders.example.com:8080  # scheme, host and port; no path
```

1. **Set the surface.** An empty create call tells the two apart without changing anything:
   `curl -s -X POST "$GW/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" -d '{}'`
   answers `the payload should contain x-tyk-api-gateway` on an open source Gateway and
   `Due to enabled use_db_app_configs, please use the Dashboard API` on a Dashboard-managed one.

2. **Find the Gateway's matching mode.** Read `http_server_options` in its `tyk.conf` and the
   `TYK_GW_HTTPSERVEROPTIONS_*` variables for `enable_strict_routes`,
   `enable_path_prefix_matching` and `enable_path_suffix_matching`. The Docker images and the
   Helm chart set all three to `true` (exact matching); a hand-made `tyk.conf` without them runs
   with all three `false`, where `/users` also matches `/usersX` and `/v1/users`. If you cannot
   read the configuration, run the probe in `references/listen-paths-and-matching.md`.

3. **Write the definition**, or skip to step 5 to import a plain OpenAPI document. Set
   `info.orgId` (here `default`): on an open source Gateway keys and policies must carry the
   same value, or key creation fails. The Dashboard replaces it with the user's organisation.

```bash
cat > orders.oas.json <<EOF
{
  "openapi": "3.0.3",
  "info": {"title": "Orders", "version": "1.0.0"},
  "paths": {
    "/orders": {"get": {"operationId": "listOrders", "responses": {"200": {"description": "OK"}}}},
    "/orders/{id}": {"get": {"operationId": "getOrder",
      "parameters": [{"name": "id", "in": "path", "required": true, "schema": {"type": "integer"}}],
      "responses": {"200": {"description": "OK"}}}}
  },
  "x-tyk-api-gateway": {
    "info": {"id": "orders", "orgId": "default", "name": "Orders", "state": {"active": true}},
    "upstream": {"url": "$UPSTREAM"},
    "server": {"listenPath": {"value": "/orders-api/", "strip": true}},
    "middleware": {"operations": {
      "listOrders": {"allow": {"enabled": true}},
      "getOrder": {"allow": {"enabled": true}, "validateRequest": {"enabled": true}}
    }}
  }
}
EOF
```

Rules that hold everywhere: operation paths are relative to the listen path; middleware is
keyed by `operationId`; set `strip: true` or the upstream receives the listen path too.

4. **Check it before loading.**

   ```bash
   python3 <skill-dir>/scripts/check_tyk_oas.py orders.oas.json
   # 0 error(s), 0 warning(s), 0 note(s)
   ```

   Fix every `ERROR`. Read every `WARN`: each is a definition that loads and does something
   other than what it says.

5. **Load it.**

   ```bash
   # open source Gateway: store, then reload
   curl -s -X POST "$GW/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" \
     -H 'Content-Type: application/json' --data @orders.oas.json
   # {"key":"orders","status":"ok","action":"added"}
   curl -s "$GW/tyk/reload/group" -H "x-tyk-authorization: $SECRET"
   # {"status":"ok","message":""}

   # Dashboard: no reload call; the Gateway serves it within about a second
   curl -s -X POST "$DASH/api/apis/oas" -H "Authorization: $DASHBOARD_API_KEY" \
     -H 'Content-Type: application/json' --data @orders.oas.json
   # {"Status":"OK","Message":"API created","Meta":"<database id>","ID":"orders"}
   ```

   To import a plain OpenAPI document instead (no Tyk extension in the file):

   ```bash
   python3 <skill-dir>/scripts/check_tyk_oas.py --plain openapi.json
   curl -s -X POST "$GW/tyk/apis/oas/import?apiID=orders&listenPath=/orders-api/&upstreamURL=$UPSTREAM&allowList=true&validateRequest=true" \
     -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @openapi.json
   ```

   Reload as above. The Dashboard takes the same query parameters on
   `POST $DASH/api/apis/oas/import`.

6. **Verify: call it, then read the Gateway's view back.**

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' "$GW/orders-api/orders"   # 200
   curl -s "$GW/orders-api/orders/abc"   # 422 {"error": "request validation error: parameter \"id\" in path has an error: value abc: an invalid integer: invalid syntax"}
   curl -s "$GW/orders-api/nope"         # 403 {"error": "Requested endpoint is forbidden"}
   curl -s "$GW/tyk/apis/oas/orders" -H "x-tyk-authorization: $SECRET"   # the definition as the Gateway holds it
   ```

   Error bodies here and in the references are shown on one line; the Gateway sends indented
   JSON, so match on the `error` value. Compare the readback with what you sent: a field that
   is missing from it was not a field.

7. **Change it later.** New version of the OpenAPI document, same Tyk settings:

   ```bash
   curl -s -X PATCH "$GW/tyk/apis/oas/orders?allowList=true&validateRequest=true" \
     -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @openapi-v1.1.json
   curl -s "$GW/tyk/reload/group" -H "x-tyk-authorization: $SECRET"
   ```

   Repeat the query parameters used at import, or new operations get no middleware. For a Tyk
   setting, `GET` the definition, edit, `PUT` it back; on a Dashboard a merge patch changes
   single fields. All forms are in `references/create-import-update.md`.

## References

| File | Read it for |
| --- | --- |
| `references/definition-shape.md` | The extension's sections, the smallest definition, every rejection message, what is accepted and then ignored |
| `references/create-import-update.md` | Gateway API, files on disk, Dashboard API; import parameters; PATCH; merge patch; reload |
| `references/listen-paths-and-matching.md` | Which API a request reaches, strict routes, custom domains, the four matching modes with a results table, path parameters and wildcards |
| `references/operation-middleware.md` | Allow, block, ignore authentication, validation, mocks, header and body transforms, URL rewrite, method, cache, timeouts, circuit breaker, limits: block and real response for each |
| `references/upstream.md` | URL, load balancing, service discovery, upstream authentication, mutual TLS, certificate pinning |
| `references/versioning-and-authentication.md` | Base API and versions; where auth token, JWT and combined methods go |
| `references/limits-and-classic.md` | OpenAPI versions, references that fail, what needs the classic format |

## Pitfalls

- An invented field in the extension is accepted and dropped. So is middleware under an
  `operationId` that does not exist.
- OpenAPI `security` alone protects nothing; without `server.authentication` the API is open.
- On an open source Gateway nothing changes until a reload, and editing `<id>-oas.json` by hand
  changes nothing at all: the Gateway API must write both files.
- An allow list on some operations blocks every other path and method with 403.
- In wildcard or prefix mode an allow-listed `/orders` also admits every path under a listen
  path that starts the same way (`/orders2/`). Exact matching does not.
- `{id:[0-9]+}` in an operation path matches any value; only a listen path honours it.
- An operation cache needs `middleware.global.cache.enabled`; `$tyk_context.` values need
  `middleware.global.contextVariables.enabled`.
- A URL rewrite pattern sees the listen path and the query string. A response body transform is
  skipped when the upstream URL has a path. A certificate pin on `host:port` is not applied.
- A PATCH without the import query parameters leaves new operations blocked by the allow list.
- Two APIs on one listen path both load; the second moves to `<listen path>-<api id>`.

## Limitations

- GraphQL and TCP proxying cannot be expressed in Tyk OAS 5.16.0; they need a classic definition.
- Swagger 2.0 is not accepted. OpenAPI 3.0.x and 3.1.x are; 3.2 is not. Convert a Swagger 2.0
  file to OpenAPI 3.0 outside Tyk first, then run `check_tyk_oas.py --plain` on the result.
- References to other files or URLs, references inside `webhooks`, and `components.pathItems`
  fail to load. Bundle the document into one file first.
- A `mutualTLS` security scheme is rejected; client certificates are set in the extension.
- Upstream basic auth and OAuth do nothing on the open source Gateway.
- The Gateway API has no merge patch, and the Gateway does not watch its files for changes.
- `scripts/check_tyk_oas.py` reads JSON only, knows the 5.16.0 schema and skips MCP settings.

## Verification checklist

Before reporting success:

- [ ] `check_tyk_oas.py` reports no errors, and every warning is understood.
- [ ] The create, import or update call answered 200 and, on an open source Gateway, a reload
      followed.
- [ ] One declared operation answers as expected through the Gateway.
- [ ] One undeclared path and one wrong method answer as intended (403 with an allow list).
- [ ] Each middleware you added shows its effect in a real response, not only in the definition.
- [ ] The Gateway's own readback (`GET /tyk/apis/oas/{id}`) holds every field you set and the
      listen path you asked for.
- [ ] The Gateway log has no `Listen path collision`, `Spec not valid, skipped!` or
      `supported only in Tyk Enterprise Edition` line for this API.

## Hand-offs

- Keys, policies, quotas, per-consumer limits: the `tyk-rate-limits-keys-policies` skill.
- Definitions through Git, Tyk Sync or Tyk Operator: the `tyk-gitops-sync-operator` skill.
- MCP proxies: the `tyk-mcp-gateway` skill.
- Licensing, editions and versions: the `tyk-facts` skill. A stack to try this on: the
  `tyk-demo-builder` skill, or the `tyk-deployment` skill for the open source Gateway. Moving an
  API from another gateway: the `tyk-migration-planner` skill.
