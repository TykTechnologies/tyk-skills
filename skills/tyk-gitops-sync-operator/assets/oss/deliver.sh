#!/usr/bin/env bash
# Build the Gateway's file pairs from the Tyk OAS documents in apis/, deliver them with the
# policies, reload, verify. APPS_DIR and POLICIES_DIR are the directories the Gateways read.
set -euo pipefail
# Set GATEWAY_IMAGE to the Gateway tag you resolved live and run everywhere (same release).
GATEWAY_IMAGE=${GATEWAY_IMAGE:-tykio/tyk-gateway:v5.16.0}
GEN_PORT=${GEN_PORT:-18080}
: "${APPS_DIR:?}" "${POLICIES_DIR:?}" "${GATEWAY_URL:?}" "${GATEWAY_SECRET:?}"

rm -rf build && mkdir -p build
docker network create ${GEN_SUBNET:+--subnet "$GEN_SUBNET"} tyk-gen-net >/dev/null
trap 'docker rm -f tyk-gen tyk-gen-redis >/dev/null 2>&1; docker network rm tyk-gen-net >/dev/null 2>&1' EXIT
docker run -d --name tyk-gen-redis --network tyk-gen-net redis:7.2-alpine >/dev/null
docker run -d --name tyk-gen --network tyk-gen-net --user "$(id -u):$(id -g)" \
  -p "127.0.0.1:${GEN_PORT}:8080" -e TYK_GW_SECRET=gen -e TYK_GW_STORAGE_HOST=tyk-gen-redis \
  -e TYK_GW_APPPATH=/build -v "$PWD/build:/build" "$GATEWAY_IMAGE" >/dev/null
for _ in $(seq 1 30); do curl -fsS "http://localhost:${GEN_PORT}/hello" >/dev/null 2>&1 && break; sleep 1; done
for f in apis/*.oas.json; do
  curl -fsS -X POST "http://localhost:${GEN_PORT}/tyk/apis/oas" -H 'x-tyk-authorization: gen' \
    -H 'Content-Type: application/json' -d @"$f"; echo
done

rsync -a --delete build/ "$APPS_DIR/"
rsync -a --delete policies/ "$POLICIES_DIR/"
curl -fsS "$GATEWAY_URL/tyk/reload/group" -H "x-tyk-authorization: $GATEWAY_SECRET"; echo
sleep 3
curl -fsS "$GATEWAY_URL/tyk/apis" -H "x-tyk-authorization: $GATEWAY_SECRET" \
  | python3 -c 'import json,sys; print("loaded:", sorted(a["api_id"] for a in json.load(sys.stdin)))'
