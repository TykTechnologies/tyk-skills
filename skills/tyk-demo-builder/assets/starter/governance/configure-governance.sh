#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/common.sh"

[[ -n "${DASHBOARD_API_KEY:-}" ]] || {
  echo "Run scripts/bootstrap-demo-stack.sh before configuring governance." >&2
  exit 1
}

CATEGORY="__SLUG__-payments"
OWASP_RULESET_NAME="__DISPLAY_NAME__ OWASP Baseline"
STANDARDS_RULESET_NAME="__DISPLAY_NAME__ Gateway Standards"
RETRY_ATTEMPTS="${GOVERNANCE_RETRY_ATTEMPTS:-10}"
RETRY_DELAY="${GOVERNANCE_RETRY_DELAY:-2}"

mkdir -p "${RUNTIME_DIR}"
chmod 700 "${RUNTIME_DIR}"
response_file="$(mktemp "${RUNTIME_DIR}/governance.XXXXXX")"
trap 'rm -f "${response_file}"' EXIT

last_status=""

# Governance is an Enterprise capability gated by the `governance` claim in the Dashboard
# license scope. Without the claim every governance endpoint answers 403, which is a
# licensing fact rather than a broken demo: record it so verification reports `blocked`
# instead of `failed`, and leave the rest of the bundle usable.
governance_unlicensed() {
  echo "Dashboard returned 403 for ${1} on every one of ${RETRY_ATTEMPTS} attempts." >&2
  echo "API Governance requires the 'governance' claim in the Dashboard license scope." >&2
  echo "It is disabled by default and enabled per organisation on request to Tyk, not self-service." >&2
  echo "Once a licence carrying the claim is installed, re-run ./scripts/configure-demo.sh." >&2
  runtime_set GOVERNANCE_LICENSED false
  exit 0
}

# common.sh documents that Dashboard endpoints can answer 403 briefly right after bootstrap
# while the org and credentials propagate. A single 403 is therefore not proof that the
# licence lacks the governance claim, so every call retries before that conclusion is drawn.
request_with_retry() {
  local method="$1" url="$2" payload="${3:-}" attempt
  for attempt in $(seq 1 "${RETRY_ATTEMPTS}"); do
    if [[ -n "${payload}" ]]; then
      last_status="$(curl -sS -o "${response_file}" -w '%{http_code}' -X "${method}" "${url}" \
        -H "authorization: ${DASHBOARD_API_KEY}" \
        -H 'Content-Type: application/json' \
        --data "${payload}")"
    else
      last_status="$(curl -sS -o "${response_file}" -w '%{http_code}' -X "${method}" "${url}" \
        -H "authorization: ${DASHBOARD_API_KEY}")"
    fi
    if [[ "${last_status}" != 403 ]]; then
      return 0
    fi
    if [[ "${attempt}" == "${RETRY_ATTEMPTS}" ]]; then
      return 0
    fi
    sleep "${RETRY_DELAY}"
  done
}

