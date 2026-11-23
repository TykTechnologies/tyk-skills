#!/usr/bin/env bash
# AISTUDIO-TYKMCP-001 setup: import the Tyk Dashboard's MCP proxies into
# Tyk AI Studio (Enterprise) over a READ-ONLY "catalogue" connection.
#
# Only meaningful when the bundle selected the mcp module: the Dashboard belongs
# to the Tyk Gateway bundle two directories up - a separate product and a
# separate stack. This script only READS from it:
#   - its Dashboard user API key and org id come from ../../.runtime/runtime.env
#     (read in a subshell, never written, never printed, and never put on a
#     command line: the request body reaches curl on stdin, see lib/admin.sh);
#   - the connection is created in "catalogue" mode, which only reads from the
#     Dashboard (the probe reports read capabilities only). Broker and full
#     modes, policy creation, proxy registration and push are never used here.
#
# Flow: create (or refresh) the connection -> probe -> activate -> sync?wait=true
# (retried on 409 while the activation's own sync runs) -> list the imported MCP
# servers. Idempotent.
#
# host.docker.internal is an internal hostname to AI Studio's URL policy and is
# refused (422 "url is not allowed by the Tyk Dashboard URL policy") unless the
# connection sets allow_internal_host (or the host is named in
# TYK_MCP_ALLOWED_HOSTS, as the 422 message says). compose.yaml maps the name for Linux engines, but there it is the bridge
# gateway, which the bundle's loopback-bound Dashboard does not answer: connect the AI
# Studio container to the bundle's network and set TYK_DASHBOARD_URL (compose.yaml).
#
# TYK_MCP_WAIT_SECONDS (default 900) is how long to wait for the Dashboard.
# Exit codes: 0 imported, 2 blocked (mcp not selected, or the Dashboard is not
# reachable), 1 failed. The outcome is also written to
# evidence/bodies/tyk-mcp-status.json.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE_DIR="$(cd "${ROOT_DIR}/../.." && pwd)"
cd "${ROOT_DIR}"
# shellcheck disable=SC2034  # JAR is read by lib/admin.sh
JAR="${ROOT_DIR}/.runtime/cookies-tykmcp.jar"
EVIDENCE="${ROOT_DIR}/evidence/bodies"
STATUS_FILE="${EVIDENCE}/tyk-mcp-status.json"
mkdir -p "${EVIDENCE}" "${ROOT_DIR}/.runtime"

set -a
# shellcheck disable=SC1091
source ./secret.env
set +a
ADMIN_URL="${ADMIN_URL:-http://localhost:__AISTUDIO_ADMIN_PORT__}"
# shellcheck source=../lib/admin.sh
source "${ROOT_DIR}/lib/admin.sh"

