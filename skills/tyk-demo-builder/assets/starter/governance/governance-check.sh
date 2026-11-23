#!/usr/bin/env bash
set -euo pipefail

# Sandbox compliance gate for a raw OAS document. Nothing is persisted in the Dashboard.
#
# Exit codes:
#   0  compliant: no error and no warning findings
#   1  warnings only
#   2  non-compliant: at least one error-severity finding
#   3  could not evaluate: usage error, missing spec, missing binary or environment value,
#      HTTP failure, unparsable body, `.error` non-null, `truncated: true`, zero matched
#      rulesets, or zero gating rulesets
#
# The sandbox answers HTTP 200 even for a non-compliant document, so the gate reads the
# body, never the status line.
#
# By default EVERY matched ruleset counts, which is what the Dashboard does: an API is
# non-compliant when any matched ruleset reports an error, and `action` is only the
# Deployment Warning toggle. `--honor-action` skips `action: none` rulesets to match the
# public docs' reference script; that mode can pass a spec the Dashboard marks
# non-compliant, so it is opt-in rather than the default.

EXIT_COMPLIANT=0
EXIT_WARNINGS=1
EXIT_NON_COMPLIANT=2
EXIT_CANNOT_EVALUATE=3

usage() {
  local code="${1:-${EXIT_CANNOT_EVALUATE}}"
  # An explicit --help is a successful request for the text, so it goes to stdout; every
  # other path here is an error and belongs on stderr.
  if [[ "${code}" -eq 0 ]]; then
    usage_text
    exit 0
  fi
  usage_text >&2
  exit "${code}"
}

usage_text() {
  cat <<'USAGE'
Usage: governance-check.sh <spec-file> (--categories a,b | --ruleset-ids a,b)
                           [--api-id <id>] [--api-name <name>]
                           [--strictness strict|standard|permissive]
                           [--honor-action]

Options:
  --categories      Up to 20 comma-separated API categories to evaluate against.
  --ruleset-ids     Up to 20 comma-separated ruleset IDs to evaluate against.
  --api-id          Identifier reported back in the evaluation, for traceability.
  --api-name        Display name reported back in the evaluation.
  --strictness      Client-side mapping of the result onto the exit status. It is not sent
                    to the Dashboard. strict (default) reports the raw contract, standard
                    treats warnings as a pass, permissive passes anything that evaluated.
  --honor-action    Ignore rulesets whose action is "none", matching the public docs'
                    reference script. Off by default because the Dashboard ignores action
                    and decides compliance on severity alone.

Exit codes:
  0 compliant   1 warnings only   2 non-compliant   3 could not evaluate

Environment:
  TYK_DASHBOARD_URL    Dashboard base URL, for example https://dashboard.example.com
  TYK_DASHBOARD_TOKEN  Dashboard user token with at least the apis:read permission
USAGE
}

fail_usage() { usage "${EXIT_CANNOT_EVALUATE}"; }

