# The authentication lab

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), Redis 7.2, Keycloak 26.8.1, Ory Hydra v26.2.0

A throwaway stack to prove each method before it reaches a real environment. Open source
Gateway only: no licence. The examples in this skill assume it. For anything
long-lived, install with the `tyk-deployment` skill; for a Dashboard-managed demo, the
`tyk-demo-builder` skill.

## Start it

Copy the asset folder **out of this skill** into your workspace, then run everything there
(it writes certificates, keys and API files beside itself):

```bash
cp -R "$SKILL/assets/auth-lab" ./auth-lab && cd ./auth-lab
mkdir -p apps policies middleware jwks
python3 - <<'EOF'
import secrets
open(".env", "w").write(
    "TYK_GATEWAY_VERSION=v5.16.0\n"
    f"TYK_GW_SECRET={secrets.token_hex(16)}\n"
    f"KC_ADMIN_PASSWORD={secrets.token_hex(12)}\n"
    f"HYDRA_SYSTEM_SECRET={secrets.token_hex(24)}\n")
EOF
chmod 600 .env
./make_certs.sh                       # CA, server and client test certificates in ./certs
echo '{"keys": []}' > jwks/jwks.json
docker compose up -d                  # Redis, upstream, JWKS server, two Gateways
docker compose --profile idp up -d    # add Keycloak and Hydra when needed
set -a; . ./.env; set +a
GW=http://localhost:8080; GWS=https://localhost:8443; SECRET=$TYK_GW_SECRET
curl -s $GW/hello                     # {"status":"pass","version":"5.16.0",...}
curl -s --cacert certs/ca.crt $GWS/hello
```

Ports can be moved with `GATEWAY_PORT`, `GATEWAY_TLS_PORT`, `KEYCLOAK_PORT`,
`HYDRA_PUBLIC_PORT` and `HYDRA_ADMIN_PORT` in `.env`; the examples in this skill use the
defaults. Both Gateways read the same `apps` folder and Redis: an API created through
`$GW/tyk/apis/oas` followed by `GET $GW/tyk/reload/group` is live on both.

## Load an API, a policy, a key

```bash
curl -s -X POST "$GW/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @api.json
curl -s -X POST "$GW/tyk/policies" -H "x-tyk-authorization: $SECRET" -H 'Content-Type: application/json' --data @policy.json
curl -s "$GW/tyk/reload/group" -H "x-tyk-authorization: $SECRET"
```

Upstream URL inside the lab: `http://upstream:8080` (it echoes the request, so you can see
which credentials reached it). Policies and keys use org id `default`, as the API's
`info.orgId` must. The `tyk-rate-limits-keys-policies` skill has the full key and policy calls.

## Keycloak: a realm with two machine clients

```bash
KC=http://localhost:8180
ADMIN=$(curl -s -d grant_type=password -d client_id=admin-cli -d username=admin \
  --data-urlencode "password=$KC_ADMIN_PASSWORD" "$KC/realms/master/protocol/openid-connect/token" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
kc() { curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' "$@"; }
kc -X POST "$KC/admin/realms" -d '{"realm": "example", "enabled": true}'                          # 201
for s in orders:read orders:write; do
  kc -X POST "$KC/admin/realms/example/client-scopes" \
    -d "{\"name\": \"$s\", \"protocol\": \"openid-connect\", \"attributes\": {\"include.in.token.scope\": \"true\"}}"
done
for c in partner-a:orders:read partner-b:orders:write; do
  id=${c%%:*}; scope=${c#*:}
  sec=$(python3 -c 'import secrets; print(secrets.token_hex(16))'); echo "$id=$sec" >> kc-clients.env
  kc -X POST "$KC/admin/realms/example/clients" -d "{\"clientId\": \"$id\", \"secret\": \"$sec\",
    \"publicClient\": false, \"serviceAccountsEnabled\": true, \"standardFlowEnabled\": false,
    \"protocolMappers\": [{\"name\": \"aud\", \"protocol\": \"openid-connect\", \"protocolMapper\": \"oidc-audience-mapper\",
      \"config\": {\"included.custom.audience\": \"orders-api\", \"access.token.claim\": \"true\"}}]}"
  cid=$(curl -s -H "Authorization: Bearer $ADMIN" "$KC/admin/realms/example/clients?clientId=$id" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
  sid=$(curl -s -H "Authorization: Bearer $ADMIN" "$KC/admin/realms/example/client-scopes" | python3 -c "import json,sys; print([s['id'] for s in json.load(sys.stdin) if s['name']=='$scope'][0])")
  kc -X PUT "$KC/admin/realms/example/clients/$cid/default-client-scopes/$sid"                   # 204
done
chmod 600 kc-clients.env
kctoken() { curl -s -d grant_type=client_credentials -d "client_id=$1" \
  --data-urlencode "client_secret=$(grep "^$1=" kc-clients.env | cut -d= -f2)" \
  "$KC/realms/example/protocol/openid-connect/token" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
TA=$(kctoken partner-a)
```

