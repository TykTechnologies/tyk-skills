#!/usr/bin/env bash
# Opt-in installer for a DISPOSABLE kind cluster running tyk-operator against this bundle's
# Compose Dashboard. By default it creates or reuses kind cluster
# ${KIND_CLUSTER_NAME:-__SLUG__-operator} and addresses it as kind-<name> explicitly, never
# through whatever context happens to be current. ALLOW_NON_KIND_CONTEXT=true instead uses the
# current context as-is and does not need kind; use it only for a disposable cluster.
# The context used is recorded in .runtime/operator-kube-context, and apply-operator.sh and
# the verify scripts target it.
#
# The kind cluster's credentials go to a bundle-local kubeconfig, .runtime/operator-kubeconfig,
# never to ~/.kube/config: `kind create cluster` otherwise switches the user's current
# kubectl context to the new cluster and leaves it there. operator/operator-lib.sh points
# every later kubectl call at the same file. To use kubectl by hand against the cluster:
#   ./operator/kubectl.sh get pods -A
# which is KUBECONFIG=.runtime/operator-kubeconfig kubectl --context kind-<name> get pods -A
#
#   ./operator/install-operator-kind.sh
#   source operator/operator-env.sh
#   ./operator/apply-operator.sh
#
# Steps: create the kind cluster if missing; install cert-manager unless it is already
# there (the Operator webhooks need it for TLS); write the
# tyk-operator-conf Secret from the bundle runtime and TYK_LICENSE_KEY; install the published
# tyk-operator Helm chart pinned to OPERATOR_VERSION; and, when governance is selected,
# assert the TykRuleset CRD and the admission webhook exist.
#
# Requires kind, kubectl, helm, jq, a running and bootstrapped demo stack, and
# TYK_LICENSE_KEY in the private .env (a governance-scoped Dashboard licence validates as
# the Operator licence). No secret is printed.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/common.sh"

CLUSTER="${KIND_CLUSTER_NAME:-__SLUG__-operator}"
OPERATOR_NAMESPACE=tyk-operator-system
CERT_MANAGER_VERSION="${CERT_MANAGER_VERSION:-v1.15.3}"
HELM_REPO_URL=https://helm.tyk.io/public/helm/charts/

fail() {
  echo "$1" >&2
  exit 1
}

required=(kubectl helm jq)
[[ "${ALLOW_NON_KIND_CONTEXT:-false}" == true ]] || required+=(kind)
for binary in "${required[@]}"; do
  command -v "${binary}" >/dev/null || fail "${binary} is required."
done

# Fail on every precondition before touching any cluster.
[[ -n "${OPERATOR_VERSION:-}" ]] || fail "OPERATOR_VERSION is not set in .env."
if [[ -z "${OPERATOR_CHART_VERSION:-}" ]]; then
  if [[ "${OPERATOR_VERSION}" == *-rc* ]]; then
    fail "OPERATOR_VERSION=${OPERATOR_VERSION} is a release candidate, and there is no published tyk-operator Helm chart for rc tags (chart versions track GA app versions). Use a released OPERATOR_VERSION such as v1.6.0, or set OPERATOR_CHART_VERSION to a published chart and keep the rc image tag."
  fi
  OPERATOR_CHART_VERSION="${OPERATOR_VERSION#v}"
fi
[[ -n "${TYK_LICENSE_KEY:-}" ]] || fail "TYK_LICENSE_KEY is empty in .env; the Operator needs it as TYK_OPERATOR_LICENSEKEY."
if [[ -z "${DASHBOARD_API_KEY:-}" || -z "${ORG_ID:-}" ]]; then
  fail "No Dashboard bootstrap state in .runtime/runtime.env. Run ./scripts/start-demo-stack.sh and ./scripts/bootstrap-demo-stack.sh first."
fi
operator_url="${TYK_OPERATOR_URL:-http://host.docker.internal:${DASHBOARD_PORT}}"

mkdir -p "${RUNTIME_DIR}"
chmod 700 "${RUNTIME_DIR}"
kind_kubeconfig="${RUNTIME_DIR}/operator-kubeconfig"

