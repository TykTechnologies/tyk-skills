#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

: "${PORTAL_ADMIN_EMAIL:?Set PORTAL_ADMIN_EMAIL for API-first Portal bootstrap}"
: "${PORTAL_ADMIN_PASSWORD:?Set PORTAL_ADMIN_PASSWORD for API-first Portal bootstrap}"
[[ -n "${DASHBOARD_API_KEY:-}" && -n "${ORG_ID:-}" ]] || {
  echo "Run scripts/bootstrap-demo-stack.sh before configuring Portal." >&2
  exit 1
}

PORTAL_API_BASE="${PORTAL_URL%/}/portal-api"
PORTAL_API_TOKEN="${PORTAL_API_TOKEN:-}"

# The Portal starts in two phases: a temporary bootstrap server serves /portal-api/bootstrap,
# then SHUTS DOWN and the main application starts listening on the same port. Configuration
# therefore has to survive an HTTP server restart in the middle of its own run. Waiting only
# for the Portal to answer at startup is not enough, which is why the documented remedy was to
# run configuration twice: repetition hid the restart rather than handling it, and gave no
# signal when the second run was also too early. A clean bundle needed three runs.
#
# The main API answers 401/403 when it is mounted and 404 while it is not, so a non-404 is the
# readiness signal. Configuration remains idempotent; it just no longer relies on repetition.
portal_ready_timeout="${PORTAL_READY_TIMEOUT_SECONDS:-120}"

wait_for_portal_api() {
  local code
  for _ in $(seq 1 "${portal_ready_timeout}"); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' "${PORTAL_API_BASE}/themes" 2>/dev/null || true)"
    case "${code}" in
      000|404|"") ;;
      *) return 0 ;;
    esac
    sleep 1
  done
  echo "Portal API did not become ready at ${PORTAL_API_BASE} within ${portal_ready_timeout}s; not configuring." >&2
  return 1
}

for _ in $(seq 1 "${portal_ready_timeout}"); do
  curl -fsS -o /dev/null "${PORTAL_URL%/}/" 2>/dev/null && break
  sleep 1
done

portal_api() {
  local method="$1" path="$2"
  shift 2
  curl -fsS -X "${method}" "${PORTAL_API_BASE}${path}" \
    -H "Authorization: ${PORTAL_API_TOKEN}" "$@"
}
# Portal list endpoints return one page of 20 unless per_page is passed (page_size is ignored),
# and the order changes after an update, so every list a lookup depends on asks for 100. The
# default Home page alone has more than 20 content blocks.

# Credential-bearing bodies (the admin password, the Dashboard key in the provider) reach jq
# through its environment and curl on stdin, never through arguments: those are visible to
# any local user in the process list.
if [[ -z "${PORTAL_API_TOKEN}" ]]; then
  bootstrap_payload="$(portal_password="${PORTAL_ADMIN_PASSWORD}" jq -nc \
    --arg username "${PORTAL_ADMIN_EMAIL}" \
    --arg first_name "${PORTAL_ADMIN_FIRST_NAME:-__DISPLAY_NAME__}" \
    --arg last_name "${PORTAL_ADMIN_LAST_NAME:-Administrator}" \
    '{username:$username,password:env.portal_password,first_name:$first_name,last_name:$last_name}')"
  bootstrap_response="$(curl -fsS "${PORTAL_API_BASE}/bootstrap" \
    -H 'Content-Type: application/json' --data-binary @- <<<"${bootstrap_payload}")" || {
      echo "Portal bootstrap did not succeed. If it is already bootstrapped, set PORTAL_API_TOKEN." >&2
      exit 1
    }
  PORTAL_API_TOKEN="$(jq -er '.data.api_token' <<<"${bootstrap_response}")"
  runtime_set PORTAL_API_TOKEN "${PORTAL_API_TOKEN}"
  # The bootstrap server shuts down at this point and the main application takes over the
  # port. Wait for it rather than racing the very next call.
  wait_for_portal_api || exit 1
fi

wait_for_portal_api || exit 1
portal_api GET "/themes?per_page=100" >/dev/null

