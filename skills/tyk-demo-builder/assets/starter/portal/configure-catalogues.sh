#!/usr/bin/env bash
# portal-catalogues module (proof PORTAL-CAT-001).
#
# One Portal, audience-restricted catalogues:
#   - "__DISPLAY_NAME__ Internal Catalogue": private, audience = organisation "__DISPLAY_NAME__ Internal Teams"
#   - "__DISPLAY_NAME__ Partner Catalogue":  private, audience = organisation "__DISPLAY_NAME__ Partners"
# The generated Portal product goes to both (and stays in the public catalogue). The
# "__DISPLAY_NAME__ Partner Services" product, from its own ACL policy, goes to the partner
# catalogue only, together with a documentation-only product that publishes the partner
# OpenAPI document without issuing credentials. One developer per organisation lets the
# proof show the difference from the developer's side.
#
# What the proof relies on (Portal 1.20.1):
#   - A private catalogue with an audience is shown to members of the audience's team; the
#     audience must carry both the organisation and the team (see ensure_catalogue).
#   - Users are rewritten only when their password is not on record, and each organisation's
#     default team is re-synchronised with its full user list afterwards, so a developer's
#     membership is what this script last wrote.
#   - A product's ReferenceID is the Dashboard `_id` of its policy, not the policy's custom
#     `id`, so products are found by that `_id`, never by display name.
#
# Run after portal/configure-portal.sh. Idempotent: every object is found by name, slug,
# e-mail or ReferenceID and updated in place.
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

[[ -n "${PORTAL_API_TOKEN:-}" ]] || { echo "Run portal/configure-portal.sh first (no PORTAL_API_TOKEN)." >&2; exit 1; }
[[ -n "${DASHBOARD_API_KEY:-}" ]] || { echo "Run scripts/bootstrap-demo-stack.sh first." >&2; exit 1; }
PORTAL_API_BASE="${PORTAL_URL%/}/portal-api"

portal_api() {
  local method="$1" path="$2"
  shift 2
  curl -fsS -X "${method}" "${PORTAL_API_BASE}${path}" -H "Authorization: ${PORTAL_API_TOKEN}" "$@"
}
# The body goes in on stdin, never on curl's command line: developer payloads carry a
# password, and arguments are visible to any local user in the process list.
portal_json() {
  portal_api "$1" "$2" -H 'Content-Type: application/json' --data-binary @- <<<"$3"
}

# --- organisations and their default ("All users") team -----------------------------
ensure_organisation() {
  local name="$1" id
  id="$(portal_api GET '/organisations?per_page=100' | jq -r --arg n "${name}" '.[]? | select(.Name == $n) | .ID' | head -1)"
  if [[ -z "${id}" ]]; then
    id="$(portal_json POST /organisations "$(jq -nc --arg n "${name}" '{Name:$n}')" | jq -er '.ID')"
  fi
  printf '%s' "${id}"
}
default_team() {
  portal_api GET "/organisations/${1}/teams?per_page=100" | jq -r '[.[]? | select(.Default == true)][0].ID // empty'
}

internal_org="$(ensure_organisation "__DISPLAY_NAME__ Internal Teams")"
partner_org="$(ensure_organisation "__DISPLAY_NAME__ Partners")"
internal_team="$(default_team "${internal_org}")"
partner_team="$(default_team "${partner_org}")"
[[ -n "${internal_team}" && -n "${partner_team}" ]] || { echo "Portal organisations have no default team." >&2; exit 1; }

