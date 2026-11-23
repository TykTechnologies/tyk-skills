# Observability of MCP traffic

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0`), Dashboard 5.16.0, Pump 1.18.0 with PostgreSQL 16, OpenTelemetry Collector 0.100.0, Tempo 2.5.0 and Prometheus 2.53.0

See https://tyk.io/docs/ai-management/mcp-gateway/mcp-observability,
https://tyk.io/docs/ai-management/mcp-gateway/mcp-analytics,
https://tyk.io/docs/ai-management/mcp-gateway/mcp-access-logs,
https://tyk.io/docs/ai-management/mcp-gateway/mcp-metrics,
https://tyk.io/docs/api-management/dashboard-analytics/control-plane-pumps.

| Signal | Needs | MCP detail you get |
| --- | --- | --- |
| Analytics records | `middleware.global.trafficLogs.enabled: true` on the proxy, Tyk Pump with an MCP pump | method, tool name, error code, protocol version per request |
| Dashboard "Activity by MCP" | The MCP aggregate pump and `enable_aggregate_lookups` on the Dashboard | hits and errors per proxy and per tool |
| Access logs | `access_logs.enabled` on the Gateway | `api_type=mcp`, `mcp_method`, `mcp_primitive_type`, `mcp_primitive_name`, `mcp_error_code` |
| Traces | OpenTelemetry traces on the Gateway | one span per method and per tool; `mcp.method.name`, `mcp.tool.name` |
| Metrics | OpenTelemetry metrics on the Gateway and your own instrument | any of the four MCP fields as labels |

## Analytics and Tyk Pump

MCP records do not go through the ordinary pumps. With only the `sql` and `sql_aggregate` pumps
running, the `tyk_analytics` table holds no row for any MCP proxy. The documentation says the
same: MCP records are excluded from the standard SQL pump. See
https://tyk.io/docs/api-management/dashboard-analytics/control-plane-pumps.

Two pump types handle them (PostgreSQL shown; `mongo-mcp` and `mongo-mcp-aggregate` are the
MongoDB equivalents in Tyk Pump 1.18.0):

```text
TYK_PMP_PUMPS_SQLMCP_TYPE=sql-mcp
TYK_PMP_PUMPS_SQLMCP_META_TYPE=postgres
TYK_PMP_PUMPS_SQLMCP_META_CONNECTIONSTRING=host=... port=5432 dbname=tyk_analytics user=... password=... sslmode=disable
TYK_PMP_PUMPS_SQLMCPAGGREGATE_TYPE=sql-mcp-aggregate
TYK_PMP_PUMPS_SQLMCPAGGREGATE_META_TYPE=postgres
TYK_PMP_PUMPS_SQLMCPAGGREGATE_META_CONNECTIONSTRING=host=... (same)
```

- `sql-mcp` writes one row per request to `tyk_analytics_mcp`. Useful columns: `analytics_apiid`,
  `analytics_responsecode`, `jsonrpc_method`, `primitive_type`, `primitive_name`,
  `jsonrpc_error_code`, `effective_protocol_version`, `declared_protocol_version`,
  `protocol_version_source`. Sample rows:

  ```text
  orders-mcp | 200 | tools/call | tool | generate_uuid | 0      | 2026-07-28 | header_body
  orders-mcp | 403 | tools/call | tool | delete_user   | -32002 | 2025-11-25 | header
  orders-mcp | 401 | initialize |      |               | -32001 | 2025-11-25 | body
  ```

- `sql-mcp-aggregate` writes `tyk_mcp_aggregated`, which the Dashboard reads. Its `dimension`
  values include `methods` (`tools/call`), `names` (`tool_generate_uuid`), `primitives` (`tool`),
  `errors` (`403`), `apikeys` and `tags`.
- With MDCB (distributed data planes), the Hybrid Pump and MDCB settings decide where MCP traffic
  is aggregated; with aggregation on the data plane the individual MCP records are not kept
  (see https://tyk.io/docs/api-management/dashboard-analytics/data-plane-pump, section "MCP
  Proxy Traffic").
- A proxy without `trafficLogs` enabled produces no rows.
- For a REST-to-MCP proxy, the tool call is one MCP row for the proxy, and the internal call to
  the source API is an ordinary row in `tyk_analytics` under the source API's id (when the source
  API has `trafficLogs` on).

## Dashboard

The Dashboard needs `enable_aggregate_lookups: true` (`TYK_DB_ENABLEAGGREGATELOOKUPS=true`).
Without it, `GET /api/usage/mcp/17/11/2026/17/11/2026?res=day&p=-1` returns the REST API and no
MCP proxy, and the Dashboard logs `Aggregates disabled`. With it:

```text
GET /api/usage/mcp/17/11/2026/17/11/2026?res=day&p=-1
  {"data":[{"id":{... "api_id":"orders-mcp","api_name":"Orders MCP"},"hits":11,"error":2, ...}],"pages":0}
