# Create, import and change a definition

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`) and Tyk Dashboard 5.16.0

See https://tyk.io/docs/api-management/gateway-config-managing-oas

## Which surface you have

| | Open source Gateway | Dashboard-managed Gateway |
| --- | --- | --- |
| Write through | Gateway API, `http://<gateway>/tyk/...` | Dashboard API, `http://<dashboard>/api/...` |
| Credential | header `x-tyk-authorization: <secret from tyk.conf>` | header `Authorization: <Dashboard user API key>` |
| Stored | two files per API in the Gateway's `app_path` | the Dashboard's database |
| Takes effect | after a reload call | by itself, within about a second |
| Success body | `{"key":"orders","status":"ok","action":"added"}` | `{"Status":"OK","Message":"API created","Meta":"<database id>","ID":"orders"}` |

A Gateway that takes its definitions from a Dashboard refuses writes on its own API:
`POST /tyk/apis/oas` answers `500 {"status":"error","message":"Due to enabled use_db_app_configs, please use the Dashboard API"}`.
Reads still work there, which is how you check what the Gateway loaded
(`GET /tyk/apis/oas/{id}` with the Gateway secret).

The Dashboard needs a licence; the Gateway API and files are the open source Gateway alone. The
`tyk-facts` skill has the licensing detail.

## Endpoints

| Action | Gateway API | Dashboard API |
| --- | --- | --- |
| Create from a full definition | `POST /tyk/apis/oas` | `POST /api/apis/oas` |
| Import a plain OpenAPI document | `POST /tyk/apis/oas/import` | `POST /api/apis/oas/import` |
| Read one, read all | `GET /tyk/apis/oas/{id}`, `GET /tyk/apis/oas` | `GET /api/apis/oas/{id}` |
| Replace | `PUT /tyk/apis/oas/{id}` | `PUT /api/apis/oas/{id}` |
| New OpenAPI document, keep Tyk settings | `PATCH /tyk/apis/oas/{id}` | `PATCH /api/apis/oas/{id}` |
| Change a few fields | not available | `PATCH /api/apis/oas/{id}` with `Content-Type: application/merge-patch+json` |
| Delete | `DELETE /tyk/apis/oas/{id}` | `DELETE /api/apis/oas/{id}` |
| Plain OpenAPI back out | `GET /tyk/apis/oas/{id}/export?mode=public` | `GET /api/apis/oas/{id}/export?mode=public` |
| Reload | `GET /tyk/reload/group` | none needed |

`export?mode=public` returns the document without the Tyk extension. A plain `GET` always
includes it.

## Open source Gateway: Gateway API

```bash
curl -s -X POST "$GW/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" \
  -H 'Content-Type: application/json' --data @orders.oas.json
# 200 {"key":"orders","status":"ok","action":"added"}

curl -s "$GW/orders-api/orders"          # 404 Not Found: not loaded yet
curl -s "$GW/tyk/reload/group" -H "x-tyk-authorization: $SECRET"
# 200 {"status":"ok","message":""}
curl -s "$GW/orders-api/orders"          # 200 from the upstream
```

- Nothing is served until a reload. `GET /tyk/reload/group` signals every Gateway that shares
  the Redis; `GET /tyk/reload` reloads the node you call, and `GET /tyk/reload?block=true`
  returns only when that reload has finished. All three answer `200 {"status":"ok","message":""}`.
- `POST` with an id that already exists overwrites it and still answers `"action":"added"`.
  Use `PUT` to make the intent clear; it answers `"action":"modified"` and `404` for an
  unknown id.
- `DELETE` removes both files at once; the API keeps answering until the next reload.

## Open source Gateway: files on disk

The Gateway API writes two files into `app_path` for each Tyk OAS API:

| File | Content |
| --- | --- |
| `<id>.json` | the classic-format form of the API with `"is_oas": true` |
| `<id>-oas.json` | your Tyk OAS document |

What a Gateway reading that folder did with other layouts:

| On disk | Result after reload |
| --- | --- |
| Only `<id>-oas.json` | Not loaded. Log: `Listen path is empty`, `Spec not valid, skipped!` |
| The Tyk OAS document saved as `<id>.json` | Not loaded as an API you can call (404), no error in the log |
| `<id>-oas.json` plus a hand-written `<id>.json` holding only `api_id` and `is_oas` | Not loaded. Log: `Listen path is empty` |
| The generated pair, then `<id>-oas.json` edited by hand (new upstream, new listen path, new middleware) | No change. The Gateway kept serving the old upstream and path |
| The generated pair, then `<id>.json` edited by hand | Routing changed to match that file |

