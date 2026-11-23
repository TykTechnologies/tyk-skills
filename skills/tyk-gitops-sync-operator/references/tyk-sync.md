# Tyk Sync: commands, files, IDs, deletion, authentication

Last verified: 2026-11-17 against Tyk Sync 2.3.0 (`tykio/tyk-sync:v2.3.0`), Tyk Dashboard 5.16.0 and Tyk Gateway 5.16.0

The commands, output lines and exit codes below are those of the images above. Public pages:
https://tyk.io/docs/api-management/automations/sync (overview),
https://tyk.io/docs/product-stack/tyk-sync/commands (command reference),
https://tyk.io/docs/product-stack/tyk-sync/installing-tyk-sync (install),
https://tyk.io/docs/developer-support/release-notes/sync (release notes).

Tyk Sync is a command line tool. It reads a directory (or a Git repository) and calls the Tyk
Dashboard API. It keeps no state and runs no server: something must run it, by hand or in CI.

## Run it from the container image

```bash
docker run --rm tykio/tyk-sync:v2.3.0 version      # prints v2.3.0, exit 0
```

- The image runs as user `65532`, its entrypoint is `./tyk-sync` and its working directory is
  `/opt/tyk-sync`. Mount the directory you work on and give absolute paths inside the container.
- On Linux the default user cannot write into a directory owned by someone else. A `dump` into
  a directory owned by uid 1001 ends with
  `Error writing file: open /work/api-legacy.json: permission denied`; the same command with
  `--user "$(id -u):$(id -g)"` ends with `Done.` Always pass `--user` in CI.
- The secret can come from the environment variable `TYKGIT_DB_SECRET` instead of `-s`. Pass
  it by name (`-e TYKGIT_DB_SECRET`), so the value never appears on a command line. When both
  are set, `-s` wins.
- `localhost` inside the container is the container. For a Dashboard published on the same
  machine use `http://host.docker.internal:<port>` and add
  `--add-host host.docker.internal:host-gateway` on Linux.
- Packages for Linux and a Homebrew formula (`brew install TykTechnologies/tyk/tyk-sync`, from
  2.2.0) are documented on the install page.

## The four commands

| Command | What it does | Stops when |
| --- | --- | --- |
| `dump -d <url> -t <dir>` | Writes every object of the organisation to `<dir>` and an index, `.tyk.json` | the Dashboard refuses the key |
| `publish -d <url> -p <dir>` | Creates what the directory lists. Creates only | any listed object already exists |
| `update -d <url> -p <dir>` | Updates what the directory lists. Updates only | any listed object does not exist |
| `sync -d <url> -p <dir>` | Creates, updates and **deletes**, so the organisation matches the directory | the Dashboard refuses a change |

Flags printed by `--help` in 2.3.0:

| Flag | dump | publish | update | sync |
| --- | --- | --- | --- | --- |
| `-d, --dashboard` URL | yes | yes | yes | yes |
| `-s, --secret` (or `TYKGIT_DB_SECRET`) | yes | yes | yes | yes |
| `-t, --target` output directory | yes | | | |
| `-p, --path` source directory | | yes | yes | yes |
| `-b, --branch` (default `refs/heads/master`), `-k, --key` SSH key file | | yes | yes | yes |
| `-o, --org` organisation ID | | | | yes |
| `--no-delete` | | | | yes |
| `--apis`, `--oas-apis`, `--policies`, `--templates` (comma-separated IDs) | yes | yes | yes | |
| `--idps` | yes | | | |
| `--allow-duplicate-listenpaths` | | yes | | |
| `--test` | | yes | yes | yes |

`publish -o <org>` fails with `unknown shorthand flag: 'o' in -o`: only `sync` takes an
organisation.

`--test` writes nothing and shows no plan, so it is not a dry run. Against Dashboard 5.16.0,
each command prints the `Fetched ...` lines for the directory and
`Using publisher: Mock Publisher`, exits 0, and leaves the Dashboard's APIs and policies
unchanged:

| Command with `--test` | Directory | Printed after `Using publisher: Mock Publisher` |
| --- | --- | --- |
| `publish` | one new API, one new policy | `Creating API ID: mock (on:  to: )`, `Done` |
| `update` | one changed API, one changed policy | `Updating API ID:  (on:  to: )`, `Done` |
| `sync` | one changed API, one new API, one API left out, one changed and one new policy | `Processing Policies...`, `Processing APIs...`, `Processing Assets...`, `Processing IdPs...` (the order varies between runs) |