GET /api/activity/mcp/primitives/17/11/2026/17/11/2026?res=day&p=-1
  {"data":[{"id":{... "mcp_primitive_name":"generate_uuid"},"hits":2,"success":2,"error":0, ...}, ...]}
GET /api/activity/mcp/primitives/errors/17/11/2026/17/11/2026?res=day&p=-1
  {"data":[{"id":{... "code":403,"mcp_primitive_name":"delete_user"},"hits":1,"error":1, ...}]}
```

The screen is **Monitoring > Activity by MCP**; the user needs the `analytics` and `mcp`
permissions. See https://tyk.io/docs/ai-management/mcp-gateway/mcp-analytics.

## Access logs

Set `TYK_GW_ACCESSLOGS_ENABLED=true` (`access_logs.enabled`). One line per request:

```text
level=info prefix=access-log api_id=orders-mcp api_type=mcp mcp_method=tools/call mcp_primitive_name=generate_uuid
  mcp_primitive_type=tool method=POST path=/mcp status=200 upstream_addr="http://mcp-server.example.com:7878/mcp" trace_id=...
level=info prefix=access-log api_id=orders-mcp api_type=mcp mcp_error_code=-32002 mcp_method=tools/call
  mcp_primitive_name=delete_user mcp_primitive_type=tool path="/mcp-tool:delete_user" ...
level=info prefix=access-log api_id=orders-mcp api_type=mcp mcp_error_code=-32001 mcp_method=initialize ... response_flag=AMF
```

For a REST-to-MCP proxy on 5.16.0, a tool call logs one line, for the source API:
`api_id=orders-api api_type=oas original_path=/orders-rest-mcp/mcp path=/orders/77 status=200`.
No `api_type=mcp` line appears for the proxy. The documentation describes two lines; rely on
the analytics table or the metric below for the MCP view of a REST-to-MCP proxy.

## Traces

With OpenTelemetry traces on, one `tools/call` produces a trace with these spans:

```text
POST /orders-mcp/mcp                 the request on the listen path (tyk.api.id, tyk.api.name)
POST /json-rpc-method:tools/call     the JSON-RPC method
POST /mcp-tool:generate_uuid         the tool
HTTP POST                            the call to the MCP server
```

With `server.detailedTracing.enabled: true` on the proxy, each middleware has its own span, and
the `JSONRPCMiddleware` span carries `mcp.method.name=tools/call`, `mcp.tool.name=generate_uuid`
and `mcp.trace_source`. The response carries the trace id in the `X-Tyk-Trace-Id` header. A
token exchange adds an `oauth2.exchange` span (`token-exchange-keycloak.md`).

## Metrics

The default instruments (`tyk_api_requests_total`, `http_server_request_duration_seconds`,
`tyk_gateway_request_duration_seconds`, `tyk_upstream_request_duration_seconds`) have no MCP
label: an MCP proxy shows up by `tyk_api_id`, HTTP method and status only.

MCP labels come from an instrument you define in `opentelemetry.metrics.api_metrics`:

```text
TYK_GW_OPENTELEMETRY_METRICS_ENABLED=true
TYK_GW_OPENTELEMETRY_METRICS_EXPORTER=grpc
TYK_GW_OPENTELEMETRY_METRICS_ENDPOINT=otel-collector:4317
TYK_GW_OPENTELEMETRY_METRICS_APIMETRICS=[{"name":"tyk.mcp.requests.total","type":"counter","description":"MCP requests by method and primitive","dimensions":[{"source":"metadata","key":"mcp_method","label":"mcp_method","default":""},{"source":"metadata","key":"mcp_primitive_type","label":"primitive_type","default":""},{"source":"metadata","key":"mcp_primitive_name","label":"primitive_name","default":""},{"source":"metadata","key":"mcp_error_code","label":"error_code","default":""},{"source":"metadata","key":"api_id","label":"api_id"}]}]
```

Prometheus then shows:

```text
tyk_mcp_requests_total{api_id="orders-mcp",mcp_method="tools/call",primitive_name="generate_uuid",primitive_type="tool"} 1
tyk_mcp_requests_total{api_id="orders-mcp",mcp_method="tools/call",primitive_name="delete_user",primitive_type="tool",error_code="-32002"} 1
tyk_mcp_requests_total{api_id="orders-mcp",mcp_method="initialize"} 2
tyk_mcp_requests_total{api_id="orders-api",mcp_method="tools/call",primitive_name="get_order",primitive_type="tool"} 1
```

- The last line is a REST-to-MCP tool call: it is counted under the source REST API's id.
- Setting `api_metrics` replaces the default request instruments; list the default ones again if
  you still want them.
- Histograms and other examples: https://tyk.io/docs/ai-management/mcp-gateway/mcp-metrics.
