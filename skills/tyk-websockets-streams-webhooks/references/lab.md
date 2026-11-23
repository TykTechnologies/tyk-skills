# The streams and events lab

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`), Redis 7.2, Apache Kafka 4.4.0, go-httpbin 2.25.0

A throwaway stack to prove each behaviour before it reaches a real environment: the open source
Gateway (WebSocket, SSE, webhooks) and the Enterprise Edition Gateway with Tyk Streams switched on,
side by side, plus Kafka, an SSE and HTTP echo, a WebSocket echo and a webhook receiver. No
Dashboard. Tyk Streams is an Enterprise Edition feature, and the Enterprise Edition image is
commercially licensed; whether and how you may run it is a licensing question for the `tyk-facts`
skill. For a Dashboard-managed demo use the `tyk-demo-builder` skill
(module `streams`); for an installation, the `tyk-deployment` skill.

## Start it

Copy the asset folder out of this skill into your workspace and run everything there:

```bash
SKILL=<this skill's folder>
mkdir -p ~/tyk-workspace && cp -R "$SKILL/assets/streams-lab" ~/tyk-workspace/streams-lab && cd ~/tyk-workspace/streams-lab
cp "$SKILL/scripts/webhook_receiver.py" .
mkdir -p apps apps-ee policies && echo '{}' > policies/policies.json
python3 -c 'import secrets; open(".env","w").write("TYK_GATEWAY_VERSION=v5.16.0\nTYK_GW_SECRET=%s\n" % secrets.token_hex(16))'
chmod 600 .env
docker compose up -d          # the WebSocket echo installs its library on first start: allow 20 s
set -a; . ./.env; set +a
GW=http://localhost:8080; GWE=http://localhost:8081; HOOKS=http://localhost:9000; SECRET=$TYK_GW_SECRET
curl -s $GW/hello; curl -s $GWE/hello      # {"status":"pass","version":"5.16.0","description":"Tyk GW"}
```

Both Gateway images run as user `65532` (`docker image inspect tykio/tyk-gateway:v5.16.0
--format '{{.Config.User}}'`) and write each API loaded through the Gateway API into `apps` or
`apps-ee`. If loading answers `{"status":"error","message":"file object creation failed, write
error"}`, that folder is not writable for user 65532 (for example a bind-mounted folder owned by
your own user with mode `755`). For the lab, `chmod 777 apps apps-ee` and load again; the load
then answers `200 added`.

Ports move with `GATEWAY_PORT`, `GATEWAY_EE_PORT` and `HOOKS_PORT` in `.env`. If Docker answers
`all predefined address pools have been fully subnetted`, add a `compose.override.yaml` that
names the default network and gives it a free subnet (`networks: default: name: ... ipam: config:
[{subnet: ...}]`).

## Load the APIs and keys

```bash
load() { curl -s -X POST "$1/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" --data @"definitions/$2.json"; echo; }
for a in realtime chat alerts; do load $GW $a; done
for a in order-events-in order-events-out; do load $GWE $a; done
curl -s $GW/tyk/reload/group -H "x-tyk-authorization: $SECRET"; curl -s $GWE/tyk/reload/group -H "x-tyk-authorization: $SECRET"
sleep 3   # the reload returns at once; until the APIs have loaded, their paths answer 404 Not Found
key() {  # key <gateway> <partner> <rate per 60 s> <api id>...
  local gw=$1 partner=$2 rate=$3; shift 3
  local rights=$(python3 -c 'import json,sys; print(json.dumps({a: {"api_id": a, "api_name": a, "versions": ["Default"]} for a in sys.argv[1:]}))' "$@")
  curl -s -X POST "$gw/tyk/keys" -H "x-tyk-authorization: $SECRET" \
    -d "{\"org_id\":\"default\",\"rate\":$rate,\"per\":60,\"allowance\":$rate,\"quota_max\":-1,\"meta_data\":{\"partner\":\"$partner\"},\"access_rights\":$rights}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])'; }
KEY=$(key $GW partner-a 100 realtime chat)
SLOW=$(key $GW partner-a 2 alerts)
KA=$(key $GWE partner-a 100 order-events-in order-events-out)
KB=$(key $GWE partner-b 100 order-events-out)
```

The definitions are in `assets/streams-lab/definitions/`; `scripts/check_events_def.py` reports
`0 error(s), 0 warning(s), 0 note(s)` for each.

## The main recipe

```bash
# 1. WebSocket through the open source Gateway, key in the query string
python3 "$SKILL/scripts/ws_probe.py" "ws://localhost:8080/chat/room/1?access_token=$KEY" --send headers --send hi
# 2. Server-Sent Events, one event a second
curl -s -N -H "Authorization: $KEY" "$GW/realtime/sse?count=3&duration=3s&delay=0"
# 3. Kafka to two clients (one per partner), then publish through HTTP. Create the topic
#    first: a consumer that starts before its topic exists misses the first messages.
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
  --create --if-not-exists --topic orders
curl -s -N --max-time 15 -H "Authorization: $KA" $GWE/order-events/sse > a.txt &
python3 "$SKILL/scripts/ws_probe.py" ws://localhost:8081/order-events/ws -H "Authorization: $KB" --listen 15 > b.txt &
sleep 6
for i in 1 2; do curl -s -w '%{http_code}\n' -X POST $GWE/order-events-in/publish -H "Authorization: $KA" -d "{\"order\":$i}"; done
sleep 10; cat a.txt b.txt
# 4. A webhook on a rate limit
curl -s -X DELETE $HOOKS/_received >/dev/null
for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code} ' -H "Authorization: $SLOW" $GW/alerts/get; done; sleep 2
curl -s $HOOKS/_received
```

Step 3 prints `200` twice, and both `a.txt` (SSE, `data: {"order":1,"partner":"partner-a"}`) and
`b.txt` (WebSocket frames) hold both orders, because each partner has its own consumer group. If a
consumer printed `{"error": "invalid route handler"}`, that was the first request for its
configuration; start it again. What you should see is in the reference for each part: `websocket-and-sse.md`,
`streams-definitions.md`, `gateway-events-and-webhooks.md`. Right after the reload the stream paths
can answer `404 Not Found` for about a second (`streams-operations.md`); retry.

## Kafka from the command line

```bash
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic orders --from-beginning --timeout-ms 5000
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --list
docker compose stop kafka     # broker outage; `docker compose start kafka` to end it
```

## Tear down

```bash
docker compose down -v
```
