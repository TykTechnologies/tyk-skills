#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

: "${PORTAL_API_TOKEN:?Run portal/configure-portal.sh first or set PORTAL_API_TOKEN}"
command -v jq >/dev/null
command -v python3 >/dev/null

# Everything under .runtime/ is private (0700 directories, 0600 files), which
# validate_bundle.py enforces; the theme archives and build trees included.
umask 077
PORTAL_API_BASE="${PORTAL_URL%/}/portal-api"
THEME_RUNTIME_DIR="${RUNTIME_DIR}/portal-theme"
mkdir -p "${THEME_RUNTIME_DIR}"
chmod 700 "${RUNTIME_DIR}" "${THEME_RUNTIME_DIR}"
work_dir="$(mktemp -d "${THEME_RUNTIME_DIR}/build.XXXXXX")"
trap 'rm -rf "${work_dir}"' EXIT
archive="${THEME_RUNTIME_DIR}/neutral-demo.zip"

portal_api() {
  local method="$1" path="$2"
  shift 2
  curl -fsS -X "${method}" "${PORTAL_API_BASE}${path}" \
    -H "Authorization: ${PORTAL_API_TOKEN}" "$@"
}

themes="$(portal_api GET "/themes?per_page=100")"
default_theme_id="$(jq -r '.[]? | select(.ID == "default" or .Name == "default") | .ID' <<<"${themes}" | head -1)"
[[ -n "${default_theme_id}" ]] || {
  echo "The running Portal did not expose a default theme." >&2
  exit 1
}

# Extract the default theme from the running Portal through its management API.
# A release-specific container path may be supplied at runtime as a fallback,
# but reusable assets never embed an image filesystem path.
theme_source="${work_dir}/default.zip"
if ! portal_api GET "/themes/${default_theme_id}/download" -o "${theme_source}"; then
  : "${PORTAL_THEME_SOURCE:?Portal theme download failed; set PORTAL_THEME_SOURCE for a container-copy fallback}"
  theme_source="${work_dir}/default"
  compose cp "tyk-portal:${PORTAL_THEME_SOURCE}" "${theme_source}"
fi
# portal/brand/ holds the brand kit (brand.json, its assets, content.json). The theme carries
# the kit's logo, favicon, hero image, footer text and colours; customise-content.sh replaces
# the stock page copy.
python3 "${ROOT_DIR}/portal/build-theme.py" \
  --source "${theme_source}" \
  --overlay "${ROOT_DIR}/portal/theme-overrides/assets/stylesheets/neutral-overlay.css" \
  --output "${archive}" \
  --brand "${ROOT_DIR}/portal/brand"
# Files copied out of a container keep the container's modes.
chmod -R go-rwx "${THEME_RUNTIME_DIR}"
portal_api POST /themes/upload -F "file=@${archive}" >/dev/null
portal_api PUT /themes/neutral-demo/activate >/dev/null
active_status="$(portal_api GET /themes/neutral-demo | jq -r '.Status')"
[[ "${active_status}" == "Current" ]] || {
  echo "Branded theme upload completed but activation was not confirmed." >&2
  exit 1
}

echo "Branded theme built from the running Portal default and portal/brand/, uploaded, and activated."
