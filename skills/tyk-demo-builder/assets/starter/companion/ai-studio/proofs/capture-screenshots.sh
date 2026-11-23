#!/usr/bin/env bash
# Optional: capture the AI Studio admin UI and AI Portal screenshots into
# evidence/screenshots/ with the agent-browser CLI. Skipped (exit 0) when
# agent-browser is not installed; nothing else depends on it.
#
# Rules (references/ai-studio.md, "Admin UI screenshots"):
#   - log in ONCE, with an Authorization header set so the SPA's own login call
#     skips CSRF, and reach every page by clicking the sidebar (a full page load
#     over plain HTTP drops the session);
#   - agent-browser scroll does not move the content pane, so scroll with eval +
#     scrollIntoView wrapped in an IIFE (a top-level const persists across evals
#     and the second call fails with "Identifier has already been declared");
#   - check the URL and a page marker before every save, and refuse to save a
#     login page;
#   - every login replaces the admin's single session token, so do not run the
#     other scripts while this one runs. Nothing is ever saved in a form.
#
# Run after ./configure-ai-studio.sh (it reads object ids from .runtime/ai-studio.env).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

AB="${AGENT_BROWSER:-agent-browser}"
if ! command -v "${AB}" >/dev/null 2>&1; then
  echo "agent-browser is not installed; screenshots skipped (optional)."
  exit 0
