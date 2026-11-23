#!/usr/bin/env bash
# Start the Tyk AI Studio companion stack and configure the demo.
#
# SEPARATE PRODUCT, SEPARATE STACK. This does not touch the Tyk Gateway bundle in
# the parent directory and does not require it to be running.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

fail() {
  printf '\n'
  printf '==========================================================================\n' >&2
  printf ' TYK AI STUDIO COMPANION STACK NOT STARTED\n' >&2
  printf '==========================================================================\n' >&2
  printf '%s\n' "$@" >&2
  printf '==========================================================================\n' >&2
  exit 1
}

# --- Licence gate, before anything is created --------------------------------
# The Model Router, guardrails, budgets and the audit trail are Enterprise
# features (https://tyk.io/docs/ai-management/ai-studio/overview). Starting
# without a licence would produce containers, a database and an admin user, and
# then fail at the one step that matters. Fail here instead.
[[ -f secret.env ]] || fail \
  "  secret.env is missing." \
  "" \
  "  The generator should have written one. Recreate it with:" \
  "      cp secret.env.example secret.env" \
  "  then mint fresh values for every empty key in it and add the licence." \
  "  Note secret.env.example ships those keys EMPTY on purpose - copying it" \
  "  unedited leaves the control plane and the edge with mismatched secrets."

set -a
# shellcheck disable=SC1091
source ./secret.env
set +a

if [[ -z "${TYK_AI_LICENSE:-}" || "${TYK_AI_LICENSE}" == *ENTER-YOUR-LICENSE* ]]; then
  fail \
    "  No Tyk AI Studio ENTERPRISE licence in secret.env (TYK_AI_LICENSE)." \
    "" \
    "  This module demonstrates the Enterprise Model Router, guardrails, budgets," \
    "  the audit trail and the Tyk Dashboard MCP import, all Enterprise features" \
    "  (see https://tyk.io/docs/ai-management/ai-studio/overview)." \
    "" \
    "  There is no partial demo without it. Nothing here is worth starting." \
    "" \
    "  Put a Tyk AI Studio 2.3 Enterprise licence in ./secret.env:" \
    "      TYK_AI_LICENSE=<licence>" \
    "  The same value is read by the control plane and the edge. secret.env is" \
    "  gitignored and mode 0600. Never paste a licence into secret.env.example."
fi

for key in GRPC_AUTH_TOKEN EDGE_AUTH_TOKEN MICROGATEWAY_ENCRYPTION_KEY ENCRYPTION_KEY JWT_SECRET TYK_AI_SECRET_KEY METRICS_AUTH_TOKEN; do
  [[ -n "${!key:-}" ]] || fail "  secret.env has an empty ${key}. See secret.env.example for what it is for."
done
# The admin login lives in admin.env, which only the host scripts read: it is
# not an env_file of either container (secret.env is, for both). Checked in a
# subshell so the password is never exported to docker compose.
[[ -f admin.env ]] || fail \
  "  admin.env is missing." \
  "" \
  "  The generator should have written one with a minted admin password." \
  "  Recreate it with: cp admin.env.example admin.env && chmod 600 admin.env" \
  "  then set AISTUDIO_ADMIN_PASSWORD (see the complexity rule in the file)."
( set +u; source ./admin.env >/dev/null 2>&1; [[ -n "${AISTUDIO_ADMIN_PASSWORD:-}" ]] ) \
  || fail "  admin.env has an empty AISTUDIO_ADMIN_PASSWORD. See admin.env.example for the rule it must meet."
! grep -qE '^AISTUDIO_ADMIN_PASSWORD=.' secret.env || fail \
  "  secret.env sets AISTUDIO_ADMIN_PASSWORD. secret.env is an env_file of both" \
  "  containers, so the admin password would sit in their environments. Move" \
  "  AISTUDIO_ADMIN_EMAIL and AISTUDIO_ADMIN_PASSWORD to admin.env."
[[ "${GRPC_AUTH_TOKEN}" == "${EDGE_AUTH_TOKEN}" ]] || fail \
  "  GRPC_AUTH_TOKEN and EDGE_AUTH_TOKEN must hold the SAME value." \
  "  They are the control side and edge side of one shared token; when they" \
  "  differ the edge cannot work with the control plane."
[[ "${MICROGATEWAY_ENCRYPTION_KEY}" == "${ENCRYPTION_KEY}" ]] || fail \
  "  MICROGATEWAY_ENCRYPTION_KEY and ENCRYPTION_KEY must hold the SAME value."
[[ "${#ENCRYPTION_KEY}" == 32 ]] || fail \
  "  ENCRYPTION_KEY must be exactly 32 characters (it is an AES-256 key)."

# --- State directories -------------------------------------------------------
# The images run as uid 65532, so host-owned bind mounts are not writable by the
# process.
# World-writable is the portable way to let an arbitrary container UID write on
# every host (chown to 65532 needs root, and Docker Desktop maps ownership
# differently from Linux). It is acceptable for a disposable demo because ./data is
# gitignored and holds only demo state, and compose.yaml publishes every port on
# 127.0.0.1 only, so nothing here is reachable from another machine.
mkdir -p data/appdata/cache/plugins data/appdata/marketplace-cache data/ai-studio data/mgw data/cache/plugins
chmod -R 777 data

docker compose up -d
exec ./configure-ai-studio.sh
