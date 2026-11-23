Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`), Tyk Dashboard 5.16.0 (`tykio/tyk-dashboard:v5.16.0`) and Redis 7.2

# Recipes

The output blocks show the output on Gateway 5.16.0, shortened to the lines that matter.
Recipes 1 to 4 use
the open source Gateway through the Gateway API; recipe 5 repeats the per-partner case through
the Dashboard API. On an existing deployment skip "Lab" and use your own Gateway URL, secret,
API id and organisation id.

`$SKILL` is this skill's folder.

## Lab: one or two open source Gateways

A throwaway stack for testing limits: Redis, a test upstream, one Gateway, and a second
Gateway on demand. For anything long-lived use the `tyk-deployment` skill.

```bash
LAB=${TYK_WORKSPACE:-$HOME/tyk-workspace}/limits/demos/limits-lab
mkdir -p "$LAB" && cp -R "$SKILL/assets/limits-lab/." "$LAB/" && cd "$LAB"
mkdir -p apps policies
cp .env.example .env
```

Edit `.env`: set `TYK_GATEWAY_VERSION` to the current tag (resolve it live with the
`tyk-facts` skill; the outputs below are from `v5.16.0`) and `TYK_GW_SECRET` to the output of
`openssl rand -hex 16`. The Gateway image runs as uid 65532 and writes API and policy files
into `apps/` and `policies/`, so on a Linux host make those two folders writable for that uid.

```bash
docker compose up -d && sleep 5
GW=http://localhost:8080      # GATEWAY_1_PORT in .env
GW2=http://localhost:8081     # GATEWAY_2_PORT in .env, used from recipe 2
curl -s $GW/hello
# {"status":"pass","version":"5.16.0","description":"Tyk GW"}

SECRET=$(grep ^TYK_GW_SECRET= .env | cut -d= -f2)
gw() { curl -s -X "$1" "$GW/tyk$2" \
         -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' "${@:3}"; }
burst() { python3 "$SKILL/scripts/burst_test.py" "$@"; }
newkey() { gw POST /keys -d "$1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["key"])'; }

gw POST /apis/oas -d @orders.oas.json
# {"key":"orders","status":"ok","action":"added"}
gw GET /reload/group
# {"status":"ok","message":""}
```

The lab's `tyk.conf` sets `http_server_options` as the Gateway image does (strict routes,
prefix and suffix matching all `true`: exact path matching); `objects-and-merge-rules.md`
shows what changes in the other modes. `orders.oas.json` is a Tyk OAS API with id `orders`,
organisation `default`, listen path `/orders/`, an auth token in the `Authorization` header
and two operations, `GET /anything/orders` and `GET /anything/reports`. `burst` and `newkey`
are shell functions because zsh does not split a command held in a variable.

## 1. Two partners, two limits, one API

Partner A may make 5 requests per minute and partner B 10, on the same API.

```bash
gw POST /policies -d '{"id":"partner-a","name":"Partner A","org_id":"default","active":true,
  "rate":5,"per":60,"quota_max":-1,
  "access_rights":{"orders":{"api_id":"orders","api_name":"Orders","versions":["Default"]}}}'
# {"key":"partner-a","status":"ok","action":"added"}
gw POST /policies -d '{"id":"partner-b","name":"Partner B","org_id":"default","active":true,
  "rate":10,"per":60,"quota_max":-1,
  "access_rights":{"orders":{"api_id":"orders","api_name":"Orders","versions":["Default"]}}}'
# {"key":"partner-b","status":"ok","action":"added"}
gw GET /reload/group && sleep 2

export KEY_A=$(newkey '{"org_id":"default","alias":"partner-a","apply_policies":["partner-a"]}')
export KEY_B=$(newkey '{"org_id":"default","alias":"partner-b","apply_policies":["partner-b"]}')

burst --url $GW/orders/anything/orders --header-env Authorization=KEY_A \
      --count 12 --window 60 --expect-ok 5 --expect-reject 429
burst --url $GW/orders/anything/orders --header-env Authorization=KEY_B \
      --count 12 --window 60 --expect-ok 10 --expect-reject 429
```

```text
sequence: 200 200 200 200 200 429 429 429 429 429 429 429
http://localhost:8080/orders/anything/orders: 5 x 200, 7 x 429
successful: 5   rejected: 7   elapsed: 0.059 s
first rejection: request 6, HTTP 429, body { "error": "Rate Limit Exceeded" }
PASS

sequence: 200 200 200 200 200 200 200 200 200 200 429 429
http://localhost:8080/orders/anything/orders: 10 x 200, 2 x 429
successful: 10   rejected: 2   elapsed: 0.026 s
first rejection: request 11, HTTP 429, body { "error": "Rate Limit Exceeded" }
PASS
```

The policy, the key and the API all carry the same organisation id (`default`). If the key
call returns `Failed to create key, ensure security settings are correct.`, read the table in
`creating-keys-and-policies.md`: the usual causes are a missing `org_id` or a missing reload.

## 2. The same limit across two Gateways

```bash
docker compose --profile two up -d tyk-gateway-2 && sleep 10
curl -s $GW2/hello

