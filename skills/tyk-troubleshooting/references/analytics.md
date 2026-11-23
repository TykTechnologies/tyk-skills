Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0` and `tykio/tyk-gateway:v5.16.0`), Tyk Dashboard 5.16.0, Tyk Pump 1.18.0 (SQL and SQL aggregate pumps to PostgreSQL 16)

# Analytics missing

The path is: the Gateway writes one record per request to the Redis list
`analytics-tyk-system-analytics`; Pump reads and empties that list every `purge_delay`
seconds and writes to its configured stores; the Dashboard reads the store. Find the hop
where records stop. Results below are those of Gateway 5.16.0 and Pump 1.18.0.

```bash
# 1. Does the Gateway record this API at all?
curl -s $GW/tyk/apis/<api id> -H "x-tyk-authorization: $SECRET" | python3 -c \
  'import sys,json; print("do_not_track:", json.load(sys.stdin).get("do_not_track"))'
# 2. Are records waiting in Redis? (run from the Redis network)
redis-cli -h <redis host> llen analytics-tyk-system-analytics
# 3. Is Pump writing? Look for "Purged N records..." per pump
docker logs --since 5m <pump container> 2>&1 | grep -E 'Purged|level=error'
```

## Cause 1: the API definition switches recording off

| Definition | Gateway view | Analytics |
| --- | --- | --- |
| Created (`POST /tyk/apis/oas`, `POST /api/apis/oas`) **without** `middleware.global.trafficLogs` | `"do_not_track": true` | None: nothing reaches Redis and Pump stays silent |
| `"middleware": {"global": {"trafficLogs": {"enabled": true}}}` | `"do_not_track": false` | Recorded |
| `"trafficLogs": {"enabled": false}` | `"do_not_track": true` | None |

This holds on both the open source Gateway and through the Dashboard: an API created without the
block (through the Gateway API or the Dashboard API) records no rows, however many requests it takes; after a `PUT` that
adds `"trafficLogs": {"enabled": true}` the next requests are recorded. The import endpoints
(`/tyk/apis/oas/import`, `/api/apis/oas/import`) switch it on when the block is missing: a
plain OpenAPI document imported through each comes out with `"do_not_track": false`. That is
why imported APIs record and hand-written ones do not.

Fix: add the block to every Tyk OAS definition you write by hand.

Docs: https://tyk.io/docs/api-management/logs/traffic-logs

## Cause 2: Pump not running, or not draining

| State | What you see |
| --- | --- |
| Pump stopped | The Redis list grows (5 requests give `LLEN` 5); the store gets nothing. Pump started again: `Purged 5 records...` for each pump, and the 5 rows arrive |
| Pump stopped for longer than the Gateway's `analytics_config.storage_expiration_time` (60 s when unset) | Records waiting in Redis are deleted about `storage_expiration_time` seconds (default 60) after the list gets its expiry, whether or not traffic continues. With one request a second the list grows to about 56 records and is deleted 60 seconds after the first; Pump started later never sees them |
| One pump's store unreachable | Pump stays up and `/health` stays `{"status": "ok"}`; at start it logs `level=error message="Pump init error (skipping): failed to connect to …" pump="SQL Pump"` and runs without that pump |
| Pump cannot reach Redis | `level=error message="Error on Purge Loop. Is Temporal Storage down?: …"` |

The Gateway prints `AnalyticsConfig.StorageExpirationTime is 0, defaulting to 60s` at start
when the setting is not given. If Pump can be away longer, raise `storage_expiration_time`, or
set `-1` to keep the records until Pump drains them; watch Redis memory either way. Why the
whole list goes at once, and the lists used with `enable_multiple_analytics_keys`: the
`tyk-observability` skill.

The diagnostic script reads `LLEN analytics-tyk-system-analytics` twice, a few seconds apart,
and raises a finding when the backlog is not shrinking. With `enable_multiple_analytics_keys`
the records sit in `analytics-tyk-system-analytics_0` to `_9` instead; read those.

Docs: https://tyk.io/docs/api-management/tyk-pump

## Cause 3: analytics switched off on the Gateway

`enable_analytics` (`TYK_GW_ENABLEANALYTICS`) must be `true` on every Gateway; the traffic-logs
page above documents it. Read the value with config inspection (`configuration.md`).

## Other stores and metrics

This file follows records to PostgreSQL through the SQL pumps. For pumps to other stores
(Elasticsearch, Prometheus, Kafka and others), the Dashboard's aggregated charts,
OpenTelemetry metrics and traces, use the `tyk-observability` skill.
