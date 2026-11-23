#!/usr/bin/env bash
# Set the Portal's API product access flow.
#
# UNSUPPORTED: /auth/password/login and /admin/general are QOR admin-framework UI form
# endpoints, not a documented product API. Field names, routes and the auth mechanism may
# change in any Portal patch release without deprecation. The readback assertion below is
# therefore mandatory: QOR returns HTTP 200 for unrecognised form fields, so a rename would
# otherwise succeed silently and leave the Portal on the previous flow.
#
# usage: portal/set-access-flow.sh [direct|cart]
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

desired="${1:-direct}"
case "${desired}" in
  direct|cart) ;;
  *) echo "Access flow must be 'direct' or 'cart'." >&2; exit 2 ;;
esac

# Direct access flow requires Portal 1.16.0 or later.
portal_version="${PORTAL_VERSION:-$(grep -E '^PORTAL_VERSION=' "${ROOT_DIR}/.env" 2>/dev/null | cut -d= -f2- || true)}"
portal_version="${portal_version#v}"
if [[ -n "${portal_version}" ]]; then
  major="${portal_version%%.*}"; rest="${portal_version#*.}"; minor="${rest%%.*}"
  if [[ "${major}" -lt 1 || ( "${major}" -eq 1 && "${minor}" -lt 16 ) ]]; then
    echo "Portal ${portal_version} predates the direct access flow (1.16.0); skipping."
    exit 0
  fi
fi

[[ -n "${PORTAL_ADMIN_EMAIL:-}" && -n "${PORTAL_ADMIN_PASSWORD:-}" ]] || {
  echo "Portal admin credentials are not in runtime state; run configure-demo.sh first." >&2
  exit 1
}

field="${ACCESS_FLOW_FIELD:-QorResource.APIProductAccessFlow}"
cookie_jar="$(mktemp)"
trap 'rm -f "${cookie_jar}"' EXIT

# Credentials come from the mode-600 runtime file and are never echoed.
curl -sS -o /dev/null -c "${cookie_jar}" -X POST "${PORTAL_URL}/auth/password/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "login=${PORTAL_ADMIN_EMAIL}" \
  --data-urlencode "password=${PORTAL_ADMIN_PASSWORD}"
grep -q 'portal-session' "${cookie_jar}" || {
  echo "Portal admin login did not return a session cookie." >&2
  exit 1
}

write_code="$(curl -sS -o /dev/null -w '%{http_code}' -b "${cookie_jar}" \
  -X PUT "${PORTAL_URL}/admin/general" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data "${field}=${desired}")"

# MANDATORY readback. A 200 is not proof.
applied="$(curl -sS -b "${cookie_jar}" "${PORTAL_URL}/admin/general" | python3 -c '
import re, sys
html = sys.stdin.read()
selected = [
    re.search(r"value=\"([^\"]*)\"", tag).group(1)
    for tag in re.findall(r"<input[^>]*>", html)
    if "APIProductAccessFlow" in tag and "checked" in tag.lower()
]
print(selected[0] if len(selected) == 1 else "INDETERMINATE")
')"

if [[ "${applied}" != "${desired}" ]]; then
  echo "Access flow was NOT applied. Endpoint returned ${write_code} but readback reports '${applied}'." >&2
  echo "The form field may have been renamed upstream; this endpoint is undocumented." >&2
  exit 1
fi

echo "API product access flow set to '${desired}' and confirmed by readback."
