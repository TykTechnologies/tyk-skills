#!/usr/bin/env bash
# Import every Tyk OAS API in apis/ by its stable ID, and prune owned APIs that are gone.
#
# Versioned APIs. A file whose top-level x-demo-version-of block names a base API is a child
# version. The Dashboard links a version to its base only through
#   POST /api/apis/oas?base_api_id=<base>&base_api_version_name=<v1>&new_version_name=<v2>&set_default=false
# (the body is a normal OAS document), so every base is imported before any child and a
# missing child is created that way; a base file that hand-writes info.versioning is not
# linked. An update of a versioned API carries info.dbId, info.orgId and info.versioning from
# its readback (on 5.15.1): without dbId the Dashboard inserts a second row with an
# empty id, a ghost that later updates trip over with 403 "Failed to update Org object to DB",
# and without orgId it answers 400 "Failed to retrieve organisation". Pruning never deletes a
# child version.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[[ -n "${DASHBOARD_API_KEY:-}" ]] || { echo "Run bootstrap-demo-stack.sh first." >&2; exit 1; }
response_file="$(mktemp "${RUNTIME_DIR}/api-import.XXXXXX")"
merged_file="$(mktemp "${RUNTIME_DIR}/api-merge.XXXXXX")"
trap 'rm -f "${response_file}" "${merged_file}"' EXIT

dashboard_oas() {
  curl -fsS "${DASHBOARD_URL}/api/apis/oas/$1" -H "authorization: ${DASHBOARD_API_KEY}"
}

desired_ids="$(for file in "${ROOT_DIR}"/apis/*.oas.json; do [[ -e "${file}" ]] || continue; jq -r '."x-tyk-api-gateway".info.id' "${file}"; done)"
# All pages (p=-1): the default page holds only the first APIs, so pruning would miss the rest.
api_list="$(curl -fsS "${DASHBOARD_URL}/api/apis?mode=detailed&p=-1" -H "authorization: ${DASHBOARD_API_KEY}" || true)"
candidate_ids="$(jq -r '[.apis[]?.api_model.api_id, .apis[]?.api_id, .apis[]?.api_definition.api_id, .Data[]?.api_model.api_id, .Data[]?.api_id] | .[]? // empty' <<<"${api_list}" 2>/dev/null | sort -u || true)"
while IFS= read -r candidate_id; do
  [[ -n "${candidate_id}" ]] || continue
  grep -Fxq "${candidate_id}" <<<"${desired_ids}" && continue
  readback="$(dashboard_oas "${candidate_id}" 2>/dev/null || true)"
  # A child version goes with its base or by hand, never through pruning.
  jq -e 'has("x-demo-version-of")' <<<"${readback}" >/dev/null 2>&1 && continue
  owner="$(jq -r '."x-demo-owner" // empty' <<<"${readback}" 2>/dev/null || true)"
  if [[ "${owner}" == "__SLUG__" ]]; then
    curl -fsS -X DELETE "${DASHBOARD_URL}/api/apis/${candidate_id}" -H "authorization: ${DASHBOARD_API_KEY}" >/dev/null
  fi
done <<<"${candidate_ids}"

# put_api <api-id> <file>: update in place. A versioned API (a base whose readback carries
# info.versioning, or a child) gets dbId, orgId and versioning from its readback.
put_api() {
  local api_id="$1" file="$2" readback body="$2"
  readback="$(dashboard_oas "${api_id}")"
  if jq -e '."x-tyk-api-gateway".info.versioning != null or has("x-demo-version-of")' <<<"${readback}" >/dev/null 2>&1 \
    || jq -e 'has("x-demo-version-of")' "${file}" >/dev/null; then
    printf '%s' "${readback}" | jq -s '.[0] as $file | .[1]."x-tyk-api-gateway".info as $live
      | $file
      | ."x-tyk-api-gateway".info += ({dbId: $live.dbId, orgId: $live.orgId} | with_entries(select(.value != null)))
      | if $live.versioning != null then ."x-tyk-api-gateway".info.versioning = $live.versioning else . end' "${file}" - >"${merged_file}"
    body="${merged_file}"
  fi
  curl -fsS -X PUT "${DASHBOARD_URL}/api/apis/oas/${api_id}" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data-binary "@${body}" >/dev/null
}

refuse_unowned() {
  local api_id="$1" existing_owner
  existing_owner="$(dashboard_oas "${api_id}" | jq -r '."x-demo-owner" // empty')"
  [[ "${existing_owner}" == "__SLUG__" ]] || {
    echo "Refusing to replace unowned API ID: ${api_id}" >&2
    exit 1
  }
}

import_api() {
  local file="$1" api_id status query="" live_id=""
  api_id="$(jq -r '."x-tyk-api-gateway".info.id' "${file}")"
  if jq -e 'has("x-demo-version-of")' "${file}" >/dev/null; then
    # A child version: update it when it exists, otherwise create it linked to its base.
    if dashboard_oas "${api_id}" >/dev/null 2>&1; then
      refuse_unowned "${api_id}"
      put_api "${api_id}" "${file}"
      status=200
    else
      query="$(jq -r '."x-demo-version-of" | "?base_api_id=\(.base_api_id | @uri)&base_api_version_name=\(.base_api_version_name | @uri)&new_version_name=\(.new_version_name | @uri)&set_default=\(.set_default // false)"' "${file}")"
      status="$(curl -sS -o "${response_file}" -w '%{http_code}' "${DASHBOARD_URL}/api/apis/oas${query}" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data-binary "@${file}")"
    fi
  else
    status="$(curl -sS -o "${response_file}" -w '%{http_code}' "${DASHBOARD_URL}/api/apis/oas" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data-binary "@${file}")"
    if [[ "${status}" == 409 ]]; then
      refuse_unowned "${api_id}"
      put_api "${api_id}" "${file}"
      status=200
    fi
  fi
  if [[ ! "${status}" =~ ^2 ]]; then
    echo "Import failed for ${api_id}: HTTP ${status}" >&2
    cat "${response_file}" >&2
    exit 1
  fi
  for _ in $(seq 1 20); do
    live_id="$(dashboard_oas "${api_id}" 2>/dev/null | jq -r '."x-tyk-api-gateway".info.id // empty' || true)"
    [[ "${live_id}" == "${api_id}" ]] && break
    sleep 1
  done
  [[ "${live_id}" == "${api_id}" ]] || { echo "Readback failed for ${api_id}." >&2; exit 1; }
}

# Bases (every file without x-demo-version-of) first, then child versions.
for pass in base child; do
  for file in "${ROOT_DIR}"/apis/*.oas.json; do
    [[ -e "${file}" ]] || continue
    is_child="$(jq -r 'has("x-demo-version-of")' "${file}")"
    if [[ "${pass}" == base && "${is_child}" == false ]] || [[ "${pass}" == child && "${is_child}" == true ]]; then
      import_api "${file}"
    fi
  done
done
if module_enabled governance; then
  # API categories are stored outside the OAS document, so an API this import has just created
  # carries none and every governance ruleset would match nothing until they are assigned.
  "${ROOT_DIR}/governance/assign-categories.sh"
fi

echo "API import and readback completed."