So the first file drives routing, and editing the Tyk OAS file alone changes nothing. Do not
hand-write or hand-edit the pair. Keep the Tyk OAS document as your source and let a Gateway
generate both files from it:

1. Send the document to `POST /tyk/apis/oas` (or `PUT /tyk/apis/oas/{id}`) on a Gateway whose
   `app_path` is writable. This can be the running Gateway or a short-lived one started only for
   this, pointed at the folder you ship.
2. Commit or ship both files.
3. Reload the Gateway that reads them. It does not watch the folder: an edited file stays
   ignored until the reload call.

If the Gateway cannot write to `app_path`, the create call fails with
`{"status":"error","message":"file object creation failed, write error"}`. The Gateway image runs as user `65532`
(`docker image inspect tykio/tyk-gateway:v5.16.0`), so the folder must be writable by the user
the container runs as.

A short-lived Gateway that only generates the pair. `<network>` is a Docker network on which a
Redis answers as `redis`, `<port>` any free port, `apps/` the folder you ship:

```bash
docker run -d --rm --name tyk-oas-pair --network <network> -p 127.0.0.1:<port>:8080 \
  --user "$(id -u):$(id -g)" -v "$(pwd)/apps:/opt/tyk-gateway/apps" \
  -e TYK_GW_SECRET=pair-secret -e TYK_GW_STORAGE_TYPE=redis -e TYK_GW_STORAGE_ADDRS=redis:6379 \
  -e TYK_GW_USEDBAPPCONFIGS=false -e TYK_GW_APPPATH=/opt/tyk-gateway/apps \
  tykio/tyk-gateway:v5.16.0
curl -s -X POST localhost:<port>/tyk/apis/oas -H 'x-tyk-authorization: pair-secret' \
  -H 'Content-Type: application/json' --data @orders.oas.json
# {"key":"orders","status":"ok","action":"added"}   apps/ now holds orders.json and orders-oas.json
docker stop tyk-oas-pair
```