export KEY_A2=$(newkey '{"org_id":"default","alias":"partner-a","apply_policies":["partner-a"]}')
burst --url $GW/orders/anything/orders --url $GW2/orders/anything/orders \
      --header-env Authorization=KEY_A2 --count 12 --window 60 --expect-ok 5 --expect-reject 429
docker compose exec redis redis-cli --scan --pattern 'rate-limit-*'
python3 "$SKILL/scripts/which_limiter.py" --rate 5 --per 60 --gateways 2
```

```text
sequence: 200 200 200 200 200 429 429 429 429 429 429 429
http://localhost:8080/orders/anything/orders: 3 x 200, 3 x 429
http://localhost:8081/orders/anything/orders: 2 x 200, 4 x 429
successful: 5   rejected: 7   elapsed: 0.062 s
PASS
rate-limit-2be5817c
limiter: redis-rolling-window
```

Five in total across both Gateways, through one Redis entry. The `sleep 10` matters: the
Gateways need a moment to see each other, and until then each counts on its own (a key can get
10 instead of 5 at the moment the second Gateway comes up, and 5 from about two seconds later). Use a
fresh key for this test so the count starts from zero. A limit above `drl_threshold` ×
Gateways requests per second behaves differently: read `limiters-and-counters.md` before
promising one shared counter.

## 3. A tighter limit on one endpoint

Two different tools.

**For one consumer:** an `endpoints` entry in that consumer's policy. Partner C has 100 per
minute overall and 2 per minute on `GET /anything/orders`.

```bash
gw POST /policies -d '{"id":"partner-c","name":"Partner C","org_id":"default","active":true,
  "rate":100,"per":60,"quota_max":-1,
  "access_rights":{"orders":{"api_id":"orders","api_name":"Orders","versions":["Default"],
    "endpoints":[{"path":"^/anything/orders$","methods":[{"name":"GET","limit":{"rate":2,"per":60}}]}]}}}'
# {"key":"partner-c","status":"ok","action":"added"}
```

**For every caller together:** a `rateLimit` on the operation in the Tyk OAS definition.
`GET /anything/reports` (operation id `getReports`) is capped at 3 per minute in total. `per`
is a duration string here.

```bash
python3 - <<'EOF'
import json
d = json.load(open("orders.oas.json"))
d["x-tyk-api-gateway"]["middleware"] = {
    "operations": {"getReports": {"rateLimit": {"enabled": True, "rate": 3, "per": "1m"}}}}
json.dump(d, open("orders-capped.oas.json", "w"), indent=2)
EOF
gw PUT /apis/oas/orders -d @orders-capped.oas.json
# {"key":"orders","status":"ok","action":"modified"}
gw GET /reload/group && sleep 2

export KEY_C=$(newkey '{"org_id":"default","alias":"partner-c","apply_policies":["partner-c"]}')
burst --url $GW/orders/anything/orders --header-env Authorization=KEY_C \
      --count 4 --window 60 --expect-ok 2 --expect-reject 429
burst --url $GW/orders/anything/reports --header-env Authorization=KEY_C \
      --count 5 --window 60 --expect-ok 3 --expect-reject 429
```

```text
sequence: 200 200 429 429
first rejection: request 3, HTTP 429, body { "error": "Rate Limit Exceeded" }
PASS
sequence: 200 200 200 429 429
first rejection: request 4, HTTP 429, body { "error": "API Rate Limit Exceeded" }
PASS
```

The two bodies differ: the first is partner C's own limit, the second is the shared cap. After
the second burst the cap is used up for every caller for the rest of the minute. Writing the
rest of the definition is the `tyk-api-definitions-routing` skill.

## 4. Plans: Free and Pro

One policy per plan; a key is on a plan by naming its policy; an upgrade changes the key's
policy. The numbers are small so the test is quick.

```bash
gw POST /policies -d '{"id":"plan-free","name":"Free","org_id":"default","active":true,
  "rate":5,"per":10,"quota_max":8,"quota_renewal_rate":60,
  "access_rights":{"orders":{"api_id":"orders","api_name":"Orders","versions":["Default"]}}}'
gw POST /policies -d '{"id":"plan-pro","name":"Pro","org_id":"default","active":true,
  "rate":20,"per":10,"quota_max":100,"quota_renewal_rate":3600,
  "access_rights":{"orders":{"api_id":"orders","api_name":"Orders","versions":["Default"]}}}'
gw GET /reload/group && sleep 2

export KEY_P=$(newkey '{"org_id":"default","alias":"example-corp-app","apply_policies":["plan-free"]}')
burst --url $GW/orders/anything/orders --header-env Authorization=KEY_P \
      --count 7 --window 10 --expect-ok 5 --expect-reject 429      # the rate: 5 per 10 s
