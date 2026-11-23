#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT_DIR}/generated/certs"
mkdir -p "${CERT_DIR}"
chmod 700 "${CERT_DIR}"

[[ -f "${CERT_DIR}/server.pem" && -f "${CERT_DIR}/client.crt" && -f "${CERT_DIR}/untrusted-client.crt" ]] && exit 0

openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=__DISPLAY_NAME__ Client CA" -keyout "${CERT_DIR}/ca.key" -out "${CERT_DIR}/ca.crt" >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -subj "/CN=localhost" -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.csr" >/dev/null 2>&1
printf 'subjectAltName=DNS:localhost,DNS:tyk-gateway,IP:127.0.0.1\n' >"${CERT_DIR}/server.ext"
openssl x509 -req -days 30 -in "${CERT_DIR}/server.csr" -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial -out "${CERT_DIR}/server.crt" -extfile "${CERT_DIR}/server.ext" >/dev/null 2>&1
cat "${CERT_DIR}/server.crt" "${CERT_DIR}/server.key" >"${CERT_DIR}/server.pem"
openssl req -newkey rsa:2048 -nodes -subj "/CN=trusted-demo-client" -keyout "${CERT_DIR}/client.key" -out "${CERT_DIR}/client.csr" >/dev/null 2>&1
openssl x509 -req -days 30 -in "${CERT_DIR}/client.csr" -CA "${CERT_DIR}/ca.crt" -CAkey "${CERT_DIR}/ca.key" -CAcreateserial -out "${CERT_DIR}/client.crt" >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=untrusted-demo-client" -keyout "${CERT_DIR}/untrusted-client.key" -out "${CERT_DIR}/untrusted-client.crt" >/dev/null 2>&1
chmod 600 "${CERT_DIR}"/*.key "${CERT_DIR}/server.pem"
