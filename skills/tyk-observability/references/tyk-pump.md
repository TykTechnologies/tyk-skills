# Tyk Pump: analytics records to your back ends

Last verified: 2026-11-19 against Tyk Pump 1.18.0 (`tykio/tyk-pump-docker-pub:v1.18.0`), Tyk Gateway 5.16.0, Elasticsearch 7.17.28, OpenSearch 2.19.4, PostgreSQL 16

Pages: https://tyk.io/docs/api-management/tyk-pump,
https://tyk.io/docs/tyk-pump/tyk-pump-configuration/tyk-pump-environment-variables,
https://tyk.io/docs/api-management/logs/traffic-logs

## What moves

1. With `enable_analytics: true` (`TYK_GW_ENABLEANALYTICS=true`) the Gateway writes one
   analytics record per request to the Redis list `analytics-tyk-system-analytics`. The open
   source image's own `tyk.conf` leaves analytics off.
2. Tyk Pump reads and empties that list every `purge_delay` seconds and hands each batch to
   every pump it has configured.
3. Records nobody reads expire. Records waiting in Redis are deleted about
   `storage_expiration_time` seconds (default 60) after the list gets its expiry, whether or not
   traffic continues; set `-1` to keep them until Pump drains them (watch Redis memory).
   The Gateway gives the list an expiry when it finds one without, and new records do not
   extend it ([`gateway/redis_analytics_purger.go`](https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/redis_analytics_purger.go#L36-L59)),
   so the whole backlog goes at once. On Gateway 5.16.0, with Pump stopped and one request a
   second, the list grows to about 56 records with a falling TTL and is deleted 60 seconds
   after the first record; a few records left with no further traffic are likewise gone within
   about 70 seconds. The Gateway logs
   `StorageExpirationTime is 0, defaulting to 60s` when the setting is not given. With
   `analytics_config.enable_multiple_analytics_keys` the records are spread over
   `analytics-tyk-system-analytics_0` to `_9`, each with its own expiry.

A record (stdout pump, JSON) holds: `api_id`, `api_name`, `org_id`, `method`, `host`, `path`
(the endpoint template when the endpoint is tracked), `raw_path`, `original_path`,
`listen_path`, `response_code`, `latency` (`total`, `upstream`, `gateway`, ms), `request_time`,
`alias`, `api_key` (hashed), `oauth_id`, `ip_address`, `user_agent`, `tags` (with
`trace-id-<id>` when tracing is on, `key-<hash>`, `api-<id>`, `org-<id>` and tag headers),
`track_path`, `expireAt`, `raw_request` and `raw_response` (empty unless detailed recording is
on), plus `geo`, `network`, `graphql_stats` and `mcp_stats`. Field reference: traffic-logs page.

## The same pumps in every Pump that reads a Redis

Every Pump that reads a Redis must carry the same set of pumps; two Pumps with different pump
sets split the records. Each Pump process takes whatever records are in the list when it
reads, so with a stdout Pump and a separate Elasticsearch Pump, 30 requests can give 28
records to one and 2 to the other. Configure every back end as a pump inside each Pump. Several replicas
of one Pump configuration share the work (for sizing, the `tyk-infra-sizing` skill).

## Configure by environment

`TYK_PMP_OMITCONFIGFILE=true` makes Pump ignore the `pump.conf` in the image. That file defines
MongoDB, Kafka, Prometheus, DogStatsD and other pumps; without the switch, Pump 1.18.0 tries
MongoDB on `localhost` and exits:

```text
level=fatal msg="Failed to connect: server selection error: ... localhost:27017 ... connection refused" prefix=mongo-pump
```

With the switch, the uptime pump still defaults to MongoDB and exits the same way
(`can't connect without connection string`). Set `TYK_PMP_DONTPURGEUPTIMEDATA=true` unless you
store uptime-check results.

Each pump is `TYK_PMP_PUMPS_<NAME>_TYPE=<type>` and its settings are
`TYK_PMP_PUMPS_<NAME>_META_<FIELD>`, field names upper-cased without underscores between words
(`ELASTICSEARCHURL`, `BULKCONFIG_FLUSHINTERVAL`). `assets/lab/compose.yaml` is a working set.

| Back end | Pump type | Settings | Notes |
| --- | --- | --- | --- |
| stdout | `stdout` | `META_FORMAT=json`, `META_LOGFIELDNAME=analytics_record` | one JSON line per request, the record under the field you name; good for a log agent |
| CSV | `csv` | `META_CSVDIR=/tmp/analytics-csv` | one file per hour, `2026-November-19-13.csv` |
| Prometheus | `prometheus` | `META_ADDR=0.0.0.0:9090`, `META_PATH=/metrics` | `tyk_http_status{api,code}`, `tyk_http_status_per_path`, `tyk_http_status_per_key`, `tyk_latency_*`; prefer the Gateway's own metrics (`metrics.md`) |
| Elasticsearch 7 | `elasticsearch` | `META_ELASTICSEARCHURL`, `META_VERSION=7`, `META_INDEXNAME`, `META_BULKCONFIG_FLUSHINTERVAL=1` | see below |
| OpenSearch 2 | `elasticsearch` | the same, with `META_VERSION=7` | OpenSearch 2.19.4 accepts the records |
| PostgreSQL for the Dashboard | `sql` and `sql_aggregate` | `META_TYPE=postgres`, `META_CONNECTIONSTRING` | `dashboard-analytics.md` |

Pump 1.18.0 also ships Kafka, Splunk, Datadog (DogStatsD), StatsD, Graylog, Syslog, Moesif,
InfluxDB, Timestream, SQS, Kinesis, Logz.io, Segment, Resurface and MongoDB pumps
(https://github.com/TykTechnologies/tyk-pump/blob/v1.18.0/pumps/init.go).

## Elasticsearch and OpenSearch

- `META_VERSION` accepts `3`, `5`, `6` or `7`. With `8`, Pump 1.18.0 exits at start:
  `level=fatal msg="Invalid version: Only 3, 5, 6, 7 are valid values for this field" prefix=elasticsearch-pump`.
  For Elasticsearch 8 or later, put a log shipper (stdout or Kafka pump plus Logstash or
  Filebeat) in between. Without `META_VERSION` the pump assumes 3
  (https://github.com/TykTechnologies/tyk-pump/blob/v1.18.0/pumps/elasticsearch.go).
- Set `META_BULKCONFIG_FLUSHINTERVAL`. Without it, ten requests give ten stdout records and
  zero Elasticsearch documents after 20 seconds. The batch waits for 1,000 records or 5 MB;
  the waiting records are written when the Pump stops.
- A document: `@timestamp`, `api_id`, `api_name`, `alias`, `api_key`, `org_id`, `http_method`,
  `request_uri` (template), `request_uri_full`, `response_code`, `request_time_ms`, `tags`,
  `ip_address`. `META_EXTENDEDSTATS=true` adds raw request, raw response and user agent.

## Aggregate versus raw

| | Raw records | Aggregates |
| --- | --- | --- |
| Pumps | `sql`, `mongo`, `elasticsearch`, `stdout`, ... | `sql_aggregate`, `mongo-pump-aggregate` |
| Stored | one row per request | counts and latency per hour (per minute optional) per API, key, endpoint, status, tag |
| Feeds | Dashboard Log Browser, your search | Dashboard traffic charts with `enable_aggregate_lookups` |
| Volume | grows with traffic | grows with the number of dimension values |

With raw rows deleted, the Dashboard's usage chart still shows the hits once it reads
aggregates (`dashboard-analytics.md`). For the dimension list and the per-minute option, see
https://tyk.io/docs/api-management/dashboard-analytics/control-plane-pumps.

## Detailed recording and its cost

Detailed recording puts the whole request and response (headers and body, base64) into
`raw_request` and `raw_response`.

- Per API: `server.detailedActivityLogs.enabled: true`. Turned on for one API, its records
  hold the request and response (for example a 196-byte request and a 1,292-byte response);
  other APIs stay empty.
- For the whole Gateway: `analytics_config.enable_detailed_recording` (`TYK_GW_ANALYTICSCONFIG_ENABLEDETAILEDRECORDING`).
- The `Authorization` header is stored as `***`. Everything else is stored as sent, including
  any credential or personal data the upstream puts in its response body.
- Each record grows by the size of the exchange, in Redis, in the Pump and in every back end.
  Turn it on per API, for a short time, for debugging.
