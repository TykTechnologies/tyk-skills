#!/usr/bin/env bash
# Rewrites Portal CMS pages from portal/brand/content.json, so no stock Tyk copy remains on the
# Home and About pages. Idempotent: pages are matched by path and content blocks by name, then
# updated in place (only when they differ) and read back.
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

: "${PORTAL_API_TOKEN:?Run portal/configure-portal.sh first or set PORTAL_API_TOKEN}"
CONTENT="${ROOT_DIR}/portal/brand/content.json"
[[ -f "${CONTENT}" ]] || { echo "No portal/brand/content.json; the Portal page copy is unchanged."; exit 0; }
PORTAL_API_BASE="${PORTAL_URL%/}/portal-api"

portal_api() {
  local method="$1" path="$2"
  shift 2
  curl -fsS -X "${method}" "${PORTAL_API_BASE}${path}" -H "Authorization: ${PORTAL_API_TOKEN}" "$@"
}
portal_json() { portal_api "$1" "$2" -H 'Content-Type: application/json' --data-binary @- <<<"$3"; }

# Lists are read with per_page=100: the API returns 20 items by default (page_size is ignored)
# and the order changes after an update, so a lookup by name on the default Home page, which
# has more than 20 blocks, silently misses some.
pages="$(portal_api GET "/pages?per_page=100")"
updated=0
while IFS= read -r path; do
  page="$(jq -c --arg p "${path}" '[.[]? | select(.Path == $p)][0] // empty' <<<"${pages}")"
  [[ -n "${page}" ]] || { echo "Portal page ${path} named in portal/brand/content.json was not found." >&2; exit 1; }
  page_id="$(jq -r '.ID' <<<"${page}")"
  title="$(jq -r --arg p "${path}" '.pages[$p].title // empty' "${CONTENT}")"
  if [[ -n "${title}" && "$(jq -r '.Title' <<<"${page}")" != "${title}" ]]; then
    portal_json PUT "/pages/${page_id}" "$(jq -c --arg t "${title}" '.Title = $t' <<<"${page}")" >/dev/null
    updated=$((updated + 1))
  fi
  blocks="$(portal_api GET "/pages/${page_id}/content-blocks?per_page=100")"
  while IFS= read -r entry; do
    name="$(jq -r '.key' <<<"${entry}")"
    block="$(jq -c --arg n "${name}" '[.[]? | select(.Name == $n)][0] // empty' <<<"${blocks}")"
    [[ -n "${block}" ]] || { echo "Content block ${name} is not on the Portal page ${path}." >&2; exit 1; }
    jq -e --argjson e "${entry}" '.Content == $e.value and ((.MarkdownEnabled | not) or .MarkdownContent == $e.value)' <<<"${block}" >/dev/null && continue
    # A Markdown-enabled block renders MarkdownContent, so both fields carry the copy.
    portal_json PUT "/pages/${page_id}/content-blocks/$(jq -r '.ID' <<<"${block}")" \
      "$(jq -c --argjson e "${entry}" '.Content = $e.value | if .MarkdownEnabled then .MarkdownContent = $e.value else . end' <<<"${block}")" >/dev/null
    updated=$((updated + 1))
  done < <(jq -c --arg p "${path}" '.pages[$p].blocks // {} | to_entries[]' "${CONTENT}")
  # Readback: the title and every named block must now hold the copy from the file.
  mismatched="$(portal_api GET "/pages/${page_id}/content-blocks?per_page=100" | jq -r --slurpfile c "${CONTENT}" --arg p "${path}" \
    '. as $blocks | [($c[0].pages[$p].blocks // {}) | to_entries[] | .key as $n | .value as $v
      | select(([$blocks[]? | select(.Name == $n and .Content == $v)] | length) == 0) | $n] | join(", ")')"
  [[ -z "${mismatched}" ]] || { echo "Portal page ${path}: content did not persist for ${mismatched}." >&2; exit 1; }
  if [[ -n "${title}" ]]; then
    [[ "$(portal_api GET "/pages/${page_id}" | jq -r '.Title')" == "${title}" ]] \
      || { echo "Portal page ${path}: title did not persist." >&2; exit 1; }
  fi
done < <(jq -r '.pages | keys[]' "${CONTENT}")

echo "Portal page copy matches portal/brand/content.json ($(jq '.pages | length' "${CONTENT}") page(s), ${updated} change(s))."
