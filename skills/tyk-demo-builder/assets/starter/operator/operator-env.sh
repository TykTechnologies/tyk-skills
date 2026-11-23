#!/usr/bin/env bash
# Exports the connection and ownership values apply-operator.sh needs, derived from this
# bundle's runtime state. Source it from the bundle root in bash or zsh:
#
#   source operator/operator-env.sh
#
# It exports TYK_OPERATOR_AUTH (the Dashboard API key), TYK_OPERATOR_ORG, TYK_OPERATOR_URL
# (default http://host.docker.internal:<DASHBOARD_PORT>, the address kind pods use to reach
# the Compose Dashboard; an existing value is kept), TYK_USER_OWNER_ID (the bootstrap admin
# user) and TYK_TEAM_OWNER_ID, the id of a Dashboard user group named __SLUG__-api-platform
# that is found by name or created. Nothing else in the bundle provisions that group.
#
# Idempotent, and it never prints a secret or puts one on a command line. The work runs in a
# child bash process that emits `export` lines, so strict-mode options never leak into the
# calling shell and a failure returns non-zero instead of closing it. Only lines of the form
# `export TYK_...=` from that child are evaluated.

if [ "${TYK_OPERATOR_ENV_EMIT:-}" != 1 ]; then
  # ---- sourced part: keep this portable between bash and zsh ----
  if [ -n "${ZSH_VERSION:-}" ]; then
    # zsh-only expansion, behind eval so bash never parses it: the path of the file being
    # sourced, independent of FUNCTION_ARGZERO.
    eval '_tyk_operator_env_script="${(%):-%x}"'
  elif [ -n "${BASH_VERSION:-}" ]; then
    _tyk_operator_env_script="${BASH_SOURCE[0]}"
    if [ "${BASH_SOURCE[0]}" = "$0" ]; then
      echo "Source this file so the exports reach your shell: source operator/operator-env.sh" >&2
      exit 1
    fi
  else
    _tyk_operator_env_script="$0"
  fi
  if _tyk_operator_env_out="$(TYK_OPERATOR_ENV_EMIT=1 TYK_OPERATOR_URL="${TYK_OPERATOR_URL:-}" bash "${_tyk_operator_env_script}")"; then
    _tyk_operator_env_out="$(printf '%s\n' "${_tyk_operator_env_out}" | grep -E '^export TYK_[A-Z_]+=')" || _tyk_operator_env_out=""
    eval "${_tyk_operator_env_out}"
    unset _tyk_operator_env_out _tyk_operator_env_script
    echo "Exported TYK_OPERATOR_AUTH (hidden), TYK_OPERATOR_ORG, TYK_OPERATOR_URL=${TYK_OPERATOR_URL}, TYK_USER_OWNER_ID and TYK_TEAM_OWNER_ID (user group __SLUG__-api-platform)."
    return 0
  fi
  unset _tyk_operator_env_out _tyk_operator_env_script
  echo "Operator environment was not exported." >&2
  return 1
fi

# ---- emitting part: runs in a child bash process only ----
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/common.sh"

GROUP_NAME=__SLUG__-api-platform

fail() {
  echo "$1" >&2
  exit 1
}

command -v curl >/dev/null || fail "curl is required."
command -v jq >/dev/null || fail "jq is required."
if [[ -z "${DASHBOARD_API_KEY:-}" || -z "${ORG_ID:-}" || -z "${DASHBOARD_USER_ID:-}" ]]; then
  fail "No Dashboard bootstrap state in .runtime/runtime.env. Run ./scripts/start-demo-stack.sh and ./scripts/bootstrap-demo-stack.sh first."
fi

operator_url="${TYK_OPERATOR_URL:-http://host.docker.internal:${DASHBOARD_PORT}}"

# The key reaches curl through a private header file, never argv, so it cannot show up in ps.
header_file="$(mktemp)"
trap 'rm -f "${header_file}"' EXIT
chmod 600 "${header_file}"
printf 'authorization: %s\n' "${DASHBOARD_API_KEY}" >"${header_file}"

# shellcheck disable=SC2329  # invoked through retry_capture
dashboard_get() {
  curl -fsS "${DASHBOARD_URL}$1" -H "@${header_file}"
}

first_page="$(retry_capture 5 2 dashboard_get "/api/usergroups")" \
  || fail "Could not list Dashboard user groups at ${DASHBOARD_URL}. Is the stack running and the stored credential still valid?"
pages="$(jq -r '.pages // 1' <<<"${first_page}")"
[[ "${pages}" =~ ^[0-9]+$ ]] || pages=1

group_id="$(jq -r --arg name "${GROUP_NAME}" '[.groups[]? | select(.name == $name) | .id][0] // empty' <<<"${first_page}")"
page=2
while [[ -z "${group_id}" ]] && (( page <= pages )); do
  body="$(retry_capture 5 2 dashboard_get "/api/usergroups?p=${page}")" || fail "Could not read user group page ${page}."
  group_id="$(jq -r --arg name "${GROUP_NAME}" '[.groups[]? | select(.name == $name) | .id][0] // empty' <<<"${body}")"
  page=$((page + 1))
done

if [[ -z "${group_id}" ]]; then
  payload="$(jq -nc --arg name "${GROUP_NAME}" \
    '{name:$name,description:"Owner group for Tyk Operator managed demo resources.",active:true,user_permissions:{apis:"write"}}')"
  response="$(curl -fsS "${DASHBOARD_URL}/api/usergroups" -H "@${header_file}" \
    -H 'Content-Type: application/json' --data "${payload}")" || fail "Could not create the ${GROUP_NAME} user group."
  group_id="$(jq -r '.Meta // .meta // .id // empty' <<<"${response}")"
  [[ -n "${group_id}" ]] || fail "The Dashboard did not return an id for the ${GROUP_NAME} user group."
  echo "Created Dashboard user group ${GROUP_NAME}." >&2
else
  echo "Reusing Dashboard user group ${GROUP_NAME}." >&2
fi

printf 'export %s=%q\n' \
  TYK_OPERATOR_AUTH "${DASHBOARD_API_KEY}" \
  TYK_OPERATOR_ORG "${ORG_ID}" \
  TYK_OPERATOR_URL "${operator_url}" \
  TYK_USER_OWNER_ID "${DASHBOARD_USER_ID}" \
  TYK_TEAM_OWNER_ID "${group_id}"
exit 0
