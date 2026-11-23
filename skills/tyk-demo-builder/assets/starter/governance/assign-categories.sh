#!/usr/bin/env bash
set -euo pipefail

# Assign the governance demo category to both governed APIs.
#
# This is a separate script because it has two callers. `configure-governance.sh` needs the
# category to exist before it can create a ruleset that references it (the Dashboard answers
# 400 "categories [...] are not valid org API categories" otherwise), and `import-apis.sh`
# runs it after every import: categories are stored outside the OAS document, so an API the
# import has just created carries none, and every ruleset would match nothing. (A PUT of the
# OAS document to an existing API leaves its categories as they were.)

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

[[ -n "${DASHBOARD_API_KEY:-}" ]] || {
  echo "Run scripts/bootstrap-demo-stack.sh before assigning governance categories." >&2
  exit 1
}

CATEGORY="__SLUG__-payments"
GOVERNED_API_IDS=("__SLUG__-governed-legacy" "__SLUG__-governed-standard")
RETRY_ATTEMPTS="${GOVERNANCE_RETRY_ATTEMPTS:-10}"
RETRY_DELAY="${GOVERNANCE_RETRY_DELAY:-2}"

mkdir -p "${RUNTIME_DIR}"
chmod 700 "${RUNTIME_DIR}"
response_file="$(mktemp "${RUNTIME_DIR}/governance-categories.XXXXXX")"
trap 'rm -f "${response_file}"' EXIT

last_status=""

# common.sh documents that Dashboard endpoints can answer 403 briefly right after bootstrap
# while the org and credentials propagate. A single 403 is therefore not proof that the
# licence lacks the governance claim, so every call retries before that conclusion is drawn.
request_with_retry() {
  local method="$1" url="$2" payload="${3:-}" attempt
  for attempt in $(seq 1 "${RETRY_ATTEMPTS}"); do
    if [[ -n "${payload}" ]]; then
      last_status="$(curl -sS -o "${response_file}" -w '%{http_code}' -X "${method}" "${url}" \
        -H "authorization: ${DASHBOARD_API_KEY}" \
        -H 'Content-Type: application/json' \
        --data "${payload}")"
    else
      last_status="$(curl -sS -o "${response_file}" -w '%{http_code}' -X "${method}" "${url}" \
        -H "authorization: ${DASHBOARD_API_KEY}")"
    fi
    if [[ "${last_status}" != 403 ]]; then
      return 0
    fi
    if [[ "${attempt}" == "${RETRY_ATTEMPTS}" ]]; then
      return 0
    fi
    sleep "${RETRY_DELAY}"
  done
}

governance_unlicensed() {
  echo "Dashboard returned 403 for ${1} on every one of ${RETRY_ATTEMPTS} attempts." >&2
  echo "API Governance requires the 'governance' claim in the Dashboard license scope." >&2
  echo "It is disabled by default and enabled per organisation on request to Tyk, not self-service." >&2
  runtime_set GOVERNANCE_LICENSED false
  exit 0
}

category_list() {
  # The response shape is read defensively: indexing an array with a string is a hard jq
  # error, so the array case is decided before any object key is touched.
  jq -c '[((if type == "array" then . else (.categories // .Categories // []) end)[]?)]' \
    <"${response_file}" 2>/dev/null || printf '[]'
}

assign_category() {
  local api_id="$1" current union

  request_with_retry GET "${DASHBOARD_URL}/api/apis/oas/${api_id}/categories"
  if [[ "${last_status}" == 403 ]]; then
    governance_unlicensed "GET /api/apis/oas/${api_id}/categories"
  fi
  if [[ ! "${last_status}" =~ ^2 ]]; then
    echo "Could not read categories for ${api_id}: HTTP ${last_status}" >&2
    cat "${response_file}" >&2
    exit 1
  fi
  current="$(category_list)"

  # Merge rather than replace. The demo owns one category; anything else already on the API
  # belongs to whoever put it there, and a blind PUT would delete it.
  if ! union="$(jq -c --arg category "${CATEGORY}" '. + [$category] | unique' <<<"${current}")"; then
    union="$(jq -nc --arg category "${CATEGORY}" '[$category]')"
  fi

  request_with_retry PUT "${DASHBOARD_URL}/api/apis/oas/${api_id}/categories" \
    "$(jq -nc --argjson categories "${union}" '{categories: $categories}')"
  if [[ "${last_status}" == 403 ]]; then
    governance_unlicensed "PUT /api/apis/oas/${api_id}/categories"
  fi
  if [[ ! "${last_status}" =~ ^2 ]]; then
    echo "Category assignment failed for ${api_id}: HTTP ${last_status}" >&2
    cat "${response_file}" >&2
    exit 1
  fi

  request_with_retry GET "${DASHBOARD_URL}/api/apis/oas/${api_id}/categories"
  if [[ ! "${last_status}" =~ ^2 ]] || ! jq -e --arg category "${CATEGORY}" \
    'index($category) != null' <<<"$(category_list)" >/dev/null; then
    echo "Category ${CATEGORY} was not readable back on ${api_id}." >&2
    exit 1
  fi
}

for api_id in "${GOVERNED_API_IDS[@]}"; do
  assign_category "${api_id}"
done

echo "Governance category ${CATEGORY} asserted on ${#GOVERNED_API_IDS[@]} governed APIs."
