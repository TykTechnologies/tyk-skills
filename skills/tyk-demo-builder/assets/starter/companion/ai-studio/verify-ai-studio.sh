#!/usr/bin/env bash
# Evidence pass for the Tyk AI Studio companion stack (AI Studio 2.3).
#
# SEPARATE PRODUCT, SEPARATE STACK: this verifies Tyk AI Studio only. It does not
# touch the Tyk Gateway bundle in the parent directory, and scripts/verify-demo.sh
# there does not cover these test IDs. Output format mirrors it deliberately.
#
#   AISTUDIO-ENT-001        the running build reports Enterprise 2.3+ (+ every feature flag)
#   AISTUDIO-ROUTER-001     legacy /router/{slug}/ endpoint: two model names, two backends
#   AISTUDIO-UNIFIED-001    one OpenAI-compatible endpoint (/v1) for every LLM and router
#   AISTUDIO-BUDGET-001     a zero-budget App is refused with code budget_exceeded
#   AISTUDIO-GUARDRAIL-001  a built-in guardrail blocks an IBAN and records a compliance event
#   AISTUDIO-AUDIT-001      the audit trail recorded the configuration run, secrets redacted
#   AISTUDIO-TYKMCP-001     MCP proxies imported from the Tyk Dashboard (read-only; needs mcp)
#   AISTUDIO-METRICS-001    /metrics answers a bearer scrape and refuses anything else
#   AISTUDIO-FAILOVER-001   stop a backend: the LLM's failover waterfall serves the call
#
# Idempotent and re-runnable. FAILOVER-001 runs last: it stops the llm-alpha
# stub and always starts it again (the EXIT trap covers an interrupted run).
#
# evidence/results.json and evidence/validation.md are written from the EXIT
# trap, so they always describe THIS run: the previous run's files are removed
# first, and a run that stops early (a 401 after a browser login replaced the
# admin session, Ctrl-C, a dead stack) records every proof it did not finish as
# failed instead of leaving an earlier run's passes on disk.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"
RUNTIME_ENV="${ROOT_DIR}/.runtime/ai-studio.env"
EV="${ROOT_DIR}/evidence/bodies"
RESULTS="${ROOT_DIR}/evidence/results.json"
REPORT="${ROOT_DIR}/evidence/validation.md"
mkdir -p "${EV}"
rm -f "${RESULTS}" "${REPORT}"

PROOFS=(AISTUDIO-ENT-001 AISTUDIO-ROUTER-001 AISTUDIO-UNIFIED-001 AISTUDIO-BUDGET-001 AISTUDIO-GUARDRAIL-001
        AISTUDIO-AUDIT-001 AISTUDIO-TYKMCP-001 AISTUDIO-METRICS-001 AISTUDIO-FAILOVER-001)
results='[]'
failed=0
completed=""
version=unknown
alpha_svc="llm-alpha"
alpha_stopped=""
record() {
  local test_id="$1" status="$2" detail="$3"
  results="$(jq -c --arg id "${test_id}" --arg status "${status}" --arg detail "${detail}" \
    '. + [{test_id:$id,status:$status,detail:$detail}]' <<<"${results}")"
  [[ "${status}" != failed ]] || failed=1
  printf '%-24s %-8s %s\n' "${test_id}" "${status}" "${detail}"
}

write_evidence() {
  printf '%s\n' "${results}" | jq . >"${RESULTS}"
  {
    echo '# AI Studio Companion Validation'
    echo
    echo "Tyk AI Studio ${version} Enterprise. A separate product and a separate stack"
    echo 'from the Tyk Gateway demo bundle in the parent directory.'
    echo
    echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [[ -z "${completed}" ]]; then
      echo
      echo "**This run stopped early (exit $1).** Every proof it did not finish is recorded as failed."
    fi
    echo
    echo '| Test | Status | Detail |'
    echo '| --- | --- | --- |'
    jq -r '.[] | "| `\(.test_id)` | `\(.status)` | \(.detail | gsub("\\|"; "\\\\|")) |"' "${RESULTS}"
  } >"${REPORT}"
}

# finish RC: the EXIT trap. Restores the failover stub if it is still stopped,
# records a failed row for every proof without one, writes the evidence files.
finish() {
  local rc="$1" id
  set +e
  trap - EXIT
  if [[ -n "${alpha_stopped}" ]]; then docker compose start "${alpha_svc}" >/dev/null 2>&1; fi
  for id in "${PROOFS[@]}"; do
    jq -e --arg id "${id}" 'any(.[]; .test_id == $id)' <<<"${results}" >/dev/null 2>&1 && continue
    if [[ -n "${completed}" ]]; then
      record "${id}" failed "verify-ai-studio.sh finished without recording this proof"
    else
      record "${id}" failed "verify-ai-studio.sh stopped (exit ${rc}) before this proof completed; no earlier result is kept"
    fi
  done
  write_evidence "${rc}"
  if [[ -n "${completed}" ]]; then exit "${failed}"; fi
  (( rc != 0 )) || rc=1
  exit "${rc}"
}
trap 'finish $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

set -a
# shellcheck disable=SC1091
source ./secret.env
# shellcheck disable=SC1090
[[ -f "${RUNTIME_ENV}" ]] && source "${RUNTIME_ENV}"
set +a

