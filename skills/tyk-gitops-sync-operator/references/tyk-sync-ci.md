# Tyk Sync in CI: promote on merge, detect drift, roll back

Last verified: 2026-11-17 against Tyk Sync 2.3.0 (`tykio/tyk-sync:v2.3.0`), Tyk Dashboard 5.16.0 and Tyk Gateway 5.16.0

The workflow is `assets/github-actions/tyk-sync.yml`. Its `validate`, `promote` and `drift`
jobs run every `run` step in order with `bash -e -o pipefail`, and work in a Linux runner as a
non-root user (uid 1001) with Docker, against Dashboard 5.16.0. Read `tyk-sync.md` first for
what each command does.

## Repository layout

```text
tyk/                        the directory `tyk-sync dump` wrote, .tyk.json included
ci/check_sync_repo.py       copy of this skill's scripts/check_sync_repo.py
ci/sync_drift.py            copy of this skill's scripts/sync_drift.py
.github/workflows/tyk-sync.yml
```

Commit what `dump` writes, not a hand-trimmed version. To add an API, write the wrapped
document (`{"oas": {...}}` with `x-tyk-api-gateway.info.id`), list it in `.tyk.json`, promote,
then `dump` again and commit the Dashboard's form of it.

## Settings

| Setting | Where | Value |
| --- | --- | --- |
| `TYK_DASHBOARD_URL` | variable of the GitHub environment `production` | Dashboard URL the runner can reach |
| `TYK_DASHBOARD_API_KEY` | secret of the same environment | access key of the pipeline's Dashboard user, with `apis`, `policies`, `api_assets` and `mcp` write and `users` read (`tyk-sync.md`, "Authentication and permissions") |

A GitHub-hosted runner reaches only a Dashboard with a public address. For a private
Dashboard use a self-hosted runner in the same network. Add required reviewers to the
`production` environment to put an approval in front of every promotion.

## What the jobs do

| Job | Runs on | Steps | Fails when |
| --- | --- | --- | --- |
| `validate` | every pull request and push | `python3 ci/check_sync_repo.py tyk` | an API has no ID, a Tyk OAS document is not wrapped, a listed file is missing, a file is not in the index, two APIs share an ID or a listen path |
| `promote` | push to `main`, manual run | `tyk-sync sync`, then `tyk-sync dump` into a temporary directory, then `python3 ci/sync_drift.py tyk <dump>` | Tyk Sync exits 1, or the Dashboard does not match the commit afterwards |
| `drift` | every hour | `tyk-sync dump`, then `sync_drift.py` | someone changed the Dashboard by hand |

The promote step is:

```bash
docker run --rm --user "$(id -u):$(id -g)" -e TYKGIT_DB_SECRET \
  -v "$PWD/$TYK_DIR:/work:ro" "$TYK_SYNC_IMAGE" \
  sync -d "$TYK_DASHBOARD_URL" -p /work
```

`sync` deletes what is not in `tyk/`. When other tools write APIs or policies to the same
organisation, change it to `sync --no-delete` and add `--ignore-extra` to both
`sync_drift.py` calls. API Templates are deleted even then (`tyk-sync.md`, "Deletion").

## The two scripts

`check_sync_repo.py <dir> [--allow-unlisted]`: exit 0 clean, 1 errors, 2 no usable index.

```text
$ python3 ci/check_sync_repo.py tyk
OK: every listed file exists, is identified and has a unique route
```

`sync_drift.py <source-dir> <dump-dir> [--ignore-extra]`: exit 0 in sync, 1 drift, 2 unusable
directory. It ignores what the Dashboard owns (database IDs, organisation ID, timestamps,
generated `servers`, unset defaults) and reports the rest:

```text
$ python3 ci/sync_drift.py tyk "$RUNNER_TEMP/readback"
IN SYNC: 9 object(s) match
```

Run the tests with `python3 -m unittest discover -s scripts -p 'test_*.py'`.

## Promotion, drift and rollback with the workflow

1. **Promote.** `validate` prints `OK: every listed file exists, ...`. In `promote`, the sync
   step prints `SYNC Updated ...` lines, the read-back step writes a dump, and the compare step
   prints `IN SYNC: 9 object(s) match` (for a directory of nine objects). Job exit 0.
2. **Drift.** After a policy rate and an API listen path are changed in the Dashboard by hand,
   the `drift` job fails with exit 1:

   ```text
   CHANGED  api:orders
              .oas.x-tyk-api-gateway.server.listenPath.value: source "/orders/" != Dashboard "/orders-drifted/"
   CHANGED  policy:payments-standard
              .rate: source 60 != Dashboard 999
   DRIFT: the Dashboard does not match the source directory
   ```

3. **Drift correction.** Running `promote` again restores both values (rate 60, listen path
   `/orders/`) and ends `IN SYNC: 9 object(s) match`.
4. **Change by commit.** A commit that sets the policy rate to 120 and adds a path to an API
   is on the Dashboard after `promote`: rate 120 and both paths.
5. **Rollback.** `git revert --no-edit HEAD`, then `promote`: the Dashboard returns rate 60
   and the original path list. The Gateway answers 200 on the API, and a key issued before the
   change still returns 200.
6. **Retire and restore an API.** A commit that removes `oas-inventory.json` and its index
   entry makes `promote` print `SYNC Deleting: inventory` and `IN SYNC: 8 object(s) match`, and
   the Gateway answers 404. After `git revert` and `promote`: `SYNC Created: Inventory`,
   `IN SYNC: 9 object(s) match`, 200 through the Gateway, the same API ID `inventory` with a
   new database ID.

The same sequence works on an empty target Dashboard, starting with `tyk-sync dump` of the
source environment.

Rollback is a revert commit followed by the normal promotion. Nothing else is stored: there is
no release history on the Dashboard side to roll back to.

## Pitfalls

- **Pass `--user "$(id -u):$(id -g)"`.** Without it the `dump` step fails on Linux with
  `permission denied`, because the image's user cannot write into the runner's directory.
- **Drift correction is not continuous.** The Dashboard is put back when the pipeline runs:
  on the next merge, on a manual run, or when the `drift` job is changed to run `promote`. The
  hourly job only reports.
- **A green `sync` is not proof.** `sync` exits 0 after writing; it does not compare. The
  compare step is what proves that the Dashboard matches the commit.
- **Do not run two promotions at once.** The workflow has one `concurrency` group for that.
- **A reverted commit that deleted an API recreates it with the same API ID**, because the ID
  is in the file. Its database ID changes.
- **Keep generated directories out of Git.** A read-back written inside the repository and
  then committed by `git add -A` makes the next `git revert` fail. The workflow writes it to
  `$RUNNER_TEMP`, outside the checkout.
- **The secret is a Dashboard user key.** Rotate it like one. It appears in no file and on no
  command line when passed as `-e TYKGIT_DB_SECRET`.

## Other CI systems

The steps are three shell commands and need only Docker and Python 3. In GitLab CI, Jenkins or
a Makefile run the same `docker run ... sync`, `docker run ... dump` and the two scripts, with
`TYKGIT_DB_SECRET` from the system's secret store.
