#!/usr/bin/env bash
# Helpers shared by apply-operator.sh, verify-reconciliation.sh, verify-governance.sh and
# kubectl.sh (the wrapper for kubectl by hand).
# Source it after ROOT_DIR is set; it is not meant to be run on its own.

KUBE_CONTEXT_FILE="${ROOT_DIR}/.runtime/operator-kube-context"
# install-operator-kind.sh keeps the kind cluster's credentials in a bundle-local kubeconfig
# rather than ~/.kube/config, so the user's current context is never switched. When it is
# present every kubectl call below reads it; otherwise the user's own kubeconfig applies.
KIND_KUBECONFIG_FILE="${ROOT_DIR}/.runtime/operator-kubeconfig"

# The organisation the evidence belongs to. scripts/verify-demo.sh refuses evidence whose
# org_id is not this stack's ORG_ID, so a pass from an earlier or different stack cannot be
# reported against this one. TYK_OPERATOR_ORG (what the Operator writes to) wins.
operator_org_id() {
  if [[ -n "${TYK_OPERATOR_ORG:-}" ]]; then
    printf '%s' "${TYK_OPERATOR_ORG}"
  elif [[ -f "${ROOT_DIR}/.runtime/runtime.env" ]]; then
    # shellcheck disable=SC1091
    (set +u; source "${ROOT_DIR}/.runtime/runtime.env"; printf '%s' "${ORG_ID:-}")
  fi
}

utc_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

# write_started_marker <evidence-file> <proof-id>
#
# Written before any other work so that a bare `set -e` exit, a missing tool, or an
# interrupted run leaves failed evidence rather than an earlier pass. Plain printf rather
# than jq, because a missing jq is one of the failures this marker must survive.
write_started_marker() {
  local org
  org="$(operator_org_id)"
  org="${org//[^A-Za-z0-9_.-]/}"
  mkdir -p "$(dirname "$1")"
  printf '{"proof":"%s","status":"failed","reason":"run started but did not complete","org_id":"%s","recorded_at":"%s"}\n' \
    "$2" "${org}" "$(utc_now)" >"$1"
}

# resolve_kube_context: sets KUBE_CONTEXT, or prints why not and returns 1.
#
# install-operator-kind.sh records the context it installed into. Without that record the
# current context is used only when it is a kind- context, or when ALLOW_NON_KIND_CONTEXT=true
# says a non-kind cluster is disposable, so a demo never lands in a shared cluster by accident.
resolve_kube_context() {
  local current
  if [[ -s "${KIND_KUBECONFIG_FILE}" ]]; then
    export KUBECONFIG="${KIND_KUBECONFIG_FILE}"
  fi
  if [[ -s "${KUBE_CONTEXT_FILE}" ]]; then
    KUBE_CONTEXT="$(<"${KUBE_CONTEXT_FILE}")"
    return 0
  fi
  current="$(kubectl config current-context 2>/dev/null)" || {
    echo "No kubectl context is selected; run ./operator/install-operator-kind.sh first." >&2
    return 1
  }
  if [[ "${current}" != kind-* && "${ALLOW_NON_KIND_CONTEXT:-false}" != true ]]; then
    echo "Refusing to use non-kind context ${current}. Run ./operator/install-operator-kind.sh, or set ALLOW_NON_KIND_CONTEXT=true only for a disposable cluster." >&2
    return 1
  fi
  KUBE_CONTEXT="${current}"
}

kc() {
  kubectl --context "${KUBE_CONTEXT}" "$@"
}