provider_payload="$(provider_secret="${DASHBOARD_API_KEY}" jq -nc \
  --arg name "__DISPLAY_NAME__ Dashboard" \
  --arg url "${PORTAL_PROVIDER_DASHBOARD_URL:-http://tyk-dashboard:3000}" \
  --arg org "${ORG_ID}" \
  '{URL:$url,Secret:env.provider_secret,OrgID:$org,Gateway:"",PoliciesTags:[],InsecureSkipVerify:false} as $metadata
   | {Name:$name,Type:"tyk-pro",Configuration:{MetaData:($metadata | tojson)}}')"

providers="$(portal_api GET "/providers?per_page=100")"
provider_id="$(jq -r --arg name "__DISPLAY_NAME__ Dashboard" '.[]? | select(.Name == $name) | .ID' <<<"${providers}" | head -1)"
if [[ -z "${provider_id}" ]]; then
  provider_response="$(portal_api POST /providers -H 'Content-Type: application/json' --data-binary @- <<<"${provider_payload}")"
  provider_id="$(jq -er '.ID' <<<"${provider_response}")"
else
  portal_api PUT "/providers/${provider_id}" -H 'Content-Type: application/json' --data-binary @- <<<"${provider_payload}" >/dev/null
fi

portal_api PUT "/providers/${provider_id}/synchronize" >/dev/null
products="$(portal_api GET "/products?per_page=100")"
plans="$(portal_api GET "/plans?per_page=100")"
product_ids='[]'
while IFS= read -r product_id; do
  [[ -n "${product_id}" ]] || continue
  product="$(portal_api GET "/products/${product_id}")"
  if jq -e --argjson provider_id "${provider_id}" '.ProviderID == $provider_id or .Provider.ID == $provider_id' <<<"${product}" >/dev/null; then
    product_ids="$(jq -c --argjson id "${product_id}" '. + [$id]' <<<"${product_ids}")"
  fi
done < <(jq -r '.[]?.ID' <<<"${products}")
product_count="$(jq 'length' <<<"${product_ids}")"
[[ "${product_count}" -gt 0 ]] || {
  echo "Portal provider synchronized but no owned products were correlated." >&2
  exit 1
}
plan_ids='[]'
while IFS= read -r plan_id; do
  [[ -n "${plan_id}" ]] || continue
  plan="$(portal_api GET "/plans/${plan_id}")"
  if jq -e --argjson provider_id "${provider_id}" '.ProviderID == $provider_id or .Provider.ID == $provider_id' <<<"${plan}" >/dev/null; then
    plan_ids="$(jq -c --argjson id "${plan_id}" '. + [$id]' <<<"${plan_ids}")"
  fi
done < <(jq -r '.[]?.ID' <<<"${plans}")
plan_count="$(jq 'length' <<<"${plan_ids}")"
[[ "${plan_count}" -gt 0 ]] || { echo "Portal provider synchronized but no owned plans were correlated." >&2; exit 1; }

catalogues="$(portal_api GET "/catalogues?per_page=100")"
catalogue_id="$(jq -r '.[]? | select(.NameWithSlug == "__SLUG__-public-catalogue") | .ID' <<<"${catalogues}" | head -1)"
if [[ -z "${catalogue_id}" ]]; then
  catalogue_payload="$(jq -nc \
    --arg name "__DISPLAY_NAME__ Public Catalogue" \
    --arg slug "__SLUG__-public-catalogue" \
    '{Name:$name,NameWithSlug:$slug,VisibilityStatus:"Public",Products:[],Plans:[]}')"
  catalogue_response="$(portal_api POST /catalogues -H 'Content-Type: application/json' --data-binary "${catalogue_payload}")"
  catalogue_id="$(jq -er '.ID' <<<"${catalogue_response}")"
fi
# Only the products meant for everyone go to the public catalogue. Every synchronised
# product used to be attached here, which published audience-restricted products (a partner
# product from portal-catalogues, for instance) publicly on every run. A product's
# ReferenceID is the Dashboard `_id` of the policy that defines it, not the policy's custom
# `id`, so the public set is resolved from the owned policies by stable name.
public_policy_names='["__SLUG__-portal-product-policy"]'
if module_enabled dcr; then
  # The DCR self-service product is public so any developer can request credentials.
  public_policy_names='["__SLUG__-portal-product-policy","__SLUG__-jwt-product-policy"]'
fi
owned_policies="$(retry_capture 30 2 curl -fsS "${DASHBOARD_URL}/api/portal/policies/?p=-1" -H "authorization: ${DASHBOARD_API_KEY}")" \
  || { echo "Dashboard policy list was not readable." >&2; exit 1; }
public_references="$(jq -c --argjson names "${public_policy_names}" \
  '[.Data[]? | select(.meta_data.demo_owner == "__SLUG__" and (.meta_data.stable_name as $name | $names | index($name) != null)) | ._id]' \
  <<<"${owned_policies}")"
public_product_ids='[]'
for product_id in $(jq -r '.[]' <<<"${product_ids}"); do
  reference_id="$(portal_api GET "/products/${product_id}" | jq -r '.ReferenceID // empty')"
  if jq -e --arg reference "${reference_id}" 'index($reference) != null' <<<"${public_references}" >/dev/null; then
    public_product_ids="$(jq -c --argjson id "${product_id}" '. + [$id]' <<<"${public_product_ids}")"
  fi
done
[[ "$(jq 'length' <<<"${public_product_ids}")" -gt 0 ]] || {
  echo "The generated Portal product was not found among the synchronised products." >&2
  exit 1
}
runtime_set PORTAL_PUBLIC_PRODUCT_IDS "${public_product_ids}"
catalogue="$(portal_api GET "/catalogues/${catalogue_id}")"
catalogue_payload="$(jq --argjson products "${public_product_ids}" --argjson plans "${plan_ids}" '.Products = $products | .Plans = $plans' <<<"${catalogue}")"
portal_api PUT "/catalogues/${catalogue_id}" -H 'Content-Type: application/json' --data-binary "${catalogue_payload}" >/dev/null
runtime_set PORTAL_PROVIDER_ID "${provider_id}"
runtime_set PORTAL_CATALOGUE_ID "${catalogue_id}"

documentation_path="/docs/__SLUG__-api"
documentation_title="__DISPLAY_NAME__ API Documentation"
pages="$(portal_api GET "/pages?per_page=100")"
documentation_page_id="$(jq -r --arg path "${documentation_path}" '.[]? | select(.Path == $path) | .ID' <<<"${pages}" | head -1)"
if [[ -z "${documentation_page_id}" ]]; then
  documentation_payload="$(jq -nc \
    --arg title "${documentation_title}" \
    --arg path "${documentation_path}" \
    '{Title:$title,Path:$path,Template:"flip_flop",Status:"Published",AllowFormSubmission:false}')"
  documentation_response="$(portal_api POST /pages -H 'Content-Type: application/json' --data-binary "${documentation_payload}")"
  documentation_page_id="$(jq -er '.ID' <<<"${documentation_response}")"
fi

documentation_blocks="$(portal_api GET "/pages/${documentation_page_id}/content-blocks?per_page=100")"
while IFS=$'\t' read -r block_id block_name; do
  [[ -n "${block_id}" ]] || continue
  case "${block_name}" in
    HeaderDescription)
      markdown="Use the public catalogue to inspect the API product, its plan, and access options."
      ;;
    NoImageSectionTitle)
      markdown="API reference"
      ;;
    NoImageSectionDescription)
      markdown=$'- Base path: `/demo/__SLUG__/api-key/`\n- Authentication: API key in the `Authorization` header\n- Example operation: `GET /v1/echo`'
      ;;
    *)
      continue
      ;;
  esac
  block="$(jq -c --argjson id "${block_id}" '.[] | select(.ID == $id)' <<<"${documentation_blocks}")"
  block_payload="$(jq -c --arg markdown "${markdown}" '.Content = $markdown | .MarkdownContent = $markdown | .MarkdownEnabled = true' <<<"${block}")"
  portal_api PUT "/pages/${documentation_page_id}/content-blocks/${block_id}" \
    -H 'Content-Type: application/json' --data-binary "${block_payload}" >/dev/null
