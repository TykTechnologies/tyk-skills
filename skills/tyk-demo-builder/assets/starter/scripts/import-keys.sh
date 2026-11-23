#!/usr/bin/env bash
# key-import module (proof KEY-IMPORT-001).
#
# Imports consumer credentials exported from a previous gateway with their EXISTING
# values, so migrated consumers keep the key they already have. It uses the Dashboard
# custom-key endpoint: GET /api/keys/{keyId} detects an existing key, POST
# /api/keys/{keyId} creates one with the caller-chosen value, and PUT updates it in
# place, so repeated runs never create a second copy. The alias is asserted on readback.
#
# Every key is attached to the dedicated no-quota policy named in
# migration/imported-keys.json, never to the generated API-key policy, so the proof and
# the presenter can call with an imported key as often as they like without spending the
# demo quota.
#
# Rules worth knowing (gateway source at v5.15.0):
# - A custom key shorter than the gateway's min_token_length (default 3) is refused.
# - With hash_keys on (the image default) the gateway stores only a hash; clients still
#   present the raw value, and the Dashboard readback shows key_hash as null. Assert on
#   key_id and the alias, never on key_hash.
# - Policy meta_data is merged over the key's meta_data on readback, so ownership markers
#   on a key are not reliable when the policy carries the same field.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[[ -n "${DASHBOARD_API_KEY:-}" && -n "${ORG_ID:-}" ]] || { echo "Run bootstrap-demo-stack.sh first." >&2; exit 1; }

export_file="${ROOT_DIR}/migration/imported-keys.json"
example_file="${ROOT_DIR}/migration/imported-keys.example.json"
# imported-keys.json holds live key values, so it is git-ignored and never shared. A shared
# bundle carries only the example; mint fresh values from it, as the generator does.
if [[ ! -f "${export_file}" && -f "${example_file}" ]]; then
  python3 - "${example_file}" "${export_file}" <<'PY'
import json
import os
import secrets
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    data = json.load(source)
for record in data["keys"]:
    record["key"] = "migrated-" + secrets.token_hex(16)
descriptor = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    stream.write(json.dumps(data, indent=2) + "\n")
PY
  echo "Minted fresh key values into migration/imported-keys.json."
fi
[[ -f "${export_file}" ]] || { echo "Missing ${export_file}." >&2; exit 1; }
stable_name="$(jq -r '.policy_stable_name' "${export_file}")"
policies="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/portal/policies/?p=-1" -H "authorization: ${DASHBOARD_API_KEY}")" \
  || { echo "Dashboard policy list was not readable." >&2; exit 1; }
policy_id="$(jq -r --arg s "${stable_name}" '.Data[]? | select(.meta_data.stable_name == $s and .meta_data.demo_owner == "__SLUG__") | .id' <<<"${policies}" | head -1)"
[[ -n "${policy_id}" ]] || { echo "Owned policy ${stable_name} was not found; run create-policies.sh first." >&2; exit 1; }

count=0
while IFS= read -r record; do
  [[ -n "${record}" ]] || continue
  key="$(jq -r '.key' <<<"${record}")"
  alias_name="$(jq -r '.alias' <<<"${record}")"
  if (( ${#key} < 3 )); then
    echo "Imported key for ${alias_name} is shorter than the gateway's default min_token_length (3)." >&2
    exit 1
  fi
  payload="$(jq -c --arg org "${ORG_ID}" --arg pol "${policy_id}" \
    '{org_id:$org, alias:.alias, apply_policies:[$pol], quota_max:-1, meta_data:(.meta_data // {})}' <<<"${record}")"
  existing="$(curl -sS -o /dev/null -w '%{http_code}' "${DASHBOARD_URL}/api/keys/${key}" -H "authorization: ${DASHBOARD_API_KEY}" || echo 000)"
  method=POST
  [[ "${existing}" == 200 ]] && method=PUT
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X "${method}" "${DASHBOARD_URL}/api/keys/${key}" \
    -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data "${payload}" || echo 000)"
  [[ "${code}" == 200 ]] || { echo "Import of ${alias_name} failed with HTTP ${code} (${method})." >&2; exit 1; }
  readback="$(retry_capture 10 1 curl -fsS "${DASHBOARD_URL}/api/keys/${key}" -H "authorization: ${DASHBOARD_API_KEY}")" \
    || { echo "${alias_name} was imported but could not be read back." >&2; exit 1; }
  jq -e --arg alias "${alias_name}" '.data.alias == $alias' <<<"${readback}" >/dev/null \
    || { echo "${alias_name} was imported but its alias did not read back." >&2; exit 1; }
  count=$((count + 1))
  action=imported
  [[ "${method}" == PUT ]] && action=updated
  echo "${alias_name} ${action} with its existing credential value."
done < <(jq -c '.keys[]' "${export_file}")
echo "Imported ${count} consumer credential(s) with their existing values."
