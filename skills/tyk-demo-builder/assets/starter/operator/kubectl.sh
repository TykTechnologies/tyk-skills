#!/usr/bin/env bash
# kubectl against this bundle's Operator cluster, whatever context your shell has selected.
#
#   ./operator/kubectl.sh get tykruleset
#   ./operator/kubectl.sh apply -f .runtime/operator/governed-api-noncompliant.yaml
#
# install-operator-kind.sh keeps the kind cluster's credentials in the bundle-local
# .runtime/operator-kubeconfig and records the context it installed into in
# .runtime/operator-kube-context; it never switches your current context. A bare `kubectl`
# therefore talks to whatever cluster your own kubeconfig points at, or fails. This wrapper
# uses the same resolution as apply-operator.sh and the verify scripts (operator-lib.sh), so
# it is equivalent to:
#   KUBECONFIG=.runtime/operator-kubeconfig kubectl --context kind-<cluster> "$@"
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=operator-lib.sh
source "${ROOT_DIR}/operator/operator-lib.sh"
resolve_kube_context || exit 1
exec kubectl --context "${KUBE_CONTEXT}" "$@"