# shellcheck disable=SC2034  # JAR is read by lib/admin.sh
JAR="${ROOT_DIR}/.runtime/cookies-verify.jar"
# shellcheck source=lib/admin.sh
source "${ROOT_DIR}/lib/admin.sh"
# Host-only admin credentials (not exported). Without admin.env the admin-backed
# proofs record failed or blocked; the edge proofs still run.
load_admin_env || true

ADMIN_URL="${AISTUDIO_ADMIN_URL:-http://localhost:__AISTUDIO_ADMIN_PORT__}"
EDGE_URL="${AISTUDIO_ROUTER_URL:-http://localhost:__AISTUDIO_ROUTER_PORT__}"
UNIFIED_PATH="${GATEWAY_UNIFIED_ROUTER_PATH:-$(conf_value confs/mgw-ent.env GATEWAY_UNIFIED_ROUTER_PATH)}"
UNIFIED_PATH="${UNIFIED_PATH:-/v1}"
alpha_svc="${AISTUDIO_LLM_A_HOST:-llm-alpha}"
ROUTER_SLUG="${AISTUDIO_ROUTER_SLUG:-__SLUG__-router}"
APP_KEY="${AISTUDIO_APP_KEY:-}"
BUDGET_KEY="${AISTUDIO_BUDGET_APP_KEY:-}"
SLUG_A="${AISTUDIO_LLM_A_SLUG:-}"
SLUG_B="${AISTUDIO_LLM_B_SLUG:-}"
SRC_A="${AISTUDIO_POOL_A_SOURCE_MODEL:-gpt-4o}"
DST_A="${AISTUDIO_POOL_A_TARGET_MODEL:-alpha-fast}"
SRC_B="${AISTUDIO_POOL_B_SOURCE_MODEL:-claude-3-5-sonnet}"
DST_B="${AISTUDIO_POOL_B_TARGET_MODEL:-beta-deep}"
UNMATCHED_MODEL="${AISTUDIO_UNMATCHED_MODEL:-llama-3}"
# A syntactically valid IBAN (the widely published German example; mod-97 checks
# out) and a prompt with no personal data in it.
IBAN_PROMPT="${AISTUDIO_IBAN_PROMPT:-Please transfer the refund to DE89 3704 0044 0532 0130 00 today}"
IBAN_NEEDLE="${AISTUDIO_IBAN_NEEDLE:-0532 0130}"
CLEAN_PROMPT="${AISTUDIO_CLEAN_PROMPT:-Summarise the release notes in three bullet points}"
AISTUDIO_ADMIN_EMAIL="${AISTUDIO_ADMIN_EMAIL:-admin@__SLUG__.invalid}"
SLUG_CLASH_MESSAGE="route name is already used by another LLM or router"

# admin_login: true when a fresh admin session was obtained. Never exits the
# script: aistudio_login's ${...:?} guard would, so an absent password is a no.
admin_login() { [[ -n "${AISTUDIO_ADMIN_PASSWORD:-}" ]] && aistudio_login 2>/dev/null; }

# chat KEY PATH MODEL PROMPT NAME: POST an OpenAI chat request to the edge,
# keep headers and body under evidence/bodies/NAME.{headers,json}, print the status.
# The App key is a header read from stdin (-H @-), so it never appears in ps.
chat() {
  local key="$1" path="$2" model="$3" prompt="$4" name="$5" payload
  payload="$(MODEL="${model}" PROMPT="${prompt}" python3 -c "
import json,os
print(json.dumps({'model':os.environ['MODEL'],'messages':[{'role':'user','content':os.environ['PROMPT']}]}))")"
  curl -sS -D "${EV}/${name}.headers" -o "${EV}/${name}.json" -w '%{http_code}' \
    -X POST "${EDGE_URL}${path}" \
    -H @- -H 'Content-Type: application/json' \
    -d "${payload}" <<<"Authorization: Bearer ${key}" 2>/dev/null || echo 000
}
hdr() { grep -i "^$2:" "${EV}/$1.headers" 2>/dev/null | head -1 | cut -d' ' -f2- | tr -d '\r' || true; }
content() { jq -r '.choices[0].message.content // ""' "${EV}/$1.json" 2>/dev/null || true; }
field() { jq -r "$2 // \"\"" "${EV}/$1.json" 2>/dev/null || true; }

have_admin=""
if admin_login; then have_admin=yes; fi

# --- AISTUDIO-ENT-001 --------------------------------------------------------
# GET /auth/features reports edition, version and the feature flags. The proof
# needs edition "enterprise", feature_model_router true and version 2.3 or
# later, and records every flag that is on and license_days_left when present.
features="$(curl -sS "${ADMIN_URL}/auth/features" 2>/dev/null || echo '{}')"
printf '%s\n' "${features}" | jq . >"${EV}/auth-features.json" 2>/dev/null || printf '%s\n' "${features}" >"${EV}/auth-features.json"
edition="$(jq -r '.edition // "unknown"' <<<"${features}" 2>/dev/null || echo unknown)"
version="$(jq -r '.version // "unknown"' <<<"${features}" 2>/dev/null || echo unknown)"
router_flag="$(jq -r '.features.feature_model_router // false' <<<"${features}" 2>/dev/null || echo false)"
flags="$(jq -r '[.features | to_entries[] | select(.value==true) | .key] | join(", ")' <<<"${features}" 2>/dev/null || true)"
at_least_2_3="$(python3 -c "
import re,sys
m=re.match(r'v?(\d+)\.(\d+)',sys.argv[1])
print('yes' if m and (int(m.group(1)),int(m.group(2)))>=(2,3) else 'no')" "${version}")"
if [[ "${edition}" == enterprise && "${router_flag}" == true && "${at_least_2_3}" == yes ]]; then
  days="$(jq -r 'if has("license_days_left") then "licence expires in \(.license_days_left) days" else "no licence day count reported" end' <<<"${features}" 2>/dev/null)" \
    || days="licence day count unreadable"
  record AISTUDIO-ENT-001 passed "Build ${version} reports edition=enterprise (${days}); features on: ${flags} (evidence: evidence/bodies/auth-features.json)"