Nothing names a policy, the API that `sync` would delete, or the real API IDs. The same `sync`
without `--test` prints `SYNC Deleting: keep-b`, `SYNC Updated Policy: Pol A`,
`SYNC Created Policy: Pol New` and `SYNC Created: New C`. Use `scripts/sync_drift.py` to see
what would change (`tyk-sync-ci.md`).

## What `dump` writes

A `dump` of an organisation holding one classic API, two Tyk OAS APIs, one MCP proxy, one
policy, one API Template, one API Fragment and one identity provider record prints:

```text
--> Fetched 1 Policies
--> Fetched 1 Classic APIs
--> Fetched 3 OAS APIs
--> Fetched 2 Templates
--> Fetched 1 IdPs
> Creating spec file in: /repo/.tyk.json
Done.
```

and writes:

| File | Object | Shape |
| --- | --- | --- |
| `oas-<api id>.json` | Tyk OAS API | `{"oas": {<the Tyk OAS document>}, "hook_references": [], "is_site": false, "sort_by": 0, "user_group_owners": [], "user_owners": []}` |
| `mcp-<api id>.json` | MCP proxy | the same wrapper plus an `api_definition` object whose `application_protocol` is `mcp` |
| `api-<api id>.json` | Tyk Classic API | `{"api_definition": {...}, ...}` |
| `policy-<policy id>.json` | Policy | the policy object, with `_id`, `id` and `org_id` |
| `asset-<asset id>.json` | API Template (`"kind": "oas-template"`) or API Fragment (`"kind": "fragment"`, with `tags`) | the asset object |
| `clientidp-<id>.json` | Identity Provider Registry record | the record |
| `.tyk.json` | Index | below |

```json
{
  "type": "apidef",
  "files": [{"file": "api-legacy.json", "oas": {}}, {"file": "oas-orders.json", "oas": {}}],
  "policies": [{"file": "policy-payments-standard.json"}],
  "assets": [{"file": "asset-cors-allow-list.json"}, {"file": "asset-keyless-template.json"}],
  "client_idps": [{"file": "clientidp-6afc60faa02d690001c1b32a.json"}]
}
```

The dumped Tyk OAS document carries fields that belong to the source environment:
`x-tyk-api-gateway.info.dbId`, `x-tyk-api-gateway.info.orgId` and a `servers` list with the
source Gateway's address. Tyk Sync replaces the organisation and the database ID when it
writes to another Dashboard (see "IDs").

**Not in a dump:** access keys. A key created with `POST /api/keys` before the dump appears in
no file. Tyk Sync has no command for keys, certificates, users or Dashboard settings; the four
commands above are all it has. Create keys through the Dashboard API, the Developer Portal or
the Operator's `TykSession` resource (the `tyk-rate-limits-keys-policies` skill owns keys).

