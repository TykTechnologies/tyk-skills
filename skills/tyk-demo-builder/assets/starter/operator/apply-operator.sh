#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=operator-lib.sh
source "${ROOT_DIR}/operator/operator-lib.sh"
EVIDENCE="${ROOT_DIR}/evidence/operator-reconciliation.json"
# Written before any check that can exit (including a forgotten `source operator/operator-env.sh`),
# so every early exit leaves OPERATOR-001 failed until verify-reconciliation.sh records the outcome.
write_started_marker "${EVIDENCE}" OPERATOR-001
NAMESPACE="${TYK_OPERATOR_NAMESPACE:-default}"
[[ "${NAMESPACE}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || { echo "Invalid Kubernetes namespace: ${NAMESPACE}" >&2; exit 1; }
RENDER_DIR="${ROOT_DIR}/.runtime/operator"

: "${TYK_OPERATOR_AUTH:?Set TYK_OPERATOR_AUTH (source operator/operator-env.sh)}"
: "${TYK_OPERATOR_ORG:?Set TYK_OPERATOR_ORG (source operator/operator-env.sh)}"
: "${TYK_OPERATOR_URL:?Set TYK_OPERATOR_URL (source operator/operator-env.sh)}"
: "${TYK_USER_OWNER_ID:?Set TYK_USER_OWNER_ID (source operator/operator-env.sh)}"
: "${TYK_TEAM_OWNER_ID:?Set TYK_TEAM_OWNER_ID (source operator/operator-env.sh)}"


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
kc get crd tykoasapidefinitions.tyk.tyk.io securitypolicies.tyk.tyk.io operatorcontexts.tyk.tyk.io >/dev/null \
  || fail "Tyk Operator CRDs are missing in context ${KUBE_CONTEXT}; run ./operator/install-operator-kind.sh"
mkdir -p "${RENDER_DIR}"
chmod 700 "${ROOT_DIR}/.runtime" "${RENDER_DIR}"
for source in "${ROOT_DIR}"/operator/*.yaml; do
  target="${RENDER_DIR}/$(basename "${source}")"
  sed -e "s/namespace: default/namespace: ${NAMESPACE}/g" \
    -e "s/\.default\.svc\.cluster\.local/.${NAMESPACE}.svc.cluster.local/g" \
    "${source}" >"${target}"
  chmod 600 "${target}"
done

# Digest the desired content of every Operator-managed resource this apply submits, so
# verify-reconciliation.sh can tell which ones changed since the last successful verification
# and wait for the Operator to reconcile exactly those (see operator-lib.sh). Each entry names
# the status JSON and the hash fields that move when that content is reconciled; the
# ConfigMap's data is tracked through latestConfigMapHash on the API that references it.
api_digest="$(desired_digest "${RENDER_DIR}/tyk-oas-api.yaml" TykOasApiDefinition __SLUG__-operator-api spec)" \
  || fail "could not digest the rendered TykOasApiDefinition"
oas_digest="$(desired_digest "${RENDER_DIR}/tyk-oas-api.yaml" ConfigMap __SLUG__-operator-oas data)" \
  || fail "could not digest the rendered OAS ConfigMap"
policy_digest="$(desired_digest "${RENDER_DIR}/security-policy.yaml" SecurityPolicy __SLUG__-operator-policy spec)" \
  || fail "could not digest the rendered SecurityPolicy"
pending_tmp="$(mktemp "${DIGESTS_PENDING}.XXXXXX")"
chmod 600 "${pending_tmp}"
jq -n --arg api "${api_digest}" --arg oas "${oas_digest}" --arg policy "${policy_digest}" '{resources: {
  "tykoasapidefinition/__SLUG__-operator-api": {digest: $api, source: "api", fields: ["latestCRDSpecHash"]},
  "configmap/__SLUG__-operator-oas": {digest: $oas, source: "api", fields: ["latestConfigMapHash"]},
  "securitypolicy/__SLUG__-operator-policy": {digest: $policy, source: "policy", fields: ["latestCRDSpecHash"]}
}}' >"${pending_tmp}"
mv "${pending_tmp}" "${DIGESTS_PENDING}"

kc -n "${NAMESPACE}" create secret generic __SLUG__-operator-connection \
  --from-literal=TYK_AUTH="${TYK_OPERATOR_AUTH}" \
  --from-literal=TYK_ORG="${TYK_OPERATOR_ORG}" \
  --from-literal=TYK_URL="${TYK_OPERATOR_URL}" \
  --from-literal=TYK_MODE=pro \
  --from-literal=TYK_TLS_INSECURE_SKIP_VERIFY=false \
  --from-literal=TYK_USER_OWNERS="${TYK_USER_OWNER_ID}" \
  --from-literal=TYK_USER_GROUP_OWNERS="${TYK_TEAM_OWNER_ID}" \
  --dry-run=client -o yaml | kc apply -f - >/dev/null

kc apply -f "${RENDER_DIR}/upstream.yaml" >/dev/null
kc apply -f "${RENDER_DIR}/operator-context.yaml" >/dev/null
kc apply -f "${RENDER_DIR}/tyk-oas-api.yaml" >/dev/null
kc apply -f "${RENDER_DIR}/security-policy.yaml" >/dev/null

"${ROOT_DIR}/operator/verify-reconciliation.sh"
