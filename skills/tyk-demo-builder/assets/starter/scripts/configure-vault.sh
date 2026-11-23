#!/usr/bin/env bash
# vault module (proof SECRETS-VAULT-001).
#
# Seeds the upstream credential the gateway injects from HashiCorp Vault. The Vault dev
# server keeps its data in memory, so every configuration run seeds it again, but only when
# it is absent: an existing (possibly rotated) value is never overwritten.
#
# Layout: the dev server mounts KV v2 at secret/, and the value lives at
# __VAULT_SECRET_PATH__ in the field __VAULT_SECRET_FIELD__. The API definition references it as
# $secret_vault.__VAULT_SECRET_PATH__.__VAULT_SECRET_FIELD__ and the gateway reads
# secret/data/... itself (KV version 2, TYK_GW_KV_VAULT_KVVERSION=2). Rules worth knowing,
# on Gateway 5.15.1:
# - The reference is split at its FIRST dot into a path and a field, so neither may contain a
#   dot, and the path must start with the mount.
# - The documented short forms $secret_vault.<key> (fixed path secret/tyk-apis) and
#   $secret_vault.tyk-apis.<field> both resolved to an empty value on 5.15.1.
# - An unresolvable reference becomes an empty header value and the request is still
#   proxied, so a typo fails open; SECRETS-VAULT-001 checks the value the upstream received.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[[ -n "${VAULT_TOKEN:-}" ]] || { echo "VAULT_TOKEN is empty in .env; regenerate the bundle with the vault module." >&2; exit 1; }

for attempt in $(seq 1 30); do
  [[ "$(vault_api GET sys/health -o /dev/null -w '%{http_code}' 2>/dev/null || true)" == 200 ]] && break
  [[ "${attempt}" == 30 ]] && { echo "Vault did not become ready at ${VAULT_URL}." >&2; exit 1; }
  sleep 2
done

# KV v2 reads and writes go to <mount>/data/<path>.
data_path="__VAULT_DATA_PATH__"
current="$(vault_api GET "${data_path}" 2>/dev/null | jq -r '.data.data.__VAULT_SECRET_FIELD__ // empty' 2>/dev/null || true)"
if [[ -n "${current}" ]]; then
  echo "Vault already holds the upstream credential at __VAULT_SECRET_PATH__; left unchanged."
  exit 0
fi
# The value reaches jq through its environment and curl on stdin, never as an argument.
upstream_credential="$(openssl rand -hex 24)"
upstream_credential="${upstream_credential}" jq -nc '{data: {__VAULT_SECRET_FIELD__: env.upstream_credential}}' \
  | vault_api POST "${data_path}" -H 'Content-Type: application/json' --data-binary @- -o /dev/null -w '%{http_code}' \
  | grep -Fx 200 >/dev/null || { echo "Vault refused the upstream credential write." >&2; exit 1; }
echo "Seeded the upstream credential in Vault at __VAULT_SECRET_PATH__."
