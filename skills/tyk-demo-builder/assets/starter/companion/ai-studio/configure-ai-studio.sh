#!/usr/bin/env bash
# Configure the Tyk AI Studio companion stack for the __DISPLAY_NAME__ demo.
#
# Idempotent: safe to re-run against a live stack. It converges, in order:
#   1. the first-run admin user (the first user registered on an empty database
#      is the administrator)
#   2. an EDITION GATE - refuses to go further unless the build is Enterprise
#   3. two LLM providers (by default the two stub OpenAI-compatible backends;
#      every provider setting is an environment variable, see README)
#   4. a Model Router with two model-name-pattern pools and model mappings
#      (created on the first run, PATCHed to the full desired state on every
#      later run, so a changed POOL_* or ROUTER_NAME takes effect), and a live
#      check that a router slug equal to an LLM route slug is refused 400
#   5. a guardrail filter (Enterprise, built-in pattern library) attached to the
#      first LLM as a request filter
#   6. a failover waterfall on the first LLM: when its upstream fails, the
#      second LLM serves the request
#   7. an LLM catalogue publishing both LLMs and the router to the Default team
#   8. two Apps: "__DISPLAY_NAME__ Assistant App" (both LLMs + the router, no budget
#      limit) and "__DISPLAY_NAME__ Zero Budget App" (first LLM only, budget 0)
#   9. an edge reload, so the microgateway actually loads all of the above
#  10. when the bundle also selected the mcp module, a read-only Tyk Dashboard
#      connection that imports the Dashboard's MCP proxies
#      (proofs/tyk-mcp-import.sh; skipped, never fatal, when the Dashboard is down)
#
# Step 9 is the one people miss. Every runtime path is served by the
# MICROGATEWAY, not by this control plane; configuration is pushed to the edge
# over gRPC. Without POST /api/v1/edges/reload-all the edge keeps serving its
# previous snapshot (GET /api/v1/edges shows sync_status "pending").
#
# Behaviour this script depends on (AI Studio 2.3):
#   - Model Routers must be GRANTED to an App (model_router_ids on
#     POST/PATCH /api/v1/apps). An ungranted router is still served from the
#     App's own LLMs (the edge logs a deprecation warning) and GET /v1/models
#     does not list it, so a 200 alone does not prove the grant.
#   - A router slug that equals an LLM's route slug is refused 400.
#   - Every App and LLM PATCH below sends the full desired state, so a re-run
#     converges whatever the stored values are.
#   - A new App with no budget gets DEFAULT_APP_BUDGET; monthly_budget null
#     means no limit and 0 means zero spend (refused with 403 budget_exceeded).
#   - Nothing shows in the AI Portal until a catalogue publishes it.
#
# Writes the App credentials to .runtime/ai-studio.env (gitignored, mode 0600).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"
RUNTIME_DIR="${ROOT_DIR}/.runtime"
# shellcheck disable=SC2034  # JAR is read by lib/admin.sh
JAR="${RUNTIME_DIR}/cookies.jar"