sleep 10
burst --url $GW/orders/anything/orders --header-env Authorization=KEY_P \
      --count 5 --window 10 --expect-ok 3 --expect-reject 403      # the quota: 8 per minute

gw PUT "/keys/$KEY_P" -d '{"org_id":"default","alias":"example-corp-app","apply_policies":["plan-pro"]}'
# {"key":"…","status":"ok","action":"modified"}
sleep 11
burst --url $GW/orders/anything/orders --header-env Authorization=KEY_P \
      --count 22 --window 10 --expect-ok 20 --expect-reject 429    # Pro: 20 per 10 s
```

```text
sequence: 200 200 200 200 200 429 429
first rejection: request 6, HTTP 429, body { "error": "Rate Limit Exceeded" }
PASS
sequence: 200 200 200 403 403
headers on the first response: {'X-RateLimit-Limit': '8', 'X-RateLimit-Remaining': '2', 'X-RateLimit-Reset': '1794926642'}
first rejection: request 4, HTTP 403, body { "error": "Quota exceeded" }
PASS
sequence: 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 429 429
headers on the first response: {'X-RateLimit-Limit': '100', 'X-RateLimit-Remaining': '99', 'X-RateLimit-Reset': '1794930204'}
PASS
```

- The `sleep 11` after the upgrade matters: a Gateway caches a key for 10 seconds. A burst
  sent straight after the `PUT` is rejected under the Free plan, because the Gateway is still
  using the Free key it has cached.
- The upgrade resets the quota (`Remaining: 99` of 100). Add `?suppress_reset=1` to the `PUT`
  to keep the count.
- To change what a plan allows, change the policy. Read "When a change reaches traffic" in
  `objects-and-merge-rules.md` first: on a single open source Gateway a changed rate needs a
  new `last_updated` in the policy file.
- To give one plan different limits per API, or to split access, rate and quota into separate
  policies, use `per_api` or partitioned policies (`objects-and-merge-rules.md`).

## 5. Two partners through the Dashboard API

On a Dashboard-managed stack (the `tyk-demo-builder` skill builds one; it needs a Dashboard
licence, see the `tyk-facts` skill). `DASHBOARD_API_KEY` is a Dashboard user's API key (a
builder bundle keeps it in `.runtime/runtime.env`), `DASH` the Dashboard URL and `GW` the
Gateway URL. Keys and API keys stay in environment variables; do not print them.

The API first, if it is not there yet. It is the lab's definition without `orgId` (the
Dashboard sets the organisation) and with an upstream the Gateway can reach; `demo-upstream`
is the builder bundle's test upstream.

```bash
cp "$SKILL/assets/limits-lab/orders.oas.json" . && python3 - <<'EOF'
import json
d = json.load(open("orders.oas.json"))
x = d["x-tyk-api-gateway"]
x["info"].pop("orgId")
x["upstream"]["url"] = "http://demo-upstream:8080"
json.dump(d, open("orders.dash.oas.json", "w"), indent=2)
EOF

dash() { curl -s -X "$1" "$DASH$2" \
           -H "authorization: $DASHBOARD_API_KEY" -H 'Content-Type: application/json' "${@:3}"; }
dashkey() { dash POST /api/keys -d "$1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["key_id"])'; }

dash POST /api/apis/oas -d @orders.dash.oas.json
# {"Status":"OK","Message":"API created","Meta":"<database id>","ID":"orders"}
sleep 3
```

Then the policies and keys:

```bash
dash POST /api/portal/policies/ -d '{"id":"partner-a","name":"Partner A","active":true,
  "rate":5,"per":60,"quota_max":-1,
  "access_rights":{"orders":{"api_id":"orders","api_name":"Orders","versions":["Default"]}}}'
# {"Status":"OK","Message":"<database id>","Meta":null}
dash POST /api/portal/policies/ -d '{"id":"partner-b","name":"Partner B","active":true,
  "rate":10,"per":60,"quota_max":-1,
  "access_rights":{"orders":{"api_id":"orders","api_name":"Orders","versions":["Default"]}}}'
sleep 3

export KEY_A=$(dashkey '{"alias":"partner-a","apply_policies":["partner-a"]}')
export KEY_B=$(dashkey '{"alias":"partner-b","apply_policies":["partner-b"]}')
burst --url $GW/orders/anything/orders --header-env Authorization=KEY_A \
      --count 12 --window 60 --expect-ok 5 --expect-reject 429
burst --url $GW/orders/anything/orders --header-env Authorization=KEY_B \
      --count 12 --window 60 --expect-ok 10 --expect-reject 429
```

No `org_id` and no reload: the Dashboard fills in the organisation and tells the Gateways.
The results are the same as recipe 1: five 200s then 429 for partner A, ten for partner B,
body `{ "error": "Rate Limit Exceeded" }`.

## Clean up the lab

```bash
cd "$LAB" && docker compose --profile two down -v
```
