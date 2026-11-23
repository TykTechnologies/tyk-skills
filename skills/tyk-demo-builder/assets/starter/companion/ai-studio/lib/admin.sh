# shellcheck shell=bash
# Shared admin-API helpers for configure-ai-studio.sh, verify-ai-studio.sh and
# proofs/*.sh. Source it after ADMIN_URL and JAR are set.
#
# The admin API authenticates the session cookie and skips its CSRF check when
# the request carries an Authorization header, so every call here sends the
# cookie plus a placeholder Authorization value. Without the header an admin
# POST answers 403 "Forbidden - CSRF token not found in request".
#
# Every login replaces the user's single session token, so each script keeps its
# own cookie jar and a login in one (or in a browser) logs the others out.
#
# Request bodies travel on curl's stdin (--data-binary @-), never as a -d
# argument: bodies carry the admin password, LLM api keys and the Tyk Dashboard
# access token, and a command-line argument is readable by anyone running ps.
# Bearer tokens go the same way (-H @- reads the header from stdin).

# load_admin_env: read AISTUDIO_ADMIN_EMAIL / AISTUDIO_ADMIN_PASSWORD from the
# host-only admin.env (gitignored, mode 0600). It is deliberately not an
# env_file of any container, and it is sourced without `set -a`, so the password
# is neither inside the containers nor exported to child processes.
load_admin_env() {
  local file="${ROOT_DIR:?}/admin.env"
  [[ -f "${file}" ]] || {
    echo "admin.env missing; the generator writes it (copy admin.env.example and set a password)" >&2
    return 1
  }
  # shellcheck disable=SC1090
  source "${file}"
}

# conf_value FILE KEY: the last KEY=value in an env-style conf file, unquoted.
# Lets the host scripts read the edge's GATEWAY_UNIFIED_ROUTER_PATH instead of
# hardcoding the default.
conf_value() {
  sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -1 | tr -d '"'"'"
}

# api METHOD PATH [BODY]: any non-2xx answer prints the status and the start of
# the body to stderr and returns 1, so a rejected admin call never reads as success.
api() {
  local method="$1" path="$2" body="${3:-}" response status
  local args=(-sS -b "${JAR}" -H 'Authorization: session' -X "${method}" -w '\n%{http_code}')
  if [[ -n "${body}" ]]; then
    args+=(-H 'Content-Type: application/json' --data-binary @-)
    response="$(curl "${args[@]}" "${ADMIN_URL}${path}" <<<"${body}")"
  else
    response="$(curl "${args[@]}" "${ADMIN_URL}${path}" </dev/null)"
  fi || {
    echo "${method} ${path}: request failed" >&2
    return 1
  }
  status="${response##*$'\n'}"
  response="${response%$'\n'*}"
  if [[ "${status}" != 2?? ]]; then
    echo "${method} ${path} returned HTTP ${status}: ${response:0:400}" >&2
    return 1
  fi
  printf '%s' "${response}"
}

# api_raw METHOD PATH [BODY]: prints "<body>\n<status>" whatever the status, for
# calls whose non-2xx answer is itself the evidence.
api_raw() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -b "${JAR}" -H 'Authorization: session' -X "${method}" -w '\n%{http_code}')
  if [[ -n "${body}" ]]; then
    args+=(-H 'Content-Type: application/json' --data-binary @-)
    curl "${args[@]}" "${ADMIN_URL}${path}" <<<"${body}"
  else
    curl "${args[@]}" "${ADMIN_URL}${path}" </dev/null
  fi
}

jqr() { python3 -c "import os,sys,json;$1"; }

# id_by_name LIST_PATH NAME: the id of the first record with that name. Handles
# both JSON:API lists ({"data":[{"id","attributes":{"name"}}]}) and plain arrays.
id_by_name() {
  api GET "$1" | MATCH="$2" jqr "
d=json.load(sys.stdin)
d=d.get('data',d) if isinstance(d,dict) else d
m=[str(x['id']) for x in (d or []) if ((x.get('attributes') or x).get('name'))==os.environ['MATCH']]
print(m[0] if m else '')"
}

# aistudio_login: a fresh session cookie in JAR for AISTUDIO_ADMIN_EMAIL/PASSWORD
# (from admin.env, see load_admin_env). The password reaches curl on stdin only.
aistudio_login() {
  local email="${AISTUDIO_ADMIN_EMAIL:?}" password="${AISTUDIO_ADMIN_PASSWORD:?}" payload
  mkdir -p "$(dirname "${JAR}")"
  rm -f "${JAR}"
  payload="$(EMAIL="${email}" PASSWORD="${password}" python3 -c "
import json,os
print(json.dumps({'data':{'type':'users','attributes':{'email':os.environ['EMAIL'],'password':os.environ['PASSWORD']}}}))")"
  curl -sS -c "${JAR}" -X POST "${ADMIN_URL}/auth/login" \
    -H 'Content-Type: application/json' -H 'Authorization: bootstrap' --data-binary @- <<<"${payload}" \
    | grep -q 'Login successful' || { echo "admin login failed for ${email}" >&2; return 1; }
  chmod 600 "${JAR}"
}