[[ -f secret.env ]] || { echo "secret.env missing; run ./start.sh" >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source ./secret.env
set +a

# shellcheck source=lib/admin.sh
source "${ROOT_DIR}/lib/admin.sh"   # api, api_raw, jqr, id_by_name, aistudio_login, load_admin_env, conf_value
# The admin credentials live in the host-only admin.env, not in secret.env (an
# env_file of both containers). Sourced without set -a: never exported.
load_admin_env || exit 1

ADMIN_URL="${ADMIN_URL:-http://localhost:__AISTUDIO_ADMIN_PORT__}"
ROUTER_URL="${ROUTER_URL:-http://localhost:__AISTUDIO_ROUTER_PORT__}"
# The unified ingress path is the edge's GATEWAY_UNIFIED_ROUTER_PATH; read it
# from the conf the edge actually loads instead of assuming the default.
UNIFIED_PATH="${GATEWAY_UNIFIED_ROUTER_PATH:-$(conf_value confs/mgw-ent.env GATEWAY_UNIFIED_ROUTER_PATH)}"
UNIFIED_PATH="${UNIFIED_PATH:-/v1}"
HUB_UNIFIED_PATH="$(conf_value confs/ai-studio-ent.env UNIFIED_ROUTER_PATH)"
if [[ -n "${HUB_UNIFIED_PATH}" && "${HUB_UNIFIED_PATH}" != "${UNIFIED_PATH}" ]]; then
  echo "confs/ai-studio-ent.env UNIFIED_ROUTER_PATH=${HUB_UNIFIED_PATH} differs from the edge's GATEWAY_UNIFIED_ROUTER_PATH=${UNIFIED_PATH}; the AI Portal would advertise the wrong ingress. Make them agree." >&2
  exit 1
fi
AISTUDIO_ADMIN_EMAIL="${AISTUDIO_ADMIN_EMAIL:-admin@__SLUG__.invalid}"
: "${AISTUDIO_ADMIN_PASSWORD:?set AISTUDIO_ADMIN_PASSWORD in admin.env}"
ADMIN_NAME="${AISTUDIO_ADMIN_NAME:-__DISPLAY_NAME__ Administrator}"

# Display names. Everything a person sees in the admin UI or the AI Portal is
# named after the bundle; an LLM's route slug is slug.Make(name).
LLM_A_NAME="${LLM_A_NAME:-__DISPLAY_NAME__ Alpha}"
LLM_B_NAME="${LLM_B_NAME:-__DISPLAY_NAME__ Beta}"
ROUTER_NAME="${ROUTER_NAME:-__DISPLAY_NAME__ LLM Router}"
ROUTER_SLUG="${ROUTER_SLUG:-__SLUG__-router}"
APP_NAME="${APP_NAME:-__DISPLAY_NAME__ Assistant App}"
# Earlier bundles named this App "<display name> Demo App", which a screenshot shows. It is
# renamed in place on the next run (same id, same credential), never duplicated.
LEGACY_APP_NAME="__DISPLAY_NAME__ Demo App"
BUDGET_APP_NAME="${BUDGET_APP_NAME:-__DISPLAY_NAME__ Zero Budget App}"
GUARDRAIL_NAME="${GUARDRAIL_NAME:-__DISPLAY_NAME__ Payment and Contact Data Guardrail}"
GUARDRAIL_BLOCK_MESSAGE="${GUARDRAIL_BLOCK_MESSAGE:-Blocked by the __DISPLAY_NAME__ data protection policy}"
CATALOGUE_NAME="${CATALOGUE_NAME:-__DISPLAY_NAME__ LLM Catalogue}"

# Compose service names of the stub backends (the default api_endpoint hosts,
# and the service AISTUDIO-FAILOVER-001 stops).
LLM_A_HOST="${LLM_A_HOST:-llm-alpha}"
LLM_B_HOST="${LLM_B_HOST:-llm-beta}"

# Pool A: models matching this glob go to the first LLM, renamed.
POOL_A_NAME="${POOL_A_NAME:-openai-family}"
POOL_A_PATTERN="${POOL_A_PATTERN:-gpt-*}"
POOL_A_SOURCE_MODEL="${POOL_A_SOURCE_MODEL:-gpt-4o}"
POOL_A_TARGET_MODEL="${POOL_A_TARGET_MODEL:-alpha-fast}"
# Pool B: same, second LLM.
POOL_B_NAME="${POOL_B_NAME:-anthropic-family}"
POOL_B_PATTERN="${POOL_B_PATTERN:-claude-*}"
POOL_B_SOURCE_MODEL="${POOL_B_SOURCE_MODEL:-claude-3-5-sonnet}"
POOL_B_TARGET_MODEL="${POOL_B_TARGET_MODEL:-beta-deep}"

# Provider settings. Defaults are the two in-network stub backends (vendor
# openai, one per compose service). Every value is an environment variable (or
# a secret.env entry), so the same run can target real providers instead - see
# README "Point the LLMs at Azure OpenAI or Bedrock". LLM_x_METADATA_JSON carries
# vendor credentials that do not live in api_key (Bedrock: aws_access_key_id /
# aws_secret_access_key, ideally as $SECRET/NAME references). The settings are
# re-applied on every run, so a changed variable converges.
LLM_A_VENDOR="${LLM_A_VENDOR:-openai}"
LLM_A_ENDPOINT="${LLM_A_ENDPOINT:-http://${LLM_A_HOST}:8080}"
LLM_A_API_KEY="${LLM_A_API_KEY:-stub-key}"
LLM_A_METADATA_JSON="${LLM_A_METADATA_JSON:-}"
LLM_A_DESCRIPTION="${LLM_A_DESCRIPTION:-OpenAI-compatible stub backend standing in for a first provider}"
LLM_B_VENDOR="${LLM_B_VENDOR:-openai}"
LLM_B_ENDPOINT="${LLM_B_ENDPOINT:-http://${LLM_B_HOST}:8080}"
LLM_B_API_KEY="${LLM_B_API_KEY:-stub-key}"
LLM_B_METADATA_JSON="${LLM_B_METADATA_JSON:-}"
LLM_B_DESCRIPTION="${LLM_B_DESCRIPTION:-OpenAI-compatible stub backend standing in for a second provider}"

mkdir -p "${RUNTIME_DIR}"
chmod 700 "${RUNTIME_DIR}"

# mkjson PYTHON_EXPR: print the expression as JSON. E is os.environ, so values
# travel through the environment and never need shell quoting. Build a payload
# into a variable first: macOS bash 3.2 mis-parses a quoted string inside a
# command substitution nested in another quoted command substitution.
mkjson() { python3 -c "import json,os;E=os.environ;print(json.dumps($1))"; }

# The edge routes an LLM by its name, lower-cased and hyphenated ("Example Corp
# Alpha" answers to example-corp-alpha). This mirrors it for the display-name
# character set.
route_slug() {
  python3 -c "
import re,sys
s=sys.argv[1].strip().replace('&',' and ').replace('@',' at ').lower()
s=re.sub(r'[^a-z0-9_-]+','-',s); s=re.sub(r'-+','-',s)
print(s.strip('-_'))" "$1"
}

echo "==> waiting for the AI Studio control plane at ${ADMIN_URL}"
for _ in $(seq 1 60); do
  curl -sf -o /dev/null "${ADMIN_URL}/healthz" && break
  sleep 2
done
curl -sf -o /dev/null "${ADMIN_URL}/healthz" || {
  echo "control plane never became healthy; check: docker compose logs ai-studio" >&2
  exit 1
}

echo "==> first-run admin '${ADMIN_NAME}' (${AISTUDIO_ADMIN_EMAIL})"
register_body="$(EMAIL="${AISTUDIO_ADMIN_EMAIL}" NAME="${ADMIN_NAME}" PASSWORD="${AISTUDIO_ADMIN_PASSWORD}" \
  mkjson "{'data':{'type':'users','attributes':{'email':E['EMAIL'],'name':E['NAME'],'password':E['PASSWORD'],'with_portal':True,'with_chat':True}}}")"
register_out="$(curl -sS -X POST "${ADMIN_URL}/auth/register" \
  -H 'Content-Type: application/json' -H 'Authorization: bootstrap' --data-binary @- <<<"${register_body}")"
unset register_body
case "${register_out}" in
  *"registered successfully"*) echo "    admin created" ;;
  *"already in use"*)          echo "    admin already exists" ;;
  *) echo "    unexpected register response: ${register_out}" >&2; exit 1 ;;
