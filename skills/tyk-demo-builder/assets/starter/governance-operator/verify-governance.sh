#!/usr/bin/env bash
# Proves the Operator-side governance hard block (GOV-003) against a live cluster:
#   1. a compliant governed TykOasApiDefinition is admitted and synced to the Dashboard, which
#      also creates the __SLUG__-gitops category the ruleset needs,
#   2. the block TykRuleset is synced to the Dashboard,
#   3. the compliant API's preflight passes against that ruleset,
#   4. a non-compliant one is denied by the admission webhook and never saved.
# Run after ./operator/apply-operator.sh, which renders every operator/*.yaml into
# .runtime/operator with the target namespace substituted. Writes
# evidence/operator-governance.json on every outcome; scripts/verify-demo.sh reads it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=operator-lib.sh
source "${ROOT_DIR}/operator/operator-lib.sh"
NAMESPACE="${TYK_OPERATOR_NAMESPACE:-default}"
TIMEOUT_SECONDS="${TYK_OPERATOR_VERIFY_TIMEOUT:-120}"
INTERVAL_SECONDS="${TYK_OPERATOR_VERIFY_INTERVAL:-3}"
RENDER_DIR="${ROOT_DIR}/.runtime/operator"
EVIDENCE="${ROOT_DIR}/evidence/operator-governance.json"
RULESET=__SLUG__-governed-standards
GOOD=__SLUG__-governed-block-good
BAD=__SLUG__-governed-block-bad
EXPECTED_RULE=operation-operationid-required

# From here on any exit, including a bare set -e one, leaves GOV-003 failed.
write_started_marker "${EVIDENCE}" GOV-003

fail() {
  if command -v jq >/dev/null; then
    jq -n --arg reason "$1" --arg org "$(operator_org_id)" --arg at "$(utc_now)" \
      '{proof:"GOV-003",status:"failed",reason:$reason,org_id:$org,recorded_at:$at}' >"${EVIDENCE}"
  fi
  echo "GOV-003 failed: $1" >&2
  exit 1
}

command -v kubectl >/dev/null || fail "kubectl is required"
command -v jq >/dev/null || fail "jq is required"
for file in tyk-ruleset.yaml governed-api-compliant.yaml governed-api-noncompliant.yaml; do
  [[ -f "${RENDER_DIR}/${file}" ]] || fail "missing ${RENDER_DIR}/${file}; run ./operator/apply-operator.sh first"
done
resolve_kube_context || fail "no usable Kubernetes context"
kc get crd tykrulesets.tyk.tyk.io >/dev/null 2>&1 \
  || fail "TykRuleset CRD not found: install tyk-operator v1.5.0 or later"

# wait_for <description> <kind> <name> <check...>
# On timeout the reason carries the resource's last reconcile error, when it has one.
wait_for() {
  local description="$1" kind="$2" name="$3" started_at error
  shift 3
  started_at="$(date +%s)"
  until "$@"; do
    if (( $(date +%s) - started_at >= TIMEOUT_SECONDS )); then
      error="$(kc -n "${NAMESPACE}" get "${kind}" "${name}" -o jsonpath='{.status.latestTransaction.error}' 2>/dev/null || true)"
      fail "timed out waiting for ${description}${error:+ (last error: ${error})}"
    fi
    sleep "${INTERVAL_SECONDS}"
  done
}

good_synced() {
  local status
  status="$(kc -n "${NAMESPACE}" get tykoasapidefinition "${GOOD}" -o json 2>/dev/null)" || return 1
  jq -e '(.status.id // "") != "" and .status.latestTransaction.status == "Successful"' <<<"${status}" >/dev/null
}

ruleset_state() {
  local status
  status="$(kc -n "${NAMESPACE}" get tykruleset "${RULESET}" -o json 2>/dev/null)" || return 1
  jq -cn --argjson ruleset "${status}" '{ruleset: $ruleset}'
}

