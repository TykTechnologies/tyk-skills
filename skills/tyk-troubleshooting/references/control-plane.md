Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0`) and Tyk Dashboard 5.16.0

# Gateway and Dashboard cannot talk

A Dashboard-managed Gateway (`use_db_app_configs: true`) registers with the Dashboard at
start, then fetches its APIs and policies. The Dashboard in turn calls the Gateway's API to
create keys. Each direction has its own URL and secret:

| Direction | Gateway or Dashboard setting | Must equal |
| --- | --- | --- |
| Gateway to Dashboard: URL | `TYK_GW_DBAPPCONFOPTIONS_CONNECTIONSTRING` and `TYK_GW_POLICIES_POLICYCONNECTIONSTRING` | The Dashboard address as the Gateway reaches it |
| Gateway to Dashboard: secret | `TYK_GW_NODESECRET` | The Dashboard's `TYK_DB_NODESECRET` |
| Dashboard to Gateway: URL | `TYK_DB_TYKAPI_HOST` and `TYK_DB_TYKAPI_PORT` | The Gateway address as the Dashboard reaches it |
| Dashboard to Gateway: secret | `TYK_DB_TYKAPI_SECRET` | The Gateway's `TYK_GW_SECRET` |

In all the "Gateway cannot register" cases below the Gateway keeps retrying every 5 seconds
and recovers on its own once the cause is fixed; no Gateway restart is needed when the
Dashboard side is fixed.

## Gateway cannot register: every API 404

| Cause | `/hello` | `/ready` | Gateway log (prefix=dashboard) |
| --- | --- | --- | --- |
| Wrong `TYK_GW_NODESECRET` | `pass`, dashboard `pass` | `503 A successful API reload did not happen` | `level=error message="Response failed with code 403; retrying in 5s"` |
| Wrong Dashboard URL (host does not resolve) | `warn`, dashboard `fail` `dashboard is down? Heartbeat is failing` | `503 A successful API reload did not happen` | `level=error message="Request failed with error Get \"http://dashboard-typo:3000/register/node\": dial tcp: lookup dashboard-typo on 127.0.0.11:53: no such host; retrying in 5s"` |
| Dashboard down when the Gateway starts | as above | as above | as above, with the right host name |
| Licence node limit reached | `pass` | `503 A successful API reload did not happen` | `level=error message="Response failed with code 404; retrying in 5s"` (`licences.md`) |
| Dashboard licence invalid | `pass` | as above | as above, code 404 (`licences.md`) |
| Dashboard cannot reach Redis | `warn` (redis) | `503 Redis connection not available` | `level=warning message="Registration deferred (409 with status: Error); retrying in 5s"` (`redis.md`) |

The Dashboard logs nothing at `info` level for the secret, URL and node-limit cases. The
status code in the Gateway's retry line is the quickest discriminator: 403 secret, 404
licence slot, 409 Redis, a transport error for the URL.

A Gateway refused for its node secret is also missing from the Dashboard's node list
(`GET $DASH/api/system/nodes` shows `"nodes":[]` and `active_node_count` 0 on a one-Gateway
stack).

Check from the Gateway's side:

```bash
curl -s $GW/ready
docker logs --since 5m <gateway container> 2>&1 | grep 'prefix=dashboard'
```

## Dashboard down after the Gateway has loaded

The Gateway keeps serving what it has: open and keyed APIs answer 200, `/ready` stays 200,
`/hello` shows dashboard `fail`. If the Gateway restarts while the Dashboard is still down, it comes up with no APIs
(every API 404) until the Dashboard returns; it then loads within about 6 seconds.

## Dashboard cannot reach the Gateway

The Gateway keeps serving traffic. What fails is anything the Dashboard writes through the
Gateway API, such as creating a key:

```bash
curl -s -X POST $DASH/api/keys -H "authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/json' -d '{"alias":"test","apply_policies":["<policy id>"]}'
```

| Cause | Response |
| --- | --- |
| Wrong `TYK_DB_TYKAPI_HOST` | `403 {"Status":"Error","Message":"Failed to save new session object to Tyk: Failed to create key: Post \"http://gateway-typo:8080/tyk/keys/create\": dial tcp: lookup gateway-typo on 127.0.0.11:53: no such host","Meta":null}` |
| Wrong `TYK_DB_TYKAPI_SECRET` | `403 {"Status":"Error","Message":"Failed to save new session object to Tyk: Attempted administrative access with invalid or missing key!","Meta":null}`; the Gateway logs `level=warning message="Attempted administrative access with invalid or missing key!" prefix=main` |

The URL the Dashboard used is in the message: the host comes from `TYK_DB_TYKAPI_HOST` and
the port from `TYK_DB_TYKAPI_PORT`.

## A killed Gateway still holds its node slot

A Gateway stopped normally deregisters, and its licence slot is free within seconds. One that
is killed (`docker kill`, an out-of-memory kill) holds its slot for about 30 seconds; a
replacement started in that window waits with `/ready` 503 until the slot expires.

Docs: https://tyk.io/docs/tyk-oss-gateway/configuration and
https://tyk.io/docs/tyk-dashboard/configuration (the settings above, by name).

## Scope

This file covers Gateways connected straight to a Dashboard. For Gateways connected through MDCB
(hybrid), use https://tyk.io/docs/api-management/mdcb and the `tyk-deployment-runner` skill.