spec=""
categories=""
ruleset_ids=""
api_id=""
api_name=""
strictness="strict"
honor_action=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --categories) [[ $# -ge 2 ]] || fail_usage; categories="$2"; shift 2 ;;
    --ruleset-ids) [[ $# -ge 2 ]] || fail_usage; ruleset_ids="$2"; shift 2 ;;
    --api-id) [[ $# -ge 2 ]] || fail_usage; api_id="$2"; shift 2 ;;
    --api-name) [[ $# -ge 2 ]] || fail_usage; api_name="$2"; shift 2 ;;
    --strictness) [[ $# -ge 2 ]] || fail_usage; strictness="$2"; shift 2 ;;
    --honor-action) honor_action=true; shift ;;
    -h|--help) usage 0 ;;
    -*) echo "Unknown option: $1" >&2; fail_usage ;;
    *) [[ -z "${spec}" ]] || fail_usage; spec="$1"; shift ;;
  esac
done

[[ -n "${spec}" ]] || fail_usage
[[ -f "${spec}" ]] || { echo "Spec file not found: ${spec}" >&2; exit "${EXIT_CANNOT_EVALUATE}"; }
[[ -n "${categories}" || -n "${ruleset_ids}" ]] || fail_usage
[[ -z "${categories}" || -z "${ruleset_ids}" ]] || { echo "Use --categories or --ruleset-ids, not both." >&2; exit "${EXIT_CANNOT_EVALUATE}"; }
if [[ ! "${strictness}" =~ ^(strict|standard|permissive)$ ]]; then
  echo "Unknown strictness: ${strictness}" >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi
for binary in curl jq; do
  command -v "${binary}" >/dev/null || { echo "Required binary not found: ${binary}" >&2; exit "${EXIT_CANNOT_EVALUATE}"; }
done
[[ -n "${TYK_DASHBOARD_URL:-}" ]] || { echo "Set TYK_DASHBOARD_URL." >&2; exit "${EXIT_CANNOT_EVALUATE}"; }
[[ -n "${TYK_DASHBOARD_TOKEN:-}" ]] || { echo "Set TYK_DASHBOARD_TOKEN." >&2; exit "${EXIT_CANNOT_EVALUATE}"; }

# The sandbox rejects more than 20 categories or ruleset IDs with a 400. Fail early with a
# message that names the limit rather than surfacing an opaque Dashboard error.
selector_count() { awk -F, '{print NF}' <<<"$1"; }
if [[ -n "${categories}" ]] && (( $(selector_count "${categories}") > 20 )); then
  echo "At most 20 categories are accepted per evaluation." >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi
if [[ -n "${ruleset_ids}" ]] && (( $(selector_count "${ruleset_ids}") > 20 )); then
  echo "At most 20 ruleset IDs are accepted per evaluation." >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi

urlencode() { printf '%s' "$1" | jq -sRr @uri; }

# Strictness is a local decision about what counts as a CI failure. It is deliberately not
# appended to the query: the sandbox contract has no strictness parameter, and sending one
# would be silently ignored while implying server-side behaviour that does not exist.
query=""
[[ -z "${categories}" ]] || query="categories=$(urlencode "${categories}")"
[[ -z "${ruleset_ids}" ]] || query="ruleset_ids=$(urlencode "${ruleset_ids}")"
[[ -z "${api_id}" ]] || query="${query}&api_id=$(urlencode "${api_id}")"
[[ -z "${api_name}" ]] || query="${query}&api_name=$(urlencode "${api_name}")"

content_type='application/json'
case "$(printf '%s' "${spec}" | tr '[:upper:]' '[:lower:]')" in
  *.yaml|*.yml) content_type='application/yaml' ;;
esac

body_file="$(mktemp)"
trap 'rm -f "${body_file}"' EXIT

# The bundle's own scripts authenticate with a bare `authorization: <token>` header. The
# Dashboard accepts `Bearer <token>` as well, so either works against a real stack.
status="$(curl -sS -o "${body_file}" -w '%{http_code}' -X POST \
  "${TYK_DASHBOARD_URL%/}/api/evaluations/sandbox/oas?${query}" \
  -H "authorization: ${TYK_DASHBOARD_TOKEN}" \
  -H "Content-Type: ${content_type}" \
  --data-binary "@${spec}")"

if [[ "${status}" == 403 ]]; then
  echo "Dashboard returned 403: the licence scope does not include the governance claim." >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi
if [[ ! "${status}" =~ ^2 ]]; then
  echo "Sandbox evaluation failed: HTTP ${status}" >&2
  cat "${body_file}" >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi
if ! jq -e . "${body_file}" >/dev/null 2>&1; then
  echo "Sandbox evaluation returned a body that is not JSON." >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi
if jq -e '.error != null' "${body_file}" >/dev/null; then
  echo "Sandbox evaluation reported an error: $(jq -r '.error' "${body_file}")" >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi
# A truncated response hides findings, so a pass derived from it would be a guess.
if jq -e '.truncated == true' "${body_file}" >/dev/null; then
  echo "Sandbox evaluation was truncated; the result cannot be trusted as a gate." >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi
# An unknown or misspelled category returns HTTP 200 with zero rulesets and zero issues,
# which a naive gate reads as "compliant" forever. Matching nothing is a configuration
# failure, never a pass.
if jq -e '(.rulesets | length) == 0' "${body_file}" >/dev/null; then
  echo "No ruleset matched the requested categories or ruleset IDs." >&2
  echo "Check the spelling; an unknown category evaluates nothing and would pass silently." >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi

if ${honor_action}; then
  gating_filter='select(.action != "none")'
  echo "Gating mode: --honor-action (rulesets with action \"none\" are ignored; docs parity)."
else
  gating_filter='select(true)'
  echo "Gating mode: all matched rulesets (Dashboard parity: severity decides, not action)."
fi

errors="$(jq "[.rulesets[] | ${gating_filter} | .error_count] | add // 0" "${body_file}")"
warnings="$(jq "[.rulesets[] | ${gating_filter} | .warn_count] | add // 0" "${body_file}")"
gating="$(jq "[.rulesets[] | ${gating_filter}] | length" "${body_file}")"

jq -r '"Evaluated \(.evaluated_apis // 1) spec(s) in \(.elapsed_ms // 0)ms against \(.rulesets | length) ruleset(s):",
  (.rulesets[] | "  \(.name) action=\(.action) errors=\(.error_count) warns=\(.warn_count)")' "${body_file}"
jq -r '.issues[]? | "  [\(.severity)] \(.rule_id // .code // "rule"): \(.message) (\(.path // ""))"' "${body_file}"

# Only reachable with --honor-action: every matched ruleset was advisory, so nothing was
# actually checked. That is a configuration failure, not a pass.
if [[ "${gating}" -eq 0 ]]; then
  echo "Every matched ruleset is advisory (action: none), so nothing gated this specification." >&2
  echo "Drop --honor-action to gate on severity the way the Dashboard does." >&2
  exit "${EXIT_CANNOT_EVALUATE}"
fi

if [[ "${errors}" -gt 0 ]]; then
  echo "Non-compliant: ${errors} error-severity finding(s)." >&2
  result="${EXIT_NON_COMPLIANT}"
elif [[ "${warnings}" -gt 0 ]]; then
  echo "Compliant with ${warnings} warning-severity finding(s)."
  result="${EXIT_WARNINGS}"
else
  echo "Compliant: no error or warning findings."
  result="${EXIT_COMPLIANT}"
fi

case "${strictness}" in
  permissive)
    # Anything that evaluated is a pass; only a failure to evaluate stops the pipeline.
    [[ "${result}" -eq "${EXIT_CANNOT_EVALUATE}" ]] || result="${EXIT_COMPLIANT}"
    ;;
  standard)
    # Warnings are advisory; only error-severity findings fail.
    [[ "${result}" -ne "${EXIT_WARNINGS}" ]] || result="${EXIT_COMPLIANT}"
    ;;
  strict) ;;
esac

exit "${result}"
