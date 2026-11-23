#!/usr/bin/env bash
# dcr module, Portal side (proof PORTAL-DCR-AUD-001).
#
# Wires Portal Dynamic Client Registration to the bundled identity service for the
# "__DISPLAY_NAME__ Self-Service" product (the JWT API), then triggers the two Portal events
# that rewrite a JWT API's security scheme and snapshots the API after each:
#   1. a product save with DCR enabled (enabling DCR is the first such save), and
#   2. the approval of a DCR access request (a developer app provisioned for the product).
#
# Portal 1.17 and 1.18 regenerated the JWT scheme on these events and dropped
# allowedAudiences and allowedIssuers and zeroed the JWKS cacheTimeout, so a token for any
# audience from any issuer signed by the JWKS was accepted. Portal 1.19.0 with Dashboard
# 5.15.0 keeps them; the product save still adds defaultPolicies, policyFieldName,
# basePolicyClaims, subjectClaims and jtiValidation. The snapshots go to
# evidence/portal-dcr-events.json, which scripts/verify-demo.sh asserts.
#
# The JWT API is restored from apis/ before each event, so each snapshot shows only what
# that event changed, and re-imported afterwards, so the running definition matches the
# bundle again. The re-import removes the Portal-managed defaultPolicies; the next product
# save puts them back.
#
# Idempotent: provider, client type, organisation, developer and app are found by name or
# e-mail; the product save repeats on every run (it is the regression trigger); the approval
# runs once per app, and its snapshot is kept from the run that performed it.
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

[[ -n "${PORTAL_API_TOKEN:-}" && -n "${DASHBOARD_API_KEY:-}" && -n "${ORG_ID:-}" ]] \
  || { echo "Run scripts/bootstrap-demo-stack.sh and portal/configure-portal.sh first." >&2; exit 1; }
PORTAL_API_BASE="${PORTAL_URL%/}/portal-api"
JWT_API_ID="__SLUG__-jwt"
EVENTS_FILE="${ROOT_DIR}/evidence/portal-dcr-events.json"
mkdir -p "${ROOT_DIR}/evidence/bodies"

portal_api() {
  local method="$1" path="$2"
  shift 2
  curl -fsS -X "${method}" "${PORTAL_API_BASE}${path}" -H "Authorization: ${PORTAL_API_TOKEN}" "$@"
}
# The body goes in on stdin, never on curl's command line: the integrator payload carries a
# password, and arguments are visible to any local user in the process list.
portal_json() { portal_api "$1" "$2" -H 'Content-Type: application/json' --data-binary @- <<<"$3"; }