# `grep -Fx ... >/dev/null`, never `grep -q`, after a pipe: under pipefail, grep -q exits on
# the first match, the writer then dies of SIGPIPE, and the pipeline reports failure for a
# context that exists (seen with a kubeconfig holding many contexts).
if [[ "${ALLOW_NON_KIND_CONTEXT:-false}" == true ]]; then
  context="$(kubectl config current-context 2>/dev/null)" || fail "No kubectl context is selected."
  [[ -n "${context}" ]] || fail "No kubectl context is selected."
  # The current context lives in the user's own kubeconfig, so a kind kubeconfig recorded by
  # an earlier run must not shadow it.
  rm -f "${kind_kubeconfig}"
  echo "ALLOW_NON_KIND_CONTEXT=true: using the current context ${context} without creating a kind cluster."
else
  if ! kind get clusters 2>/dev/null | grep -Fx "${CLUSTER}" >/dev/null; then
    echo "Creating kind cluster ${CLUSTER} (credentials in .runtime/operator-kubeconfig; your current kubectl context is unchanged)."
    kind create cluster --name "${CLUSTER}" --wait 120s --kubeconfig "${kind_kubeconfig}"
  elif [[ ! -s "${kind_kubeconfig}" ]]; then
    # A cluster created by an earlier installer keeps its credentials in the default
    # kubeconfig; copy them here. When that fails, the context check below stops the run.
    kind export kubeconfig --name "${CLUSTER}" --kubeconfig "${kind_kubeconfig}" >/dev/null 2>&1 \
      || echo "kind export kubeconfig --name ${CLUSTER} did not write .runtime/operator-kubeconfig." >&2
  fi
  [[ -f "${kind_kubeconfig}" ]] && chmod 600 "${kind_kubeconfig}"
  export KUBECONFIG="${kind_kubeconfig}"
  context="kind-${CLUSTER}"
  kubectl config get-contexts -o name 2>/dev/null | grep -Fx "${context}" >/dev/null \
    || fail "kind cluster ${CLUSTER} exists but .runtime/operator-kubeconfig has no ${context} context; run: kind export kubeconfig --name ${CLUSTER} --kubeconfig .runtime/operator-kubeconfig"
fi
kc() { kubectl --context "${context}" "$@"; }

# Record the target before any cluster change, so the scripts that follow never fall back to
# whatever context is current.
(umask 077; printf '%s\n' "${context}" >"${RUNTIME_DIR}/operator-kube-context")
chmod 600 "${RUNTIME_DIR}/operator-kube-context"

if kc -n cert-manager get deployment cert-manager cert-manager-cainjector cert-manager-webhook >/dev/null 2>&1; then
  echo "cert-manager is already installed; skipping its manifest."
else
  echo "Installing cert-manager ${CERT_MANAGER_VERSION}."
  kc apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml" >/dev/null
fi
kc -n cert-manager wait --for=condition=Available deployment --all --timeout=180s >/dev/null
# Available deployments do not mean the cert-manager webhook serves yet: on a fresh cluster its
# CA bundle is injected a little later, and the Operator chart's Issuer and Certificate are
# rejected until then. Probe with a server-side dry run of a throwaway Issuer.
cert_manager_probe='{"apiVersion":"cert-manager.io/v1","kind":"Issuer","metadata":{"name":"webhook-readiness-probe","namespace":"cert-manager"},"spec":{"selfSigned":{}}}'
for attempt in $(seq 1 60); do
  probe_error="$(kc create --dry-run=server -f - <<<"${cert_manager_probe}" 2>&1 >/dev/null)" && break
  [[ "${attempt}" -lt 60 ]] \
    || fail "cert-manager webhook did not accept a dry-run Issuer within 120s (needs create on issuers in cert-manager): ${probe_error}"
  sleep 2
done

kc create namespace "${OPERATOR_NAMESPACE}" --dry-run=client -o yaml | kc apply -f - >/dev/null