The tyk-install repository's open source Docker setup ships the same idea as
`scripts/add-oas-api.sh` (https://github.com/TykTechnologies/tyk-install/tree/main/docker/oss).
Delivery through a pipeline belongs to the `tyk-gitops-sync-operator` skill.

## Dashboard API

```bash
curl -s -X POST "$DASH/api/apis/oas" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/json' --data @orders.oas.json
# 200 {"Status":"OK","Message":"API created","Meta":"6afc63f93a5df70001ed73ba","ID":"orders"}
```

The Gateway served the new API 0.3 to 1.0 seconds later with no reload call.

- **Update with PUT by sending back what you read.** `GET /api/apis/oas/{id}`, change the
  document, `PUT` it. The readback carries `x-tyk-api-gateway.info.dbId` and `info.orgId`. A
  `PUT` of a base API that has versions fails without them:
  `400 {"Status":"Error","Message":"Failed to retrieve organisation","Meta":null}`.
- `GET` returns an `ETag` header. Send it as `If-Match` on a merge patch to refuse the write if
  someone changed the API in between. Send it exactly as returned, double quotes included
  (`If-Match: "1795093626407"`): on Dashboard 5.16.0 the same value without the quotes is
  answered with 412 every time. See
  https://tyk.io/docs/api-management/gateway-config-managing-oas.
- A second API on a listen path that is taken is accepted with `200`; see
  `definition-shape.md` for what the Gateway then does.

## Import a plain OpenAPI document

The body is the OpenAPI document with no Tyk extension. JSON and YAML both work on the Gateway
API and on the Dashboard API. The Dashboard also takes `{"url": "https://..."}` and fetches the
document itself, so the URL must be reachable from the Dashboard.

```bash
curl -s -X POST "$GW/tyk/apis/oas/import?apiID=orders&listenPath=/orders-api/&upstreamURL=http://orders.example.com:8080&allowList=true&validateRequest=true" \
  -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @openapi.json
# 200 {"key":"orders","status":"ok","action":"added"}
```

| Query parameter | Effect | Without it |
| --- | --- | --- |
| `apiID` | Sets `info.id` | A random id is generated |
| `listenPath` | Sets `server.listenPath.value` | `/` |
| `upstreamURL` | Sets `upstream.url` | The first `servers[].url` of the document; error if there is none |
| `customDomain` | Sets `server.customDomain` to `{enabled: true, name: <value>}` | No custom domain |
| `allowList=true` | Adds `allow: {enabled: true}` to every operation | No allow list |
| `validateRequest=true` | Adds `validateRequest: {enabled: true, errorResponseCode: 422}` to every operation that declares parameters or a request body | No validation |
| `mockResponse=true` | Adds `mockResponse` from the examples to every operation whose responses have an example or schema | No mocks |
| `authentication=true` | Sets `server.authentication.enabled` and enables each scheme named in the document's top-level `security` | The API is open |
| `securityProcessingMode=compliant` (or `legacy`) | Sets `server.authentication.securityProcessingMode`, which decides how several `security` entries are read (`versioning-and-authentication.md`). Use it with `authentication=true` | Legacy mode: only the first `security` entry is used |

`authentication=compliant` is not a shortcut for both: on Gateway 5.16.0 it sets
`securityProcessingMode: "compliant"` but leaves `server.authentication.enabled: false`, and the
imported API answers 200 to a request with no credential.

Import always sets `info.state.active: true`, `listenPath.strip: true`,
`middleware.global.contextVariables.enabled: true` and
`middleware.global.trafficLogs.enabled: true`, and takes `info.name` from `info.title`.

An operation without an `operationId` gets one built from the path without its leading slash and
the upper-case method: `GET /orders/{id}` became `orders/{id}GET`. Give every operation your own
`operationId` first if you intend to add middleware by hand later.

`authentication=true` only switches the schemes on. An HTTP bearer scheme with
`bearerFormat: JWT` came out with `enabled: true` and the `Authorization` header as its source,
and no signing settings; add those before the API can accept a token.

Import errors (`400`):

| Cause | Message |
| --- | --- |
| No `servers` in the document and no `upstreamURL` | `The ‘servers’ object is empty in your OAS. You can either add a ‘servers’ section to your OpenAPI description or provide a Custom Upstream URL in the manual configuration options below.` |
| First `servers` URL is relative, for example `/v1` | `The first entry in the ‘servers’ object of your OAS is not valid. The URL must be absolute and properly formatted (e.g. https://example.com). Please update "/v1" to be a valid URL and try again.` |
| `upstreamURL=/relative` | `The manually configured upstream URL is not valid. The URL must be absolute and properly formatted (e.g. https://example.com). Please check the URL format and try again.` |
| `authentication=true` and no top-level `security` | `The ‘security’ object is empty in your OAS. When enabling authentication, your OpenAPI description must include a ‘security’ object that defines the authentication schemes. ...` |
| The body carries the Tyk extension | `the import payload should not contain x-tyk-api-gateway` |

`upstreamURL=orders.example.com:8080` (no scheme) is accepted and then fails at request time; see
`definition-shape.md`. A `servers` URL with variables is filled from each variable's `default`.

## PATCH: a new OpenAPI document, same Tyk settings

Send the new plain OpenAPI document. The stored extension is kept: listen path, upstream,
authentication, and middleware you added by hand.

```bash
curl -s -X PATCH "$GW/tyk/apis/oas/orders?allowList=true&validateRequest=true" \
  -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @openapi-v1.1.json
# 200 {"key":"orders","status":"ok","action":"modified"}
```

- **Pass the same query parameters you imported with.** Without them a new operation gets no
  middleware. On an API with an allow list that means the new operation returns
  `403 {"error": "Requested endpoint is forbidden"}` until you PATCH again with
  `allowList=true`.
- `listenPath` and `upstreamURL` on a PATCH change those settings.
- An operation that is no longer in the document: the Gateway API removes its middleware block.
  The Dashboard API removes it only when the PATCH has at least one query parameter; a PATCH with
  none left the block behind under an `operationId` that no longer exists.
- A body that carries the Tyk extension: the Gateway API accepts it and treats the call as a
  full replace. The Dashboard API refuses it:
  `400 Tyk vendor extension ("x-tyk-api-gateway") is not allowed when updating the OpenAPI description of an API.`
- On the Dashboard the body may be `{"url": "https://..."}`.

## Dashboard merge patch: change a few fields

From Dashboard 5.16.0 (see the managing page above):

```bash
curl -s -X PATCH "$DASH/api/apis/oas/orders" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/merge-patch+json' \
  -d '{"x-tyk-api-gateway": {"upstream": {"url": "http://orders-v2.example.com:8080"}}}'
# 200 {"Status":"OK","Message":"Api updated","Meta":null}
```

- Only the fields in the body change. Set a field to `null` to remove it. Arrays are replaced
  whole.
- The same patch a second time answers `200 ... "Message":"Api unchanged"`.
- With a stale `If-Match`:
  `412 {"Status":"Error","Message":"The API definition has changed since it was read: read it again and retry","Meta":null}`.
- The patched result is validated like a full update: removing `upstream.url` answers
  `400 x-tyk-api-gateway.upstream: url is required`. An unknown field is accepted and dropped,
  as everywhere else.
- The Gateway API has no merge patch. The same request there is read as a full document and
  rejected.
