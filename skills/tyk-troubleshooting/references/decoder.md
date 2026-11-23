Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0` and `tykio/tyk-gateway:v5.16.0`), Tyk Dashboard 5.16.0, Tyk Pump 1.18.0, Tyk Developer Portal 1.20.1, Redis 7.2

# Decoder: symptom to cause to check to fix

Each symptom is the exact response or log line for the versions above. `GW`, `DASH`, `SECRET`
and `DASHBOARD_API_KEY` are set as in the SKILL.md workflow. Error bodies are shown on one
line; the Gateway sends indented JSON, so match on the `error` value.

## Start from what the caller sees

| Symptom | Likely cause | Check | Fix | Detail |
| --- | --- | --- | --- | --- |
| `404`, `Content-Type: text/plain`, body `Not Found`, no `X-Generator` header | No loaded API matches the path: wrong path, API not loaded, Gateway has loaded nothing | `curl -s $GW/tyk/apis -H "x-tyk-authorization: $SECRET"` lists the loaded API ids; `curl $GW/ready` | Correct the path or listen path; load the API (reload, registration, tags) | `api-not-loaded.md` |
| Every API `404` on one Gateway, `/hello` says `pass` | Gateway never received its APIs from the Dashboard | `curl $GW/ready` gives `503 {"message":"A successful API reload did not happen"}`; Gateway log `Response failed with code 403` or `404`, or `register/node` errors | Node secret, Dashboard URL, licence, node count | `control-plane.md`, `licences.md` |
| `401 {"error": "Authorization field missing"}` | No credential sent, or sent in another header | The API's `server.authentication` and the header name in `components.securitySchemes` | Send the header the API expects | `status-codes.md` |
| `400 {"error": "Authorization field missing"}` | Same, on a JWT API | as above | as above | `status-codes.md` |
| `403 {"error": "Access to this API has been disallowed"}` for a key that works elsewhere | Key unknown to this Gateway's Redis, no access rights to this API, **or Redis is down** (a valid key is refused once the Gateway's 10-second local copy of it expires; a key with a quota at once) | `curl $GW/hello` redis status; `GET $GW/tyk/keys/<key>` | Fix Redis, or the key's access rights | `redis.md`, `status-codes.md` |
| `401 {"error": "Key not authorized: token used before issued"}` or `token is not valid yet` | Token issuer's clock is ahead of the Gateway's, even by 2 seconds | Decode `iat`/`nbf`, compare with `date -u` on the Gateway host | Sync clocks (NTP); set `issuedAtValidationSkew` / `notBeforeValidationSkew` | `status-codes.md` |
| `401 {"error": "Key not authorized: token has expired"}` | `exp` in the past, or Gateway clock ahead | as above | New token; sync clocks; `expiresAtValidationSkew` | `status-codes.md` |
| `429 {"error": "Rate Limit Exceeded"}` / `API Rate Limit Exceeded`, `403 Quota exceeded` | A limit fired; or Redis is down and the credential has a quota (`redis.md`) | The body says which | The `tyk-rate-limits-keys-policies` skill | `status-codes.md` |
| `500 {"error": "There was a problem proxying the request"}` | Upstream refused the connection, or TLS to it failed | Gateway log line with `mw=ReverseProxy`: `connection refused` or `x509: ...` | Fix the upstream URL or port; trust its CA; renew its certificate | `upstream-errors.md` |
| `500 {"error": "Upstream host lookup failed"}` | Upstream host name does not resolve from the Gateway | Log `lookup <host> ... no such host` | Fix the name or the Gateway's DNS | `upstream-errors.md` |
| `504 {"error": "Upstream service reached hard timeout."}` | Upstream slower than the timeout that applies | Request duration equals a configured timeout; log `timeout awaiting response headers` or `context deadline exceeded` | Speed up the upstream or raise the right timeout | `upstream-errors.md` |
| `500` or `503` **without** `X-Generator: tyk.io` | The upstream's own error, passed through | Response carries the upstream's headers (for example `Server`) | Debug the upstream, not Tyk | `status-codes.md` |
| `502` | Not from any of the REST proxy failures in `upstream-errors.md` (those give `500` or `504`): a load balancer or the upstream sent it | Look for `X-Generator: tyk.io`; call the Gateway directly | Debug the hop that sent it | `upstream-errors.md` |
| Gateway latency about 40 ms on every call to a test backend | A Python `http.server` stand-in with Nagle on | Compare with a backend that sets `disable_nagle_algorithm = True` | Fix the stand-in, not Tyk | `upstream-errors.md` |

## Start from a health endpoint or a log line

| Symptom | Likely cause | Check | Fix | Detail |
| --- | --- | --- | --- | --- |
| Gateway `/hello` redis `fail` (overall `"status":"fail"` on an open source Gateway, `"warn"` on a Dashboard-managed one), `/ready` `503 Redis connection not available` | Redis unreachable | `redis-cli -h <host> ping` from the Gateway's network | Restore Redis; the Gateway reconnects by itself within seconds | `redis.md` |
| Gateway `/hello` dashboard `fail` `dashboard is down? Heartbeat is failing` | Dashboard down or wrong URL | Gateway log `Request failed with error Get "http://.../register/node"` | Start the Dashboard or fix `TYK_GW_DBAPPCONFOPTIONS_CONNECTIONSTRING` | `control-plane.md` |
| Gateway log `Response failed with code 403; retrying in 5s` prefix=dashboard | Node secret mismatch | Compare `TYK_GW_NODESECRET` with the Dashboard's `TYK_DB_NODESECRET` | Make them equal, restart the Gateway | `control-plane.md` |
| Gateway log `Response failed with code 404; retrying in 5s` prefix=dashboard | No licence slot: node limit reached, or Dashboard licence invalid | `GET $DASH/api/system/nodes` (`nodes_remaining`, `402`) | Stop an unused Gateway or get a licence with more nodes | `licences.md` |
| Gateway log `Registration deferred (409 with status: Error)` | The Dashboard cannot use Redis | Dashboard API answers `401 Not authorised` to a valid key | Restore the Dashboard's Redis | `redis.md` |
| Dashboard API `402 {"Status":"Error","Message":"Not authorised"}`; UI title `Unlicensed Dashboard` | Dashboard licence invalid or expired | Dashboard log `License key is invalid or has expired, please renew` | Set a valid `TYK_DB_LICENSEKEY` and restart; licence questions to the `tyk-facts` skill | `licences.md` |
| Dashboard API `401 Not authorised` for a key that worked a minute ago | Dashboard lost Redis | Dashboard log `dial tcp: lookup <redis> ...` | Restore Redis | `redis.md` |
| `POST $DASH/api/keys` `403 Failed to save new session object to Tyk: ...` | Dashboard cannot call the Gateway API: host or secret | The text after the colon: `no such host` (host) or `Attempted administrative access with invalid or missing key!` (secret) | `TYK_DB_TYKAPI_HOST`/`PORT`; `TYK_DB_TYKAPI_SECRET` = Gateway secret | `control-plane.md` |
| Portal `/live` and `/ready` answer `200` with an HTML page | Portal licence invalid | `curl -i $PORTAL/ready`: `text/html` instead of `{"code":"OK","message":"Success"}`; log `License is invalid` | Set a valid `PORTAL_LICENSEKEY` | `licences.md` |
| No analytics for one API, others fine | `middleware.global.trafficLogs` missing or `false` on a created definition | `GET $GW/tyk/apis/<id>` shows `"do_not_track": true` | Add `"trafficLogs": {"enabled": true}` | `analytics.md` |
| No analytics at all, Pump `/health` ok | Pump stopped or its store unreachable; records waiting in Redis are deleted about `storage_expiration_time` seconds (default 60) after the list gets its expiry, whether or not traffic continues | `LLEN analytics-tyk-system-analytics` in Redis; Pump log `Pump init error (skipping)` | Fix the pump's store; restart Pump | `analytics.md` |
| A setting in `tyk.conf` seems ignored | An environment variable overrides it, or the variable name is wrong | `GET $GW/config?field=<json key>` with config inspection on | Fix or remove the variable | `configuration.md` |
| Gateway log `Listen path collision, changed to /x/-<api id>` | Two APIs on one listen path | `GET $GW/tyk/apis` listen paths | Give each API its own listen path | `api-not-loaded.md` |

Docs: https://tyk.io/docs/api-management/troubleshooting-debugging (the full list of Gateway
error texts, under "Gateway Error Response Status Codes").
