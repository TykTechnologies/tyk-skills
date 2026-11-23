#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

[[ -n "${TYK_LICENSE_KEY:-}" ]] || { echo "Set TYK_LICENSE_KEY in .env." >&2; exit 1; }
if module_enabled portal && [[ -z "${TYK_PORTAL_LICENSE:-}" ]]; then
  echo "Portal is selected; set TYK_PORTAL_LICENSE in .env." >&2
  exit 1
fi
__CERT_GENERATION__
if module_enabled go-plugin; then
  gateway_arch="$(docker image inspect "tykio/tyk-gateway-ee:${GATEWAY_VERSION}" --format '{{.Architecture}}' 2>/dev/null || uname -m)"
  TYK_GATEWAY_VERSION="${GATEWAY_VERSION}" TYK_GATEWAY_ARCH="${gateway_arch}" "${ROOT_DIR}/plugins/go/build.sh"
fi
if module_enabled upstream-mtls; then
  # The upstream mounts its certificate and every gateway the CA directory at start.
  "${ROOT_DIR}/scripts/generate-upstream-certs.sh"
fi
# Say what is about to run and where, before anything starts: every service of this Compose
# project and the host address and port each one publishes (BIND_ADDRESS, 127.0.0.1 by default).
echo "Starting Compose project __SLUG__. Services and the host ports they bind:"
compose config --format json | jq -r '.services | to_entries[] | "  \(.key): " +
  (if ((.value.ports // []) | length) == 0 then "no host port (Compose network only)"
   else ([.value.ports[] | "\(.host_ip // "0.0.0.0"):\(.published) -> \(.target)"] | join(", ")) end)'
compose up -d
for attempt in $(seq 1 60); do
  curl -fsS "${DASHBOARD_URL}/hello" >/dev/null && break
  [[ "${attempt}" == 60 ]] && { echo "Dashboard did not become ready." >&2; exit 1; }
  sleep 2
done
for attempt in $(seq 1 60); do
  gw_curl -fsS "${GATEWAY_URL}/hello" >/dev/null && break
  [[ "${attempt}" == 60 ]] && { echo "Gateway did not become ready." >&2; exit 1; }
  sleep 2
done
if module_enabled gateway-sharding; then
  for attempt in $(seq 1 60); do
    gw_curl -fsS "${GATEWAY_EXTERNAL_URL}/hello" >/dev/null && break
    [[ "${attempt}" == 60 ]] && { echo "External gateway did not become ready." >&2; exit 1; }
    sleep 2
  done
  echo "External gateway: ${GATEWAY_EXTERNAL_URL} (segmented on tag external; the gateway below loads every API)"
fi
echo "Dashboard: ${DASHBOARD_URL}"
echo "Gateway:   ${GATEWAY_URL}"
# Use an if block, not `module_enabled portal && echo`: as the final command a
# short-circuited && would make a healthy non-portal stack exit non-zero.
if module_enabled portal; then
  echo "Portal:    ${PORTAL_URL}"
fi