esac

aistudio_login || exit 1
echo "    logged in"
ADMIN_USER_ID="$(api GET /common/me | jqr "
d=json.load(sys.stdin); d=d.get('data',d)
print(d.get('id') or d.get('attributes',{}).get('id') or 1)")"

# --- Edition gate ------------------------------------------------------------
# Stop here rather than half-build. GET /auth/features must report
# edition "enterprise" and feature_model_router true.
features="$(curl -sS "${ADMIN_URL}/auth/features" || echo '{}')"
edition="$(jqr "print(json.load(sys.stdin).get('edition','unknown'))" <<<"${features}")"
router_feature="$(jqr "print(json.load(sys.stdin).get('features',{}).get('feature_model_router',False))" <<<"${features}")"
if [[ "${edition}" != enterprise || "${router_feature}" != True ]]; then
  cat >&2 <<GATE

==========================================================================
 NOT AN ENTERPRISE BUILD - REFUSING TO CONFIGURE A DEMO THAT CANNOT WORK
==========================================================================
  GET ${ADMIN_URL}/auth/features reports:
      edition              = ${edition}
      feature_model_router = ${router_feature}

  The Model Router, guardrails, budgets and the audit trail need both to be
  enterprise / true (they are Enterprise features), so there is nothing to
  show.

  Check the ENTERPRISE licence in ./secret.env (TYK_AI_LICENSE), then:
      docker compose down && ./start.sh
  The control plane logs the licence decision at startup:
      docker compose logs ai-studio | grep -i licen
==========================================================================
GATE
  exit 1
fi
echo "    edition=enterprise, feature_model_router=true"

# --- LLM providers -----------------------------------------------------------
echo "==> LLM providers (${LLM_A_VENDOR} at ${LLM_A_ENDPOINT}, ${LLM_B_VENDOR} at ${LLM_B_ENDPOINT})"
# ensure_llm and ensure_app run inside $(...), where errexit does not stop the
# function (bash 3.2 does not inherit it, and a caller's || disables it), so
# every call that can fail carries an explicit || return 1 and the callers stop
# the run. A rejected PATCH (for example a bad LLM_B_METADATA_JSON answered 400)
# must never end in "Done".
ensure_llm() {
  # $1 = display name, $2 = vendor, $3 = endpoint, $4 = api key, $5 = default model,
  # $6 = description, $7 = metadata JSON (optional)
  local name="$1" id body
  body="$(NAME="$1" VENDOR="$2" ENDPOINT="$3" KEY="$4" MODEL="$5" DESC="$6" META="${7:-}" python3 -c "
import json,os
E=os.environ
a={'name':E['NAME'],'vendor':E['VENDOR'],'api_endpoint':E['ENDPOINT'],'api_key':E['KEY'],
   'default_model':E['MODEL'],'short_description':E['DESC'],'active':True,'privacy_score':50}
if E['META']:
    a['metadata']=json.loads(E['META'])
print(json.dumps({'data':{'type':'llms','attributes':a}}))")" || { echo "LLM '${name}': the metadata JSON does not parse" >&2; return 1; }
  id="$(id_by_name "/api/v1/llms?page_size=200" "${name}")" || return 1
  if [[ -z "${id}" ]]; then
    # vendor=openai stubs: each names itself in its answer, so routing and
    # failover decisions are visible in the response.
    api POST /api/v1/llms "${body}" >/dev/null || return 1
    id="$(id_by_name "/api/v1/llms?page_size=200" "${name}")" || return 1
  else
    api PATCH "/api/v1/llms/${id}" "${body}" >/dev/null || return 1
  fi
  [[ -n "${id}" ]] || { echo "LLM '${name}' not found after saving it" >&2; return 1; }
  echo "${id}"
}
LLM_A_ID="$(ensure_llm "${LLM_A_NAME}" "${LLM_A_VENDOR}" "${LLM_A_ENDPOINT}" "${LLM_A_API_KEY}" "${POOL_A_TARGET_MODEL}" "${LLM_A_DESCRIPTION}" "${LLM_A_METADATA_JSON}")" \
  || { echo "Configuration did NOT complete: LLM '${LLM_A_NAME}' was rejected (see the HTTP status above)" >&2; exit 1; }