done < <(jq -r '.[] | select(.Name == "HeaderDescription" or .Name == "NoImageSectionTitle" or .Name == "NoImageSectionDescription") | [.ID, .Name] | @tsv' <<<"${documentation_blocks}")

documentation_blocks="$(portal_api GET "/pages/${documentation_page_id}/content-blocks?per_page=100")"
jq -e '[.[] | select((.Name == "HeaderDescription" or .Name == "NoImageSectionTitle" or .Name == "NoImageSectionDescription") and .MarkdownEnabled == true and (.MarkdownContent | length > 0))] | length == 3' <<<"${documentation_blocks}" >/dev/null || {
  echo "Portal documentation content was not published." >&2
  exit 1
}
runtime_set PORTAL_DOCUMENTATION_PAGE_ID "${documentation_page_id}"

# Attach each API's OAS to its Portal product so the product's API specification tab renders
# for a developer. Notes on the mechanism, all established empirically:
#  - /products/{id}/spec-details is rejected for a provider-synced product ("cannot save spec
#    details for non-documentation-only product"), so it is not the route here.
#  - APIDetails[].OASDocument accepts a PUT and returns 200 but does NOT persist. Only OASUrl
#    persists, so a 200 must never be treated as success; read the value back.
#  - The specification renderer fetches OASUrl from the DEVELOPER'S BROWSER, not from the
#    Portal container, so a compose-internal hostname is unreachable and the URL must be
#    host-facing. The response also needs a permissive CORS header, otherwise the renderer
#    reports only "Document could not be loaded" with an opaque "Failed to fetch" in the
#    browser console. The bundled upstream serves the definitions at /openapi/<file> for this.
#
# Because the browser resolves this, localhost is only correct when the Portal is viewed on
# the host running the stack. Set PORTAL_SPEC_BASE_URL to a reachable address when demoing
# from another machine.
spec_base_url="${PORTAL_SPEC_BASE_URL:-http://localhost:${UPSTREAM_PORT}}"
for product_id in $(jq -r '.[]' <<<"${product_ids}"); do
  product="$(portal_api GET "/products/${product_id}")"
  api_details="$(jq -c '.APIDetails // []' <<<"${product}")"
  [[ "$(jq 'length' <<<"${api_details}")" -gt 0 ]] || continue
  patched="${api_details}"
  attachable=0
  while IFS= read -r api_id; do
    [[ -n "${api_id}" ]] || continue
    spec_file="${ROOT_DIR}/apis/${api_id}.oas.json"
    [[ -f "${spec_file}" ]] || continue
    attachable=$((attachable + 1))
    spec_url="${spec_base_url%/}/openapi/${api_id}.oas.json"
    patched="$(jq -c --arg id "${api_id}" --arg url "${spec_url}" \
      'map(if .APIID == $id then .OASUrl = $url else . end)' <<<"${patched}")"
  done < <(jq -r '.[].APIID' <<<"${api_details}")
  [[ "${attachable}" -gt 0 ]] || continue
  if [[ "${patched}" != "${api_details}" ]]; then
    portal_api PUT "/products/${product_id}" -H 'Content-Type: application/json' \
      --data-binary "$(jq -nc --argjson details "${patched}" '{APIDetails: $details}')" >/dev/null
  fi
  # Every API whose definition exists on disk must come back with an OASUrl. Asserting only
  # "at least one" would pass a product that silently attached a single specification out of
  # several, which is the partial-success case this readback exists to catch.
  attached="$(portal_api GET "/products/${product_id}" | jq '[.APIDetails[]? | select((.OASUrl // "") != "")] | length')"
  [[ "${attached}" -ge "${attachable}" ]] || {
    echo "Portal product ${product_id}: ${attached}/${attachable} API specifications attached after configuration." >&2
    echo "A PUT returning 200 does not persist OASUrl; the readback above is the only proof." >&2
    exit 1
  }
  echo "Portal product ${product_id}: ${attached}/${attachable} API specification(s) attached."
done

if module_enabled portal-branding; then
  "${ROOT_DIR}/portal/apply-neutral-theme.sh"
fi

echo "Portal API bootstrap/configuration verified: provider, catalogue, ${product_count} product(s), ${plan_count} plan(s), user and documentation page."
