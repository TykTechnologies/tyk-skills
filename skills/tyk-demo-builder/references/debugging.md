# When A Bundle Proof Fails

Last verified: 2026-11-19 against the builder's scripts and Tyk Gateway and Dashboard 5.16.0.

This page covers failures of the bundle itself: its scripts, its generated files and its
proofs. For finding the cause of a Tyk component failure (Gateway, Dashboard, Redis, Pump,
Portal), use the `tyk-troubleshooting` skill, which collects real error bodies, health output and
logs.

## Order

1. `python3 <skill-dir>/scripts/validate_bundle.py .`: generated files, unresolved
   placeholders, module dependencies, private-file permissions, Compose configuration.
2. `docker compose ps` for the project, and `HEALTH-001` in `evidence/results.json`.
3. Call the bundled upstream directly on `UPSTREAM_PORT`, then through the gateway.
4. Read the API back from the Dashboard (`GET /api/apis/oas/<id>`) and compare the stable id,
   listen path, upstream URL, authentication and operation middleware with the file in `apis/`.
5. Reproduce with the smallest `curl` request, on the pinned image tags.

Say what an explanation rests on. "The gateway does X" and "the gateway did X on this image tag
in this bundle" are different claims; only the second belongs in a write-up.

## Bundle-Specific Checks

- A row reads `blocked` with "did not run": `verify-demo.sh` stopped early or a module's
  separate runner (Operator scripts, the AI Studio companion's `verify-ai-studio.sh`) has not
  written its evidence for this stack. `VERIFY-RUN-001` names the line a stopped run reached.
- A script reports that something listed is missing although it is there: look for
  `| grep -q` under `set -o pipefail`. `grep -q` exits at the first match, the writer gets
  SIGPIPE, and the pipeline fails. The bundle's scripts use `grep -F ... >/dev/null`.
- `create-policies.sh` or an import finds no object it created earlier: a Dashboard list was
  read without `?p=-1`, so only the first page was searched. Every list call in the bundle's
  scripts passes `?p=-1`; Portal list calls pass `per_page=100`.
- A configuration step skips work and the stack is half-configured after a purge: an inherited
  value from an old `.runtime/runtime.env` in your shell. `common.sh` clears the runtime keys
  before sourcing; open a fresh terminal and rerun `bootstrap-demo-stack.sh`.
- `SYNC-001` is `blocked` with "does not match apis/": an API file changed after the last
  promotion, or a rollback restored older content. Promote again.
- `PORTAL-UI-001` is `blocked` on screenshots: capture the four pages and register each with
  `scripts/record-screenshot.sh`; a capture whose file, Portal, organisation or brand kit
  changed since registration is not counted.
- A segmented gateway answers 404 for an API: the API lacks the gateway's tag in
  `server.gatewayTags`. Read the gateway's own `/tyk/apis` with the node secret to see what it
  loaded.
- `SECRETS-VAULT-001` fails with an empty credential upstream: Vault runs in dev mode and keeps
  its data in memory, so a restarted Vault has lost the value. Rerun `configure-demo.sh`, which
  seeds it again.