else
  record AISTUDIO-ENT-001 failed "Expected an Enterprise 2.3+ build with the model router feature; got version=${version} edition=${edition} feature_model_router=${router_flag}. Check TYK_AI_LICENSE in secret.env."
fi

if [[ -z "${APP_KEY}" || -z "${BUDGET_KEY}" || -z "${SLUG_A}" || -z "${SLUG_B}" ]]; then
  for id in AISTUDIO-ROUTER-001 AISTUDIO-UNIFIED-001 AISTUDIO-BUDGET-001 AISTUDIO-GUARDRAIL-001 AISTUDIO-FAILOVER-001; do
    record "${id}" blocked "No App credentials in .runtime/ai-studio.env; run ./configure-ai-studio.sh first"
  done
  APP_KEY=""
fi

# --- AISTUDIO-ROUTER-001 -----------------------------------------------------
# /router/{slug}/v1/... is the Model Router's alias path. The App holds the
# router (model_router_ids), so routing runs without the deprecated fallback.
# Negative cases: a model no pool claims is 400; a router slug that an LLM
# already answers to is refused 400 on save.
if [[ -n "${APP_KEY}" ]]; then
  s_a="$(chat "${APP_KEY}" "/router/${ROUTER_SLUG}/v1/chat/completions" "${SRC_A}" verify "router-${SRC_A}")"
  s_b="$(chat "${APP_KEY}" "/router/${ROUTER_SLUG}/v1/chat/completions" "${SRC_B}" verify "router-${SRC_B}")"
  s_u="$(chat "${APP_KEY}" "/router/${ROUTER_SLUG}/v1/chat/completions" "${UNMATCHED_MODEL}" verify router-unmatched)"
  c_a="$(content "router-${SRC_A}")"; c_b="$(content "router-${SRC_B}")"
  clash="no-admin"
  if [[ -n "${have_admin}" ]]; then
    body="$(NAME="__DISPLAY_NAME__ Slug Clash Check" SLUG="${SLUG_A}" A="${AISTUDIO_LLM_A_ID:-0}" python3 -c "
import json,os
E=os.environ
print(json.dumps({'data':{'type':'model-routers','attributes':{'name':E['NAME'],'slug':E['SLUG'],'api_compat':'openai','active':False,
  'pools':[{'name':'clash','model_pattern':'clash-*','selection_algorithm':'round_robin','priority':1,
            'vendors':[{'llm_id':int(E['A']),'weight':1,'active':True}]}]}}}))")"
    out="$(api_raw POST /api/v1/model-routers "${body}" 2>/dev/null)" || out=$'\n000'
    clash="${out##*$'\n'}"
    printf '%s\n' "${out%$'\n'*}" | jq . >"${EV}/router-slug-clash.json" 2>/dev/null || printf '%s\n' "${out%$'\n'*}" >"${EV}/router-slug-clash.json"
    if [[ "${clash}" == 2?? ]]; then
      # A future release that accepts the clash must not leave the router behind.
      rid="$(jq -r '.data.id // empty' "${EV}/router-slug-clash.json" 2>/dev/null || true)"
      if [[ -n "${rid}" ]]; then api DELETE "/api/v1/model-routers/${rid}" >/dev/null 2>&1 || true; fi
    fi
    # Only the slug rule's own refusal counts, not any 400.
    if [[ "${clash}" == 400 ]] && ! grep -qF "${SLUG_CLASH_MESSAGE}" "${EV}/router-slug-clash.json"; then
      clash="400 without \"${SLUG_CLASH_MESSAGE}\""
    fi
  fi
  if [[ "${s_a}" == 200 && "${s_b}" == 200 \
     && "${c_a}" == "SERVED-BY=alpha MODEL=${DST_A}" && "${c_b}" == "SERVED-BY=beta MODEL=${DST_B}" \
     && "$(field "router-${SRC_A}" .model)" == "${DST_A}" && "$(field "router-${SRC_B}" .model)" == "${DST_B}" \
     && "$(hdr "router-${SRC_A}" X-Tyk-Router)" == "${ROUTER_SLUG}" \
     && "${s_u}" == 400 && "${clash}" == 400 ]]; then
    record AISTUDIO-ROUTER-001 passed "POST /router/${ROUTER_SLUG}/v1/chat/completions: ${SRC_A} -> backend alpha as ${DST_A} (pool $(hdr "router-${SRC_A}" X-Tyk-Route)), ${SRC_B} -> backend beta as ${DST_B} (pool $(hdr "router-${SRC_B}" X-Tyk-Route)); unmatched ${UNMATCHED_MODEL} rejected ${s_u}; a router slug equal to LLM slug ${SLUG_A} is refused ${clash} \"${SLUG_CLASH_MESSAGE}\" (evidence: evidence/bodies/router-*.json)"
  else
    record AISTUDIO-ROUTER-001 failed "Routing mismatch: ${SRC_A}=${s_a} '${c_a}', ${SRC_B}=${s_b} '${c_b}', unmatched=${s_u}, slug clash=${clash}. An empty completion with 200 means the stub backend is not answering stream:true; check: docker compose logs mgw | grep -i 'model routers loaded'"
  fi