fi
[[ -f .runtime/ai-studio.env ]] || { echo "run ./configure-ai-studio.sh first" >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source ./secret.env
# shellcheck disable=SC1091
source ./.runtime/ai-studio.env
set +a
# The admin login comes from the host-only admin.env (never exported).
[[ -f admin.env ]] || { echo "admin.env missing; nothing captured" >&2; exit 1; }
# shellcheck disable=SC1091
source ./admin.env
AISTUDIO_ADMIN_EMAIL="${AISTUDIO_ADMIN_EMAIL:-admin@__SLUG__.invalid}"

export AGENT_BROWSER_SESSION="${AGENT_BROWSER_SESSION:-__SLUG__-aistudio}"
ADMIN_URL="${AISTUDIO_ADMIN_URL:-http://localhost:__AISTUDIO_ADMIN_PORT__}"
SHOTS=evidence/screenshots
mkdir -p "${SHOTS}"
saved=0 refused=0

ref_of() { "${AB}" snapshot -i -c 2>/dev/null | grep -F "$1" | head -1 | grep -o 'ref=e[0-9]*' | cut -d= -f2; }
click_ref() {
  local r; r="$(ref_of "$1")"
  [[ -n "${r}" ]] || { echo "no element: $1" >&2; return 1; }
  "${AB}" scrollintoview "@${r}" >/dev/null 2>&1
  "${AB}" click "@${r}" >/dev/null
  sleep "${2:-3}"
}
# expand GROUP CHILD: open a collapsed sidebar group unless its child is visible.
expand() { [[ -n "$(ref_of "$2")" ]] && return 0; click_ref "button \"$1\"" 1; }
# scroll_to_heading TEXT OFFSET: IIFE so repeated evals never redeclare a const.
scroll_to_heading() {
  "${AB}" eval "(()=>{const h=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].find(e=>e.textContent.trim()===$(printf '%s' "$1" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')); if(!h) return 0; h.scrollIntoView({block:'start'}); window.scrollBy(0,-${2:-120}); return 1})()" >/dev/null 2>&1
  sleep 1
}
# save FILE URL_SUFFIX MARKER...: FULL=1 for a full-page capture.
save() {
  local file="$1" want="$2"; shift 2; local url txt m
  url="$("${AB}" get url 2>/dev/null)"; txt="$("${AB}" get text body 2>/dev/null)"
  if [[ "${url}" != *"${want}" ]]; then echo "NOT SAVED ${file}: url ${url}"; refused=$((refused + 1)); return 1; fi
  if [[ "${url}" == */login* || "${txt}" == *"Log in to your account"* ]]; then echo "NOT SAVED ${file}: login page"; refused=$((refused + 1)); return 1; fi
  for m in "$@"; do
    [[ "${txt}" == *"${m}"* ]] || { echo "NOT SAVED ${file}: missing '${m}'"; refused=$((refused + 1)); return 1; }
  done
  if [[ "${FULL:-}" == 1 ]]; then "${AB}" screenshot --full "${SHOTS}/${file}" >/dev/null; else "${AB}" screenshot "${SHOTS}/${file}" >/dev/null; fi
  echo "saved ${file}"; saved=$((saved + 1))
}

"${AB}" close >/dev/null 2>&1
"${AB}" set viewport 1440 900 >/dev/null
"${AB}" --headers '{"Authorization":"session"}' open "${ADMIN_URL}/login" >/dev/null; sleep 2
"${AB}" fill "@$(ref_of 'textbox "Email address"')" "${AISTUDIO_ADMIN_EMAIL}" >/dev/null
# The password goes to agent-browser on stdin (batch), never as an argument.
PW_REF="@$(ref_of 'textbox "Password"')" PW="${AISTUDIO_ADMIN_PASSWORD}" python3 -c \
  'import json,os;print(json.dumps([["fill",os.environ["PW_REF"],os.environ["PW"]]]))' | "${AB}" batch >/dev/null
click_ref 'button "Log in"' 4
[[ "$("${AB}" get url)" == */admin* ]] || { echo "login failed; nothing captured" >&2; "${AB}" close >/dev/null 2>&1; exit 1; }

# LLM providers, filtered to the bundle's two (the product seeds two quick-start
# LLMs that would otherwise appear too).
expand "LLM management" 'link "LLM providers"'
click_ref 'link "LLM providers"'
search="$(ref_of 'textbox "Search LLM providers')"
[[ -n "${search}" ]] && { "${AB}" fill "@${search}" "__DISPLAY_NAME__" >/dev/null; sleep 2; }
save aistudio-llms.png /admin/llms "${AISTUDIO_LLM_A_NAME}" "${AISTUDIO_LLM_B_NAME}"

# Model router detail
click_ref 'link "Model Routers"'
"${AB}" find text "${AISTUDIO_ROUTER_NAME}" click >/dev/null 2>&1; sleep 3
FULL=1 save aistudio-model-router.png "/admin/model-routers/${AISTUDIO_ROUTER_ID}" "Model Pools (2)" "${AISTUDIO_APP_NAME}"

# Failover section of the first LLM (edit form, never saved)
click_ref 'link "LLM providers"'
"${AB}" find text "${AISTUDIO_LLM_A_NAME}" click >/dev/null 2>&1; sleep 3
click_ref 'button "Edit LLM"' 3
click_ref 'button "Failover Triggers (advanced)"' 1
scroll_to_heading "Failover" 170
save aistudio-failover.png "/admin/llms/edit/${AISTUDIO_LLM_A_ID}" "Failover Triggers" "${AISTUDIO_LLM_B_NAME}"

# Apps (AI Portal section of the admin sidebar)
click_ref 'link "Back to LLM providers"' 2 || true
expand "AI Portal" 'link "Edge Gateways"'
click_ref 'link "Apps"'
save aistudio-apps.png /admin/apps "${AISTUDIO_APP_NAME}" "${AISTUDIO_BUDGET_APP_NAME}"

# Guardrail filter detail
expand "Context management" 'link "Filters"'
click_ref 'link "Filters"'
"${AB}" find text "${AISTUDIO_GUARDRAIL_NAME}" click >/dev/null 2>&1; sleep 3
FULL=1 save aistudio-guardrail.png "/admin/filters/${AISTUDIO_GUARDRAIL_ID}" "Guardrail" "${AISTUDIO_LLM_A_NAME}"

# Tyk MCP servers (only meaningful after proofs/tyk-mcp-import.sh)
if [[ "$(jq -r '.status // empty' evidence/bodies/tyk-mcp-status.json 2>/dev/null)" == passed ]]; then
  click_ref 'link "MCP servers"'
  save aistudio-tyk-mcp.png /admin/mcp-servers "__DISPLAY_NAME__ Tyk Dashboard"
fi

# Audit trail: search "Create", expand the first LLM's create row
expand "Governance" 'link "Audit trail"'
click_ref 'link "Audit trail"' 4
search="$(ref_of 'textbox "Search"')"
[[ -n "${search}" ]] && "${AB}" fill "@${search}" "Create" >/dev/null
click_ref 'button "Apply filters"' 3
row="$("${AB}" snapshot -i -c 2>/dev/null | grep -F -A2 "Create LLMllm${AISTUDIO_LLM_A_NAME}" | grep 'button "expand"' | head -1 | grep -o 'ref=e[0-9]*' | cut -d= -f2)"
if [[ -n "${row}" ]]; then "${AB}" scrollintoview "@${row}" >/dev/null; "${AB}" click "@${row}" >/dev/null; sleep 2; fi
"${AB}" eval '(()=>{window.scrollTo(0,0); return 1})()' >/dev/null
FULL=1 save aistudio-audit-trail.png /admin/audit "Create Model Router" "Create Filter"

# AI Portal App page, Main Ingress card
click_ref 'tab "AI Portal"' 4
click_ref 'link "Apps"' 3
"${AB}" find text "${AISTUDIO_APP_NAME}" click >/dev/null 2>&1; sleep 4
"${AB}" set viewport 1440 1300 >/dev/null; sleep 1
scroll_to_heading "LLM Access Details" 80
save aistudio-portal-app.png "/portal/apps/${AISTUDIO_APP_ID}" "Main Ingress" "${AISTUDIO_LLM_A_SLUG}/${AISTUDIO_POOL_A_TARGET_MODEL}" "${AISTUDIO_ROUTER_SLUG}"
"${AB}" set viewport 1440 900 >/dev/null
"${AB}" close >/dev/null 2>&1

echo "screenshots saved=${saved} refused=${refused} (evidence/screenshots/)"
[[ "${refused}" == 0 ]]
