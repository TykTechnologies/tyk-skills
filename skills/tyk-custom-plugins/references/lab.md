# The plugin lab

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), `tykio/tyk-plugin-compiler:v5.16.0-ng`, Redis 7.2, go-httpbin 2.25.0

A throwaway stack that proves every kind of plugin on the open source Gateway, with no licence.
For anything long-lived install with the `tyk-deployment` skill; for a Dashboard demo use the
`tyk-demo-builder` skill.

## Start it

Copy the asset folder out of this skill into the user's workspace, then work there. Set the
Gateway version to the one you target (resolve it with the `tyk-facts` skill; the compiler tag
must match it). The Gateways run as your own user (`LAB_USER`), so they can write to the folders
you created without opening their permissions:

```bash
LAB="${TYK_WORKSPACE:-$HOME/tyk-workspace}/<project>/demos/plugin-lab"
mkdir -p "$(dirname "$LAB")" && cp -R "$SKILL/assets/plugin-lab" "$LAB" && cd "$LAB"
mkdir -p apps policies middleware middleware-python bundles plugins/go
python3 - <<'EOF'
import os, secrets
open(".env", "w").write(f"TYK_GATEWAY_VERSION=v5.16.0\nTYK_GW_SECRET={secrets.token_hex(16)}\n"
                        f"LAB_USER={os.getuid()}:{os.getgid()}\n")
EOF
chmod 600 .env
cid=$(docker create tykio/tyk-gateway:v5.16.0) && docker cp "$cid:/opt/tyk-gateway/coprocess/proto/." grpc/ && docker rm "$cid"
docker compose up -d --build
set -a; . ./.env; set +a; GW=http://localhost:8080; SECRET=$TYK_GW_SECRET
curl -s $GW/hello            # {"status":"pass","version":"5.16.0","description":"Tyk GW"}
```

Ports move with `GATEWAY_PORT` and `PYTHON_GATEWAY_PORT` in `.env`.

## Build what needs building

```bash
docker run --rm -v "$PWD/go:/plugin-source" tykio/tyk-plugin-compiler:${TYK_GATEWAY_VERSION}-ng demo.so
cp go/demo_${TYK_GATEWAY_VERSION}_linux_*.so plugins/go/
bundle() { (cd "$1" && docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/..:/work" -w "/work/$1" \
  --entrypoint /opt/tyk-gateway/tyk "tykio/tyk-gateway:${TYK_GATEWAY_VERSION}" bundle build -y -o "../bundles/$2"); }
bundle bundle-js js-v1.zip
bundle bundle-python py-v1.zip
```

## Load the APIs and keys

```bash
for f in apis/*.json; do
  curl -s -X POST "$GW/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" \
    -H 'Content-Type: application/json' --data @"$f"; echo
done
curl -s "$GW/tyk/reload/group" -H "x-tyk-authorization: $SECRET"
key() { curl -s -X POST "$GW/tyk/keys" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' \
  -d "{\"alias\":\"partner-a\",\"org_id\":\"default\",\"rate\":2,\"per\":60,\"quota_max\":-1,
       \"access_rights\":{\"$1\":{\"api_id\":\"$1\",\"api_name\":\"$1\",\"versions\":[\"Default\"]}}}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])'; }
KJS=$(key js-hooks); KGO=$(key go-hooks); KGRPC=$(key grpc-hooks)
```

`python-hooks.json` also loads on the stock Gateway; its requests get 500 there (no Python), which
is the expected proof of that limitation.

## What each API proves

| Call | Expect |
| --- | --- |
| `curl -s $GW/js-hooks/headers -H "Authorization: $KJS"` | Upstream echo with `X-Stage-Pre: alias=none;builtin=no`, `X-Stage-Post-Auth: alias=partner-a;builtin=no`, `X-Stage-Post: alias=partner-a;builtin=yes`; response header `X-Stage-Response` |
| The same, a third time within a minute | 429; `docker compose exec redis redis-cli get jsvm-store:order:post` stays where it was while `order:pre` and `order:postauth` go up |
| `curl -s $GW/go-hooks/headers -H "Authorization: $KGO"` | `X-Go-Alias: partner-a`, `X-Go-Region: eu`, `X-Go-Api: go-hooks`; response header `X-Go-Response: stamped` |
| Add `-H 'X-Block: 1'` to the Go or gRPC call | `403 {"code":"PARTNER_BLOCKED"}` from the pre hook |
| `curl -s $GW/grpc-hooks/headers -H "Authorization: $KGRPC"` | `X-Grpc-Pre: ran`, `X-Grpc-Post: alias=partner-a;config={"region":"eu"}`, response header `X-Grpc-Response` |
| `curl -s -H 'X-Partner-Token: tok-a-123' $GW/go-auth/headers` three times, then `tok-b-456` (same for `js-auth`, `grpc-auth`) | 200, 200, 429, then 200: one counter per partner and API |
| `curl -s $GW/virtual/aggregate` | `{"upstreamStatus":200,"upstreamBody":{"uuid":"..."}}` |
| `curl -s $GW/bundle-js/headers` | `X-Bundle: js-v1` |

## Python

```bash
docker compose --profile python up -d --build tyk-gateway-python
KPY=$(key python-hooks)
curl -s http://localhost:8081/python-hooks/headers -H "Authorization: $KPY"   # X-Py-Pre, X-Py-Post
```

The Python Gateway shares the apps folder and Redis, so the same APIs and keys work there; it
unpacks bundles into its own `middleware-python` folder.

## Tear down

```bash
docker compose --profile python down -v
```