LLM_B_ID="$(ensure_llm "${LLM_B_NAME}" "${LLM_B_VENDOR}" "${LLM_B_ENDPOINT}" "${LLM_B_API_KEY}" "${POOL_B_TARGET_MODEL}" "${LLM_B_DESCRIPTION}" "${LLM_B_METADATA_JSON}")" \
  || { echo "Configuration did NOT complete: LLM '${LLM_B_NAME}' was rejected (see the HTTP status above)" >&2; exit 1; }
LLM_A_SLUG="$(route_slug "${LLM_A_NAME}")"
LLM_B_SLUG="$(route_slug "${LLM_B_NAME}")"
echo "    ${LLM_A_NAME}=${LLM_A_ID} (slug ${LLM_A_SLUG})  ${LLM_B_NAME}=${LLM_B_ID} (slug ${LLM_B_SLUG})"

# --- Model router ------------------------------------------------------------
if [[ "${ROUTER_SLUG}" == "${LLM_A_SLUG}" || "${ROUTER_SLUG}" == "${LLM_B_SLUG}" ]]; then
  echo "ROUTER_SLUG=${ROUTER_SLUG} equals an LLM route slug; AI Studio refuses that with 400. Choose another ROUTER_SLUG." >&2
  exit 1
fi
router_id_for() {
  api GET /api/v1/model-routers | MATCH="$1" jqr "
d=json.load(sys.stdin)
m=[str(x['id']) for x in d.get('data',[]) if x['attributes']['slug']==os.environ['MATCH']]
print(m[0] if m else '')"
}
echo "==> model router '${ROUTER_SLUG}'"
# The full desired state, sent on create AND on every later run. PATCH
# /api/v1/model-routers/:id replaces the router: name, description, active and
# namespace are taken from the body, and its pools (with their vendors and
# mappings) are replaced. So a changed POOL_*, ROUTER_NAME or LLM_* setting
# converges instead of leaving the first run's mapping live; the read-back
# below checks it. The catalogue link is re-applied below with PUT.
router_body="$(NAME="${ROUTER_NAME}" SLUG="${ROUTER_SLUG}" A="${LLM_A_ID}" B="${LLM_B_ID}" \
  PAN="${POOL_A_NAME}" PAP="${POOL_A_PATTERN}" PAS="${POOL_A_SOURCE_MODEL}" PAT="${POOL_A_TARGET_MODEL}" \
  PBN="${POOL_B_NAME}" PBP="${POOL_B_PATTERN}" PBS="${POOL_B_SOURCE_MODEL}" PBT="${POOL_B_TARGET_MODEL}" mkjson "
{'data':{'type':'model-routers','attributes':{
  'name':E['NAME'],'slug':E['SLUG'],'api_compat':'openai','active':True,'namespace':'',
  'description':'Routes by model-name pattern to two different LLM backends and renames the model per backend',
  'pools':[
    {'name':E['PAN'],'model_pattern':E['PAP'],'selection_algorithm':'round_robin','priority':10,
     'vendors':[{'llm_id':int(E['A']),'weight':1,'active':True,'mappings':[{'source_model':E['PAS'],'target_model':E['PAT']}]}]},
    {'name':E['PBN'],'model_pattern':E['PBP'],'selection_algorithm':'round_robin','priority':10,
     'vendors':[{'llm_id':int(E['B']),'weight':1,'active':True,'mappings':[{'source_model':E['PBS'],'target_model':E['PBT']}]}]}]}}}")"
ROUTER_ID="$(router_id_for "${ROUTER_SLUG}")"
if [[ -z "${ROUTER_ID}" ]]; then
  api POST /api/v1/model-routers "${router_body}" >/dev/null
  ROUTER_ID="$(router_id_for "${ROUTER_SLUG}")"
  [[ -n "${ROUTER_ID}" ]] || { echo "failed to create the model router" >&2; exit 1; }
  echo "    router created (id ${ROUTER_ID})"
else
  api PATCH "/api/v1/model-routers/${ROUTER_ID}" "${router_body}" >/dev/null \
    || { echo "Configuration did NOT complete: the update of model router ${ROUTER_ID} was rejected" >&2; exit 1; }
  echo "    router already present (id ${ROUTER_ID}); desired state re-applied with PATCH"
fi
# Read it back: the stored pools must be exactly the desired ones.
api GET "/api/v1/model-routers/${ROUTER_ID}" | NAME="${ROUTER_NAME}" A="${LLM_A_ID}" B="${LLM_B_ID}" \
  PAN="${POOL_A_NAME}" PAP="${POOL_A_PATTERN}" PAS="${POOL_A_SOURCE_MODEL}" PAT="${POOL_A_TARGET_MODEL}" \
  PBN="${POOL_B_NAME}" PBP="${POOL_B_PATTERN}" PBS="${POOL_B_SOURCE_MODEL}" PBT="${POOL_B_TARGET_MODEL}" jqr "