# --- one developer per organisation --------------------------------------------------
# ensure_developer <email> <last-name> <organisation-id> <recorded-password>
# Sets DEVELOPER_ID, and DEVELOPER_PASSWORD to the password the user now has. Runs in the
# current shell, never in a command substitution, so its results reach the caller.
ensure_developer() {
  local email="$1" last="$2" org="$3" payload
  DEVELOPER_PASSWORD="${4}"
  DEVELOPER_ID="$(portal_api GET '/users?per_page=200' | jq -r --arg e "${email}" '.[]? | select(.Email == $e) | .ID' | head -1)"
  if [[ -n "${DEVELOPER_ID}" && -n "${DEVELOPER_PASSWORD}" ]]; then
    return 0
  fi
  [[ -n "${DEVELOPER_PASSWORD}" ]] || DEVELOPER_PASSWORD="$(openssl rand -hex 16)"
  # The password reaches jq through its environment, not its arguments, for the same reason.
  payload="$(portal_password="${DEVELOPER_PASSWORD}" jq -nc --arg e "${email}" --arg f "__DISPLAY_NAME__" --arg l "${last}" --argjson o "${org}" \
    '{Email:$e, First:$f, Last:$l, OrganisationID:$o, Role:"consumer-admin", Provider:"password", Active:true, Password:env.portal_password, ResetPassword:false}')"
  if [[ -z "${DEVELOPER_ID}" ]]; then
    DEVELOPER_ID="$(portal_json POST /users "${payload}" | jq -er '.ID')"
  else
    portal_json PUT "/users/${DEVELOPER_ID}" "${payload}" >/dev/null
  fi
}
# Catalogue audiences are teams: keep every member of the organisation in its default team,
# which is what the Portal does on user creation and what a user PUT undoes.
sync_default_team() {
  local org="$1" team="$2" organisation team_name user_ids
  organisation="$(portal_api GET "/organisations/${org}")"
  team_name="$(jq -r --argjson t "${team}" '.Teams[]? | select(.ID == $t) | .Name' <<<"${organisation}")"
  user_ids="$(jq -c '[.Users[]?.ID]' <<<"${organisation}")"
  portal_json PUT "/organisations/${org}/teams/${team}" "$(jq -nc --arg n "${team_name}" --argjson u "${user_ids}" '{Name:$n, Users:$u}')" >/dev/null
}
assert_team_member() {
  local user_id="$1" org="$2" team="$3" team_name
  team_name="$(portal_api GET "/organisations/${org}/teams?per_page=100" | jq -r --argjson t "${team}" '.[]? | select(.ID == $t) | .Name')"
  portal_api GET "/users/${user_id}" | jq -e --arg n "${team_name}" '(.Teams // []) | index($n) != null' >/dev/null \
    || { echo "Portal user ${user_id} is not a member of team ${team_name}." >&2; exit 1; }
}

internal_email="internal-developer@__SLUG__.invalid"
partner_email="partner-developer@__SLUG__.invalid"
ensure_developer "${internal_email}" "Internal Developer" "${internal_org}" "${PORTAL_INTERNAL_DEVELOPER_PASSWORD:-}"
internal_developer_id="${DEVELOPER_ID}"
runtime_set PORTAL_INTERNAL_DEVELOPER_PASSWORD "${DEVELOPER_PASSWORD}"
ensure_developer "${partner_email}" "Partner Developer" "${partner_org}" "${PORTAL_PARTNER_DEVELOPER_PASSWORD:-}"
partner_developer_id="${DEVELOPER_ID}"
runtime_set PORTAL_PARTNER_DEVELOPER_PASSWORD "${DEVELOPER_PASSWORD}"
sync_default_team "${internal_org}" "${internal_team}"
sync_default_team "${partner_org}" "${partner_team}"
assert_team_member "${internal_developer_id}" "${internal_org}" "${internal_team}"
assert_team_member "${partner_developer_id}" "${partner_org}" "${partner_team}"

# --- products, by the Dashboard policy that defines them -----------------------------
policies="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/portal/policies/?p=-1" -H "authorization: ${DASHBOARD_API_KEY}")" \
  || { echo "Dashboard policy list was not readable." >&2; exit 1; }
products="$(portal_api GET '/products?per_page=200')"
product_by_policy() {
  local reference
  reference="$(jq -r --arg s "$1" '.Data[]? | select(.meta_data.stable_name == $s and .meta_data.demo_owner == "__SLUG__") | ._id' <<<"${policies}" | head -1)"
  [[ -n "${reference}" ]] || { echo "Dashboard policy $1 was not found." >&2; return 1; }
  jq -r --arg r "${reference}" '.[]? | select(.ReferenceID == $r) | .ID' <<<"${products}" | head -1
}
shared_product="$(product_by_policy __SLUG__-portal-product-policy)"
partner_product="$(product_by_policy __SLUG__-partner-product-policy)"
[[ -n "${shared_product}" && -n "${partner_product}" ]] \
  || { echo "Synchronised products are missing (shared=${shared_product} partner=${partner_product}); check the policy partitions and the provider sync." >&2; exit 1; }

