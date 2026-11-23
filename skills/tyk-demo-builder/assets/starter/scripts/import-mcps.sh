#!/usr/bin/env bash
# Import MCP proxy definitions.
#
# MCP proxies live on their own Dashboard resource, /api/mcps, not on
# /api/apis/oas. A Tyk OAS document with MCP configuration posted to the OAS
# endpoint is rejected, which is the single most common way to lose time here.
#
# The payload is a Tyk OAS document that must carry x-tyk-api-gateway, and it is
# validated against the MCP JSON schema (/tyk/schema?type=mcp). That schema
# refuses a set of middleware which cannot apply to JSON-RPC: transformRequestMethod,
# transformResponseBody, urlRewrite, internal, cache, validateRequest and
# mockResponse. A definition carrying any of them fails validation rather than
# silently ignoring the field.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[[ -n "${DASHBOARD_API_KEY:-}" ]] || { echo "Run bootstrap-demo-stack.sh first." >&2; exit 1; }

shopt -s nullglob
files=("${ROOT_DIR}"/mcp/*.mcp.oas.json)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "No MCP proxy definitions to import."
  exit 0
fi

response_file="$(mktemp "${RUNTIME_DIR}/mcp-import.XXXXXX")"
trap 'rm -f "${response_file}"' EXIT

# Remove owned proxies that are no longer described on disk, so a renamed or
# deleted asset does not leave a stale proxy serving traffic.
desired_ids="$(for file in "${files[@]}"; do jq -r '."x-tyk-api-gateway".info.id' "${file}"; done)"
existing="$(curl -fsS "${DASHBOARD_URL}/api/mcps" -H "authorization: ${DASHBOARD_API_KEY}" 2>/dev/null || true)"
candidate_ids="$(jq -r '[.. | objects | select(has("x-tyk-api-gateway")) | ."x-tyk-api-gateway".info.id] | .[]? // empty' <<<"${existing}" 2>/dev/null | sort -u || true)"
while IFS= read -r candidate_id; do
  [[ -n "${candidate_id}" ]] || continue
  grep -Fxq "${candidate_id}" <<<"${desired_ids}" && continue
  owner="$(curl -fsS "${DASHBOARD_URL}/api/mcps/${candidate_id}" -H "authorization: ${DASHBOARD_API_KEY}" 2>/dev/null | jq -r '."x-demo-owner" // empty' || true)"
  if [[ "${owner}" == "__SLUG__" ]]; then
    curl -fsS -X DELETE "${DASHBOARD_URL}/api/mcps/${candidate_id}" -H "authorization: ${DASHBOARD_API_KEY}" >/dev/null
  fi
done <<<"${candidate_ids}"

for file in "${files[@]}"; do
  mcp_id="$(jq -r '."x-tyk-api-gateway".info.id' "${file}")"
  status="$(curl -sS -o "${response_file}" -w '%{http_code}' "${DASHBOARD_URL}/api/mcps" \
    -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data-binary "@${file}")"
  if [[ "${status}" == 409 ]]; then
    existing_owner="$(curl -fsS "${DASHBOARD_URL}/api/mcps/${mcp_id}" -H "authorization: ${DASHBOARD_API_KEY}" | jq -r '."x-demo-owner" // empty')"
    [[ "${existing_owner}" == "__SLUG__" ]] || {
      echo "MCP proxy ${mcp_id} exists and is not owned by __SLUG__; refusing to overwrite." >&2
      exit 1
    }
    status="$(curl -sS -o "${response_file}" -w '%{http_code}' -X PUT "${DASHBOARD_URL}/api/mcps/${mcp_id}" \
      -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data-binary "@${file}")"
  fi
  if [[ "${status}" != 200 && "${status}" != 201 ]]; then
    echo "MCP proxy ${mcp_id} was rejected with HTTP ${status}:" >&2
    jq -r '.Message // .message // .' "${response_file}" >&2 2>/dev/null || cat "${response_file}" >&2
    exit 1
  fi
done

# Read back rather than trusting the write: a 2xx does not prove the proxy is
# present and active under the ID the asset claims.
for file in "${files[@]}"; do
  mcp_id="$(jq -r '."x-tyk-api-gateway".info.id' "${file}")"
  curl -fsS -o /dev/null "${DASHBOARD_URL}/api/mcps/${mcp_id}" -H "authorization: ${DASHBOARD_API_KEY}" || {
    echo "MCP proxy ${mcp_id} was accepted but cannot be read back." >&2
    exit 1
  }
done

gw_curl -fsS -o /dev/null "${GATEWAY_URL}/tyk/reload/group" -H "x-tyk-authorization: ${NODE_SECRET}" || true
echo "MCP proxy import and readback completed."
