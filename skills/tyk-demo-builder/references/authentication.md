# Authentication, Key-Import, Vault And Upstream-mTLS Modules

Last verified: 2026-11-19 against Tyk Gateway and Dashboard 5.16.0.

This page covers what the authentication modules generate and what their proofs assert. For how
to secure a Tyk OAS API (auth token, Basic, JWT and JWKS, OAuth 2.0, client mTLS and
certificate authentication, several methods, error behaviour), use the `tyk-authentication`
skill; for keys, policies and migrating consumer keys, the `tyk-rate-limits-keys-policies`
skill.

## Local Identity Service

The bundled identity service (`upstreams/identity_service.py`, Compose service `identity`)
issues RSA-signed JWTs, serves a JWKS and a discovery document, and implements an RFC 7591-style
registration endpoint for `dcr`. It signs any claims it is sent, so its port binds to
`127.0.0.1` (see **Network Exposure** in `references/standalone-runtime.md`). It builds the URLs
it advertises from the caller's `Host`, so the Portal (calling `identity:8080`) and host tools
(calling `localhost:<port>`) each get an address that works; set `PUBLIC_BASE_URL` only to force
one address. `--identity-mode external` leaves it out and keeps the rows that need it `blocked`.

## Module Proofs

| Module | Proof | What it asserts |
| --- | --- | --- |
| `api-key` | `AUTH-KEY-001` | a request without a key is rejected, the owned key is accepted |
| `basic-auth` | `AUTH-BASIC-001` | missing credentials rejected, the owned user accepted |
| `jwt` | `AUTH-JWT-001` | RS256 signature, issuer and audience enforced, and the scope claim mapped to a policy |
| `oauth` | `AUTH-OAUTH-001` | a client-credentials token is issued by the gateway and enforced |
| `chained-auth` | `AUTH-CHAIN-001` | in compliant security mode either an API key or Basic Auth is accepted |
| `mtls` | `AUTH-MTLS-001` | a certificate from the bundle CA is accepted; no certificate and one from an untrusted CA are rejected |

`mtls` generates a static client-certificate allow list on the API
(`server.clientCertificates`) holding the bundle CA, so any certificate that CA issues is
accepted and there is no per-client identity; when a requirement asks for per-client identity or
revocation, that is certificate authentication, which this module does not generate (the
`tyk-authentication` skill covers it). Selecting `mtls` is what puts the gateway on TLS; see
**Gateway Transport** in `references/standalone-runtime.md`.

## key-import (`KEY-IMPORT-001`)

Migrates consumers without re-issuing their keys. `scripts/import-keys.sh` reads
`migration/imported-keys.json` and, through the Dashboard custom-key endpoint, finds each key
(`GET /api/keys/<key>`), creates it with its existing value (`POST /api/keys/<key>`) or updates
it in place (`PUT`), and reads the alias back.

- The keys use their own no-quota policy, `<display name> Migrated Consumers`, never the
  API-key policy, so a `quota` bundle's limit cannot fail unrelated proofs.
- The two sample values are minted at random when the bundle is generated, never derived from
  the slug or name, because once imported they are live credentials. The file is git-ignored,
  `sanitize_bundle.py --strict-runtime` refuses it, and `import-keys.sh` mints fresh values from
  `migration/imported-keys.example.json` when it is missing. Replace the values with your own
  gateway's export only inside your own bundle.
- `KEY-IMPORT-001` asserts the imported values are accepted (200), a random key is refused
  (403), no key is rejected (401), the aliases read back, and reports whether the gateway hashes
  keys. A passing run reports `imported values accepted: 200 200; random key: 403; no key: 401;
  aliases read back: true; gateway hash_keys: true`.

## vault (`SECRETS-VAULT-001`)

Keeps the credential the gateway presents to an upstream in HashiCorp Vault.

- A Vault dev server (`hashicorp/vault:1.17.6`, KV v2 at `secret/`, root token minted into
  `.env`, port on `127.0.0.1` only) is registered on every gateway with
  `TYK_GW_KV_VAULT_ADDRESS`, `TYK_GW_KV_VAULT_TOKEN` and `TYK_GW_KV_VAULT_KVVERSION=2`.
- `<display name> Vault Credentials` adds a request header from
  `$secret_vault.secret/<slug>/upstream.api_key`. The generator keeps the path and field free of
  dots, because the reference is read as `<path>.<field>`.
- Dev mode keeps its data in memory. `scripts/configure-vault.sh` seeds the value on every
  configuration run, only when it is absent, so a rotated value survives a rerun.
- Scripts pass the root token to `curl` through a process substitution, never as an argument.
- `SECRETS-VAULT-001` has the upstream report a SHA-256 of the header it received and asserts:
  the first request carries Vault's value; a client-supplied header of the same name is
  overwritten; the value appears in no client response; after a rotation in Vault the very next
  request carries the new value with no API change or reload.

## upstream-mtls (`UPSTREAM-MTLS-001`)

Proves the gateway's side of a mutually authenticated upstream.

- A small HTTPS upstream refuses a handshake without a client certificate from the upstream CA
  and reports the client certificate's common name. Certificates are minted at start by
  `scripts/generate-upstream-certs.sh` and stored in the Dashboard certificate store by
  `scripts/configure-upstream-mtls.sh`; every gateway trusts the upstream CA through
  `SSL_CERT_DIR`.
- `<display name> Secure Upstream` presents the gateway's client certificate
  (`upstream.mutualTLS`) and pins the upstream's public key (`upstream.certificatePinning`). The
  certificate subjects derive from the display name, because the Dashboard certificate list
  shows them.
- `UPSTREAM-MTLS-001` asserts: with the correct pin, 200 and the upstream sees the gateway's
  certificate name; with the pin swapped for another stored public key, 500 with the gateway's
  proxy error body; with the pin restored, 200 again; called directly, the upstream refuses a
  caller without a certificate and accepts one with it. A passing run reports `correct pin: 200
  (upstream saw client certificate 'Example Corp Gateway'); pin swapped: 500; pin restored: 200;
  upstream direct without a client certificate: 000, with one: 200`.

## External Identity Providers

Keep Auth0, Entra ID or Keycloak credentials outside the bundle, render provider settings from
environment variables, and verify discovery before importing the APIs that depend on them. With
an external provider, rows the local identity service would prove stay `blocked` until the
provider's own run passes.
