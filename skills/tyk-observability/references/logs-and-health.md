# Access logs, log formats and health endpoints

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), Tyk Dashboard 5.16.0, Tyk Pump 1.18.0

Pages: https://tyk.io/docs/api-management/logs/access-logs,
https://tyk.io/docs/api-management/logs/application-logs,
https://tyk.io/docs/planning-for-production/ensure-high-availability/health-check

## Access logs

`TYK_GW_ACCESSLOGS_ENABLED=true` (`access_logs.enabled`) writes one line per request to the
Gateway's stdout, for every API, including APIs without `trafficLogs`. Ship stdout with your
log agent (Fluent Bit, Vector, the OpenTelemetry Collector filelog receiver, a cloud agent).

With `TYK_GW_LOGFORMAT=json` and no template, a refused request:

```json
{"api_id":"orders","api_key":"00000000","api_name":"Orders API","api_type":"oas","client_ip":"10.0.0.1",
 "error_source":"AuthKey","latency_total":0,"level":"info","method":"GET","mw":"AuthKey","org_id":"default",
 "original_path":"/orders-api/anything/orders","path":"/anything/orders","prefix":"access-log",
 "response_code_details":"auth_field_missing","response_flag":"AMF","span_id":"9db5e899de5cb5ef","status":401,
 "trace_id":"ca8c604d1788605b6df7a7b486833043","upstream_latency":0,"user_agent":"curl/8.7.1", ...}
```

an upstream error:

```json
{"api_id":"orders","api_key":"0f9bdf9e3af66a13","error_source":"Upstream","error_target":"upstream:8080",
 "response_code_details":"upstream_response_5xx","response_flag":"URS","status":503,"upstream_status":503,
 "upstream_addr":"http://upstream:8080/status/503","trace_id":"ad6885650cd55a9dbc2645a07f0dd6f5", ...}
```

- `api_key` is the key hash; a request with no valid key shows `00000000`.
- `trace_id` is there when tracing is on, and matches `X-Tyk-Trace-Id`.
- `response_flag` uses the same codes as the `tyk.response_flag` metric label.

`access_logs.template` (`TYK_GW_ACCESSLOGS_TEMPLATE`, a comma-separated list in the variable)
narrows the fields. With `api_id,status,latency_total,upstream_latency,response_flag,trace_id,client_ip`
and text format:

```text
level=info api_id=untracked api_name="Untracked API" client_ip=10.0.0.1 latency_total=41 org_id=default prefix=access-log status=200 trace_id=f1d56aea... type=request upstream_latency=39
level=info api_id=orders api_name="Orders API" ... mw=AuthKey origin=10.0.0.1 path=/orders-api/anything/orders prefix=access-log response_flag=AMF span_id=bed4979d... status=401 ...
```

`api_name`, `org_id`, `type` and `prefix` are always present; error lines also add `mw`,
`origin`, `path` and `span_id`. The field list is on the access-logs page.

## Application log format

`log_format` (`TYK_GW_LOGFORMAT`): `text` (default), `json` or `legacy`
(https://github.com/TykTechnologies/tyk/blob/v5.16.0/config/config.go).
`json` applies to every Gateway line, including the fatal start-up errors. Pump's stdout pump
writes JSON with `TYK_PMP_PUMPS_<NAME>_META_FORMAT=json`.

## Health and readiness

| Endpoint | Healthy | Redis stopped |
| --- | --- | --- |
| Gateway `GET /hello` | `200 {"status":"pass","version":"5.16.0","description":"Tyk GW", "details":{"redis":{"status":"pass",...}}}` | **`200`** `{"status":"fail",...,"details":{"redis":{"status":"fail","output":"storage: Redis is either down or was not configured",...}}}` on the open source Gateway; `"status":"warn"` on a Dashboard-managed one |
| Gateway `GET /ready` | `200 {"status":"pass","version":"5.16.0","description":"Tyk GW Ready",...}` | `503 {"status":"error","message":"Redis connection not available"}` |

Dashboard `GET /hello` answers `200 {"status": "ok"}`, and Pump `GET :8083/health` (from
inside the Compose network) answers `200 {"status": "ok"}`.

- Use `/ready` for load balancer and Kubernetes readiness checks. `/hello` answers 200 even when
  its own body says `fail`, so a status-code check on it never fails.
- `/ready` also answers 503 until the Gateway has loaded its APIs once and while it shuts down
  (https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/health_check.go).
- `status` is `pass` when every check passes, `fail` when every check fails, and `warn` when
  some fail. An open source Gateway has only the `redis` check, so Redis down gives `fail`; a
  Dashboard-managed Gateway also has the `dashboard` check, so Redis down alone gives `warn`.
- The checks run every `liveness_check.check_duration` (10 seconds by default). `/ready` turns
  503 within about 14 seconds of Redis stopping, and goes back to 200 within one check interval
  of Redis starting again.
- Names: `health_check_endpoint_name` (default `hello`) and `readiness_check_endpoint_name`
  (default `ready`). Pump: `health_check_endpoint_name` (default `health`) and
  `health_check_endpoint_port` (default 8083).
- An API on a keyless path keeps answering 200 while Redis is stopped.
