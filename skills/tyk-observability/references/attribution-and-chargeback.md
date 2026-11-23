# Per-consumer and per-API attribution (chargeback)

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), Tyk Pump 1.18.0, Prometheus 3.15.0

Page: https://tyk.io/docs/api-management/metrics/custom-metrics

## Where each attribute can come from

| Attribute | Best source | How |
| --- | --- | --- |
| Consumer (application, partner) | key alias | `session` dimension `alias` |
| Developer Portal app or organisation | session tags the Portal sets | `session` dimension `portal_app` or `portal_org` |
| Cost centre, business unit, tenant on the key | key `meta_data` | copy it into a request header with `$tyk_meta.<field>`, then a `header` dimension |
| Owning team, tier or criticality of the API | the API's `pluginConfig.data.value` (config data) | `config_data` dimension |
| Endpoint | a tracked endpoint (`trackEndpoint` on the operation) | `metadata` dimension `endpoint` |
| Tenant from a JWT | a claim | `context` dimension `jwt_claims_<claim>` (needs context variables on the API) |

Key `meta_data` is not a dimension source by itself; the header step carries it across.

## The recipe

1. The key carries the cost centre and an alias:

   ```json
   {"alias": "partner-a", "org_id": "default", "meta_data": {"cost_centre": "cc-1001"},
    "access_rights": {"orders": {"api_id": "orders", "api_name": "Orders API", "versions": ["Default"]}}}
   ```

2. The API (`assets/lab/apis/orders.oas.json`) copies it into a header, names its team and
   tracks one endpoint:

   ```json
   "middleware": {
     "global": {
       "trafficLogs": {"enabled": true},
       "pluginConfig": {"data": {"enabled": true, "value": {"team": "payments"}}},
       "transformRequestHeaders": {"enabled": true,
         "add": [{"name": "X-Cost-Centre", "value": "$tyk_meta.cost_centre"}]}
     },
     "operations": {"getOrder": {"trackEndpoint": {"enabled": true}}}
   }
   ```

3. The Gateway gets `assets/api-metrics-chargeback.json` (the four defaults plus
   `tyk.chargeback.requests.total` with `consumer`, `cost_centre`, `team`, `endpoint`,
   `tyk.api.id` and status code).

4. Result in Prometheus:

   ```text
   tyk_chargeback_requests_total{consumer="partner-a",cost_centre="cc-1001",team="payments",endpoint="/anything/orders/{id}",http_response_status_code="200",tyk_api_id="orders"} 2
   tyk_chargeback_requests_total{consumer="partner-a",cost_centre="cc-1001",team="payments",endpoint="other",http_response_status_code="200",tyk_api_id="orders"} 1
   tyk_chargeback_requests_total{consumer="anonymous",cost_centre="spoofed",team="payments",endpoint="other",http_response_status_code="401",tyk_api_id="orders"} 1
   ```

   Query by consumer: `sum by (consumer, cost_centre) (increase(tyk_chargeback_requests_total[30d]))`.

## What the lines show

- An authenticated caller that sends `X-Cost-Centre: spoofed` is still counted as `cc-1001`:
  the header transform overwrites the caller's value before the metric is recorded.
- A request refused before the transform runs (here a missing key, 401) keeps the header the
  caller sent. Attribute only successful traffic, or filter refusals out with
  `filters.status_codes: ["2xx", "3xx"]` or by status code in the query.
- `endpoint` is empty for paths that are not tracked; the `default` turns that into `other`.

## The same attribution in analytics records

Analytics records (Tyk Pump) already carry `alias`, `api_id`, `api_key` (hashed), `org_id`,
`oauth_id`, status, latency and tags. `trafficLogs.tagHeaders` turns a request header into a
tag: with `"tagHeaders": ["X-Team"]` and `X-Team: team-blue`, the record's tags are

```text
["x-team-team-blue", "trace-id-3ce864f0...", "key-0f9bdf9e3af66a13", "org-default", "api-orders"]
```

Use records when you need per-request evidence for a bill, metrics when you need totals.