E=os.environ
a=json.load(sys.stdin)['data']['attributes']
got=sorted((p['name'],p['model_pattern'],str(v['llm_id']),m['source_model'],m['target_model'])
           for p in a.get('pools') or [] for v in p.get('vendors') or [] for m in v.get('mappings') or [])
want=sorted([(E['PAN'],E['PAP'],E['A'],E['PAS'],E['PAT']),(E['PBN'],E['PBP'],E['B'],E['PBS'],E['PBT'])])
assert a['name']==E['NAME'], 'router name not converged: %r' % a['name']
assert a['active'] is True, 'router is not active'
assert got==want, 'router pools not converged: %s' % got
print('    stored: ' + '; '.join('%s %s -> llm %s, %s -> %s' % g for g in got))"

# LLM route slugs and router slugs share one namespace; a clash is refused with
# 400 "route name is already used by another LLM or router: the LLM ...
# answers to ...". Assert both the status AND that message: a 400
# for another reason (a validation change, a malformed probe) proves nothing
# about the slug rule. A release that accepted the save would leave an
# unreachable router behind, so it is removed again and the run stops for the
# reference to be re-checked.
SLUG_CLASH_MESSAGE="route name is already used by another LLM or router"
clash_body="$(NAME="__DISPLAY_NAME__ Slug Clash Check" SLUG="${LLM_A_SLUG}" A="${LLM_A_ID}" mkjson "
{'data':{'type':'model-routers','attributes':{'name':E['NAME'],'slug':E['SLUG'],'api_compat':'openai','active':False,
  'pools':[{'name':'clash','model_pattern':'clash-*','selection_algorithm':'round_robin','priority':1,
            'vendors':[{'llm_id':int(E['A']),'weight':1,'active':True}]}]}}}")"
clash_out="$(api_raw POST /api/v1/model-routers "${clash_body}")"
clash_code="${clash_out##*$'\n'}"
if [[ "${clash_code}" == 400 && "${clash_out}" == *"${SLUG_CLASH_MESSAGE}"* ]]; then
  echo "    a router slug equal to LLM slug ${LLM_A_SLUG} is refused 400 (\"${SLUG_CLASH_MESSAGE}\")"
elif [[ "${clash_code}" == 400 ]]; then
  echo "A router slug equal to LLM slug ${LLM_A_SLUG} answered 400 but without \"${SLUG_CLASH_MESSAGE}\": ${clash_out%$'\n'*}" >&2
  echo "The refusal may be for another reason; re-check references/ai-studio.md." >&2
  exit 1
else
  clash_id="$(jqr "d=json.loads(sys.stdin.read() or '{}'); print((d.get('data') or {}).get('id',''))" <<<"${clash_out%$'\n'*}" 2>/dev/null || true)"
  if [[ -n "${clash_id}" ]]; then api DELETE "/api/v1/model-routers/${clash_id}" >/dev/null 2>&1 || true; fi
  echo "A router slug equal to LLM slug ${LLM_A_SLUG} answered HTTP ${clash_code}, not 400; this release changed the route-slug rule. Re-check references/ai-studio.md." >&2
  exit 1
fi

# --- Guardrail filter (Enterprise) --------------------------------------------
# A filter of kind "guardrail" runs a detection provider instead of a script.
# The built-in provider's `detectors` take category names (secrets, pii,
# injection, leak); a single pattern id such as pii.iban is refused with 400
# ("detector ... is not offered by provider"), so the filter enables the pii
# category and excludes every pii pattern except IBAN, payment card and e-mail.
# The whole configuration is sent on every run. Every hit records a compliance
# event (guardrail.pii.iban, ...) without the matched text; edge hits reach the
# hub on the analytics pulse.
GUARDRAIL_EXCLUDE='["pii.phone_international","pii.phone_us","pii.us_ssn","pii.uk_nino","pii.uk_nhs","pii.ipv4","pii.ipv6","pii.mac_address","pii.date_of_birth","pii.passport","pii.drivers_license","pii.medical_record","pii.street_address","pii.eu_vat"]'
guardrail_body="$(NAME="${GUARDRAIL_NAME}" MSG="${GUARDRAIL_BLOCK_MESSAGE}" EXCLUDE="${GUARDRAIL_EXCLUDE}" mkjson "
{'data':{'type':'filters','attributes':{'name':E['NAME'],
  'description':'Blocks prompts that carry an IBAN, a payment card number or an e-mail address before they reach the provider',
  'kind':'guardrail','response_filter':False,
  'config':{'provider':'builtin','detectors':[{'name':'pii'}],'exclude':json.loads(E['EXCLUDE']),
    'on_detect':'block','scope':'all_user','fail_mode':'closed','timeout_ms':2000,'block_message':E['MSG']}}}}")"
