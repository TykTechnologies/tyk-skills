#!/usr/bin/env bash
# Proves OPERATOR-001: the TykOasApiDefinition and SecurityPolicy reconciled into the Dashboard,
# the policy linked to the API, and the owner/team metadata survived. Writes
# evidence/operator-reconciliation.json on every outcome; scripts/verify-demo.sh reads it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=operator-lib.sh
source "${ROOT_DIR}/operator/operator-lib.sh"
NAMESPACE="${TYK_OPERATOR_NAMESPACE:-default}"
TIMEOUT_SECONDS="${TYK_OPERATOR_VERIFY_TIMEOUT:-120}"
INTERVAL_SECONDS="${TYK_OPERATOR_VERIFY_INTERVAL:-3}"
EVIDENCE="${ROOT_DIR}/evidence/operator-reconciliation.json"
started_at="$(date +%s)"

write_started_marker "${EVIDENCE}" OPERATOR-001

# Every failure path writes a reason before exiting rather than leaving an earlier pass.
fail() {
  if command -v jq >/dev/null; then
    jq -n --arg reason "$1" --arg org "$(operator_org_id)" --arg at "$(utc_now)" \
      '{proof:"OPERATOR-001",status:"failed",reason:$reason,org_id:$org,recorded_at:$at}' >"${EVIDENCE}"
  fi
  echo "OPERATOR-001 failed: $1" >&2
  exit 1
}

command -v kubectl >/dev/null || fail "kubectl is required"
command -v jq >/dev/null || fail "jq is required"
resolve_kube_context || fail "no usable Kubernetes context"

# A resource that does not exist yet reads as an empty object, so the loop keeps waiting and
# a missing resource ends in the timeout evidence rather than a bare set -e exit.
read_status() {
  api_status="$(kc -n "${NAMESPACE}" get tykoasapidefinition __SLUG__-operator-api -o json 2>/dev/null)" || api_status='{}'
  policy_status="$(kc -n "${NAMESPACE}" get securitypolicy __SLUG__-operator-policy -o json 2>/dev/null)" || policy_status='{}'
}

# apply-operator.sh writes the desired-state digests of this apply. A resource whose digest
# differs from its last verified one must show a moved status hash before its Successful
# transaction counts; an unchanged resource needs only the ordinary convergence (see
# operator-lib.sh for why generation cannot be used). Run on its own, with no pending
# digests, there is nothing to compare.
pending_digests="$(read_json_file "${DIGESTS_PENDING}")"
digest_state() {
  jq -cn --argjson api "${api_status}" --argjson policy "${policy_status}" '{api: $api, policy: $policy}'
}

linked() {
  jq -e --arg namespace "${NAMESPACE}" \
    '.status.linked_apis // [] | any(.name == "__SLUG__-operator-api" and (.namespace // $namespace) == $namespace)' \
    <<<"${policy_status}" >/dev/null
}

while true; do
  read_status
  api_id="$(jq -r '.status.id // empty' <<<"${api_status}")"
  policy_id="$(jq -r '.status.pol_id // empty' <<<"${policy_status}")"
  api_tyk_hash="$(jq -r '.status.latestTykSpecHash // empty' <<<"${api_status}")"
  api_cr_hash="$(jq -r '.status.latestCRDSpecHash // empty' <<<"${api_status}")"
  policy_tyk_hash="$(jq -r '.status.latestTykSpecHash // empty' <<<"${policy_status}")"
  policy_cr_hash="$(jq -r '.status.latestCRDSpecHash // empty' <<<"${policy_status}")"
  api_txn="$(jq -r '.status.latestTransaction.status // empty' <<<"${api_status}")"
  policy_txn="$(jq -r '.status.latestTransaction.status // empty' <<<"${policy_status}")"

  # latestCRDSpecHash hashes the desired CR spec and latestTykSpecHash hashes the Dashboard
  # read-back, so the two are never equal. Convergence is: IDs assigned, both hashes recorded,
  # the last reconcile transaction Successful and fresh for both resources, and the policy
  # linked to the API. The link can land a reconcile after the policy itself, so it is waited
  # for here rather than failed on first sight.
  if [[ -n "${api_id}" && -n "${policy_id}" && \
        -n "${api_tyk_hash}" && -n "${api_cr_hash}" && "${api_txn}" == Successful && \
        -n "${policy_tyk_hash}" && -n "${policy_cr_hash}" && "${policy_txn}" == Successful ]] \
     && digests_fresh "${pending_digests}" "$(digest_state)" \
     && linked; then
    break
  fi

  if (( $(date +%s) - started_at >= TIMEOUT_SECONDS )); then
    kc -n "${NAMESPACE}" get tykoasapidefinition __SLUG__-operator-api -o yaml >&2 || true
    kc -n "${NAMESPACE}" get securitypolicy __SLUG__-operator-policy -o yaml >&2 || true
    api_error="$(jq -r '.status.latestTransaction.error // empty' <<<"${api_status}")"
    policy_error="$(jq -r '.status.latestTransaction.error // empty' <<<"${policy_status}")"
    link_state=linked
    linked || link_state="not linked"
    fresh_state="reconciled"
    digests_fresh "${pending_digests}" "$(digest_state)" \
      || fresh_state="a changed resource still shows the status hash of its previous content"
    fail "reconciliation did not converge within ${TIMEOUT_SECONDS}s (api transaction=${api_txn:-none}${api_error:+, api error: ${api_error}}; policy transaction=${policy_txn:-none}${policy_error:+, policy error: ${policy_error}}; policy ${link_state} to the API; ${fresh_state})"
  fi
  sleep "${INTERVAL_SECONDS}"
done

for resource in \
  "deployment/__SLUG__-operator-upstream" \
  "service/__SLUG__-operator-upstream" \
  "operatorcontext/__SLUG__-team-context" \
  "tykoasapidefinition/__SLUG__-operator-api" \
  "securitypolicy/__SLUG__-operator-policy"; do
  owner="$(kc -n "${NAMESPACE}" get "${resource}" -o jsonpath='{.metadata.labels.demo\.tyk\.io/owner}')" \
    || fail "could not read ${resource}"
  team="$(kc -n "${NAMESPACE}" get "${resource}" -o jsonpath='{.metadata.labels.demo\.tyk\.io/team}')" \
    || fail "could not read ${resource}"
  [[ "${owner}" == platform && "${team}" == api-platform ]] \
    || fail "owner/team metadata verification failed for ${resource}"
done

# Only now, with every check passed, does this content become the verified baseline; a failed
# or unconverged run leaves the previous baseline so the change is detected again next time.
digests_record "${pending_digests}" "$(digest_state)"

jq -n \
  --arg api_resource __SLUG__-operator-api \
  --arg api_id "${api_id}" \
  --arg policy_resource __SLUG__-operator-policy \
  --arg policy_id "${policy_id}" \
  --arg org "$(operator_org_id)" \
  --arg at "$(utc_now)" \
  --arg context "${KUBE_CONTEXT}" \
  '{proof:"OPERATOR-001",status:"passed",
    detail:"TykOasApiDefinition and SecurityPolicy reconciled to the Dashboard, policy linked, ownership intact",
    org_id:$org,recorded_at:$at,kube_context:$context,
    reconciled:true,api:{resource:$api_resource,id:$api_id},policy:{resource:$policy_resource,id:$policy_id},
    ownership:{owner:"platform",team:"api-platform"}}' \
  | tee "${EVIDENCE}"
