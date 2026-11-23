#!/usr/bin/env bash
# upstream-mtls module (proof UPSTREAM-MTLS-001).
#
# Uploads the gateway's client certificate (with its private key), the upstream's public key
# and an unrelated public key to the Dashboard certificate store, records the three IDs, and
# renders apis/__SLUG__-upstream-mtls.oas.json from apis/upstream-mtls.oas.template.json. Run
# import-apis.sh afterwards (configure-demo.sh does).
#
# Rules worth knowing, on Gateway and Dashboard 5.15.1:
# - upstream.mutualTLS.domainToCertificateMapping matches the upstream as host:port.
# - upstream.certificatePinning.domainToPublicKeysMapping matches the BARE host; with
#   host:port the pin silently does not apply and a wrong pin still returns 200.
# - Both take certificate-store IDs. A pin ID missing from the store is an empty allowlist,
#   which means no pinning, so the negative proof pins a different key that IS stored.
# - Uploading a certificate that is already stored fails with a message containing
#   "Certificate with <id> id already exists"; the ID is read from it, so reruns are idempotent.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[[ -n "${DASHBOARD_API_KEY:-}" ]] || { echo "Run bootstrap-demo-stack.sh first." >&2; exit 1; }

cert_dir="${ROOT_DIR}/generated/certs/upstream"
[[ -f "${cert_dir}/gateway-client.pem" ]] || "${ROOT_DIR}/scripts/generate-upstream-certs.sh"

# upload_certificate <file>: print the certificate-store ID of the file, uploading it if needed.
upload_certificate() {
  local file="$1" response id attempt
  for attempt in $(seq 1 30); do
    response="$(curl -sS "${DASHBOARD_URL}/api/certs" -H "authorization: ${DASHBOARD_API_KEY}" -F "file=@${file}" 2>/dev/null || true)"
    id="$(jq -r '.id // .ID // .Meta.id // .Meta.ID // empty' <<<"${response}" 2>/dev/null || true)"
    [[ -n "${id}" ]] || id="$(sed -nE 's/.*Certificate with ([^ ]+) id already exists.*/\1/p' <<<"${response}" | head -1)"
    if [[ -n "${id}" ]]; then
      printf '%s' "${id}"
      return 0
    fi
    # The Dashboard can answer 403 for a moment right after bootstrap.
    sleep 2
  done
  echo "The Dashboard did not accept $(basename "${file}"): ${response:0:200}" >&2
  return 1
}

client_id="$(upload_certificate "${cert_dir}/gateway-client.pem")"
pin_id="$(upload_certificate "${cert_dir}/server-public-key.pem")"
decoy_id="$(upload_certificate "${cert_dir}/decoy-public-key.pem")"
[[ "${pin_id}" != "${decoy_id}" ]] || { echo "The pinned and the unrelated public key resolved to one certificate ID." >&2; exit 1; }
runtime_set UPSTREAM_MTLS_CLIENT_CERT_ID "${client_id}"
runtime_set UPSTREAM_MTLS_PIN_ID "${pin_id}"
runtime_set UPSTREAM_MTLS_DECOY_PIN_ID "${decoy_id}"

jq --arg cert "${client_id}" --arg pin "${pin_id}" '
  ."x-tyk-api-gateway".upstream.mutualTLS.domainToCertificateMapping[0].certificate = $cert
  | ."x-tyk-api-gateway".upstream.certificatePinning.domainToPublicKeysMapping[0].publicKeys = [$pin]' \
  "${ROOT_DIR}/apis/upstream-mtls.oas.template.json" >"${ROOT_DIR}/apis/__SLUG__-upstream-mtls.oas.json"
echo "Upstream mTLS certificates stored; rendered apis/__SLUG__-upstream-mtls.oas.json."