echo "==> guardrail filter '${GUARDRAIL_NAME}' on ${LLM_A_NAME}"
GUARDRAIL_ID="$(id_by_name /api/v1/filters "${GUARDRAIL_NAME}")"
if [[ -z "${GUARDRAIL_ID}" ]]; then
  GUARDRAIL_ID="$(api POST /api/v1/filters "${guardrail_body}" | jqr "d=json.load(sys.stdin); print(d.get('data',d)['id'])")"
  echo "    created (id ${GUARDRAIL_ID})"
else
  api PATCH "/api/v1/filters/${GUARDRAIL_ID}" "${guardrail_body}" >/dev/null
  echo "    already present (id ${GUARDRAIL_ID}), configuration re-applied"
fi
# The default guardrails a fresh database holds are left unattached. Sending
# `filters` on an LLM PATCH replaces the attached list.
api PATCH "/api/v1/llms/${LLM_A_ID}" "{\"data\":{\"type\":\"llms\",\"attributes\":{\"filters\":[${GUARDRAIL_ID}]}}}" >/dev/null
api GET "/api/v1/llms/${LLM_A_ID}" | GID="${GUARDRAIL_ID}" jqr "
a=json.load(sys.stdin)['data']['attributes']
ids=[str(f.get('id') if isinstance(f,dict) else f) for f in (a.get('filters') or [])]
assert os.environ['GID'] in ids, 'guardrail not attached: %s' % ids
print('    attached to the LLM as a request filter: filters=%s' % ids)"

# --- Failover waterfall ---------------------------------------------------------
# LLM failover (Community and Enterprise). The waterfall lives on the
# PRIMARY LLM: an ordered list of {llm_id, model} rungs tried when the primary's
# upstream fails as the triggers say (here: a refused connection or a timeout).
# Only the failover key is sent; the LLM keeps its other settings.
echo "==> failover waterfall: ${LLM_A_NAME} -> ${LLM_B_NAME} (${POOL_B_TARGET_MODEL})"
failover_body="$(B="${LLM_B_ID}" M="${POOL_B_TARGET_MODEL}" mkjson "
{'data':{'type':'llms','attributes':{'failover':{'targets':[{'llm_id':int(E['B']),'model':E['M']}],
  'triggers':{'on_connection_error':True,'on_timeout':True,'attempt_timeout_seconds':10}}}}}")"
api PATCH "/api/v1/llms/${LLM_A_ID}" "${failover_body}" >/dev/null
api GET "/api/v1/llms/${LLM_A_ID}" | jqr "
f=json.load(sys.stdin)['data']['attributes'].get('failover') or {}
assert f.get('targets'), 'failover not stored'
print('    stored: '+json.dumps(f))"

# --- LLM catalogue --------------------------------------------------------------
# Nothing is visible in the AI Portal until a catalogue publishes it. One
# catalogue carries both LLMs and the Model Router and is granted to the
# Default team.
echo "==> LLM catalogue '${CATALOGUE_NAME}'"
CATALOGUE_ID="$(id_by_name /api/v1/catalogues "${CATALOGUE_NAME}")"
if [[ -z "${CATALOGUE_ID}" ]]; then
  catalogue_body="$(NAME="${CATALOGUE_NAME}" mkjson "{'data':{'type':'catalogues','attributes':{'name':E['NAME']}}}")"
  api POST /api/v1/catalogues "${catalogue_body}" >/dev/null
  CATALOGUE_ID="$(id_by_name /api/v1/catalogues "${CATALOGUE_NAME}")"
fi
[[ -n "${CATALOGUE_ID}" ]] || { echo "failed to create the LLM catalogue" >&2; exit 1; }
in_catalogue="$(api GET "/api/v1/catalogues/${CATALOGUE_ID}/llms" | jqr "
d=json.load(sys.stdin); print(' '.join(str(x['id']) for x in d.get('data',[])))")"
for llm_id in "${LLM_A_ID}" "${LLM_B_ID}"; do
  [[ " ${in_catalogue} " == *" ${llm_id} "* ]] && continue
  api POST "/api/v1/catalogues/${CATALOGUE_ID}/llms" "{\"data\":{\"type\":\"llms\",\"id\":\"${llm_id}\"}}" >/dev/null
done
api PUT "/api/v1/model-routers/${ROUTER_ID}/catalogues" "{\"catalogue_ids\":[${CATALOGUE_ID}]}" >/dev/null
DEFAULT_GROUP_ID="$(id_by_name /api/v1/groups Default)"
if [[ -n "${DEFAULT_GROUP_ID}" ]]; then
  group_cats="$(api GET "/api/v1/groups/${DEFAULT_GROUP_ID}/catalogues" | jqr "
d=json.load(sys.stdin); print(' '.join(str(x['id']) for x in d.get('data',[])))")"
  if [[ " ${group_cats} " != *" ${CATALOGUE_ID} "* ]]; then
    api POST "/api/v1/groups/${DEFAULT_GROUP_ID}/catalogues" "{\"data\":{\"type\":\"catalogues\",\"id\":\"${CATALOGUE_ID}\"}}" >/dev/null
  fi
  echo "    catalogue ${CATALOGUE_ID} carries both LLMs and the router; granted to the Default team (${DEFAULT_GROUP_ID})"