describe_product() {
  portal_json PUT "/products/${1}" "$(jq -nc --arg d "$2" --arg s "$3" --arg c "$4" '{DisplayName:$d, Description:$s, Content:$c}')" >/dev/null
}
describe_product "${partner_product}" "__DISPLAY_NAME__ Partner Services" \
  "__DISPLAY_NAME__ services for partner organisations, protected by an API key." \
  "<p>__DISPLAY_NAME__ services available to partner organisations only. Request access to receive an API key.</p>"

# --- documentation-only product: the OpenAPI document, no credentials ----------------
docs_name="__DISPLAY_NAME__ Partner Services Specification"
docs_description="The __DISPLAY_NAME__ Partner Services OpenAPI document, published without credentials."
spec_base_url="${PORTAL_SPEC_BASE_URL:-http://localhost:${UPSTREAM_PORT}}"
spec_url="${spec_base_url%/}/openapi/__SLUG__-partner-services.oas.json"
provider_id="${PORTAL_PROVIDER_ID:-$(portal_api GET "/providers?per_page=100" | jq -r '[.[]? | select(.Name == "__DISPLAY_NAME__ Dashboard")][0].ID // empty')}"
[[ -n "${provider_id}" ]] || { echo "The Dashboard provider was not found; run portal/configure-portal.sh first." >&2; exit 1; }
docs_product="$(jq -r --arg n "${docs_name}" '.[]? | select(.Name == $n and .IsDocumentationOnly == true) | .ID' <<<"${products}" | head -1)"
if [[ -z "${docs_product}" ]]; then
  docs_product="$(portal_json POST /products "$(jq -nc --arg n "${docs_name}" --arg d "${docs_description}" --argjson p "${provider_id}" \
    '{Name:$n, DisplayName:$n, ProviderID:$p, IsDocumentationOnly:true, Description:$d}')" | jq -er '.ID')"
fi
describe_product "${docs_product}" "${docs_name}" "${docs_description}" \
  "<p>Reference documentation only. No credentials are issued from this product.</p>"
if ! portal_api GET "/products/${docs_product}/spec-details?per_page=100" | jq -e --arg u "${spec_url}" '[.[]? | select(.OASUrl == $u)] | length > 0' >/dev/null; then
  portal_json POST "/products/${docs_product}/spec-details" "$(jq -nc --arg u "${spec_url}" --arg n "__DISPLAY_NAME__ Partner Services" \
    '{SpecAlias:"__SLUG__-partner-services", Name:$n, OASUrl:$u}')" >/dev/null
fi
portal_api GET "/products/${docs_product}/spec-details?per_page=100" | jq -e --arg u "${spec_url}" '[.[]? | select(.OASUrl == $u)] | length > 0' >/dev/null \
  || { echo "The documentation-only product did not keep its specification URL." >&2; exit 1; }

# --- plans: the ones synchronised from this bundle's provider -------------------------
plan_ids='[]'
while IFS= read -r plan_id; do
  [[ -n "${plan_id}" ]] || continue
  if portal_api GET "/plans/${plan_id}" | jq -e --argjson provider "${provider_id}" '.ProviderID == $provider or .Provider.ID == $provider' >/dev/null; then
    plan_ids="$(jq -c --argjson id "${plan_id}" '. + [$id]' <<<"${plan_ids}")"
  fi
done < <(portal_api GET '/plans?per_page=200' | jq -r '.[]?.ID')