fi

# --- AISTUDIO-UNIFIED-001 ----------------------------------------------------
# Unified ingress on the edge: POST ${UNIFIED_PATH}/chat/completions with
# model "{llm-slug}/{model}" or "{router-slug}/{model}", GET ${UNIFIED_PATH}/models
# scoped to the App's grants. An LLM the App was never granted is 403
# ("vendor '<slug>' not found or not supported by your access rights").
if [[ -n "${APP_KEY}" ]]; then
  u_a="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_A}/${DST_A}" verify unified-llm-a)"
  u_b="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_B}/${DST_B}" verify unified-llm-b)"
  r_a="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${ROUTER_SLUG}/${SRC_A}" verify unified-router-a)"
  r_b="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${ROUTER_SLUG}/${SRC_B}" verify unified-router-b)"
  deny="$(chat "${BUDGET_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_B}/${DST_B}" verify unified-ungranted)"
  deny_msg="$(jq -r '(.error.message // .message // "")' "${EV}/unified-ungranted.json" 2>/dev/null || true)"
  models_code="$(curl -sS -o "${EV}/unified-models.json" -w '%{http_code}' "${EDGE_URL}${UNIFIED_PATH}/models" -H @- <<<"Authorization: Bearer ${APP_KEY}" 2>/dev/null || echo 000)"
  missing_models=""
  for m in "${SLUG_A}/${DST_A}" "${SLUG_B}/${DST_B}" "${ROUTER_SLUG}/${SRC_A}" "${ROUTER_SLUG}/${SRC_B}"; do
    jq -e --arg m "${m}" '[.data[].id] | index($m)' "${EV}/unified-models.json" >/dev/null 2>&1 || missing_models+=" ${m}"
  done
  if [[ "${u_a}" == 200 && "$(content unified-llm-a)" == "SERVED-BY=alpha MODEL=${DST_A}" && "$(hdr unified-llm-a X-Tyk-Served-LLM)" == "${SLUG_A}" \
     && "${u_b}" == 200 && "$(content unified-llm-b)" == "SERVED-BY=beta MODEL=${DST_B}" && "$(hdr unified-llm-b X-Tyk-Served-LLM)" == "${SLUG_B}" \
     && "${r_a}" == 200 && "$(content unified-router-a)" == "SERVED-BY=alpha MODEL=${DST_A}" && "$(hdr unified-router-a X-Tyk-Router)" == "${ROUTER_SLUG}" \
     && "${r_b}" == 200 && "$(content unified-router-b)" == "SERVED-BY=beta MODEL=${DST_B}" && "$(hdr unified-router-b X-Tyk-Served-Model)" == "${DST_B}" \
     && "${models_code}" == 200 && -z "${missing_models}" \
     && "${deny}" == 403 && "${deny_msg}" == *"access rights"* ]]; then
    record AISTUDIO-UNIFIED-001 passed "POST ${UNIFIED_PATH}/chat/completions: ${SLUG_A}/${DST_A} -> alpha (X-Tyk-Served-LLM ${SLUG_A}), ${SLUG_B}/${DST_B} -> beta, ${ROUTER_SLUG}/${SRC_A} -> alpha as ${DST_A} (pool $(hdr unified-router-a X-Tyk-Route), reason $(hdr unified-router-a X-Tyk-Route-Reason)), ${ROUTER_SLUG}/${SRC_B} -> beta as ${DST_B}; GET ${UNIFIED_PATH}/models lists $(jq -r '[.data[].id] | join(", ")' "${EV}/unified-models.json"); an LLM the App was never granted is refused ${deny} (\"${deny_msg}\") (evidence: evidence/bodies/unified-*)"
  else
    record AISTUDIO-UNIFIED-001 failed "Unified ingress mismatch: llm-a=${u_a} '$(content unified-llm-a)' llm-b=${u_b} router-a=${r_a} router-b=${r_b} models=${models_code} missing=[${missing_models# }] ungranted=${deny} '${deny_msg}'"
  fi
fi

