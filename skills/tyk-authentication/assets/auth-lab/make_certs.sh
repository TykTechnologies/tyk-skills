#!/usr/bin/env bash
# Test certificates for the lab: a CA, the Gateway's server certificate, two partner
# certificates from that CA, one from another CA, and one from the CA that has expired.
# Needs OpenSSL 3.4 or later for -not_before/-not_after. Writes into ./certs.
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p certs; cd certs
ca() { openssl req -x509 -newkey rsa:2048 -nodes -keyout "$1.key" -out "$1.crt" -days 30 -subj "/CN=$2" 2>/dev/null; }
leaf() { # name common-name ca [not-before not-after]
  openssl req -newkey rsa:2048 -nodes -keyout "$1.key" -out "$1.csr" -subj "/CN=$2" 2>/dev/null
  printf 'subjectAltName=DNS:localhost,DNS:tyk-gateway-tls,IP:127.0.0.1\n' > ext.cnf
  if [ $# -ge 5 ]; then
    openssl x509 -req -in "$1.csr" -CA "$3.crt" -CAkey "$3.key" -CAcreateserial -out "$1.crt" \
      -not_before "$4" -not_after "$5" -extfile ext.cnf 2>/dev/null
  else
    openssl x509 -req -in "$1.csr" -CA "$3.crt" -CAkey "$3.key" -CAcreateserial -out "$1.crt" \
      -days 30 -extfile ext.cnf 2>/dev/null
  fi
  rm -f "$1.csr" ext.cnf
}
ca ca "Example Corp Test CA"
ca other-ca "Untrusted CA"
leaf server tyk-gateway-tls ca
leaf partner-a partner-a ca
leaf partner-b partner-b ca
leaf outsider outsider other-ca
leaf expired partner-expired ca 20250101000000Z 20250201000000Z
chmod 644 ./*.key
ls
