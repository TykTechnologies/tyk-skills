#!/usr/bin/env bash
# upstream-mtls module: mint the upstream CA, the upstream's server certificate, the
# gateway's client certificate and an unrelated key pair whose public key is the wrong pin.
#
# The certificate subjects are visible (the Dashboard certificate list), so they
# derive from the display name. ca-dir/ holds the CA under its OpenSSL subject hash
# (<hash>.0), the layout Go reads from SSL_CERT_DIR, and is mounted into every gateway.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT_DIR}/generated/certs/upstream"
mkdir -p "${CERT_DIR}/ca-dir"
chmod 700 "${ROOT_DIR}/generated/certs" "${CERT_DIR}"

[[ -f "${CERT_DIR}/gateway-client.pem" && -f "${CERT_DIR}/server-public-key.pem" && -f "${CERT_DIR}/decoy-public-key.pem" ]] && exit 0

# Explicit extensions (CA key usage, key identifiers, server and client usages) so strict
# verifiers accept the chain: Python 3.13 rejects a CA without keyUsage, for example. The
# extension files work with both OpenSSL and LibreSSL, unlike req -addext.
printf 'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n' >"${CERT_DIR}/ca.ext"
printf 'keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid\nsubjectAltName=DNS:__SECURE_UPSTREAM_HOST__,DNS:localhost,IP:127.0.0.1\n' >"${CERT_DIR}/server.ext"
printf 'keyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\nauthorityKeyIdentifier=keyid\n' >"${CERT_DIR}/client.ext"
openssl req -new -newkey rsa:2048 -nodes -subj "/CN=__DISPLAY_NAME__ Upstream CA" -keyout "${CERT_DIR}/ca.key" -out "${CERT_DIR}/ca.csr" >/dev/null 2>&1
openssl x509 -req -days 30 -in "${CERT_DIR}/ca.csr" -signkey "${CERT_DIR}/ca.key" -extfile "${CERT_DIR}/ca.ext" -out "${CERT_DIR}/ca.crt" >/dev/null 2>&1
openssl req -new -newkey rsa:2048 -nodes -subj "/CN=__SECURE_UPSTREAM_HOST__" -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.csr" >/dev/null 2>&1
openssl x509 -req -days 30 -in "${CERT_DIR}/server.csr" -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial -out "${CERT_DIR}/server.crt" -extfile "${CERT_DIR}/server.ext" >/dev/null 2>&1
openssl req -new -newkey rsa:2048 -nodes -subj "/CN=__DISPLAY_NAME__ Gateway" -keyout "${CERT_DIR}/gateway-client.key" -out "${CERT_DIR}/gateway-client.csr" >/dev/null 2>&1
openssl x509 -req -days 30 -in "${CERT_DIR}/gateway-client.csr" -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial -out "${CERT_DIR}/gateway-client.crt" -extfile "${CERT_DIR}/client.ext" >/dev/null 2>&1
# The certificate store takes the client certificate and its private key in one PEM.
cat "${CERT_DIR}/gateway-client.crt" "${CERT_DIR}/gateway-client.key" >"${CERT_DIR}/gateway-client.pem"
# Pins are public keys in the certificate store: the upstream's, and one from a key pair the
# upstream never uses, so the negative proof pins an ID that exists but does not match.
openssl x509 -pubkey -noout -in "${CERT_DIR}/server.crt" >"${CERT_DIR}/server-public-key.pem"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${CERT_DIR}/decoy.key" >/dev/null 2>&1
openssl pkey -in "${CERT_DIR}/decoy.key" -pubout -out "${CERT_DIR}/decoy-public-key.pem" >/dev/null 2>&1
rm -f "${CERT_DIR}"/ca-dir/*
cp "${CERT_DIR}/ca.crt" "${CERT_DIR}/ca-dir/$(openssl x509 -hash -noout -in "${CERT_DIR}/ca.crt").0"
chmod 600 "${CERT_DIR}"/*.key "${CERT_DIR}"/*.pem
# Public material only, read by the gateway's own user through the mount.
chmod 755 "${CERT_DIR}/ca-dir"
chmod 644 "${CERT_DIR}/ca.crt" "${CERT_DIR}"/ca-dir/*