# --- AISTUDIO-BUDGET-001 -----------------------------------------------------
# monthly_budget 0 means zero spend (null means no limit). The edge learns of
# the zero budget from the control plane, so allow up to 45 s after
# configuration. Refusals on /ai/ and /v1 are OpenAI error envelopes with code
# budget_exceeded (403).
if [[ -n "${APP_KEY}" ]]; then
  b_v1=000
  for _ in $(seq 1 15); do
    b_v1="$(chat "${BUDGET_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_A}/${DST_A}" verify budget-v1)"
    [[ "${b_v1}" == 403 && "$(field budget-v1 .error.code)" == budget_exceeded ]] && break
    sleep 3
  done
  b_ai="$(chat "${BUDGET_KEY}" "/ai/${SLUG_A}/v1/chat/completions" "${DST_A}" verify budget-ai)"
  budget_state="admin login failed"
  rm -f "${EV}/budget-apps.json"
  if [[ -n "${have_admin}" ]]; then
    # Guarded: a 401 here (a browser login replaced the session) must fail this
    # proof, not abort the run.
    zero="$(api GET "/api/v1/apps/${AISTUDIO_BUDGET_APP_ID:-0}" 2>/dev/null | jq -c '.data.attributes | {name, monthly_budget}' 2>/dev/null)" || zero=""
    demo="$(api GET "/api/v1/apps/${AISTUDIO_APP_ID:-0}" 2>/dev/null | jq -c '.data.attributes | {name, monthly_budget}' 2>/dev/null)" || demo=""
    if [[ -n "${zero}" && -n "${demo}" ]]; then
      printf '{"zero_budget_app":%s,"demo_app":%s}\n' "${zero}" "${demo}" | jq . >"${EV}/budget-apps.json" 2>/dev/null || rm -f "${EV}/budget-apps.json"
      budget_state="$(jq -r '"\(.zero_budget_app.name) monthly_budget=\(.zero_budget_app.monthly_budget), \(.demo_app.name) monthly_budget=\(.demo_app.monthly_budget) (no limit)"' "${EV}/budget-apps.json" 2>/dev/null)" \
        || budget_state="App budgets unreadable"
    else
      budget_state="the admin API refused the App reads (session replaced by another login?)"
    fi
  fi
  if [[ "${b_v1}" == 403 && "$(field budget-v1 .error.code)" == budget_exceeded \
     && "${b_ai}" == 403 && "$(field budget-ai .error.code)" == budget_exceeded \
     && "$(jq -r '.zero_budget_app.monthly_budget' "${EV}/budget-apps.json" 2>/dev/null)" == 0 \
     && "$(jq -r '.demo_app.monthly_budget' "${EV}/budget-apps.json" 2>/dev/null)" == null ]]; then
    record AISTUDIO-BUDGET-001 passed "Zero-budget App refused on ${UNIFIED_PATH}/chat/completions and /ai/${SLUG_A}/v1/chat/completions with HTTP ${b_v1} {\"error\":{\"code\":\"budget_exceeded\",\"type\":\"$(field budget-v1 .error.type)\",\"message\":\"$(field budget-v1 .error.message)\"}}; ${budget_state} (evidence: evidence/bodies/budget-*.json)"
  else
    record AISTUDIO-BUDGET-001 failed "Expected 403 budget_exceeded on both paths; got v1=${b_v1} '$(field budget-v1 .error.code)' ai=${b_ai} '$(field budget-ai .error.code)'; ${budget_state}"
  fi
fi

# --- AISTUDIO-GUARDRAIL-001 --------------------------------------------------
# Enterprise guardrail filter (built-in pattern library: IBAN, payment card,
# e-mail; action block) attached to the first LLM as a request filter. A block
# is HTTP 400 with the filter name and block message (OpenAI envelope,
# policy_violation); the hit is a compliance event (guardrail.pii.iban) that
# never carries the matched text, shipped edge -> hub on the analytics pulse
# (10 s here), so allow up to 60 s for it to arrive.
if [[ -n "${APP_KEY}" ]]; then
  today="$(date -u +%Y-%m-%d)"
  ce_count() {
    api GET "/api/v1/compliance/events?event_type=guardrail.pii.iban&start_date=${today}&end_date=${today}&limit=200" 2>/dev/null \
      | jq -r '.total_count // (.events | length) // 0' 2>/dev/null || echo 0
  }
  before=0; [[ -n "${have_admin}" ]] && before="$(ce_count)"
  g_block="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_A}/${DST_A}" "${IBAN_PROMPT}" guardrail-iban-blocked)"
  g_clean="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_A}/${DST_A}" "${CLEAN_PROMPT}" guardrail-clean-passed)"
  after="${before}"
  rm -f "${EV}/guardrail-compliance-event.json"
  if [[ -n "${have_admin}" ]]; then
    for _ in $(seq 1 20); do
      after="$(ce_count)"
      (( after > before )) && break
      sleep 3
    done
    api GET "/api/v1/compliance/events?event_type=guardrail.pii.iban&start_date=${today}&end_date=${today}&limit=1" 2>/dev/null \
      | jq '{total_count, sample: (.events[0] // null)}' >"${EV}/guardrail-compliance-event.json" 2>/dev/null \
      || rm -f "${EV}/guardrail-compliance-event.json"
  fi
  leaked="no"
  needle_compact="${IBAN_NEEDLE// /}"
  if grep -qF -e "${IBAN_NEEDLE}" -e "${needle_compact}" "${EV}/guardrail-compliance-event.json" 2>/dev/null; then leaked="yes"; fi
  msg="$(field guardrail-iban-blocked .error.message)"
  if [[ "${g_block}" == 400 && "${msg}" == *"${AISTUDIO_GUARDRAIL_NAME:-Guardrail}"* \
     && "${g_clean}" == 200 && "$(content guardrail-clean-passed)" == "SERVED-BY=alpha MODEL=${DST_A}" \
     && "${after}" -gt "${before}" && "${leaked}" == no && -s "${EV}/guardrail-compliance-event.json" ]]; then
    record AISTUDIO-GUARDRAIL-001 passed "Prompt with an IBAN blocked with HTTP ${g_block} (\"${msg}\") before reaching the provider; a clean prompt passed (200, served by alpha); compliance events guardrail.pii.iban today ${before} -> ${after}, event carries no matched text (evidence: evidence/bodies/guardrail-*.json)"
  else
    record AISTUDIO-GUARDRAIL-001 failed "Expected 400 block + 200 clean + a new compliance event without the matched text; got block=${g_block} '${msg}' clean=${g_clean} events ${before}->${after} leaked=${leaked} admin=${have_admin:-no}"
  fi