else
  echo "    catalogue ${CATALOGUE_ID} carries both LLMs and the router; no Default team found to grant it to" >&2
fi

# --- Apps --------------------------------------------------------------------------
# ensure_app always sends the App's full state (name, description, owner, LLM
# and router grants, budget), so a re-run converges.
# monthly_budget: null = no limit, 0 = zero spend. A new App created without a
# budget gets DEFAULT_APP_BUDGET, so the unlimited App is set to null explicitly.
ensure_app() {
  # $1 = name, $2 = description, $3 = llm_ids JSON, $4 = model_router_ids JSON, $5 = budget JSON,
  # $6 = optional former name to rename in place
  local name="$1" id body
  body="$(NAME="$1" DESC="$2" LLMS="$3" ROUTERS="$4" BUDGET="$5" USER="${ADMIN_USER_ID}" mkjson "
{'data':{'type':'apps','attributes':{'name':E['NAME'],'description':E['DESC'],'user_id':int(E['USER']),
  'llm_ids':json.loads(E['LLMS']),'model_router_ids':json.loads(E['ROUTERS']),'monthly_budget':json.loads(E['BUDGET'])}}}")" || return 1
  id="$(id_by_name "/api/v1/apps?page_size=200" "${name}")" || return 1
  if [[ -z "${id}" && -n "${6:-}" ]]; then
    # $6 = a former name of this App: adopt it and let the PATCH below rename it
    id="$(id_by_name "/api/v1/apps?page_size=200" "$6")" || return 1
  fi
  if [[ -z "${id}" ]]; then
    api POST /api/v1/apps "${body}" >/dev/null || return 1
    id="$(id_by_name "/api/v1/apps?page_size=200" "${name}")" || return 1
  fi
  [[ -n "${id}" ]] || { echo "failed to create app '${name}'" >&2; return 1; }
  api PATCH "/api/v1/apps/${id}" "${body}" >/dev/null || return 1
  # Re-activating an already active credential is not known to be idempotent,
  # so a rejection here is reported but not fatal; the probes decide.
  api POST "/api/v1/apps/${id}/activate-credential" '{}' >/dev/null 2>&1 \
    || echo "    credential activation for '${name}' was rejected (already active?)" >&2
  echo "${id}"
}
app_secret() {
  api GET "/common/apps/$1" | jqr "print(json.load(sys.stdin)['attributes']['credential']['secret'])"
}

echo "==> app '${APP_NAME}' (both LLMs + the router, no budget limit)"
APP_ID="$(ensure_app "${APP_NAME}" "Assistant App with both LLMs and the ${ROUTER_NAME} on the unified endpoint" \
  "[${LLM_A_ID},${LLM_B_ID}]" "[${ROUTER_ID}]" null "${LEGACY_APP_NAME}")" \
  || { echo "Configuration did NOT complete: app '${APP_NAME}' was rejected (see the HTTP status above)" >&2; exit 1; }
APP_SECRET="$(app_secret "${APP_ID}")"
[[ -n "${APP_SECRET}" ]] || { echo "could not read the credential of '${APP_NAME}'" >&2; exit 1; }
echo "    app id=${APP_ID}, credential active"

echo "==> app '${BUDGET_APP_NAME}' (${LLM_A_NAME} only, monthly budget 0)"
BUDGET_APP_ID="$(ensure_app "${BUDGET_APP_NAME}" "Zero monthly budget: every request is refused until a budget is allocated" \
  "[${LLM_A_ID}]" "[]" 0)" \
  || { echo "Configuration did NOT complete: app '${BUDGET_APP_NAME}' was rejected (see the HTTP status above)" >&2; exit 1; }
BUDGET_APP_SECRET="$(app_secret "${BUDGET_APP_ID}")"
[[ -n "${BUDGET_APP_SECRET}" ]] || { echo "could not read the credential of '${BUDGET_APP_NAME}'" >&2; exit 1; }
echo "    app id=${BUDGET_APP_ID}, credential active"

api GET "/api/v1/apps/${APP_ID}" | NAME="${APP_NAME}" jqr "
a=json.load(sys.stdin)['data']['attributes']
assert a['name']==os.environ['NAME'], 'app name not kept: %r' % a['name']
assert a['model_router_ids'], 'router grant missing'
assert len(a['llm_ids'])==2, 'LLM grants missing: %s' % a['llm_ids']
assert a['monthly_budget'] is None, 'demo app should have no budget limit'
print('    %s: llm_ids=%s model_router_ids=%s monthly_budget=%s' % (a['name'], a['llm_ids'], a['model_router_ids'], a['monthly_budget']))"
api GET "/api/v1/apps/${BUDGET_APP_ID}" | jqr "
a=json.load(sys.stdin)['data']['attributes']
assert a['monthly_budget']==0, 'zero budget app should have a budget of 0'
print('    %s: llm_ids=%s monthly_budget=%s' % (a['name'], a['llm_ids'], a['monthly_budget']))"