CONNECTION_NAME="${TYK_CONNECTION_NAME:-__DISPLAY_NAME__ Tyk Dashboard}"
env_value() { grep -E "^$1=" "${BUNDLE_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
DASHBOARD_PORT="$(env_value DASHBOARD_PORT)"; DASHBOARD_PORT="${DASHBOARD_PORT:-__DASHBOARD_PORT__}"
GATEWAY_PORT="$(env_value GATEWAY_PORT)"; GATEWAY_PORT="${GATEWAY_PORT:-__GATEWAY_PORT__}"
DASH_HOST_URL="http://localhost:${DASHBOARD_PORT}"
# What the AI Studio container dials (compose.yaml maps host.docker.internal).
DASH_CONTAINER_URL="${TYK_DASHBOARD_URL:-http://host.docker.internal:${DASHBOARD_PORT}}"
# What a portal user is told to call: the Gateway on the workstation.
GATEWAY_PUBLIC_URL="${TYK_GATEWAY_URL:-__GATEWAY_SCHEME__://localhost:${GATEWAY_PORT}}"
WAIT_SECONDS="${TYK_MCP_WAIT_SECONDS:-900}"
CONNECTION_ID=""

write_status() {
  # $1 = status, $2 = detail, $3 = count (optional), $4 = names JSON (optional)
  STATUS="$1" DETAIL="$2" COUNT="${3:-0}" NAMES="${4:-[]}" CONN="${CONNECTION_ID}" \
  DASH="${DASH_CONTAINER_URL}" OUT="${STATUS_FILE}" python3 -c "
import json,os,datetime
E=os.environ
json.dump({'status':E['STATUS'],'detail':E['DETAIL'],'imported_count':int(E['COUNT']),
  'imported':json.loads(E['NAMES']),'connection_id':E['CONN'],'dashboard_url':E['DASH'],
  'recorded_at':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},
  open(E['OUT'],'w'),indent=2)"
}

# --- 0. Only with the mcp module ----------------------------------------------
has_mcp="$(python3 -c "
import json,sys
try:
    print('yes' if 'mcp' in json.load(open(sys.argv[1])).get('modules',[]) else 'no')
except Exception:
    print('unknown')" "${BUNDLE_DIR}/demo.config.json")"
if [[ "${has_mcp}" != yes ]]; then
  write_status blocked "The bundle did not select the mcp module (demo.config.json: ${has_mcp}), so its Dashboard has no MCP proxies to import; regenerate with --modules ...,mcp,ai-studio"
  echo "    mcp module not selected; recorded as blocked" >&2
  exit 2
fi

# --- 1. Wait for the Dashboard ------------------------------------------------
echo "==> waiting up to ${WAIT_SECONDS}s for the Tyk Dashboard at ${DASH_HOST_URL}"
deadline=$(( $(date +%s) + WAIT_SECONDS ))
until curl -sf -o /dev/null --max-time 3 "${DASH_HOST_URL}/hello"; do
  if (( $(date +%s) >= deadline )); then
    write_status blocked "The Tyk Dashboard at ${DASH_HOST_URL} did not answer /hello within ${WAIT_SECONDS}s; start the Gateway bundle (scripts/start-demo-stack.sh, bootstrap, configure) and re-run ./proofs/tyk-mcp-import.sh"
    echo "    Dashboard not reachable; recorded as blocked" >&2
    exit 2
  fi
  sleep 5
done
echo "    Dashboard is up"

RUNTIME_ENV="${BUNDLE_DIR}/.runtime/runtime.env"
if [[ ! -r "${RUNTIME_ENV}" ]]; then
  write_status blocked "The Gateway bundle has not written .runtime/runtime.env yet (it holds the Dashboard user API key); run its scripts/bootstrap-demo-stack.sh first"
  exit 2
fi
# Read only the two values needed, in a subshell, so nothing else from the
# Gateway bundle's runtime file enters this environment and nothing is echoed.
DASH_KEY="$(bash -c 'source "$1" >/dev/null 2>&1; printf %s "${DASHBOARD_API_KEY:-}"' _ "${RUNTIME_ENV}")"
DASH_ORG="$(bash -c 'source "$1" >/dev/null 2>&1; printf %s "${ORG_ID:-}"' _ "${RUNTIME_ENV}")"
if [[ -z "${DASH_KEY}" || -z "${DASH_ORG}" ]]; then
  write_status blocked "DASHBOARD_API_KEY or ORG_ID is missing from the Gateway bundle's .runtime/runtime.env; re-run its bootstrap"
  exit 2
fi

# Host-only admin credentials (admin.env, not an env_file of any container).
load_admin_env || { write_status failed "admin.env is missing; cannot log in to AI Studio"; exit 1; }
AISTUDIO_ADMIN_EMAIL="${AISTUDIO_ADMIN_EMAIL:-admin@__SLUG__.invalid}"
aistudio_login

# --- 2. Connection ------------------------------------------------------------
echo "==> Tyk connection '${CONNECTION_NAME}' (catalogue mode, read-only)"
CONNECTION_ID="$(id_by_name /api/v1/tyk-connections "${CONNECTION_NAME}")"
if [[ -z "${CONNECTION_ID}" ]]; then
  body="$(DASH_KEY="${DASH_KEY}" DASH_ORG="${DASH_ORG}" NAME="${CONNECTION_NAME}" DASH="${DASH_CONTAINER_URL}" GW="${GATEWAY_PUBLIC_URL}" python3 -c "
import json,os
E=os.environ
print(json.dumps({
  'name': E['NAME'],
  'description': 'Read-only catalogue of the MCP proxies managed by the __DISPLAY_NAME__ Tyk Dashboard',
  'dashboard_url': E['DASH'],
  'gateway_base_url': E['GW'],
  'dashboard_access_token': E['DASH_KEY'],
  'org_id': E['DASH_ORG'],
  'declared_mode': 'catalogue',
  'sync_interval_seconds': 300,
  'allow_internal_host': True}))")"
  CONNECTION_ID="$(api POST /api/v1/tyk-connections "${body}" | jqr "print(json.load(sys.stdin)['id'])")"
  echo "    created (id ${CONNECTION_ID})"
else
  # Refresh the token and org in case the Gateway bundle was rebuilt. PATCH
  # needs the current lock_version (optimistic locking).
  lock="$(api GET "/api/v1/tyk-connections/${CONNECTION_ID}" | jqr "print(json.load(sys.stdin).get('lock_version',0))")"
  body="$(DASH_KEY="${DASH_KEY}" DASH_ORG="${DASH_ORG}" LOCK="${lock}" DASH="${DASH_CONTAINER_URL}" python3 -c "
import json,os
E=os.environ
print(json.dumps({'dashboard_access_token': E['DASH_KEY'], 'org_id': E['DASH_ORG'],
  'dashboard_url': E['DASH'], 'allow_internal_host': True, 'declared_mode': 'catalogue',
  'lock_version': int(E['LOCK'])}))")"
  api PATCH "/api/v1/tyk-connections/${CONNECTION_ID}" "${body}" >/dev/null \
    || echo "    token refresh was rejected; continuing with the stored token" >&2
  echo "    already present (id ${CONNECTION_ID}), token refreshed"
fi
unset DASH_KEY body

# --- 3. Probe -------------------------------------------------------------------
echo "==> probe (GET-only capability check in catalogue mode)"
api POST "/api/v1/tyk-connections/${CONNECTION_ID}/probe" '{}' >"${EVIDENCE}/tyk-mcp-probe.json" || {
  write_status failed "Probe of ${DASH_CONTAINER_URL} failed; see docker compose logs ai-studio"
  exit 1
}
jqr "
d=json.load(open('${EVIDENCE}/tyk-mcp-probe.json'))
caps={k:v.get('state') for k,v in (d.get('capabilities') or {}).items()}
print('    reachable=%s effective_mode=%s capabilities=%s' % (d.get('reachable'), d.get('effective_mode'), caps))"

# --- 4. Activate ----------------------------------------------------------------
status="$(api GET "/api/v1/tyk-connections/${CONNECTION_ID}" | jqr "print(json.load(sys.stdin).get('status',''))")"
if [[ "${status}" != active ]]; then
  echo "==> activate"
  api POST "/api/v1/tyk-connections/${CONNECTION_ID}/activate" '{}' >/dev/null || {
    write_status failed "Activation was refused (catalogue capabilities not all ok?); see evidence/bodies/tyk-mcp-probe.json"
    exit 1
  }
fi

# --- 5. Sync (synchronous) --------------------------------------------------------
# Activation schedules a sync straight away; a manual sync that meets it gets
# 409 until it finishes, so retry.
echo "==> sync (wait=true)"
synced="" code=000
for _ in $(seq 1 20); do
  out="$(api_raw POST "/api/v1/tyk-connections/${CONNECTION_ID}/sync?wait=true" '{}')"
  code="${out##*$'\n'}"
  if [[ "${code}" == 200 ]]; then
    printf '%s\n' "${out%$'\n'*}" >"${EVIDENCE}/tyk-mcp-sync-run.json"
    synced=yes
    break
  fi
  sleep 3
done
[[ -n "${synced}" ]] || echo "    manual sync kept answering ${code}; relying on the scheduled sync" >&2
api GET "/api/v1/tyk-connections/${CONNECTION_ID}/sync-runs?limit=5" >"${EVIDENCE}/tyk-mcp-sync-runs.json"

# --- 6. Imported MCP servers ------------------------------------------------------------
api GET "/api/v1/mcp-servers?connection_id=${CONNECTION_ID}&page_size=100" >"${EVIDENCE}/tyk-mcp-servers.json"
api GET "/api/v1/tyk-connections/${CONNECTION_ID}" | OUT="${EVIDENCE}/tyk-mcp-connection.json" jqr "
d=json.load(sys.stdin); d.pop('token_hint',None)
json.dump(d,open(os.environ['OUT'],'w'),indent=2)"
summary="$(jqr "
d=json.load(open('${EVIDENCE}/tyk-mcp-servers.json'))
s=d.get('servers') or []
print(len(s))
print(json.dumps([{'name':x.get('name'),'listen_path':x.get('listen_path'),'auth_mode':x.get('auth_mode'),'dashboard_state':x.get('dashboard_state'),'published':x.get('is_active')} for x in s]))")"
count="$(head -1 <<<"${summary}")"
names="$(tail -1 <<<"${summary}")"
if (( count > 0 )); then
  write_status passed "Imported ${count} MCP proxies from the Tyk Dashboard over a read-only catalogue connection" "${count}" "${names}"
  echo "    imported ${count} MCP servers: $(python3 -c "import json,sys;print(', '.join(x['name'] for x in json.loads(sys.argv[1])))" "${names}")"
else
  write_status failed "The connection is active but no MCP servers were imported; see evidence/bodies/tyk-mcp-sync-runs.json"
  exit 1
fi