fi

# --- AISTUDIO-AUDIT-001 ------------------------------------------------------
# Enterprise audit trail, on by default: GET /api/v1/audit/records.
# Actions are named from the route ("Create LLM", "Create Model Router",
# "Create App", "Create Filter"); diffs redact secret-looking keys as
# [REDACTED]. The list is newest first and logins pile up in front, and every
# configure and verify run adds a refused (400) slug-clash probe under
# "Create Model Router", so on a long-rehearsed stack the real create record
# falls off any fixed first page. The proof therefore asks per action for
# successful records only (status_class=2xx), oldest first (sort=asc: the
# creates are the oldest), pages until a short page (bounded), and then asserts
# on the specific resource names.
if [[ -z "${have_admin}" ]]; then
  record AISTUDIO-AUDIT-001 blocked "Admin login failed; cannot read the audit trail"
else
  audit_status="$(api GET /api/v1/audit/status 2>/dev/null)" || audit_status='{}'
  for action in "Create LLM" "Create Model Router" "Create App" "Create Filter"; do
    encoded="$(jq -rn --arg a "${action}" '$a | @uri')"
    for page in 1 2 3 4 5; do
      out="$(api GET "/api/v1/audit/records?action=${encoded}&status_class=2xx&sort=asc&page=${page}&page_size=500" 2>/dev/null)" || out='{}'
      printf '%s\n' "${out}"
      [[ "$(jq -r '(.records // []) | length' <<<"${out}" 2>/dev/null || echo 0)" == 500 ]] || break
    done
  done | jq -s '{records: (map(.records // []) | add // []), total: (map(.total // 0) | add // 0)}' >"${EV}/audit-records-page.json" 2>/dev/null \
    || echo '{"records":[],"total":0}' >"${EV}/audit-records-page.json"
  audit_total="$(api GET "/api/v1/audit/records?page_size=1" 2>/dev/null | jq -r '.total // 0' 2>/dev/null)" || audit_total="an unknown number of"
  AUDIT_ACTOR="${AISTUDIO_ADMIN_EMAIL}" LEAK="${LLM_A_API_KEY:-stub-key}" \
  WANT_LLM="${AISTUDIO_LLM_A_NAME:-}|${AISTUDIO_LLM_B_NAME:-}" WANT_ROUTER="${AISTUDIO_ROUTER_NAME:-}" \
  WANT_APP="${AISTUDIO_APP_NAME:-}|${AISTUDIO_BUDGET_APP_NAME:-}" WANT_FILTER="${AISTUDIO_GUARDRAIL_NAME:-}" \
  python3 - "${EV}/audit-records-page.json" "${EV}/audit-sample-record.json" >"${EV}/audit-check.txt" <<'PY' || true
import json, os, sys
page = json.load(open(sys.argv[1]))
recs = page.get("records") or []
E = os.environ
want = {
    "Create LLM": [n for n in E["WANT_LLM"].split("|") if n],
    "Create Model Router": [E["WANT_ROUTER"]] if E["WANT_ROUTER"] else [],
    "Create App": [n for n in E["WANT_APP"].split("|") if n],
    "Create Filter": [E["WANT_FILTER"]] if E["WANT_FILTER"] else [],
}
def name_of(r):
    if r.get("resource_name"):
        return r["resource_name"]
    d = r.get("diff") or {}
    n = d.get("name") if isinstance(d, dict) else None
    return (n or {}).get("new") if isinstance(n, dict) else None
found, missing = {}, []
for action, names in want.items():
    if not names:
        missing.append("%s (no name in .runtime/ai-studio.env)" % action)
    for n in names:
        hit = [r for r in recs if r.get("action") == action and name_of(r) == n and not r.get("error")
           and 200 <= int(r.get("status") or 0) < 300]
        if hit:
            found[n] = hit[-1]
        else:
            missing.append("%s '%s'" % (action, n))
actor_ok = bool(found) and all((r.get("user") == E["AUDIT_ACTOR"]) and r.get("timestamp") for r in found.values())
llm_names = [n for n in E["WANT_LLM"].split("|") if n]
llm = found.get(llm_names[0]) if llm_names else None
llm = llm or {}
diff = llm.get("diff") or {}
redacted = isinstance(diff.get("api_key"), dict) and diff["api_key"].get("new") == "[REDACTED]"
leak = bool(E["LEAK"]) and E["LEAK"] in json.dumps(recs)
if llm:
    json.dump(llm, open(sys.argv[2], "w"), indent=2)
print(json.dumps({"total": page.get("total"), "found": sorted(found), "missing": missing,
                  "actor_ok": actor_ok, "api_key_redacted": redacted, "plaintext_secret_found": leak,
                  "sample": {k: llm.get(k) for k in ("id", "timestamp", "user", "auth_method", "ip", "action", "method", "url", "status", "resource_type", "resource_id")}}))