ruleset_synced() {
  local state
  state="$(ruleset_state)" || return 1
  # Admission is judged against the ruleset content the Dashboard last received, so when the
  # rendered ruleset differs from the one last verified, wait for the Operator to record the
  # change; the previous successful sync would otherwise satisfy the check while the Dashboard
  # still holds old rules. The Operator hashes the metadata (latestCRDSpecHash) and the rule
  # body (latestSourceHash) separately, and a rule-body edit moves only the second, so either
  # one counts. An identical re-apply needs no hash move: generation can bump on a no-op apply
  # while no hash moves (operator-lib.sh), so it is not used as the change signal.
  jq -e '.ruleset
    | (.status.rulesetId // "") != ""
      and .status.latestTransaction.status == "Successful"
      and (.status.latestCRDSpecHash // "") != ""' <<<"${state}" >/dev/null \
    && digests_fresh "${ruleset_pending}" "${state}"
}

preflight_status() {
  kc -n "${NAMESPACE}" get tykoasapidefinition "$1" -o jsonpath='{.status.governancePreflight.status}' 2>/dev/null || true
}

good_settled() {
  [[ "$(preflight_status "${GOOD}")" == passed ]]
}

# Start from a known state. A stale non-compliant object (for example one applied before the
# ruleset existed) would turn the create test into an update test, and a compliant twin left
# by an earlier run would carry that run's preflight verdict into this one.
kc -n "${NAMESPACE}" delete tykoasapidefinition "${BAD}" --ignore-not-found --wait=true >/dev/null
kc -n "${NAMESPACE}" delete configmap "${BAD}-oas" --ignore-not-found >/dev/null
kc -n "${NAMESPACE}" delete tykoasapidefinition "${GOOD}" --ignore-not-found --wait=true >/dev/null

# The compliant twin goes first: the Dashboard rejects a ruleset whose category no API carries,
# and this API is what brings the __SLUG__-gitops category into the organisation.
good_output="$(kc apply -f "${RENDER_DIR}/governed-api-compliant.yaml" 2>&1)" \
  || fail "the compliant governed API was rejected at admission: ${good_output}"
wait_for "the compliant API ${GOOD} to sync to the Dashboard" tykoasapidefinition "${GOOD}" good_synced

ruleset_digest="$(desired_digest "${RENDER_DIR}/tyk-ruleset.yaml" TykRuleset "${RULESET}" spec)" \
  || fail "could not digest the rendered TykRuleset"
ruleset_pending="$(jq -cn --arg digest "${ruleset_digest}" --arg key "tykruleset/${RULESET}" \
  '{resources: {($key): {digest: $digest, source: "ruleset", fields: ["latestCRDSpecHash", "latestSourceHash"]}}}')"
ruleset_output="$(kc apply -f "${RENDER_DIR}/tyk-ruleset.yaml" 2>&1)" \
  || fail "the TykRuleset was rejected: ${ruleset_output}"
wait_for "TykRuleset ${RULESET} to sync to the Dashboard" tykruleset "${RULESET}" ruleset_synced
ruleset_id="$(kc -n "${NAMESPACE}" get tykruleset "${RULESET}" -o jsonpath='{.status.rulesetId}')"

# The Operator requeues governed APIs when a TykRuleset changes, so the compliant twin's
# preflight is re-evaluated against the ruleset that now exists.
wait_for "the compliant API preflight to pass" tykoasapidefinition "${GOOD}" good_settled

deny_output="$(kc apply -f "${RENDER_DIR}/governed-api-noncompliant.yaml" 2>&1)" && \
  fail "the non-compliant governed API was admitted; check that the webhook is enabled and reaches the Dashboard"
grep -q "governance preflight blocked" <<<"${deny_output}" \
  || fail "the apply failed for a reason other than the governance preflight: ${deny_output}"
if kc -n "${NAMESPACE}" get tykoasapidefinition "${BAD}" >/dev/null 2>&1; then
  fail "the non-compliant TykOasApiDefinition exists despite the denial"
fi
# The multi-document apply creates the ConfigMap before the webhook denies the API; remove it
# so re-runs stay idempotent.
kc -n "${NAMESPACE}" delete configmap "${BAD}-oas" --ignore-not-found >/dev/null

denial="$(grep -m1 "governance preflight blocked" <<<"${deny_output}" | sed 's/^.*denied the request: //')"
# grep exits 1 on zero matches, which pipefail would turn into a silent abort; an empty list
# is a real outcome here and is judged just below.
violations="$(sed -n '/governance preflight blocked/,/^Fix the violations/p' <<<"${deny_output}" \
  | { grep '^  - ' || true; } | sed 's/^  - //' | jq -R . | jq -sc .)"
jq -e --arg rule "${EXPECTED_RULE}" 'any(.[]; startswith($rule))' <<<"${violations}" >/dev/null \
  || fail "the denial did not name ${EXPECTED_RULE}; violations=${violations}; output: ${deny_output}"

# The ruleset content becomes the verified baseline only once the whole proof has passed.
ruleset_final_state="$(ruleset_state)" || fail "could not read TykRuleset ${RULESET} after the proof"
digests_record "${ruleset_pending}" "${ruleset_final_state}"

jq -n \
  --arg ruleset "${RULESET}" --arg ruleset_id "${ruleset_id}" \
  --arg good "${GOOD}" --arg bad "${BAD}" --arg denial "${denial}" --argjson violations "${violations}" \
  --arg org "$(operator_org_id)" --arg at "$(utc_now)" --arg context "${KUBE_CONTEXT}" \
  '{proof:"GOV-003",status:"passed",
    detail:("Block TykRuleset synced; " + $good + " admitted; " + $bad + " denied at admission and not saved"),
    org_id:$org,recorded_at:$at,kube_context:$context,
    ruleset:{resource:$ruleset,id:$ruleset_id,action:"block"},
    admitted:{resource:$good,preflight:"passed"},denied:{resource:$bad,message:$denial,violations:$violations,saved:false}}' \
  | tee "${EVIDENCE}"