# version_at_least <tag> <minimum>: true when vX.Y.Z >= minimum (pre-release suffix ignored).
version_at_least() {
  local IFS=.
  local -a have want
  read -r -a have <<<"${1#v}"
  read -r -a want <<<"${2#v}"
  local index
  for index in 0 1 2; do
    local left="${have[index]:-0}" right="${want[index]:-0}"
    left="${left%%-*}"
    (( 10#${left} > 10#${right} )) && return 0
    (( 10#${left} < 10#${right} )) && return 1
  done
  return 0
}

# Snapshot of the JWT scheme as the Dashboard stores it right now.
jwt_snapshot() {
  local event="$1" body_file="${ROOT_DIR}/evidence/bodies/portal-dcr-jwt-${1}.json"
  curl -fsS "${DASHBOARD_URL}/api/apis/oas/${JWT_API_ID}" -H "authorization: ${DASHBOARD_API_KEY}" \
    | jq '."x-tyk-api-gateway".server.authentication.securitySchemes.jwtAuth' >"${body_file}"
  jq -c --arg event "${event}" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{
      event: $event, recorded_at: $at,
      portal_rewrite_detected: ((.defaultPolicies // []) | length > 0),
      allowedAudiences: (.allowedAudiences // []),
      allowedIssuers: (.allowedIssuers // []),
      jwksCacheTimeouts: [(.jwksURIs // [])[] | .cacheTimeout],
      defaultPolicies: (.defaultPolicies // []),
      policyFieldName: (.policyFieldName // "")
    }' "${body_file}"
}

restore_jwt_api() {
  curl -fsS -o /dev/null -X PUT "${DASHBOARD_URL}/api/apis/oas/${JWT_API_ID}" -H "authorization: ${DASHBOARD_API_KEY}" \
    -H 'Content-Type: application/json' --data-binary "@${ROOT_DIR}/apis/${JWT_API_ID}.oas.json"
  sleep 1
}

# --- the JWT product and the plan, by the Dashboard policies that define them ----------
policies="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/portal/policies/?p=-1" -H "authorization: ${DASHBOARD_API_KEY}")" \
  || { echo "Dashboard policy list was not readable." >&2; exit 1; }
policy_reference() {
  jq -r --arg s "$1" '.Data[]? | select(.meta_data.stable_name == $s and .meta_data.demo_owner == "__SLUG__") | ._id' <<<"${policies}" | head -1
}
product_reference="$(policy_reference __SLUG__-jwt-product-policy)"
plan_reference="$(policy_reference __SLUG__-portal-plan-policy)"
[[ -n "${product_reference}" && -n "${plan_reference}" ]] || { echo "The JWT product or plan policy is missing; run create-policies.sh." >&2; exit 1; }
jwt_product="$(portal_api GET '/products?per_page=200' | jq -r --arg r "${product_reference}" '.[]? | select(.ReferenceID == $r) | .ID' | head -1)"
plan_id="$(portal_api GET '/plans?per_page=200' | jq -r --arg r "${plan_reference}" '.[]? | select(.ReferenceID == $r) | .ID' | head -1)"
[[ -n "${jwt_product}" && -n "${plan_id}" ]] || { echo "The JWT product or the plan was not synchronised to the Portal (product=${jwt_product} plan=${plan_id})." >&2; exit 1; }
portal_json PUT "/products/${jwt_product}" "$(jq -nc \
  '{DisplayName:"__DISPLAY_NAME__ Self-Service", Description:"__DISPLAY_NAME__ services protected by OIDC JWT validation, with self-service client registration.",
    Content:"<p>Client credentials for this product are registered with the identity provider through Dynamic Client Registration.</p>"}')" >/dev/null

# --- OAuth provider (Type is a capitalised enum) and client type (the DCR template) --
# The Portal container reads the discovery document and then calls the
# registration_endpoint in it, so the provider URL must be reachable from the Portal: the
# Compose service name, not localhost. The identity service builds its advertised URLs
# from the Host it was called with, so the endpoint stays on identity:8080.
provider_name="__DISPLAY_NAME__ Identity Provider"
oauth_provider="$(portal_api GET "/oauth-providers?per_page=100" | jq -r --arg n "${provider_name}" '.[]? | select(.Name == $n) | .ID' | head -1)"
if [[ -z "${oauth_provider}" ]]; then
  oauth_provider="$(portal_json POST /oauth-providers "$(jq -nc --arg n "${provider_name}" \
    '{Name:$n, Type:"Other", WellKnownURL:"http://identity:8080/.well-known/openid-configuration"}')" | jq -er '.ID')"
fi
client_type_name="__DISPLAY_NAME__ Machine-to-Machine"
client_type="$(portal_api GET "/oauth-providers/${oauth_provider}/client-types?per_page=100" | jq -r --arg n "${client_type_name}" '.[]? | select(.Name == $n) | .ID' | head -1)"
if [[ -z "${client_type}" ]]; then
  client_type="$(portal_json POST "/oauth-providers/${oauth_provider}/client-types" "$(jq -nc --arg n "${client_type_name}" \
    '{Name:$n, Description:"Server-side integrations using the client credentials grant.", ResponseTypes:"token", GrantType:"client_credentials", TokenEndpointAuthMethod:"client_secret_basic"}')" | jq -er '.ID')"
fi

# --- event 1: product save with DCR enabled -------------------------------------------
# Earlier Portal saves in this run (configure-portal.sh attaches specifications, which is a
# product save) may already have rewritten the JWT API, so restore it first: the baseline is
# then exactly the bundle's definition and the save below is the only change measured.
restore_jwt_api
baseline="$(jwt_snapshot baseline)"
jq -e '.portal_rewrite_detected == false' <<<"${baseline}" >/dev/null \
  || { echo "The JWT API still carries Portal-managed fields after restoring it from apis/." >&2; exit 1; }
dcr_was_enabled="$(portal_api GET "/products/${jwt_product}" | jq -r '.DCREnabled')"
portal_json PUT "/products/${jwt_product}" "$(jq -nc --argjson t "${client_type}" '{DCREnabled:true, Templates:[$t]}')" >/dev/null
portal_api GET "/products/${jwt_product}" | jq -e '.DCREnabled == true and ((.Templates // []) | length > 0)' >/dev/null \
  || { echo "DCR did not stay enabled on the JWT product." >&2; exit 1; }
sleep 2
if [[ "${dcr_was_enabled}" == true ]]; then
  save_event="$(jwt_snapshot product-save-with-dcr)"
else
  save_event="$(jwt_snapshot dcr-enable)"
fi

# --- event 2: approval of a DCR access request (once per app) --------------------------
# The admin provisioning and approval endpoints used here are available from Portal 1.19.0.
approval_supported=false
approval_event=""
app_id=""
if version_at_least "${PORTAL_VERSION:-v0.0.0}" v1.19.0; then
  approval_supported=true
  organisation_name="__DISPLAY_NAME__ Integrators"
  organisation="$(portal_api GET '/organisations?per_page=100' | jq -r --arg n "${organisation_name}" '.[]? | select(.Name == $n) | .ID' | head -1)"
  if [[ -z "${organisation}" ]]; then
    organisation="$(portal_json POST /organisations "$(jq -nc --arg n "${organisation_name}" '{Name:$n}')" | jq -er '.ID')"
  fi
  integrator_email="integrator@__SLUG__.invalid"
  integrator="$(portal_api GET '/users?per_page=200' | jq -r --arg e "${integrator_email}" '.[]? | select(.Email == $e) | .ID' | head -1)"
  if [[ -z "${integrator}" ]]; then
    integrator_password="$(openssl rand -hex 16)"
    # The password reaches jq through its environment, not its arguments (see portal_json).
    integrator="$(portal_json POST /users "$(portal_password="${integrator_password}" jq -nc --arg e "${integrator_email}" --argjson o "${organisation}" \
      '{Email:$e, First:"__DISPLAY_NAME__", Last:"Integrator", OrganisationID:$o, Role:"consumer-admin", Provider:"password", Active:true, Password:env.portal_password, ResetPassword:false}')" | jq -er '.ID')"
    runtime_set PORTAL_INTEGRATOR_PASSWORD "${integrator_password}"
  fi
  runtime_set PORTAL_INTEGRATOR_EMAIL "${integrator_email}"
  app_name="__DISPLAY_NAME__ Integration Client"
  app_id="$(portal_api GET '/apps?per_page=200' | jq -r --arg n "${app_name}" '.[]? | select(.Name == $n) | .ID' | head -1)"
  if [[ -z "${app_id}" ]]; then
    app_id="$(portal_json POST /apps "$(jq -nc --arg n "${app_name}" --argjson u "${integrator}" \
      '{Name:$n, Description:"Server-side integration using the client credentials grant.", UserID:$u, Visibility:"organisation"}')" | jq -er '.ID')"
  fi
  approved="$(portal_api GET "/apps/${app_id}/access-requests?per_page=100" | jq -r '[.[]? | select(.Status == "approved")] | length')"
  if [[ "${approved}" == 0 ]]; then
    pending="$(portal_api GET "/apps/${app_id}/access-requests?per_page=100" | jq -r '[.[]? | select(.Status == "pending") | .ID][0] // empty')"
    if [[ -z "${pending}" ]]; then
      portal_json PUT "/apps/${app_id}/provision" "$(jq -nc --argjson p "${plan_id}" --argjson t "${client_type}" --argjson pr "${jwt_product}" \
        '{PlanID:$p, DCRTemplateID:$t, ProductIDs:[$pr], ProvisionImmediately:false}')" >/dev/null
      pending="$(portal_api GET "/apps/${app_id}/access-requests?per_page=100" | jq -r '[.[]? | select(.Status == "pending") | .ID][0] // empty')"
    fi
    [[ -n "${pending}" ]] || { echo "No pending DCR access request was created for ${app_name}." >&2; exit 1; }
    # Restore again, so the approval snapshot shows only what the approval changed.
    restore_jwt_api
    portal_api PUT "/access_requests/${pending}/approve" >/dev/null
    portal_api GET "/access_requests/${pending}" | jq -e '.Status == "approved"' >/dev/null \
      || { echo "DCR access request ${pending} was not approved." >&2; exit 1; }
    sleep 2
    approval_event="$(jwt_snapshot access-request-approval)"
  fi
else
  echo "Portal ${PORTAL_VERSION:-unknown} predates the admin provisioning endpoints (1.19.0); only the product-save event is recorded." >&2
fi

# The approval snapshot is kept from the run that performed the approval, and only when it
# belongs to this stack.
previous_approval='null'
if [[ -f "${EVENTS_FILE}" ]] && [[ "$(jq -r '.org_id // empty' "${EVENTS_FILE}" 2>/dev/null)" == "${ORG_ID}" ]]; then
  previous_approval="$(jq -c '[.events[]? | select(.event == "access-request-approval")][0] // null' "${EVENTS_FILE}" 2>/dev/null || echo null)"
fi
jq -n --arg org "${ORG_ID}" --arg portal "${PORTAL_VERSION:-}" --arg dashboard "${DASHBOARD_VERSION:-}" \
  --argjson baseline "${baseline}" --argjson save "${save_event}" --argjson supported "${approval_supported}" \
  --argjson approval "${approval_event:-null}" --argjson previous "${previous_approval}" \
  '{proof:"PORTAL-DCR-AUD-001", org_id:$org, portal_version:$portal, dashboard_version:$dashboard,
    recorded_at:(now | todate), approval_supported:$supported, baseline:$baseline,
    events: ([$save] + [($approval // $previous)] | map(select(. != null)))}' >"${EVENTS_FILE}.tmp"
mv "${EVENTS_FILE}.tmp" "${EVENTS_FILE}"

# Leave the running JWT API exactly as the bundle defines it.
restore_jwt_api

runtime_set PORTAL_DCR_PROVIDER_ID "${oauth_provider}"
runtime_set PORTAL_DCR_CLIENT_TYPE_ID "${client_type}"
runtime_set PORTAL_DCR_PRODUCT_ID "${jwt_product}"
runtime_set PORTAL_DCR_APP_ID "${app_id}"
echo "Portal DCR configured (provider ${oauth_provider}, template ${client_type}, product ${jwt_product}); JWT scheme snapshots in evidence/portal-dcr-events.json."
