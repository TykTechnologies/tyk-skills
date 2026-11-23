#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

"${ROOT_DIR}/scripts/import-apis.sh"
"${ROOT_DIR}/scripts/create-policies.sh"
"${ROOT_DIR}/scripts/import-apis.sh"

# After the REST APIs exist: a REST-as-MCP proxy resolves tyk://<rest-api-id>/mcp
# at import, so its source API has to be present first.
if module_enabled mcp; then
  "${ROOT_DIR}/scripts/import-mcps.sh"
fi

dashboard_policies() {
  # All pages: the default page holds about ten policies, so a lookup by stable name would
  # miss a policy on a later page.
  retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/portal/policies/?p=-1" -H "authorization: ${DASHBOARD_API_KEY}"
}

if module_enabled api-key && [[ -z "${DEMO_API_KEY:-}" ]]; then
  policies="$(dashboard_policies)" || { echo "Dashboard policy list was not readable." >&2; exit 1; }
  policy_id="$(printf '%s' "${policies}" | jq -r '.Data[]? | select(.meta_data.stable_name == "__SLUG__-api-key-policy" and .meta_data.demo_owner == "__SLUG__") | .id' | head -1)"
  [[ -n "${policy_id}" ]] || { echo "Owned API key policy was not found." >&2; exit 1; }
  response="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/keys" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data "{\"org_id\":\"${ORG_ID}\",\"apply_policies\":[\"${policy_id}\"],\"meta_data\":{\"demo_owner\":\"__SLUG__\"}}")" \
    || { echo "Dashboard did not accept the API key request." >&2; exit 1; }
  api_key="$(printf '%s' "${response}" | jq -r '.key_id // .key // .Key // .api_model.key // empty')"
  [[ -n "${api_key}" ]] || { echo "Dashboard did not return an API key." >&2; exit 1; }
  runtime_set DEMO_API_KEY "${api_key}"
fi

# One key for the gateway-sharding and js-plugin proofs, on its own no-quota policy, so those
# proofs never spend the generated API-key policy's quota (10 per minute with quota selected).
if { module_enabled gateway-sharding || module_enabled js-plugin; } && [[ -z "${PLATFORM_API_KEY:-}" ]]; then
  policies="$(dashboard_policies)" || { echo "Dashboard policy list was not readable." >&2; exit 1; }
  platform_policy_id="$(printf '%s' "${policies}" | jq -r '.Data[]? | select(.meta_data.stable_name == "__SLUG__-platform-policy" and .meta_data.demo_owner == "__SLUG__") | .id' | head -1)"
  [[ -n "${platform_policy_id}" ]] || { echo "Owned platform consumers policy was not found." >&2; exit 1; }
  response="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/keys" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data "{\"org_id\":\"${ORG_ID}\",\"alias\":\"__DISPLAY_NAME__ Platform Consumer\",\"apply_policies\":[\"${platform_policy_id}\"],\"meta_data\":{\"demo_owner\":\"__SLUG__\"}}")" \
    || { echo "Dashboard did not accept the platform key request." >&2; exit 1; }
  platform_key="$(printf '%s' "${response}" | jq -r '.key_id // .key // .Key // .api_model.key // empty')"
  [[ -n "${platform_key}" ]] || { echo "Dashboard did not return a platform key." >&2; exit 1; }
  runtime_set PLATFORM_API_KEY "${platform_key}"
fi

if module_enabled key-import; then
  # Consumer credentials from a previous gateway, imported with their existing values.
  "${ROOT_DIR}/scripts/import-keys.sh"
fi

if module_enabled mcp && [[ -z "${DEMO_MCP_KEY:-}" ]]; then
  policies="$(dashboard_policies)" || { echo "Dashboard policy list was not readable." >&2; exit 1; }
  mcp_policy_id="$(printf '%s' "${policies}" | jq -r '.Data[]? | select(.meta_data.stable_name == "__SLUG__-mcp-policy" and .meta_data.demo_owner == "__SLUG__") | .id' | head -1)"
  [[ -n "${mcp_policy_id}" ]] || { echo "Owned MCP policy was not found." >&2; exit 1; }
  response="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/keys" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data "{\"org_id\":\"${ORG_ID}\",\"apply_policies\":[\"${mcp_policy_id}\"],\"meta_data\":{\"demo_owner\":\"__SLUG__\"}}")" \
    || { echo "Dashboard did not accept the MCP key request." >&2; exit 1; }
  mcp_key="$(printf '%s' "${response}" | jq -r '.key_id // .key // .Key // .api_model.key // empty')"
  [[ -n "${mcp_key}" ]] || { echo "Dashboard did not return an MCP key." >&2; exit 1; }
  runtime_set DEMO_MCP_KEY "${mcp_key}"
fi

create_basic_identity() {
  local runtime_prefix="$1" username="$2" api_id="$3" password_var password payload
  password_var="${runtime_prefix}_PASSWORD"
  password="${!password_var:-}"
  [[ -n "${password}" ]] || password="$(openssl rand -hex 18)"
  # The password reaches jq through its environment and curl on stdin, never through
  # arguments, which any local user can read from the process list.
  payload="$(basic_password="${password}" jq -nc --arg org "${ORG_ID}" --arg api "${api_id}" --arg owner "__SLUG__" '{org_id:$org,basic_auth_data:{password:env.basic_password,hash_type:"bcrypt"},access_rights:{($api):{api_id:$api,api_name:$api,versions:["Default"]}},meta_data:{demo_owner:$owner}}')"
  gw_curl -fsS -X POST "${GATEWAY_URL}/tyk/keys/${username}" -H "x-tyk-authorization: ${NODE_SECRET}" -H 'Content-Type: application/json' --data-binary @- <<<"${payload}" >/dev/null
  runtime_set "${runtime_prefix}_USERNAME" "${username}"
  runtime_set "${runtime_prefix}_PASSWORD" "${password}"
}

