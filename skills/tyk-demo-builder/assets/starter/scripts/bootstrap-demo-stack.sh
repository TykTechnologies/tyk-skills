#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

for attempt in $(seq 1 60); do
  curl -fsS "${DASHBOARD_URL}/hello" >/dev/null 2>&1 && break
  [[ "${attempt}" == 60 ]] && { echo "Dashboard did not become ready." >&2; exit 1; }
  sleep 2
done

# Skip bootstrap only when the stored credential actually authenticates. /hello is
# unauthenticated and answers from an empty database, so its success proves nothing about
# the runtime state: a stored key can outlive the database that issued it. Validating
# presence alone reports success while handing over an unusable stack.
credential_authenticates() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' "${DASHBOARD_URL}/api/apis" \
    -H "authorization: ${DASHBOARD_API_KEY:-}" 2>/dev/null || true)"
  [[ "${code}" == 200 ]]
}

if [[ -n "${DASHBOARD_API_KEY:-}" && -n "${ORG_ID:-}" ]]; then
  if credential_authenticates; then
    echo "Bootstrap state already available; stored credential validated against the Dashboard."
    exit 0
  fi
  # Credentials can reach this script from the environment as well as from the runtime
  # file, so only archive a file that is actually there. An unconditional mv aborts the
  # run under set -e and never reaches the re-bootstrap it just announced.
  if [[ -f "${RUNTIME_ENV}" ]]; then
    stale="${RUNTIME_ENV}.stale-$(date +%Y%m%d-%H%M%S)"
    mv "${RUNTIME_ENV}" "${stale}"
    echo "Stored runtime credentials did not authenticate; archived $(basename "${stale}") and re-bootstrapping." >&2
  else
    echo "Supplied runtime credentials did not authenticate; re-bootstrapping." >&2
  fi
  unset ORG_ID DASHBOARD_API_KEY DASHBOARD_USER_ID
fi

orgs="$(curl -fsS "${DASHBOARD_URL}/admin/organisations/" -H "admin-auth: ${ADMIN_SECRET}")"
org_id="$(printf '%s' "${orgs}" | jq -r '.organisations[]? | select((.owner_name // .OwnerName // .name) == "__DISPLAY_NAME__") | (.id // .ID)' | head -1)"
if [[ -z "${org_id}" ]]; then
  response="$(curl -fsS "${DASHBOARD_URL}/admin/organisations/" -H "admin-auth: ${ADMIN_SECRET}" -H 'Content-Type: application/json' --data '{"owner_name":"__DISPLAY_NAME__","cname_enabled":true}')"
  org_id="$(printf '%s' "${response}" | jq -r '.Meta // .meta // .id // empty')"
fi
[[ -n "${org_id}" ]] || { echo "Unable to create organisation." >&2; exit 1; }

response="$(curl -fsS "${DASHBOARD_URL}/admin/users/" -H "admin-auth: ${ADMIN_SECRET}" -H 'Content-Type: application/json' --data "{\"org_id\":\"${org_id}\",\"first_name\":\"__DISPLAY_NAME__\",\"last_name\":\"Administrator\",\"email_address\":\"admin@__SLUG__.invalid\",\"active\":true,\"user_permissions\":{\"IsAdmin\":\"admin\"}}")"
user_id="$(printf '%s' "${response}" | jq -r '.Meta.id // .id // empty')"
dashboard_key="$(printf '%s' "${response}" | jq -r '.Meta.access_key // .access_key // empty')"
[[ -n "${user_id}" && -n "${dashboard_key}" ]] || { echo "Unable to create Dashboard admin. If runtime state was deleted, use reset-demo-stack.sh --purge." >&2; exit 1; }

dashboard_password="${DASHBOARD_PASSWORD:-$(openssl rand -hex 18)}"
# The password reaches jq through its environment and curl on stdin, never through
# arguments, which any local user can read from the process list.
password_payload="$(admin_password="${dashboard_password}" jq -nc '{new_password:env.admin_password,user_permissions:{IsAdmin:"admin"}}')"
curl -fsS "${DASHBOARD_URL}/api/users/${user_id}/actions/reset" -H "authorization: ${dashboard_key}" -H 'Content-Type: application/json' --data-binary @- <<<"${password_payload}" >/dev/null

runtime_set ORG_ID "${org_id}"
runtime_set DASHBOARD_API_KEY "${dashboard_key}"
runtime_set DASHBOARD_USER_ID "${user_id}"
runtime_set DASHBOARD_EMAIL "admin@__SLUG__.invalid"
runtime_set DASHBOARD_PASSWORD "${dashboard_password}"
echo "Dashboard bootstrap completed. Use scripts/show-credentials.sh to display credentials."
