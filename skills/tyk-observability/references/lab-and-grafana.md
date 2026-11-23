# The observability lab and the Grafana dashboard

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), Tyk Pump 1.18.0, OpenTelemetry Collector Contrib 0.162.0, Grafana Tempo 2.10.8, Prometheus 3.15.0, Grafana 13.3.0, go-httpbin 2.26.0, Elasticsearch 7.17.28

`assets/lab/` is a Docker Compose stack with the open source Gateway, Redis, an upstream, Tyk
Pump (stdout, CSV and Prometheus pumps), an OpenTelemetry Collector, Tempo, Prometheus and
Grafana. It needs no licence. The commands below work with the versions above.

## Start it

Copy `assets/lab/` to a folder of your own (outside the plugin), then:

```bash
SKILL=<this skill's folder>
cp -R "$SKILL/assets/lab" ./tyk-obs-lab && cd ./tyk-obs-lab
python3 - <<'EOF'
import re, secrets
s = open(".env.example").read()
s = re.sub(r"^SECRET=$", "SECRET=" + secrets.token_hex(16), s, flags=re.M)
s = re.sub(r"^GRAFANA_ADMIN_PASSWORD=$", "GRAFANA_ADMIN_PASSWORD=" + secrets.token_hex(12), s, flags=re.M)
open(".env", "w").write(s)
EOF
chmod 600 .env                  # change the ports in .env if they are taken
docker compose -p tyk-obs-lab up -d
set -a; . ./.env; set +a; GW=http://127.0.0.1:${GATEWAY_PORT}
for f in apis/orders.oas.json apis/untracked.oas.json; do
  curl -s -X POST $GW/tyk/apis/oas -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @$f; echo
done
#   {"key":"orders","status":"ok","action":"added"}
#   {"key":"untracked","status":"ok","action":"added"}
curl -s $GW/tyk/reload/group -H "x-tyk-authorization: $SECRET"
#   {"status":"ok","message":""}
```

Create a key for `partner-a` (the response's `key` is the credential; keep it out of logs):

```bash
KEY=$(curl -s -X POST $GW/tyk/keys -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' \
  --data '{"alias":"partner-a","org_id":"default","rate":1000,"per":1,"quota_max":-1,
           "meta_data":{"cost_centre":"cc-1001"},
           "access_rights":{"orders":{"api_id":"orders","api_name":"Orders API","versions":["Default"]}}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
```

Send traffic:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $GW/orders-api/anything/orders/42 -H "Authorization: $KEY"   # 200
curl -s -o /dev/null -w '%{http_code}\n' $GW/orders-api/anything/orders                              # 401
curl -s -o /dev/null -w '%{http_code}\n' $GW/orders-api/status/503 -H "Authorization: $KEY"           # 503
curl -s -o /dev/null -w '%{http_code}\n' $GW/untracked/anything/x                                     # 200
```

Where to look:

| Signal | Command | Expect |
| --- | --- | --- |
| Trace | `curl -s http://127.0.0.1:$TEMPO_PORT/api/traces/<X-Tyk-Trace-Id>` | spans as in `traces.md` |
| Metrics | `curl -s --get http://127.0.0.1:$PROMETHEUS_PORT/api/v1/query --data-urlencode 'query=tyk_api_requests_total'` | series for `orders`, none for `untracked` |
| Access logs | `docker compose -p tyk-obs-lab logs tyk-gateway \| grep access-log` | one line per request, both APIs |
| Analytics records | `docker compose -p tyk-obs-lab logs tyk-pump \| grep analytics_record` | records for `orders` only |
| Readiness | `curl -s $GW/ready` | `200 {"status":"pass",...}` |

Custom metrics: write `gateway-metrics.env` with one line,
`TYK_GW_OPENTELEMETRY_METRICS_APIMETRICS=<JSON on one line>`, for example

```bash
printf 'TYK_GW_OPENTELEMETRY_METRICS_APIMETRICS=%s\n' \
  "$(python3 -c 'import json;print(json.dumps(json.load(open("api-metrics-chargeback.json")),separators=(",",":")))')" > gateway-metrics.env
docker compose -p tyk-obs-lab up -d --force-recreate tyk-gateway
```

(`api-metrics-chargeback.json` is in the skill's `assets/`; copy it next to the lab.)

Elasticsearch 7: `docker compose -p tyk-obs-lab -f compose.yaml -f compose.elasticsearch.yaml up -d`
adds Elasticsearch and an Elasticsearch pump to the same Pump; read documents with
`curl -s "http://127.0.0.1:$ES_PORT/tyk_analytics/_search?size=1"`.

Stop and delete everything: `docker compose -p tyk-obs-lab down -v`.

The Gateway image runs as uid 65532; the lab's `apps-init` service gives the API folder to that
user, otherwise `POST /tyk/apis/oas` answers `{"status":"error","message":"file object creation failed, write error"}`.

## The Grafana dashboard

`assets/lab/grafana/dashboards/tyk-gateway-otel.json` ("Tyk Gateway (OpenTelemetry)", uid
`tyk-gateway-otel`). The lab provisions it; for another Grafana, import it (Dashboards > New >
Import, or `POST /api/dashboards/db` with `{"dashboard": <file>, "overwrite": true}`, which
answers `"status":"success"`) and pick your Prometheus data source in the `Prometheus`
variable.

Panels: APIs and policies loaded, requests per second, 5xx share, requests by API and by status
code, p95 latency by API with exemplars, p95 Gateway time against upstream time, errors by
response flag, memory and goroutines, configuration reloads. Variables: data source, service
name (`job`) and API id.

It assumes the default instrument names and labels and a scrape with `honor_labels: true`.
Check it against your Prometheus before you hand it over:

```bash
python3 "$SKILL/scripts/check_dashboard_queries.py" grafana/dashboards/tyk-gateway-otel.json \
  --prometheus http://127.0.0.1:$PROMETHEUS_PORT
#   ok    panel 1 'APIs loaded': 1 series
#   ...
#   ok    panel 11 'Configuration reloads': 1 series      (exit status 0)
```

An `EMPTY` panel means a metric or label name your setup does not export: a custom
`api_metrics` list without the defaults, a scrape without `honor_labels`, or too little data.
Rate panels need two scrapes inside the window: on a fresh lab, seven of the eleven panels were
empty 25 seconds after the first requests and all were `ok` a minute later.
