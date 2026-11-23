Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` reading files, `tykio/tyk-gateway-ee:v5.16.0` managed by Tyk Dashboard 5.16.0)

# An API that is not loaded

The caller sees `404`, `Content-Type: text/plain; charset=utf-8`, body `Not Found`, and no
`X-Generator` header: no loaded API matched the request. First ask the Gateway what it has:

```bash
curl -s $GW/tyk/apis -H "x-tyk-authorization: $SECRET" | python3 -c \
  'import sys,json; [print(a["api_id"], a["proxy"]["listen_path"], a["active"]) for a in json.load(sys.stdin)]'
```

If the API is in that list, the path is wrong (listen path, `strip`, matching mode: the
`tyk-api-definitions-routing` skill). If the list is empty on a Dashboard-managed Gateway, it never
loaded its configuration (`control-plane.md`, `licences.md`, `redis.md`). If the list holds
other APIs but not this one, read on. The cases below are those of Gateway and Dashboard 5.16.0.

## Open source Gateway: no reload after a change

```bash
curl -s -X POST $GW/tyk/apis/oas -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @up.oas.json
# {"key":"up","status":"ok","action":"added"}
curl -s -o /dev/null -w '%{http_code}\n' $GW/up/x                     # 404
curl -s $GW/tyk/apis -H "x-tyk-authorization: $SECRET"                 # [] : stored, not loaded
curl -s $GW/tyk/reload/group -H "x-tyk-authorization: $SECRET"         # {"status":"ok","message":""}
curl -s -o /dev/null -w '%{http_code}\n' $GW/up/x                     # 200
```

The create call wrote the files to `app_path`; the Gateway read them only on reload. A
Dashboard-managed Gateway needs no reload call.

## Two APIs on one listen path

Both load. The second is moved to `<listen path>-<api id>`, so with listen path `/up/` and
API id `up2` it serves on `/up/-up2`, and requests to `/up/` keep going to the first API. The
Gateway logs it at `error`:

```text
level=error message="Listen path collision, changed to /up/-up2" api_id=up2 api_name=up2 org_id=default type=request
```

Fix: give each API its own listen path.

## A definition file the Gateway cannot parse

On a Gateway reading files, a file in `app_path` that is not valid JSON is skipped; the others
load:

```text
level=info message="Loading API Specification from /opt/tyk-gateway/apps/broken.json"
level=error message="Couldn't unmarshal read file: invalid character 'o' in literal null (expecting 'u')"
```

The error line does not repeat the file name; it follows the `Loading API Specification`
line that names it. Validate definitions before they reach the Gateway (the
`tyk-api-definitions-routing` skill has a checker).

## `state.active: false`

| Gateway | Result |
| --- | --- |
| Dashboard-managed | The API leaves the Gateway's `/tyk/apis` and answers 404 within seconds of the update |
| Open source, reading files | The API stays loaded (`"active": false` in `/tyk/apis`) and **keeps proxying** (200) |

On the open source Gateway, take an API out of service by deleting it
(`DELETE /tyk/apis/oas/{id}` then reload), not by setting it inactive.

## Gateway segmentation

A Dashboard-managed Gateway with `TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED=true` and
`TYK_GW_DBAPPCONFOPTIONS_TAGS=edge` loads only APIs whose `server.gatewayTags.tags` include
`edge`. With no such API it loads nothing and still reports `/ready` 200:

```text
level=info message="Detected 0 APIs" prefix=main
level=warning message="No API Definitions found, not reloading" prefix=main
```

Compare the API's tags with every Gateway's `TYK_GW_DBAPPCONFOPTIONS_TAGS`. The open source
Gateway reading files does not filter by these settings: it loads every file.

## Other reasons an API is missing on a Dashboard-managed Gateway

`scripts/collect_diagnostics.py` compares the number of active APIs the Dashboard lists with
the number each Gateway has loaded and raises a finding when a Gateway has fewer. Segment
tags (above) are the cause described here. For APIs delivered through Tyk Sync or Tyk
Operator, use the `tyk-gitops-sync-operator` skill.

Docs: https://tyk.io/docs/api-management/gateway-config-tyk-oas
