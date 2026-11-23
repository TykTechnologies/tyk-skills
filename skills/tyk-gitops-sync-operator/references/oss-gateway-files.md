# Open source Gateway alone: API definitions and policies as files

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`) with Redis 7.2, no Dashboard

Public pages:
https://tyk.io/docs/tyk-oss-gateway/configuration (Gateway configuration),
https://tyk.io/docs/tyk-gateway-api (Gateway API).

Without a Dashboard the Gateway reads API definitions from a directory (`app_path`) and
policies from files. Git holds the files; a CI job puts them in the directory and tells the
Gateway to reload. No licence is involved. Keys live in Redis and are not files.

## Gateway settings

| Environment variable | Example value | Purpose |
| --- | --- | --- |
| `TYK_GW_APPPATH` | `/opt/tyk-gateway/apps` | directory of API definition files |
| `TYK_GW_POLICIES_POLICYSOURCE` | `file` | read policies from files |
| `TYK_GW_POLICIES_POLICYPATH` | `/opt/tyk-gateway/policies` | directory of policy files, one JSON file per policy |
| `TYK_GW_SECRET` | a secret | the `x-tyk-authorization` header of the Gateway API |

With `policy_path` set, the Gateway loads every JSON file in that directory and also allows
policy management through the Gateway API; without it, policies come from the single file
named by `policies.policy_record_name`.

## A Tyk OAS API is two files

Do not put a bare Tyk OAS document in the directory. The Gateway reads it as a classic
definition and skips it:

```text
level=error msg="Listen path is empty"
level=warning msg="Spec not valid, skipped!"
```

Let a Gateway write the files. `POST /tyk/apis/oas` with the Tyk OAS document:

```bash
curl -s -X POST http://localhost:8080/tyk/apis/oas \
  -H 'x-tyk-authorization: <gateway secret>' -H 'Content-Type: application/json' \
  -d @orders.oas.json
# {"key":"orders","status":"ok","action":"added"}    HTTP 200
```

It writes two files into `app_path`: `orders.json` (a classic-format record with
`"is_oas": true`) and `orders-oas.json` (the Tyk OAS document, with a generated `servers`
list). The file names come from `x-tyk-api-gateway.info.id`, so set it.

- The API is not served until a reload: 404 before, 200 after.
- Change an API with `PUT /tyk/apis/oas/<id>` (`{"key":"orders","status":"ok","action":"modified"}`),
  then reload. Editing only `orders-oas.json` by hand and reloading changes nothing: neither a
  new listen path nor an added middleware takes effect, because `orders.json` still holds the
  old values. Keep the pair in step by always going through the Gateway API.
- Remove an API by deleting both files and reloading: the path then returns 404.

## Reload

```bash
curl -s http://localhost:8080/tyk/reload/group -H 'x-tyk-authorization: <gateway secret>'
# {"status":"ok","message":""}    HTTP 200
```

`/tyk/reload/group` reloads the whole group of Gateways. Each Gateway reads its own directory,
so every Gateway needs the files (a shared volume, a ConfigMap, or an image layer). The reload
is not instant: wait a few seconds before sending requests (`deliver.sh` waits 3).

## Policies and keys

A policy file, `policies/payments-standard.json`:

```json
{"id": "payments-standard", "name": "Payments standard", "org_id": "default", "active": true,
 "rate": 2, "per": 60, "quota_max": -1,
 "access_rights": {"payments": {"api_id": "payments", "api_name": "Payments", "versions": ["Default"]}}}
```

After a reload `GET /tyk/policies` lists it. A key that applies it:

```bash
curl -s -X POST http://localhost:8080/tyk/keys -H 'x-tyk-authorization: <gateway secret>' \
  -H 'Content-Type: application/json' -d '{"org_id": "default", "apply_policies": ["payments-standard"]}'
