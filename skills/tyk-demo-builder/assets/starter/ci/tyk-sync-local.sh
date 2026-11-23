#!/usr/bin/env bash
# Runs ci/tyk-sync.sh against this bundle's own stack through the pinned tyk-sync image, with
# no local binary and nothing to export:
#
#   ./ci/tyk-sync-local.sh validate apis
#   ./ci/tyk-sync-local.sh promote apis
#   ./ci/tyk-sync-local.sh rollback .runtime/tyk-sync/rollback/<promotion-id>
#
# The Dashboard URL, key and organisation come from the bundle's runtime state. A promotion
# records .runtime/tyk-sync/latest-promotion.json, which scripts/verify-demo.sh reads for
# SYNC-001.
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

[[ -n "${DASHBOARD_API_KEY:-}" && -n "${ORG_ID:-}" ]] || {
  echo "Run scripts/bootstrap-demo-stack.sh before promoting with Tyk Sync." >&2
  exit 1
}
export TYK_SYNC_DASHBOARD_URL="${TYK_SYNC_DASHBOARD_URL:-http://host.docker.internal:${DASHBOARD_PORT}}"
export TYK_SYNC_SECRET="${DASHBOARD_API_KEY}"
export TYK_SYNC_ORG_ID="${ORG_ID}"
export TYK_SYNC_BIN="${TYK_SYNC_BIN:-${ROOT_DIR}/ci/tyk-sync-docker.sh}"
exec "${ROOT_DIR}/ci/tyk-sync.sh" "$@"