if module_enabled basic-auth; then
  create_basic_identity BASIC_AUTH "__SLUG__-basic-user" "__SLUG__-basic-auth"
fi

if module_enabled chained-auth; then
  create_basic_identity CHAINED_AUTH "__SLUG__-chained-user" "__SLUG__-chained-auth"
fi

if module_enabled oauth && [[ -z "${OAUTH_CLIENT_ID:-}" || -z "${OAUTH_CLIENT_SECRET:-}" ]]; then
  policies="$(dashboard_policies)" || { echo "Dashboard policy list was not readable." >&2; exit 1; }
  policy_id="$(printf '%s' "${policies}" | jq -r '.Data[]? | select(.meta_data.stable_name == "__SLUG__-oauth-policy" and .meta_data.demo_owner == "__SLUG__") | .id' | head -1)"
  [[ -n "${policy_id}" ]] || { echo "Owned OAuth policy was not found." >&2; exit 1; }
  response="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/apis/oauth/__SLUG__-oauth" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data "{\"redirect_uri\":\"http://localhost/demo/oauth-callback\",\"policy_id\":\"${policy_id}\"}")" \
    || { echo "Dashboard did not accept the OAuth client request." >&2; exit 1; }
  client_id="$(jq -r '.client_id // empty' <<<"${response}")"
  client_secret="$(jq -r '.secret // empty' <<<"${response}")"
  [[ -n "${client_id}" && -n "${client_secret}" ]] || { echo "Dashboard did not return OAuth client credentials." >&2; exit 1; }
  runtime_set OAUTH_CLIENT_ID "${client_id}"
  runtime_set OAUTH_CLIENT_SECRET "${client_secret}"
fi

if module_enabled mtls; then
  cert_id="${CLIENT_CA_CERT_ID:-}"
  if [[ -z "${cert_id}" ]]; then
    cert_response="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/certs" -H "authorization: ${DASHBOARD_API_KEY}" -F "file=@${ROOT_DIR}/generated/certs/ca.crt")" \
      || { echo "Dashboard did not accept the client CA certificate." >&2; exit 1; }
    cert_id="$(printf '%s' "${cert_response}" | jq -r '.id // .ID // .Meta.id // .Meta.ID // empty')"
    runtime_set CLIENT_CA_CERT_ID "${cert_id}"
  fi
  [[ -n "${cert_id}" ]] || { echo "Dashboard did not return a client CA certificate ID." >&2; exit 1; }
  jq --arg cert "${cert_id}" '."x-tyk-api-gateway".server.clientCertificates.allowlist = [$cert]' "${ROOT_DIR}/apis/mtls.oas.template.json" >"${ROOT_DIR}/apis/__SLUG__-mtls.oas.json"
  "${ROOT_DIR}/scripts/import-apis.sh"
fi

if module_enabled vault; then
  # Vault dev mode keeps its data in memory, so the credential is seeded on every run; an
  # existing value is never overwritten.
  "${ROOT_DIR}/scripts/configure-vault.sh"
fi

if module_enabled upstream-mtls; then
  # Uploads the gateway client certificate and the pinned public keys, then renders the API.
  "${ROOT_DIR}/scripts/configure-upstream-mtls.sh"
  "${ROOT_DIR}/scripts/import-apis.sh"
fi

if module_enabled governance; then
  # Runs after import-apis.sh so both governed APIs exist: a ruleset can only reference a
  # category that already exists in the organisation, and a category only exists once an
  # API carries it.
  "${ROOT_DIR}/governance/configure-governance.sh"
fi

if module_enabled portal; then
  # Operator-supplied values are kept (common.sh never clears them) and recorded, so a
  # later shell without the exports still knows the credentials the Portal was built with.
  runtime_set PORTAL_ADMIN_EMAIL "${PORTAL_ADMIN_EMAIL:-portal-owner@__SLUG__.invalid}"
  runtime_set PORTAL_ADMIN_PASSWORD "${PORTAL_ADMIN_PASSWORD:-$(openssl rand -hex 18)}"
  "${ROOT_DIR}/portal/configure-portal.sh"
  if module_enabled portal-catalogues; then
    "${ROOT_DIR}/portal/configure-catalogues.sh"
  fi
  # The Portal is always shown in the brand kit's branding: configure-portal.sh applied the
  # theme, and the stock Home and About copy is replaced here.
  if module_enabled portal-branding; then
    "${ROOT_DIR}/portal/customise-content.sh"
  fi
  # Portal-side DCR is wired to the bundled identity service, so it needs the local one.
  if module_enabled dcr && [[ "$(jq -r '.identity_provider_mode' "${ROOT_DIR}/demo.config.json")" == local ]]; then
    "${ROOT_DIR}/portal/configure-dcr.sh"
  fi
fi

echo "Demo configuration completed without displaying secrets."