API Fragments need Sync 2.3.0 and Dashboard 5.16.0 (see
https://tyk.io/docs/platform-management/api-fragments). APIs keep their
`{"$ref": "fragment://<id>"}` references in the dump, not a copy of the Fragment.

## Only the index counts

`publish`, `update` and `sync` read `.tyk.json` and nothing else.

- A file that the index does not list is ignored. An extra `oas-stray.json` in the directory
  changes nothing: `Fetched 4 OAS API Definitions`, as before.
- A directory without an index stops the run:
  `Error:  open /repo/nonexistent/.tyk.json: no such file or directory`, exit 1.
- A Tyk OAS API must be wrapped as `{"oas": {...}}`. A bare Tyk OAS document listed in the
  index is counted as a classic API (`Fetched 2 classic API Definitions`) and created as an
  API with an empty name and a random ID, with exit 0. Wrap hand-written documents.

`scripts/check_sync_repo.py` finds all three before a run.

## IDs: how objects are matched

| Object | Identifier that travels | On the target after `publish` |
| --- | --- | --- |
| API | `x-tyk-api-gateway.info.id` (Tyk OAS) or `api_definition.api_id` (classic) | same API ID, new database ID, the target's organisation ID |
| Policy | `id` and `_id` | both unchanged, the target's organisation ID |
| Template, Fragment | `id` | same ID, new `_id`, the target's organisation ID |
| IdP record | `client_idp_id` | unchanged |

Because IDs stay the same, a key issued on the target before a promotion still works after it:
a key on the `payments-standard` policy returns 200 before and after `sync` rewrites the policy.

Rules that follow:

- **Give every API an ID.** A Tyk OAS file without `x-tyk-api-gateway.info.id` is created
  with a generated ID. The next `sync` deletes that API (its ID is not in the directory) and
  then fails to create it again: `Warning: Listen Path Exists`,
  `Error:  SyncAPIs failed: object seems to exist (same ID, API ID or Listen Path), use update()`,
  exit 1. The API is gone until the run after that.
- **Changing an API's ID is a delete and a create.** The same happens in one run: the old ID is
  deleted, the create is refused with `Listen Path Exists`, and a second run creates it. Keys
  tied to the old API ID lose access. Treat IDs as permanent.
- Policies must carry `org_id`; the value is replaced with the target's organisation.
- **An API ID is also a path.** The Gateway serves every API under `/<api id>/` as well as
  under its listen path, on a Dashboard-managed Gateway too: a request to `/<api id>/items`
  reaches the API's authentication, a random path gives 404. With readable IDs, pick ones that
  will not clash with another API's listen path.

## Collisions: `publish` and `update`

```text
$ tyk-sync publish ...          # second run, objects already there
Creating Assets 0: CORS allow list
Warning: Asset ID already exists
--> Status: FAIL, Error:object seems to exist (same ID) use update()
Failed to publish Fragments
Error:  object seems to exist (same ID) use update()          exit 1

$ tyk-sync update ...           # an API that the Dashboard does not hold
--> Status: FAIL, Error:object does not exist, use create()
Failed to publish APIs
Error:  object does not exist, use create()                   exit 1

$ tyk-sync publish ...          # new ID, listen path already used
Warning: Listen Path Exists
--> Status: FAIL, Error:object seems to exist (same ID, API ID or Listen Path), use update()
```

Both stop at the first object that does not fit. A pipeline that must run again and again
therefore uses `sync` (with `--no-delete` if needed), not `publish` plus `update`.

## Deletion: what `sync` removes

`sync` deletes what the Dashboard holds and the directory does not:

```text
Deleting policies: 1
SYNC Deleting Policy: 6afc614c76a6a40001e12241
Deleting: 1
SYNC Deleting: other-team
```

| Object | `sync` | `sync --no-delete` |
| --- | --- | --- |
| API (Tyk OAS, classic, MCP proxy) not in the directory | deleted | kept |
| Policy not in the directory | deleted | kept |
| API Fragment not in the directory | deleted, last | kept |
| API Template not in the directory | deleted | **deleted** |
| Any Template or Fragment, when the index lists no assets at all | kept | kept |

- `--no-delete` goes after the subcommand: `tyk-sync sync --no-delete -d ... -p ...`.
- API Templates are removed even with `--no-delete`. If another team keeps Templates in the
  same organisation, list them in your directory or keep `assets` out of your index.
- A Fragment that an API still uses is not deleted. The run reports
  `fragment \"cors-allow-list\" is referenced by 1 API(s): orders`, finishes the rest and
  exits 1.
- `sync` deletes objects made by any other tool, including an API and a policy that Tyk
  Operator created in the same organisation; the Operator's resources still show
  `Successful`. One organisation, one writer; or `--no-delete` everywhere.
- There is no dry run. Before the first deleting `sync`, `dump` the target and run
  `scripts/sync_drift.py <dir> <dump>`: every `EXTRA` line is an object that `sync` would
  delete.

## Authentication and permissions

Tyk Sync sends the secret as the Dashboard API `Authorization` header. It is the access key of
a Dashboard user (Dashboard, user page, "Tyk Dashboard API Access Credentials"). There is no
separate Tyk Sync licence key; the Dashboard it talks to must be licensed (see
`choosing-a-tool.md`).

| Situation | Output | Exit |
| --- | --- | --- |
| No secret | `Error:  Please set TYKGIT_DB_SECRET, or set the --secret flag, to your dashboard user secret` | 1 |
| Wrong secret | `Error getting users from dashboard: {"Status":"Error","Message":"Not authorised","Meta":null} for http://.../api/users` | 1 |
| Dashboard not reachable | `Get "http://.../api/users?p=-2": dial tcp ...: connect: connection refused` | 1 |
| Neither `-d` nor a target | `sync requires either gateway or dashboard target to be set` | 1 |

Use a user kept for the pipeline, not a person's key and not an admin. Permissions that are
enough for `dump` and `sync` of APIs, MCP proxies, policies, Templates, Fragments and IdP
records:

```json
{"apis": "write", "policies": "write", "api_assets": "write", "mcp": "write", "users": "read"}
```

- Without `mcp: write`, creating an MCP proxy is refused and the run stops part-way:
  `Error:  SyncAPIs failed: API Returned error: {"Status":"Error","Message":"Not authorised - You do not have permission, please contact your administrator","Meta":null} (code: 403)`,
  exit 1. What was written before the error stays (for example the Fragment, the IdP record,
  the policy, the Template and the APIs processed before the MCP proxy). Running `sync` again
  with the permission added creates the rest. "Access to MCP proxy management is controlled by
  the `mcp` permission" (https://tyk.io/docs/ai-management/mcp-gateway/managing-proxies). The
  other four permissions are enough for a run that has no MCP proxy to write.
- A user created through the Dashboard API gets no access key by itself on Dashboard 5.16.0;
  the key comes from the Dashboard admin API when the user is created. Ask the platform owner for
  the pipeline user's key.
- Without `users: read` every step fails with
  `Error getting users from dashboard: ... Not authorised - You do not have permission`: Tyk
  Sync reads the user list to learn its organisation. `sync -o <org id>` avoids that call, so
  `apis`, `policies` and `api_assets` are then enough. `publish`, `update` and `dump` have no
  `-o`.
- Without `api_assets: write` the asset steps fail with `Not authorised` on `/api/assets`.

## Reading from Git instead of a directory

Give a repository URL in place of `-p`:

```bash
tyk-sync sync -d <url> -b refs/heads/main <repository-url>
```

- The default branch is `refs/heads/master`. Against a repository whose branch is `main`, the
  run ends with `Error:  couldn't find remote ref "refs/heads/master"`, exit 1. Pass
  `-b refs/heads/main` (or `-b main`).
- `-p` with a `-b` other than the default is refused, exit 1:
  `sync requires either files or branch to be set, not both`. `-p` with
  `-b refs/heads/master` (the default value) is accepted.
- A local path is not a repository URL for the image: it has no `git` binary
  (`Error:  exec: "git": executable file not found in $PATH`). In CI, check the repository out
  and use `-p`; that also pins the exact commit.
- `-k` takes an SSH private key file for a private repository (see the command reference
  above).

## Output a pipeline can check

- Exit code 0 means every step succeeded; 1 means at least one failed. The last lines name the
  failure (`Error:  ...`).
- `sync` prints `SYNC Created: <name>`, `SYNC Updated OAS API Definition <database id>`,
  `SYNC Updated Classic API Definition <database id>`, `SYNC Updated Policy: <name>`,
  `SYNC Deleting: <api id>` and `SYNC Deleting Policy: <_id>`.
- **The counts are not a drift report.** With nothing changed, a second `sync` still prints
  `SYNC Updated Policy: Payments standard` and
  `SYNC Updated Classic API Definition ...`: policies and classic APIs are written on every
  run. Unchanged Tyk OAS APIs that came from a dump are skipped; a hand-written Tyk OAS file
  (without the fields the Dashboard adds) is written on every run too. To know whether the
  Dashboard matches Git, compare a fresh dump with `scripts/sync_drift.py`
  (`tyk-sync-ci.md`).

## Limitations

- Tyk Sync 2.x is supported only with a licensed Tyk Dashboard: "we will only support use of
  Tyk Sync with licensed Tyk Dashboard"
  (https://tyk.io/docs/developer-support/release-notes/sync) and "Tyk Sync is no longer usable
  with the Open Source (OSS) version of the Tyk Gateway"
  (https://tyk.io/docs/api-management/automations/sync). For the open source Gateway use files
  (`oss-gateway-files.md`). The `-g` flag still parses and prints
  `Flag --gateway has been deprecated`; `sync -g` against an open source Gateway 5.16.0
  crashes with exit 2.
- No dry run, no diff, no validation command.
- No keys, certificates, users, or Developer Portal content.
- One organisation per run: the organisation of the user key (or `-o` for `sync`).
- No templating of values per environment. Use references that each Gateway resolves
  (`environments.md`).
- "Only the most recent Tyk Sync release is supported"
  (https://tyk.io/docs/developer-support/release-notes/sync).
