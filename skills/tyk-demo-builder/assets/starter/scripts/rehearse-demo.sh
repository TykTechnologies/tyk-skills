#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

"${ROOT_DIR}/scripts/configure-demo.sh"
"${ROOT_DIR}/scripts/configure-demo.sh"
if module_enabled tyk-sync; then
  # SYNC-001 reads the latest promotion's read-back, so the rehearsal promotes the bundle's
  # own APIs to this stack through the pinned tyk-sync image.
  "${ROOT_DIR}/ci/tyk-sync-local.sh" promote apis
fi
"${ROOT_DIR}/scripts/verify-demo.sh"
gateways=(tyk-gateway)
gateway_urls=("${GATEWAY_URL}")
if module_enabled gateway-sharding; then
  gateways+=(tyk-gateway-external)
  gateway_urls+=("${GATEWAY_EXTERNAL_URL}")
fi
compose restart "${gateways[@]}"
for gateway_url in "${gateway_urls[@]}"; do
  for attempt in $(seq 1 30); do
    gw_curl -fsS "${gateway_url}/hello" >/dev/null && break
    [[ "${attempt}" == 30 ]] && { echo "Gateway ${gateway_url} did not recover after restart." >&2; exit 1; }
    sleep 2
  done
done
"${ROOT_DIR}/scripts/verify-demo.sh"
echo "CLI rehearsal completed. Finish required Dashboard and Portal browser evidence before changing blocked UI rows to live."
