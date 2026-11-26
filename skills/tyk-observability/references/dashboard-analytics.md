# Analytics in Tyk Dashboard

Last verified: 2026-11-19 against Tyk Dashboard 5.16.0, Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0`), Tyk Pump 1.18.0, PostgreSQL 16

The Dashboard is licensed; licence and package questions go to the `tyk-facts` skill.
Pages: https://tyk.io/docs/api-management/dashboard-analytics,
https://tyk.io/docs/api-management/dashboard-analytics/control-plane-pumps,
https://tyk.io/docs/api-management/dashboard-analytics/data-plane-pump

## The chain

```text
Gateway (analytics on, API trafficLogs on) -> Redis -> Tyk Pump -> PostgreSQL or MongoDB -> Dashboard
                                                        sql            tyk_analytics   (Log Browser)
                                                        sql_aggregate  tyk_aggregated  (traffic charts)
```

Pump settings for PostgreSQL:

```text
TYK_PMP_PUMPS_SQL_TYPE=sql
TYK_PMP_PUMPS_SQL_META_TYPE=postgres
TYK_PMP_PUMPS_SQL_META_CONNECTIONSTRING=host=postgres port=5432 dbname=tyk_analytics user=... password=... sslmode=disable
TYK_PMP_PUMPS_SQLAGGREGATE_TYPE=sql_aggregate
TYK_PMP_PUMPS_SQLAGGREGATE_META_TYPE=postgres
TYK_PMP_PUMPS_SQLAGGREGATE_META_CONNECTIONSTRING=host=postgres ... (same database the Dashboard reads)
TYK_PMP_PUMPS_SQLAGGREGATE_META_TRACKALLPATHS=true
```

The Dashboard reads the same database through `TYK_DB_STORAGE_ANALYTICS_TYPE=postgres` and
`TYK_DB_STORAGE_ANALYTICS_CONNECTIONSTRING`.

## `trafficLogs` decides whether an API appears at all

Example: two Tyk OAS APIs created through the Dashboard API (`POST /api/apis/oas`), one with
`middleware.global.trafficLogs.enabled: true` and one without the block. The read-back of the
second shows no `trafficLogs`. After a few requests to each:

```text
GET /api/usage/apis/19/11/2026/19/11/2026?res=day&p=-1
  {"data":[{"id":{... "api_id":"c597cb33...","api_name":"Example Corp tracked"},"hits":6,"success":6,"error":0, ...}]}
GET /api/logs/?start=<unix>&end=<unix>&p=-1
  6 rows, all "Example Corp tracked"
```

The API without `trafficLogs` has no rows anywhere. Put the block in every definition you send
to an API.

## Aggregates or live computation: `enable_aggregate_lookups`

| Dashboard setting | The traffic charts read |
| --- | --- |
| `enable_aggregate_lookups: false` (default) | raw rows in `tyk_analytics`, aggregated on every page load |
| `enable_aggregate_lookups: true` (`TYK_DB_ENABLEAGGREGATELOOKUPS=true`) | `tyk_aggregated`, written by `sql_aggregate` |

With the default, the usage call above returns the hits from the raw rows; after
`delete from tyk_analytics` it returns `[]`; after setting `TYK_DB_ENABLEAGGREGATELOOKUPS=true`
and restarting the Dashboard it returns the same hits again, from the aggregates. So with aggregate lookups on you can
keep raw rows short (or drop the raw pump) and keep the charts. The Log Browser always needs the
raw `sql` (or `mongo`) pump.

MCP proxies have no live fallback: their screen needs the MCP aggregate pump and aggregate
lookups (the `tyk-mcp-gateway` skill).

## Endpoints worth knowing

| Call | Returns |
| --- | --- |
| `GET /api/usage/apis/{dd}/{mm}/{yyyy}/{dd}/{mm}/{yyyy}?res=day&p=-1` | hits, success, error, latency per API |
| `GET /api/logs/?start=<unix>&end=<unix>&p=-1` | raw records (`APIID`, `APIName`, `Path`, `ResponseCode`, `RequestTime`, `Tags`, `RawRequest`, ...) |
| `GET /hello` | `{"status": "ok"}`, 200 |

All with the header `authorization: <Dashboard API key>` except `/hello`.

## Tracked endpoints

Charts by endpoint use `track_path`. An operation with `trackEndpoint.enabled: true` is
recorded under its template (`/anything/orders/{id}`), others under the raw path. The
aggregate pump's `track_all_paths` setting aggregates every path instead (docs page, "Controlling
Which Endpoints Are Tracked").

## Distributed data planes (MDCB)

A Hybrid Pump next to the data-plane Gateways sends records or aggregates to MDCB (see
https://tyk.io/docs/api-management/dashboard-analytics/data-plane-pump). Its `aggregated`
setting is `false` by default, but the `tyk-data-plane` Helm chart 5.5.0 sets it to `true`
(`enableAggregateAnalytics: true` in `helm show values tyk-helm/tyk-data-plane --version 5.5.0`);
with aggregates only, the Log Browser has no rows from that data plane. For how to deploy it, use the `tyk-deployment` skill.

## Usage reporting to Tyk

The Dashboard configuration reference documents `disable_telemetry`
(`TYK_DB_DISABLETELEMETRY`): "Enable or disable sending telemetry data such as analytics, API
configurations, etc." (https://tyk.io/docs/tyk-dashboard/configuration).
Set `TYK_DB_DISABLETELEMETRY=true` where nothing may leave the environment.