A partner-a token carries `iss` `http://localhost:8180/realms/example`, `aud`
`["orders-api","account"]`, `azp` and `client_id` `partner-a`, and a `scope` string that
contains `orders:read` (with `email` and `profile`, in no fixed order). In the API definition use the JWKS URL
`http://keycloak:8080/realms/example/protocol/openid-connect/certs` (the Gateway's view) and
that issuer string. The admin token is short-lived: fetch a new one if a call returns 401.

## Hydra: JWT and opaque clients

Hydra keeps everything in memory here: re-create the clients after every restart.

```bash
HY=http://localhost:4444; HYA=http://localhost:4445
for c in partner-h-jwt:jwt partner-h-opaque:opaque; do
  id=${c%%:*}; strategy=${c#*:}
  sec=$(python3 -c 'import secrets; print(secrets.token_hex(16))'); echo "$id=$sec" >> hydra-clients.env
  curl -s -o /dev/null -w '%{http_code}\n' -X POST "$HYA/admin/clients" -H 'Content-Type: application/json' -d "{
    \"client_id\": \"$id\", \"client_secret\": \"$sec\", \"grant_types\": [\"client_credentials\"],
    \"scope\": \"orders:read\", \"audience\": [\"orders-api\"], \"access_token_strategy\": \"$strategy\",
    \"token_endpoint_auth_method\": \"client_secret_post\"}"                                   # 201
done
chmod 600 hydra-clients.env
hytoken() { curl -s -d grant_type=client_credentials -d "client_id=$1" -d scope=orders:read -d audience=orders-api \
  --data-urlencode "client_secret=$(grep "^$1=" hydra-clients.env | cut -d= -f2)" "$HY/oauth2/token" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
```

The JWT client's token carries `iss` `http://hydra:4444`, `sub` and `client_id` = the client id,
`scp` `["orders:read"]`, `aud` `["orders-api"]`. JWKS URL for the Gateway:
`http://hydra:4444/.well-known/jwks.json`. Introspection endpoint (admin port, no client
authentication in this dev setup): `http://hydra:4445/admin/oauth2/introspect`.

## Your own key set

For rotation and caching tests, serve a JWKS you control from `jwks/jwks.json`
(`http://jwks:8000/jwks.json` from the Gateway) and mint tokens with `scripts/mint_jwt.py`:

```bash
openssl genrsa -out k1.pem 2048 && openssl rsa -in k1.pem -pubout -out k1.pub
python3 "$SKILL/scripts/mint_jwt.py" jwks --key k1.pub --kid k1 > jwks/jwks.json
python3 "$SKILL/scripts/mint_jwt.py" rs256 --key k1.pem --kid k1 --claim sub=client-a --ttl 300
```

## Tear down

```bash
docker compose --profile idp down -v
```