# ---- desired-state digests -----------------------------------------------------------
#
# Freshness cannot be read from the cluster. On tyk-operator v1.6.0 re-applying an identical
# rendered SecurityPolicy reports `configured` and bumps metadata.generation every time while
# latestCRDSpecHash stays put, so "the generation moved, wait for the hash" hangs forever.
# Instead the bundle keeps its own digest of each resource's desired content (a sorted-JSON
# sha256 of the rendered .spec, or .data for a ConfigMap) together with the Operator status
# hashes observed when that digest last verified successfully. A resource must show a moved
# status hash only when its digest differs from the last verified one, and it is compared with
# the hashes recorded at that verification, not with a value read just before this apply: a run
# that the Operator reconciled but that failed for another reason must not leave the next run
# waiting for a hash that already moved. With no verified record there is nothing trustworthy
# to compare with, so only the ordinary convergence checks apply.
DIGESTS_VERIFIED="${ROOT_DIR}/.runtime/operator/.verified-digests.json"
# shellcheck disable=SC2034  # used by the scripts that source this file
DIGESTS_PENDING="${ROOT_DIR}/.runtime/operator/.pending-digests.json"

sha256_stdin() {
  if command -v sha256sum >/dev/null; then
    sha256sum | cut -d' ' -f1
  else
    shasum -a 256 | cut -d' ' -f1
  fi
}

# desired_digest <rendered-file> <kind> <name> <spec|data>
# `create --dry-run=client` converts the manifest without reading or merging the live object,
# so server-side defaults never leak into the digest.
desired_digest() {
  local content
  content="$(kc create --dry-run=client -o json -f "$1" \
    | jq -cS --arg kind "$2" --arg name "$3" --arg field "$4" \
      '(if .kind == "List" then .items[] else . end) | select(.kind == $kind and .metadata.name == $name) | .[$field]')"
  [[ -n "${content}" && "${content}" != null ]] || { echo "No $2 $3 in $1" >&2; return 1; }
  printf '%s\n' "${content}" | sha256_stdin
}

read_json_file() {
  if [[ -s "$1" ]]; then cat "$1"; else printf '{}'; fi
}

# digests_fresh <pending-json> <state-json>
#
# pending: {"resources":{"<key>":{"digest":..,"source":"<state key>","fields":["<status hash>",..]}}}
# state:   {"<state key>": <resource JSON with .status>, ...}
digests_fresh() {
  jq -e -n --argjson pending "$1" --argjson state "$2" --argjson verified "$(read_json_file "${DIGESTS_VERIFIED}")" '
    ($verified.resources // {}) as $old
    | all(($pending.resources // {}) | to_entries[];
        .key as $key | .value as $entry | $old[$key] as $previous
        | if $previous == null or $previous.digest == $entry.digest then true
          else ($entry.fields | map({key: ., value: ($state[$entry.source].status[.] // "")}) | from_entries) as $now
            | ($now | to_entries | all(.value != "")) and $now != $previous.status_hashes
          end)' >/dev/null
}

# digests_record <pending-json> <state-json>: called only after a fully successful verification.
digests_record() {
  local tmp
  mkdir -p "$(dirname "${DIGESTS_VERIFIED}")"
  tmp="$(mktemp "${DIGESTS_VERIFIED}.XXXXXX")"
  chmod 600 "${tmp}"
  jq -n --argjson pending "$1" --argjson state "$2" --argjson verified "$(read_json_file "${DIGESTS_VERIFIED}")" '
    # A resource whose status hashes are not all populated is left unrecorded, and any older
    # record for it is removed, so its next run uses ordinary convergence instead of waiting
    # for an empty hash to "move" away from a stale value.
    (($pending.resources // {}) | with_entries(.value as $entry | .value = {
      digest: $entry.digest,
      status_hashes: ($entry.fields | map({key: ., value: ($state[$entry.source].status[.] // "")}) | from_entries)
    })) as $fresh
    | ($fresh | with_entries(select(.value.status_hashes | to_entries | all(.value != "")))) as $complete
    | ($fresh | keys - ($complete | keys)) as $incomplete
    | ($verified * {resources: $complete})
    | .resources |= with_entries(select(.key as $k | $incomplete | index($k) | not))' >"${tmp}"
  mv "${tmp}" "${DIGESTS_VERIFIED}"
}
