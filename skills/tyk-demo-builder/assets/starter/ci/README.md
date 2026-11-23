# Neutral Tyk Sync Delivery

`tyk-sync.sh` provides three operations:

```bash
./ci/tyk-sync.sh validate apis
./ci/tyk-sync.sh promote apis
./ci/tyk-sync.sh rollback .runtime/tyk-sync/rollback/<promotion-id>
```

Promotion validates JSON definitions, creates an immutable runtime snapshot,
and filters both pre-promotion and read-back Dashboard dumps to API IDs owned by
the generated bundle. Promotion and rollback refuse definitions outside that
owned ID set; they do not delete APIs or mutate the database directly. The
read-back must hold every owned API ID in the target organisation, and each
read-back must match what was sent on name and state, listen path, upstream,
authentication, security scheme names, gateway tags, operations and their
middleware. The result, with a SHA-256 of every delivered definition, is
recorded in `.runtime/tyk-sync/latest-promotion.json`, which
`scripts/verify-demo.sh` reads for `SYNC-001`: an API file changed since the
last delivery keeps `SYNC-001` blocked until the next promotion.

## Against this bundle's own stack

No local `tyk-sync` binary is needed:

```bash
./ci/tyk-sync-local.sh promote apis
./ci/tyk-sync-local.sh rollback .runtime/tyk-sync/rollback/<promotion-id>
```

`tyk-sync-local.sh` reads the Dashboard port, key and organisation from the
bundle's runtime state and runs `tyk-sync.sh` with `TYK_SYNC_BIN` set to
`tyk-sync-docker.sh`, which runs the `tykio/tyk-sync` image pinned by
`TYK_SYNC_VERSION` in `.env`. Inside that container `localhost` is the
container itself, so the Dashboard is reached as
`http://host.docker.internal:<DASHBOARD_PORT>`. `scripts/rehearse-demo.sh`
runs a promotion when the module is selected.

## Environment

Required for promotion and rollback:

- `TYK_SYNC_DASHBOARD_URL`: the target Dashboard. Through the Docker wrapper
  use `http://host.docker.internal:<port>` for a Dashboard on the same host.
- `TYK_SYNC_SECRET`: a Dashboard API key for the target organisation. It
  reaches tyk-sync as `TYKGIT_DB_SECRET`, never on a command line.
- `TYK_SYNC_ORG_ID`: the organisation the read-back must belong to. Tyk Sync
  2.x `publish` and `update` take no `--org`; the Dashboard assigns the key's
  organisation.

Optional:

- `TYK_SYNC_BIN`: the executable, `tyk-sync` by default. Set it to
  `./ci/tyk-sync-docker.sh` to use the pinned image instead.
- `TYK_SYNC_VERSION`: the image tag for `tyk-sync-docker.sh`; read from `.env`,
  then `.env.example`, when unset.
- `TYK_SYNC_PROMOTION_ID`: the immutable promotion ID; a UTC timestamp by
  default.

Expected API IDs are derived from the generated `apis/*.oas.json` definitions.

## How Tyk Sync 2.x is driven

- `tyk-sync dump` writes each Tyk OAS API wrapped as `{"oas": {...}}` beside
  `policy-<id>.json` files and a `.tyk.json` index. The script reads both that
  shape and the bare OAS documents in `apis/`.
- `publish` and `update` read only what a `.tyk.json` index lists, and an OAS
  API only in its `{"oas": ...}` wrapper, so every directory handed to them is
  wrapped and indexed first.
- `publish` only creates (it stops on an API ID that exists) and `update` only
  updates (it refuses one that does not). Each owned API therefore goes to
  `update` when the pre-promotion dump holds it and to `publish` when it does
  not, so promotion is idempotent.
- A rollback re-applies the definitions captured before a promotion. It does
  not delete an API that the promotion created.
- The generator refuses the `versioning` module together with `tyk-sync`: the
  link between a base API and its child version (`x-demo-version-of`) is
  created by `scripts/import-apis.sh`, which tyk-sync does not read.

## CI templates

The GitHub and GitLab files are templates. Copy the GitHub example to
`.github/workflows/tyk-sync.yml`, or include the GitLab example from the root
pipeline. Both use protected CI secrets and manual promotion/rollback gates.
The GitHub job runs the pinned image through `tyk-sync-docker.sh`; the GitLab
job downloads a pinned binary from `TYK_SYNC_DOWNLOAD_URL` (a runner with
Docker can set `TYK_SYNC_BIN: ./ci/tyk-sync-docker.sh` instead).
`CICD-001` ("CI template checks and local promotion") checks both templates
call `tyk-sync.sh` with manual gates, runs the validate step, and passes only
once `SYNC-001` has proved the same promote step against this stack. No CI
runner runs: accepting a real pipeline needs its own run, change and rollback
evidence.