PY
  check="$(cat "${EV}/audit-check.txt" 2>/dev/null || true)"
  [[ -n "${check}" ]] || check='{}'
  if [[ "$(jq -r '.available // false' <<<"${audit_status}" 2>/dev/null)" == true \
     && "$(jq -r '.missing | length' <<<"${check}" 2>/dev/null)" == 0 \
     && "$(jq -r '.actor_ok' <<<"${check}" 2>/dev/null)" == true \
     && "$(jq -r '.api_key_redacted' <<<"${check}" 2>/dev/null)" == true \
     && "$(jq -r '.plaintext_secret_found' <<<"${check}" 2>/dev/null)" == false ]]; then
    record AISTUDIO-AUDIT-001 passed "GET /api/v1/audit/records holds ${audit_total} records; create events for both LLMs, the router, both Apps and the guardrail by $(jq -r '.sample.user' <<<"${check}") (auth $(jq -r '.sample.auth_method' <<<"${check}"), from $(jq -r '.sample.ip' <<<"${check}")) with timestamps; the LLM api_key is stored as [REDACTED] (evidence: evidence/bodies/audit-sample-record.json)"
  else
    record AISTUDIO-AUDIT-001 failed "Audit trail check failed: status=$(jq -c . <<<"${audit_status}" 2>/dev/null) check=${check}"
  fi
fi

# --- AISTUDIO-TYKMCP-001 -----------------------------------------------------
# Enterprise: MCP proxies managed by the Tyk Gateway bundle's Dashboard,
# imported over a read-only catalogue connection. Runs the idempotent import
# first (short wait) so a verify after the Gateway bundle came up needs no
# separate step. Blocked, not failed, when the bundle has no mcp module or that
# separate stack is not running. The import logs in on its own and every login
# replaces the admin's session, so this script logs in again afterwards.
tykmcp_rc=0
TYK_MCP_WAIT_SECONDS="${TYK_MCP_VERIFY_WAIT_SECONDS:-20}" "${ROOT_DIR}/proofs/tyk-mcp-import.sh" >"${EV}/tyk-mcp-import.log" 2>&1 || tykmcp_rc=$?
have_admin=""
if admin_login; then have_admin=yes; fi
tykmcp_detail="$(jq -r '.detail // empty' "${EV}/tyk-mcp-status.json" 2>/dev/null || true)"
if [[ "${tykmcp_rc}" == 2 ]]; then
  record AISTUDIO-TYKMCP-001 blocked "${tykmcp_detail:-Tyk Dashboard not reachable}"
elif [[ -z "${have_admin}" ]]; then
  record AISTUDIO-TYKMCP-001 blocked "Admin login failed; cannot read Tyk connections"
else
  cname="${TYK_CONNECTION_NAME:-__DISPLAY_NAME__ Tyk Dashboard}"
  conn="$(api GET /api/v1/tyk-connections 2>/dev/null | CNAME="${cname}" jq -c '(if type=="object" then (.data // .connections // []) else . end) | [.[] | select(.name==env.CNAME)][0] // empty' 2>/dev/null || true)"
  if [[ -z "${conn}" ]]; then
    record AISTUDIO-TYKMCP-001 failed "No Tyk connection '${cname}' after the import (exit ${tykmcp_rc}): ${tykmcp_detail:-see evidence/bodies/tyk-mcp-import.log}"
  else
    cid="$(jq -r .id <<<"${conn}" 2>/dev/null)" || cid=""
    api GET "/api/v1/mcp-servers?connection_id=${cid}&page_size=100" >"${EV}/tyk-mcp-servers.json" 2>/dev/null || echo '{}' >"${EV}/tyk-mcp-servers.json"
    jq 'del(.token_hint)' <<<"${conn}" >"${EV}/tyk-mcp-connection.json" 2>/dev/null || true
    n="$(jq -r '(.servers // []) | length' "${EV}/tyk-mcp-servers.json" 2>/dev/null)" || n=0
    names="$(jq -r '[(.servers // [])[] | "\(.name) (\(.listen_path), \(.auth_mode), \(.dashboard_state))"] | join("; ")' "${EV}/tyk-mcp-servers.json" 2>/dev/null)" || names=""
    if [[ "${tykmcp_rc}" == 0 && "$(jq -r .status <<<"${conn}")" == active && "$(jq -r .effective_mode <<<"${conn}")" == catalogue && "${n}" -gt 0 ]]; then
      record AISTUDIO-TYKMCP-001 passed "Tyk connection '$(jq -r .name <<<"${conn}")' to $(jq -r .dashboard_url <<<"${conn}") is active in read-only catalogue mode (last sync $(jq -r '.last_sync_status // "n/a"' <<<"${conn}")); ${n} MCP proxies imported: ${names} (evidence: evidence/bodies/tyk-mcp-*.json)"
    else
      record AISTUDIO-TYKMCP-001 failed "Import exit ${tykmcp_rc}; connection status=$(jq -r .status <<<"${conn}") mode=$(jq -r .effective_mode <<<"${conn}") imported=${n} last_error=$(jq -r '.last_sync_error // ""' <<<"${conn}") ${tykmcp_detail}"
    fi
  fi
fi

# --- AISTUDIO-METRICS-001 ----------------------------------------------------
# With METRICS_AUTH_TOKEN set, a missing or wrong bearer on /metrics is 401
# {"error":"unauthorized"} with WWW-Authenticate: Bearer, on both planes.
if [[ -z "${METRICS_AUTH_TOKEN:-}" ]]; then
  record AISTUDIO-METRICS-001 failed "METRICS_AUTH_TOKEN is not set in secret.env; the generator mints one"
