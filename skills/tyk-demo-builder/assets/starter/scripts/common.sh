#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
RUNTIME_DIR="${ROOT_DIR}/.runtime"
RUNTIME_ENV="${RUNTIME_DIR}/runtime.env"

[[ -f "${ENV_FILE}" ]] || { echo "Missing .env; copy .env.example and set license values." >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
# Runtime state comes from runtime.env and nowhere else.
#
# `source .runtime/runtime.env` exports its values for the life of that
# terminal, and they outlive the stack that issued them. The guards in
# configure-demo.sh are written as "value present, work already done", so an
# inherited key makes the script skip creating a real one: the credential is
# never written to the new runtime.env and never exists in the new stack. A
# purge does not clear it, and every script still looks correct in isolation.
#
# The key list is derived from the bundle's own `runtime_set` calls rather than
# hardcoded, so a key added later is covered without editing this file. Keys
# whose names are computed at the call site cannot be found that way; add those
# to RUNTIME_EXTRA_KEYS.
#
# OPERATOR_SUPPLIED_KEYS are never cleared. The READMEs tell the operator to
# set them (Portal admin credentials, a Dashboard admin password, a Portal API
# token for an already bootstrapped Portal), and the scripts honour a value
# that is already present, so wiping it would silently replace the operator's
# choice with a generated one. A value recorded in runtime.env still wins,
# because runtime.env is sourced afterwards.
OPERATOR_SUPPLIED_KEYS="PORTAL_ADMIN_EMAIL PORTAL_ADMIN_PASSWORD DASHBOARD_EMAIL DASHBOARD_PASSWORD PORTAL_API_TOKEN"
RUNTIME_EXTRA_KEYS="${RUNTIME_EXTRA_KEYS:-}"
while IFS= read -r _runtime_key; do
  [[ -n "${_runtime_key}" ]] || continue
  [[ " ${OPERATOR_SUPPLIED_KEYS} " == *" ${_runtime_key} "* ]] && continue
  unset "${_runtime_key}"
done < <(
  {
    grep -rhoE 'runtime_set +"?[A-Z][A-Z0-9_]*' "${ROOT_DIR}"/scripts "${ROOT_DIR}"/portal 2>/dev/null \
      | sed -E 's/.*runtime_set +"?//'
    printf '%s\n' ${RUNTIME_EXTRA_KEYS}
  } | sort -u
)
unset _runtime_key

# shellcheck disable=SC1090
[[ -f "${RUNTIME_ENV}" ]] && source "${RUNTIME_ENV}"
set +a

DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:${DASHBOARD_PORT}}"
GATEWAY_URL="${GATEWAY_URL:-__GATEWAY_SCHEME__://localhost:${GATEWAY_PORT}}"
# The segmented gateway of the gateway-sharding module. Every bundle allocates the port, so
# the URL is always defined; only a sharding bundle runs a gateway behind it.
GATEWAY_EXTERNAL_URL="${GATEWAY_EXTERNAL_URL:-__GATEWAY_SCHEME__://localhost:${GATEWAY_EXTERNAL_PORT:-}}"
PORTAL_URL="${PORTAL_URL:-http://localhost:${PORTAL_PORT}}"
# The Vault dev server of the vault module; every bundle allocates the port.
VAULT_URL="${VAULT_URL:-http://localhost:${VAULT_PORT:-}}"

# vault_api <method> <path> [curl args...]: a Vault HTTP API call with the root token. The
# token reaches curl as a header file through process substitution, never as an argument
# any local user could read from the process list.
vault_api() {
  local method="$1" path="$2"
  shift 2
  curl -sS -X "${method}" "${VAULT_URL}/v1/${path}" -H @<(printf 'X-Vault-Token: %s\n' "${VAULT_TOKEN:-}") "$@"
}

# Every gateway request goes through gw_curl, so the listener's transport is
# decided once here rather than on each of the dozens of call sites.
__GATEWAY_CURL_FN__

compose() {
  docker compose --project-directory "${ROOT_DIR}" --env-file "${ENV_FILE}" -f "${ROOT_DIR}/compose.yaml" "$@"
}

runtime_set() {
  local key="$1" value="$2" tmp
  mkdir -p "${RUNTIME_DIR}"
  chmod 700 "${RUNTIME_DIR}"
  tmp="$(mktemp)"
  [[ -f "${RUNTIME_ENV}" ]] && grep -v "^export ${key}=" "${RUNTIME_ENV}" >"${tmp}" || true
  printf 'export %s=%q\n' "${key}" "${value}" >>"${tmp}"
  mv "${tmp}" "${RUNTIME_ENV}"
  chmod 600 "${RUNTIME_ENV}"
  export "${key}=${value}"
}

module_enabled() {
  jq -e --arg module "$1" '.modules | index($module) != null' "${ROOT_DIR}/demo.config.json" >/dev/null
}

# retry_capture <attempts> <sleep-seconds> <command...>
#
# Runs the command until it succeeds and echoes its stdout. Dashboard endpoints can
# briefly return 403 right after bootstrap while the org and credentials propagate,
# so callers that read back created resources retry instead of aborting the run.
retry_capture() {
  local attempts="$1" delay="$2"
  shift 2
  local attempt output
  for attempt in $(seq 1 "${attempts}"); do
    if output="$("$@" 2>/dev/null)"; then
      printf '%s' "${output}"
      return 0
    fi
    [[ "${attempt}" == "${attempts}" ]] && return 1
    sleep "${delay}"
  done
  return 1
}
