#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
# A purge destroys every credential the stack issued, but .runtime/runtime.env
# is an ordinary file outside Docker and survives it. Everything recorded there
# then refers to state that no longer exists, and the guards in
# configure-demo.sh read those values as "already done" and skip the work — so
# the rebuilt stack comes up half-configured, with failures that point anywhere
# but here. Archive it rather than delete it, in case a value is still wanted.
archive_runtime_env() {
  local stamp
  [[ -f "${RUNTIME_ENV}" ]] || return 0
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  mv "${RUNTIME_ENV}" "${RUNTIME_ENV}.stale-${stamp}"
  echo "Archived runtime.env as runtime.env.stale-${stamp}; re-run bootstrap-demo-stack.sh."
}

# --purge deletes data: every named volume of this project (the Dashboard, Portal and analytics
# databases, Redis, and every key, policy and credential the stack issued). It asks first. A
# non-interactive caller (an agent, CI) must pass --yes, and only after the person who owns the
# demo has agreed to lose that data.
purge=false
confirmed=false
for argument in "$@"; do
  case "${argument}" in
    --purge) purge=true ;;
    --yes) confirmed=true ;;
    *) echo "Usage: $0 [--purge [--yes]]" >&2; exit 2 ;;
  esac
done

if [[ "${purge}" == true ]]; then
  echo "--purge deletes these volumes of Compose project __SLUG__ and everything in them:"
  compose config --format json | jq -r '(.volumes // {}) | keys[] | "  " + .'
  if [[ "${confirmed}" != true ]]; then
    if [[ -t 0 ]]; then
      read -r -p "Type __SLUG__ to delete this data: " answer
      [[ "${answer}" == "__SLUG__" ]] || { echo "Nothing deleted." >&2; exit 1; }
    else
      echo "Refusing to delete data without confirmation; rerun with --purge --yes once the demo owner agrees." >&2
      exit 1
    fi
  fi
  compose down --volumes --remove-orphans
  archive_runtime_env
  # Operator evidence names objects in the Dashboard that was just deleted; verify-demo.sh
  # would refuse it anyway, but leaving it behind invites reading a stale pass by hand.
  rm -f "${ROOT_DIR}"/evidence/operator-*.json
  # The same for the Tyk Sync promotion record SYNC-001 reads; the dumps stay as history.
  rm -f "${RUNTIME_DIR}/tyk-sync/latest-promotion.json"
  echo "Removed containers and volumes owned by __SLUG__, and any Operator and Tyk Sync evidence."
else
  compose down --remove-orphans
  echo "Stopped __SLUG__; volumes were preserved."
fi