```

With that key the API answers 200, 200, then 429 (rate 2 per 60 seconds); without a key 401.
The policy's `org_id`, the key's `org_id` and the API's `x-tyk-api-gateway.info.orgId` are
all `default` here. A key request with another `org_id` is refused:
`{"status":"error","message":"Failed to create key, ensure security settings are correct."}`,
HTTP 500. Keep the three equal. After the file's rate is changed to 100 and the Gateway
reloaded, `GET /tyk/policies` shows 100 and a new key makes four calls with 200.

Keys are created through the Gateway API and stored in Redis. They are not part of the Git
repository; the `tyk-rate-limits-keys-policies` skill covers them.

## What a CI job does

Git holds the Tyk OAS documents (`apis/*.oas.json`) and the policy files (`policies/*.json`).
`assets/oss/deliver.sh` is the job:

1. Start a throwaway Gateway and Redis, and `POST` every document to it, so it writes the
   pairs into `build/`. The Gateway image runs as user `65532`; `--user "$(id -u):$(id -g)"`
   lets it write into the job's directory.
2. Replace the Gateways' directories with `build/` and `policies/` (`rsync -a --delete`; a
   ConfigMap, a volume or a new image does the same job).
3. Call `/tyk/reload/group` once, then list `GET /tyk/apis`.

```bash
APPS_DIR=/srv/tyk/apps POLICIES_DIR=/srv/tyk/policies \
GATEWAY_URL=http://localhost:8080 GATEWAY_SECRET=<gateway secret> ./deliver.sh
# {"key":"orders","status":"ok","action":"added"}
# {"key":"payments","status":"ok","action":"added"}
# {"status":"ok","message":""}
# loaded: ['orders', 'payments']
```

Add `build/` to `.gitignore`. `GEN_PORT` (default 18080) is the local port of the throwaway
Gateway; `GEN_SUBNET` sets a subnet for its network when Docker's default pools are used up.

What each commit does to the APIs after the job runs:

| Commit | `/orders/list` | `/orders-new/list` | `POST /payments/charge` |
| --- | --- | --- | --- |
| Baseline | 200, upstream sees `/list` | 404 | 401 (needs a key) |
| "Move orders to `/orders-new/`, retire payments" | see the note | 200 | 404 |
| `git revert` of that commit | 200, upstream sees `/list` | 404 | 401 |

**Rollback** is `git revert` and the same job. Because the job replaces the directories, a
file that is gone from Git removes its API or policy: `rsync --delete` also removes a policy
file that another tool wrote there.

Note on the second row: `/orders/list` still answers 200 and the upstream sees the full path
`/orders/list`. The Gateway serves every API under `/<api id>/` as well as under its listen
path, on a Dashboard-managed Gateway too. An API whose ID is `orders` therefore keeps
answering on `/orders/` after its listen path moves.
Choose IDs that are not also meant to be paths of another API, and test the old path after a
move.

Calling `POST` or `PUT /tyk/apis/oas` on the live Gateway also works when its `app_path` is
writable, but then the Gateway's disk, not Git, is where a change lands first.

## Per-environment values

`"upstream": {"url": "kv://env/ORDERS_UPSTREAM"}` in the document is resolved by the Gateway
from its own `TYK_SECRET_ORDERS_UPSTREAM`, so one set of files serves every environment
(`environments.md`).

## Other tools with the open source Gateway

- **Tyk Sync**: not supported; Tyk Sync 2.x is supported only with a licensed Dashboard
  (`tyk-sync.md`, "Limitations").
- **Tyk Operator**: works in `ce` mode, with an Operator licence key (`tyk-operator.md`, "The
  open source Gateway").

## Limitations

- No central store: every Gateway must receive the files, and nothing reports a Gateway that
  missed them. Verify each Gateway after a delivery.
- No API Fragments, API Templates, users, or role-based access: those are Dashboard features
  (https://tyk.io/docs/platform-management/api-fragments: "Tyk Gateway doesn't resolve them").
- No built-in drift report. The files on disk are the state; compare them with Git.
