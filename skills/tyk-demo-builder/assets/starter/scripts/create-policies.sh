#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[[ -n "${DASHBOARD_API_KEY:-}" && -n "${ORG_ID:-}" ]] || { echo "Run bootstrap-demo-stack.sh first." >&2; exit 1; }

# `[[ -e ]] || continue` keeps the loop's exit status 0 when the glob matches nothing (a
# policy-less module set), otherwise `set -e` aborts on the failing command substitution.
desired_ids="$(for file in "${ROOT_DIR}"/policies/*.json; do [[ -e "${file}" ]] || continue; jq -r '.id' "${file}"; done)"
# Every LIST call reads all pages (?p=-1). The default page holds about ten policies, so with
# more than that an existing policy is not found, the create below is refused with HTTP 400
# "This policy name has already been used", and pruning misses policies on later pages. The
# create (POST) URL takes no query.
policies="$(curl -fsS "${DASHBOARD_URL}/api/portal/policies/?p=-1" -H "authorization: ${DASHBOARD_API_KEY}")"
while IFS=$'\t' read -r policy_id database_id; do
  [[ -n "${policy_id}" && -n "${database_id}" ]] || continue
  grep -Fxq "${policy_id}" <<<"${desired_ids}" && continue
  curl -fsS -X DELETE "${DASHBOARD_URL}/api/portal/policies/${database_id}" -H "authorization: ${DASHBOARD_API_KEY}" >/dev/null
done < <(jq -r '.Data[]? | select(.meta_data.demo_owner == "__SLUG__") | [.id, ._id] | @tsv' <<<"${policies}")

for file in "${ROOT_DIR}"/policies/*.json; do
  [[ -e "${file}" ]] || continue
  stable_id="$(jq -r '.id' "${file}")"
  existing="$(curl -fsS "${DASHBOARD_URL}/api/portal/policies/?p=-1" -H "authorization: ${DASHBOARD_API_KEY}" | jq -r --arg id "${stable_id}" '.Data[]? | select(.id == $id and .meta_data.demo_owner == "__SLUG__") | ._id' | head -1)"
  tmp="$(mktemp)"
  jq --arg org "${ORG_ID}" '.org_id = $org' "${file}" >"${tmp}"
  if [[ -n "${existing}" ]]; then
    curl -fsS -X PUT "${DASHBOARD_URL}/api/portal/policies/${existing}" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data-binary "@${tmp}" >/dev/null
  else
    curl -fsS "${DASHBOARD_URL}/api/portal/policies/" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data-binary "@${tmp}" >/dev/null
  fi
  rm -f "${tmp}"
done
echo "Owned policies configured."