# --- Edge reload -------------------------------------------------------------------
# THE STEP PEOPLE MISS. Configuration lives on the control plane; the data plane
# is the edge. Without this the edge keeps serving its last snapshot.
echo "==> reloading the edge microgateway so it picks everything up"
# The edge exits when it cannot register with the control plane and is restarted
# by compose, so on a first start it may not be registered yet. reload-all answers
# 409 "no edges to push to" until one is, so wait for a registered edge first.
edges=0
for _ in $(seq 1 45); do
  edges="$(api GET /api/v1/edges 2>/dev/null | jqr "print((json.load(sys.stdin).get('meta') or {}).get('total_count',0))" 2>/dev/null)" || edges=0
  [[ "${edges:-0}" -gt 0 ]] && break
  sleep 2
done
[[ "${edges:-0}" -gt 0 ]] || {
  echo "Configuration did NOT complete: no edge registered with the control plane." >&2
  echo "Check: docker compose logs mgw | grep -iE 'register|control'" >&2
  exit 1
}
api POST /api/v1/edges/reload-all '{}' >/dev/null

umask 077
{
  echo "# Generated by configure-ai-studio.sh. GITIGNORED - contains live credentials."
  for key in ADMIN_URL ROUTER_URL ROUTER_SLUG ROUTER_ID ROUTER_NAME LLM_A_ID LLM_B_ID LLM_A_NAME LLM_B_NAME \
             LLM_A_SLUG LLM_B_SLUG LLM_A_HOST LLM_B_HOST LLM_A_VENDOR LLM_B_VENDOR GUARDRAIL_ID GUARDRAIL_NAME \
             CATALOGUE_ID CATALOGUE_NAME APP_ID APP_NAME BUDGET_APP_ID BUDGET_APP_NAME \
             POOL_A_SOURCE_MODEL POOL_A_TARGET_MODEL POOL_B_SOURCE_MODEL POOL_B_TARGET_MODEL; do
    printf 'AISTUDIO_%s=%q\n' "${key}" "${!key}"
  done
  printf 'AISTUDIO_APP_KEY=%q\n' "${APP_SECRET}"
  printf 'AISTUDIO_BUDGET_APP_KEY=%q\n' "${BUDGET_APP_SECRET}"
} >"${RUNTIME_DIR}/ai-studio.env"
chmod 600 "${RUNTIME_DIR}/ai-studio.env"

# Poll until the edge serves the granted router AND the unified ingress. A 401
# right after a reload is the edge's token cache not having seen the new
# credential yet; it resolves on the next sync.
echo "==> waiting for the edge to serve the router and the unified endpoint"
code_router=000 code_unified=000
for _ in $(seq 1 45); do
  # The App secret travels as a header read from stdin (-H @-), not as an argument.
  code_router="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
    "${ROUTER_URL}/router/${ROUTER_SLUG}/v1/chat/completions" \
    -H @- -H 'Content-Type: application/json' \
    -d "{\"model\":\"${POOL_A_SOURCE_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
    <<<"Authorization: Bearer ${APP_SECRET}" 2>/dev/null)" || true
  code_unified="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
    "${ROUTER_URL}${UNIFIED_PATH}/chat/completions" \
    -H @- -H 'Content-Type: application/json' \
    -d "{\"model\":\"${LLM_B_SLUG}/${POOL_B_TARGET_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
    <<<"Authorization: Bearer ${APP_SECRET}" 2>/dev/null)" || true
  # curl prints 000 while the edge is not listening yet; keep polling.
  [[ "${code_router}" == 200 && "${code_unified}" == 200 ]] && break
  sleep 2
done
if [[ "${code_router}" != 200 || "${code_unified}" != 200 ]]; then
  echo "    router=${code_router} unified=${code_unified}, expected 200 for both" >&2
  echo "Configuration did NOT complete: the edge never served the new configuration." >&2
  echo "Check: docker compose logs mgw | grep -iE 'routers loaded|configuration update'" >&2
  echo "If it keeps answering 401 for a fresh credential: docker compose up -d --force-recreate mgw" >&2
  exit 1
fi
echo "    router=${code_router} unified=${code_unified}"

# --- Optional: Tyk Dashboard MCP import (Enterprise) ----------------------------------
# Read-only catalogue connection to the Gateway bundle's Dashboard, only when the
# bundle selected the mcp module. Skipped, never fatal, when that separate stack
# is not running; ./verify-ai-studio.sh retries it.
echo "==> Tyk Dashboard MCP import (skipped unless the bundle has mcp and its Dashboard is up)"
mcp_rc=0
TYK_MCP_WAIT_SECONDS="${TYK_MCP_WAIT_SECONDS:-0}" "${ROOT_DIR}/proofs/tyk-mcp-import.sh" || mcp_rc=$?
case "${mcp_rc}" in
  0) ;;
  2) echo "    skipped: $(jq -r '.detail // "see evidence/bodies/tyk-mcp-status.json"' "${ROOT_DIR}/evidence/bodies/tyk-mcp-status.json" 2>/dev/null)" ;;
  *) echo "    Tyk MCP import did not complete; run ./proofs/tyk-mcp-import.sh once the Dashboard is up" >&2 ;;
esac

echo
echo "Done. Credentials in .runtime/ai-studio.env (gitignored)."
echo "Next: ./verify-ai-studio.sh"