# --- private catalogues with audiences ------------------------------------------------
# ensure_catalogue <name> <slug> <products-json> <organisation-id> <team-id>; sets CATALOGUE_ID.
ensure_catalogue() {
  local name="$1" slug="$2" products_json="$3" org="$4" team="$5" payload
  CATALOGUE_ID="$(portal_api GET '/catalogues?per_page=100' | jq -r --arg s "${slug}" '.[]? | select(.NameWithSlug == $s) | .ID' | head -1)"
  if [[ -z "${CATALOGUE_ID}" ]]; then
    CATALOGUE_ID="$(portal_json POST /catalogues "$(jq -nc --arg n "${name}" --arg s "${slug}" '{Name:$n, NameWithSlug:$s, VisibilityStatus:"Private", Products:[], Plans:[]}')" | jq -er '.ID')"
  fi
  payload="$(jq -nc --arg n "${name}" --arg s "${slug}" --argjson p "${products_json}" --argjson pl "${plan_ids}" \
    '{Name:$n, NameWithSlug:$s, VisibilityStatus:"Private", Products:$p, Plans:$pl}')"
  portal_json PUT "/catalogues/${CATALOGUE_ID}" "${payload}" >/dev/null
  # Without an audience a private catalogue is visible to every developer, so the audience
  # is the part that makes it private in practice. On Portal 1.20.1 an audience grants
  # visibility only when both its OrganisationID and its TeamID are set, and a newly created
  # audience can read back with OrganisationID 0 (the developer then does not see the
  # catalogue). The audience is therefore created, then corrected in place when its
  # organisation reads back wrong, and asserted on both fields.
  local audience_id
  audience_id="$(portal_api GET "/catalogues/${CATALOGUE_ID}/audiences?per_page=100" | jq -r --argjson t "${team}" '[.[]? | select(.TeamID == $t)][0].ID // empty')"
  if [[ -z "${audience_id}" ]]; then
    audience_id="$(portal_json POST "/catalogues/${CATALOGUE_ID}/audiences" "$(jq -nc --argjson o "${org}" --argjson t "${team}" '{OrganisationID:$o, TeamID:$t}')" | jq -r '.ID // empty')"
  fi
  if ! portal_api GET "/catalogues/${CATALOGUE_ID}/audiences?per_page=100" | jq -e --argjson o "${org}" --argjson t "${team}" '[.[]? | select(.TeamID == $t and .OrganisationID == $o)] | length > 0' >/dev/null; then
    if [[ -n "${audience_id}" ]]; then
      portal_json PUT "/catalogues/${CATALOGUE_ID}/audiences/${audience_id}" "$(jq -nc --argjson o "${org}" --argjson t "${team}" '{OrganisationID:$o, TeamID:$t}')" >/dev/null
    fi
  fi
  portal_api GET "/catalogues/${CATALOGUE_ID}/audiences?per_page=100" | jq -e --argjson o "${org}" --argjson t "${team}" '[.[]? | select(.TeamID == $t and .OrganisationID == $o)] | length > 0' >/dev/null \
    || { echo "Catalogue ${name} did not keep its audience (organisation ${org}, team ${team})." >&2; exit 1; }
}
ensure_catalogue "__DISPLAY_NAME__ Internal Catalogue" "__SLUG__-internal-catalogue" \
  "$(jq -nc --argjson a "${shared_product}" '[$a]')" "${internal_org}" "${internal_team}"
internal_catalogue="${CATALOGUE_ID}"
ensure_catalogue "__DISPLAY_NAME__ Partner Catalogue" "__SLUG__-partner-catalogue" \
  "$(jq -nc --argjson a "${shared_product}" --argjson b "${partner_product}" --argjson c "${docs_product}" '[$a,$b,$c]')" "${partner_org}" "${partner_team}"
partner_catalogue="${CATALOGUE_ID}"

runtime_set PORTAL_INTERNAL_CATALOGUE_ID "${internal_catalogue}"
runtime_set PORTAL_PARTNER_CATALOGUE_ID "${partner_catalogue}"
runtime_set PORTAL_SHARED_PRODUCT_ID "${shared_product}"
runtime_set PORTAL_PARTNER_PRODUCT_ID "${partner_product}"
runtime_set PORTAL_DOCS_PRODUCT_ID "${docs_product}"
runtime_set PORTAL_INTERNAL_DEVELOPER_EMAIL "${internal_email}"
runtime_set PORTAL_PARTNER_DEVELOPER_EMAIL "${partner_email}"
runtime_set PORTAL_INTERNAL_DEVELOPER_ID "${internal_developer_id}"
runtime_set PORTAL_PARTNER_DEVELOPER_ID "${partner_developer_id}"
echo "Portal catalogues configured: internal=${internal_catalogue} partner=${partner_catalogue}; products shared=${shared_product} partner=${partner_product} documentation-only=${docs_product}."
