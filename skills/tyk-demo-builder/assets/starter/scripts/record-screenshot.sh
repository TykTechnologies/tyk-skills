#!/usr/bin/env bash
# record-screenshot.sh <name> <portal-path> <marker>
#
# Registers a browser screenshot of this stack's Developer Portal as evidence for PORTAL-UI-001.
# Save the capture as evidence/screenshots/<name>.png first (portal-theme, portal-catalogue,
# portal-openapi-viewer or portal-login), then run this with the Portal path the capture shows
# and a text marker visible on that page. It refuses a file that is not a PNG and a page that
# this stack's Portal does not serve with that marker now, and records in
# evidence/screenshots/manifest.json the file's SHA-256, the page URL, the marker, the
# organisation of this stack and the digest of portal/brand/brand.json. verify-demo.sh accepts
# a screenshot only while all of those still match, so a capture from another stack, an older
# brand kit or a replaced file is not counted.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

name="${1:-}" page_path="${2:-}" marker="${3:-}"
case "${name}" in
  portal-theme|portal-catalogue|portal-openapi-viewer|portal-login) ;;
  *) echo "Usage: $0 <portal-theme|portal-catalogue|portal-openapi-viewer|portal-login> <portal-path> <marker>" >&2; exit 2 ;;
esac
[[ "${page_path}" == /* && -n "${marker}" ]] || { echo "Give the Portal path (starting with /) and a marker visible on that page." >&2; exit 2; }

shots="${ROOT_DIR}/evidence/screenshots"
file="${shots}/${name}.png"
[[ -s "${file}" ]] || { echo "No capture at evidence/screenshots/${name}.png; save the browser screenshot there first." >&2; exit 1; }
[[ "$(head -c 8 "${file}" | od -An -tx1 | tr -d ' \n')" == "89504e470d0a1a0a" ]] || { echo "evidence/screenshots/${name}.png is not a PNG." >&2; exit 1; }

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

url="${PORTAL_URL%/}${page_path}"
page="$(curl -fsS "${url}" 2>/dev/null || true)"
[[ -n "${page}" ]] || { echo "${url} is not served by this stack's Portal." >&2; exit 1; }
[[ "${page}" == *"${marker}"* ]] || { echo "${url} does not show the marker '${marker}' now; capture the page this stack serves." >&2; exit 1; }

manifest="${shots}/manifest.json"
[[ -f "${manifest}" ]] || echo '{}' >"${manifest}"
tmp="$(mktemp)"
jq --arg name "${name}" --arg sha "$(sha256 "${file}")" --arg url "${url}" --arg marker "${marker}" \
  --arg org "${ORG_ID:-}" --arg brand "$(sha256 "${ROOT_DIR}/portal/brand/brand.json")" \
  --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '.[$name] = {file: ($name + ".png"), sha256: $sha, url: $url, marker: $marker, org_id: $org, brand_digest: $brand, recorded_at: $at}' \
  "${manifest}" >"${tmp}"
mv "${tmp}" "${manifest}"
echo "Recorded ${name}.png for ${url} (marker '${marker}')."