# Rulesets are addressed by a generated ID, so idempotency means "find by name, then create
# or update". A second run of this script must not leave two rulesets behind.
#
# The lookup distinguishes four outcomes deliberately: found (echo the id, return 0),
# definitely not found (echo nothing, return 0), "could not list" (return 1), and "403
# persisted" (return 2). Treating a failed listing as "not found" would create a duplicate
# ruleset on every transient error.
#
# It runs inside a command substitution, so it must never call governance_unlicensed itself:
# an `exit` there would leave only the subshell and the caller would read an empty id and
# create a second ruleset. The verdict is carried out by the return code instead.
find_ruleset_id() {
  local name="$1" page=1 pages match listing
  while :; do
    request_with_retry GET "${DASHBOARD_URL}/api/rulesets?p=${page}"
    if [[ "${last_status}" == 403 ]]; then
      return 2
    fi
    if [[ ! "${last_status}" =~ ^2 ]]; then
      return 1
    fi
    listing="$(cat "${response_file}")"
    # The list shape is read defensively: indexing an array with a string is a hard jq
    # error, so the array case is decided before any object key is touched.
    match="$(jq -r --arg name "${name}" '
      [ ((if type == "array" then . else (.rulesets // .Data // .data // []) end)[]?) ]
      | map(select((.name // .Name) == $name))
      | (.[0].id // .[0].ID // .[0]._id // empty)' <<<"${listing}" 2>/dev/null || printf '')"
    if [[ -n "${match}" ]]; then
      printf '%s' "${match}"
      return 0
    fi
    pages="$(jq -r '(.pages // .Pages // 1)' <<<"${listing}" 2>/dev/null || printf '1')"
    [[ "${pages}" =~ ^[0-9]+$ ]] || pages=1
    if [[ "${page}" -ge "${pages}" ]]; then
      return 0
    fi
    page=$((page + 1))
  done
}

upsert_ruleset() {
  local name="$1" description="$2" action="$3" definition="$4" payload existing
  payload="$(jq -nc \
    --arg name "${name}" \
    --arg description "${description}" \
    --arg action "${action}" \
    --arg category "${CATEGORY}" \
    --arg definition "${definition}" \
    '{
      name: $name,
      description: $description,
      active: true,
      resource_type: "oas-api",
      action: $action,
      categories: [$category],
      definition: $definition,
      is_template: false
    }')"

  local lookup=0
  existing="$(find_ruleset_id "${name}")" || lookup=$?
  if [[ "${lookup}" -eq 2 ]]; then
    governance_unlicensed "GET /api/rulesets"
  fi
  if [[ "${lookup}" -ne 0 ]]; then
    echo "Could not list rulesets while looking for ${name}." >&2
    echo "Refusing to create a ruleset that may already exist." >&2
    exit 1
  fi

  if [[ -n "${existing}" ]]; then
    request_with_retry PUT "${DASHBOARD_URL}/api/rulesets/${existing}" "${payload}"
  else
    request_with_retry POST "${DASHBOARD_URL}/api/rulesets" "${payload}"
  fi

  if [[ "${last_status}" == 403 ]]; then
    governance_unlicensed "ruleset write for ${name}"
  fi
  if [[ ! "${last_status}" =~ ^2 ]]; then
    echo "Ruleset write failed for ${name}: HTTP ${last_status}" >&2
    cat "${response_file}" >&2
    exit 1
  fi
}

# A licence probe before any write, so an unlicensed org stops with one clear message
# instead of a partially configured organisation. retry_capture absorbs the transient 403s
# that common.sh documents; only a 403 that survives every attempt means the claim is absent.
if ! retry_capture "${RETRY_ATTEMPTS}" "${RETRY_DELAY}" \
  curl -fsS "${DASHBOARD_URL}/api/rulesets?is_template=true" \
  -H "authorization: ${DASHBOARD_API_KEY}" >/dev/null; then
  request_with_retry GET "${DASHBOARD_URL}/api/rulesets?is_template=true"
  if [[ "${last_status}" == 403 ]]; then
    governance_unlicensed "GET /api/rulesets?is_template=true"
  fi
  echo "Governance ruleset listing failed: HTTP ${last_status}" >&2
  cat "${response_file}" >&2
  exit 1
fi

# The probe succeeded, so the claim is present. Record that immediately, before any other
# work: the documented recovery path is "install a licence carrying the claim, then re-run
# configure-demo.sh", and an earlier unlicensed run leaves GOVERNANCE_LICENSED=false in the
# runtime env. Without clearing it here, a later success would still be read as a failure
# and verification would keep reporting GOV-001 blocked. The value is set again at the end
# once the rulesets exist.
runtime_set GOVERNANCE_LICENSED true

# Categories must exist in the organisation before a ruleset can reference them.
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assign-categories.sh"
# The child wrote any licence verdict to the shared runtime env file, but this shell read
# that file before the child ran, so the verdict has to be re-read rather than assumed.
if [[ -f "${RUNTIME_ENV}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${RUNTIME_ENV}"
  set +a
fi
if [[ "${GOVERNANCE_LICENSED:-}" == false ]]; then
  exit 0
fi

# The Dashboard injects the gateway's own server URL into the stored `servers` array on
# every save. On an http demo gateway that makes `owasp-security-hosts-https-oas3` fire an
# unfixable error on every API, so the baseline downgrades exactly that rule to `warn`.
# Remove the override when the demo gateway is fronted by https.
owasp_definition="$(jq -nc '{
  extends: [["spectral:owasp", "all"]],
  rules: {"owasp-security-hosts-https-oas3": "warn"}
}')"

# A custom ruleset proving that governance lints the Tyk extension, not just the OAS
# surface. Messages are literal text: the Vacuum engine does not interpolate Spectral
# templates such as {{description}}, so a template would reach the Dashboard issues table
# verbatim. `action: none` keeps it out of the Deployment Warning modal while it still
# decides compliance, because the Dashboard reads severity and never reads action.
# The JSONPath keys must be single-quoted, and a literal apostrophe cannot appear inside a
# single-quoted shell string: written inline it would close the jq program and silently
# produce the unquoted path $[x-tyk-api-gateway].upstream, which matches nothing. Passing it
# as a jq argument keeps the path exact.
quote="'"
standards_definition="$(jq -nc --arg q "${quote}" '{
  rules: {
    "upstream-rate-limit-required": {
      description: "Every governed API must declare an upstream rate limit.",
      message: "No upstream rate limit is declared under x-tyk-api-gateway.upstream.",
      severity: "error",
      given: ("$[" + $q + "x-tyk-api-gateway" + $q + "].upstream"),
      then: {field: "rateLimit", function: "truthy"},
      howToFix: "Add x-tyk-api-gateway.upstream.rateLimit with enabled, rate and per."
    },
    "authentication-required": {
      description: "Every governed API must enable gateway authentication.",
      message: "Gateway authentication is not enabled under x-tyk-api-gateway.server.",
      severity: "error",
      given: ("$[" + $q + "x-tyk-api-gateway" + $q + "].server"),
      then: {field: "authentication", function: "truthy"},
      howToFix: "Add x-tyk-api-gateway.server.authentication with enabled set to true and at least one security scheme."
    }
  }
}')"

upsert_ruleset "${OWASP_RULESET_NAME}" \
  "OWASP API Security baseline scoped to the ${CATEGORY} category." \
  warn \
  "${owasp_definition}"

upsert_ruleset "${STANDARDS_RULESET_NAME}" \
  "Custom gateway standards for the ${CATEGORY} category, linting the x-tyk-api-gateway extension." \
  none \
  "${standards_definition}"

runtime_set GOVERNANCE_LICENSED true
echo "Governance categories and rulesets configured for ${CATEGORY}."