# The Secret goes through a private env file rather than --from-literal so the Dashboard key
# and the licence never appear on a command line.
mkdir -p "${RUNTIME_DIR}/operator"
chmod 700 "${RUNTIME_DIR}/operator"
conf_file="$(mktemp "${RUNTIME_DIR}/operator/operator-conf.XXXXXX")"
trap 'rm -f "${conf_file}"' EXIT
chmod 600 "${conf_file}"
printf '%s\n' \
  "TYK_MODE=pro" \
  "TYK_URL=${operator_url}" \
  "TYK_AUTH=${DASHBOARD_API_KEY}" \
  "TYK_ORG=${ORG_ID}" \
  "TYK_OPERATOR_LICENSEKEY=${TYK_LICENSE_KEY}" >"${conf_file}"
kc -n "${OPERATOR_NAMESPACE}" create secret generic tyk-operator-conf \
  --from-env-file="${conf_file}" --dry-run=client -o yaml | kc apply -f - >/dev/null
echo "Secret ${OPERATOR_NAMESPACE}/tyk-operator-conf written (Dashboard ${operator_url})."

helm repo add tyk-helm "${HELM_REPO_URL}" --force-update >/dev/null
helm repo update tyk-helm >/dev/null
echo "Installing tyk-operator chart ${OPERATOR_CHART_VERSION} with image ${OPERATOR_VERSION}."
# One retry absorbs a late webhook on a cold cluster; the error text is kept, never discarded.
helm_log="$(mktemp)"
# Replaces the earlier EXIT trap, so it must keep removing the licence-bearing env file too.
trap 'rm -f "${conf_file}" "${helm_log}"' EXIT
for attempt in 1 2; do
  if helm upgrade --install tyk-operator tyk-helm/tyk-operator \
    --kube-context "${context}" \
    --version "${OPERATOR_CHART_VERSION}" \
    -n "${OPERATOR_NAMESPACE}" \
    --set image.tag="${OPERATOR_VERSION}" \
    --wait --timeout 5m >"${helm_log}" 2>&1; then
    break
  fi
  helm_error="$(grep -v 'unrecognized format' "${helm_log}" | tail -5)"
  # Only the cold-cluster webhook race is worth a retry; a bad tag or a crash loop would just
  # spend another --wait timeout before failing the same way.
  if [[ "${attempt}" -ge 2 ]] \
    || ! grep -Eqi 'failed calling webhook|x509|connection refused|no endpoints available' <<<"${helm_error}"; then
    fail "helm install of tyk-operator failed: ${helm_error}"
  fi
  echo "helm install hit the cert-manager webhook race; retrying once in ${TYK_OPERATOR_HELM_RETRY_DELAY:-15}s." >&2
  sleep "${TYK_OPERATOR_HELM_RETRY_DELAY:-15}"
done

# The Secret may have changed since the pods started; a restart makes them read it again.
kc -n "${OPERATOR_NAMESPACE}" rollout restart deployment >/dev/null
while IFS= read -r deployment; do
  kc -n "${OPERATOR_NAMESPACE}" rollout status "${deployment}" --timeout=180s >/dev/null
done < <(kc -n "${OPERATOR_NAMESPACE}" get deployment -o name)

kc get crd tykoasapidefinitions.tyk.tyk.io securitypolicies.tyk.tyk.io operatorcontexts.tyk.tyk.io >/dev/null \
  || fail "Operator CRDs are missing after the chart install."
if module_enabled governance; then
  kc get crd tykrulesets.tyk.tyk.io >/dev/null 2>&1 \
    || fail "TykRuleset CRD not found: OPERATOR_VERSION=${OPERATOR_VERSION} lacks governance. Use v1.5.0 or later."
  kc get validatingwebhookconfigurations -o json \
    | jq -e '[.items[].webhooks[]? | select(.name == "vtykoasapidefinition.kb.io")] | length > 0' >/dev/null \
    || fail "The TykOasApiDefinition validating webhook is not registered; the governance hard block cannot run."
fi

echo "tyk-operator ${OPERATOR_VERSION} is ready in ${context}."
echo "For kubectl by hand use ./operator/kubectl.sh (your current kubectl context is unchanged)."
echo "Next: source operator/operator-env.sh && ./operator/apply-operator.sh"
if module_enabled governance; then
  echo "Then: ./operator/verify-governance.sh"
fi