else
  m_ok=yes m_detail=""
  for plane in "hub|${ADMIN_URL}" "edge|${EDGE_URL}"; do
    name="${plane%%|*}"; url="${plane#*|}"
    good="$(curl -sS -o "${EV}/metrics-${name}.txt" -w '%{http_code}' -H @- "${url}/metrics" <<<"Authorization: Bearer ${METRICS_AUTH_TOKEN}" 2>/dev/null || echo 000)"
    none="$(curl -sS -o /dev/null -w '%{http_code}' "${url}/metrics" 2>/dev/null || echo 000)"
    wrong="$(curl -sS -o "${EV}/metrics-${name}-wrong-token.json" -w '%{http_code}' -H 'Authorization: Bearer not-the-token' "${url}/metrics" 2>/dev/null || echo 000)"
    series="$(grep -c '^# TYPE' "${EV}/metrics-${name}.txt" 2>/dev/null || true)"; series="${series:-0}"
    head -c 4000 "${EV}/metrics-${name}.txt" >"${EV}/metrics-${name}-head.txt" 2>/dev/null || true
    rm -f "${EV}/metrics-${name}.txt"
    if [[ "${good}" == 200 && "${series}" -gt 0 && "${none}" == 401 && "${wrong}" == 401 ]]; then
      m_detail+="${name} ${url}/metrics bearer=200 (${series} metric families), no token=${none}, wrong token=${wrong}; "
    else
      m_ok=""; m_detail+="${name}: bearer=${good} families=${series} none=${none} wrong=${wrong}; "
    fi
  done
  if [[ -n "${m_ok}" ]]; then
    record AISTUDIO-METRICS-001 passed "${m_detail}(evidence: evidence/bodies/metrics-*)"
  else
    record AISTUDIO-METRICS-001 failed "${m_detail}"
  fi
fi

# --- AISTUDIO-FAILOVER-001 ---------------------------------------------------
# LLM failover (Community and Enterprise). The first LLM carries a waterfall to the second
# (failover.targets [{llm_id, model}]). Stop its backend: the same call returns
# 200 served by the fallback with X-Tyk-Failover: true, X-Tyk-Served-LLM and
# X-Tyk-Served-Model, and the body model is the model that answered. Start the
# backend again: the primary serves again.
if [[ -n "${APP_KEY}" ]]; then
  f_up="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_A}/${DST_A}" verify failover-1-primary-up)"
  # Set BEFORE the stop: the EXIT trap (finish) starts the stub again whenever
  # this flag is set, so an interrupted run never leaves llm-alpha down.
  alpha_stopped=yes
  docker compose stop "${alpha_svc}" >/dev/null 2>&1 || true
  f_down="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_A}/${DST_A}" verify failover-2-primary-down)"
  docker compose start "${alpha_svc}" >/dev/null 2>&1 && alpha_stopped=""
  f_back=000
  for _ in $(seq 1 20); do
    f_back="$(chat "${APP_KEY}" "${UNIFIED_PATH}/chat/completions" "${SLUG_A}/${DST_A}" verify failover-3-primary-restored)"
    [[ "${f_back}" == 200 && "$(content failover-3-primary-restored)" == "SERVED-BY=alpha MODEL=${DST_A}" ]] && break
    sleep 2
  done
  if [[ "${f_up}" == 200 && "$(content failover-1-primary-up)" == "SERVED-BY=alpha MODEL=${DST_A}" && -z "$(hdr failover-1-primary-up X-Tyk-Failover)" \
     && "${f_down}" == 200 && "$(content failover-2-primary-down)" == "SERVED-BY=beta MODEL=${DST_B}" \
     && "$(hdr failover-2-primary-down X-Tyk-Failover)" == true \
     && "$(hdr failover-2-primary-down X-Tyk-Served-LLM)" == "${SLUG_B}" \
     && "$(hdr failover-2-primary-down X-Tyk-Served-Model)" == "${DST_B}" \
     && "$(field failover-2-primary-down .model)" == "${DST_B}" \
     && "${f_back}" == 200 && "$(content failover-3-primary-restored)" == "SERVED-BY=alpha MODEL=${DST_A}" ]]; then
    record AISTUDIO-FAILOVER-001 passed "model ${SLUG_A}/${DST_A}: backend up -> 200 served by ${SLUG_A}; 'docker compose stop ${alpha_svc}' -> 200 served by $(hdr failover-2-primary-down X-Tyk-Served-LLM) as $(hdr failover-2-primary-down X-Tyk-Served-Model) with X-Tyk-Failover: true; started again -> 200 served by ${SLUG_A} (evidence: evidence/bodies/failover-*)"
  else
    record AISTUDIO-FAILOVER-001 failed "up=${f_up} '$(content failover-1-primary-up)' down=${f_down} '$(content failover-2-primary-down)' failover='$(hdr failover-2-primary-down X-Tyk-Failover)' served='$(hdr failover-2-primary-down X-Tyk-Served-LLM)/$(hdr failover-2-primary-down X-Tyk-Served-Model)' restored=${f_back}"
  fi
fi

# --- Evidence files ----------------------------------------------------------
# Written by the EXIT trap (finish -> write_evidence) into evidence/results.json
# and evidence/validation.md, on this normal exit and on an early one alike.
completed=yes
exit "${failed}"
