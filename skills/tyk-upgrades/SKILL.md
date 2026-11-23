---
name: tyk-upgrades
description: Plan, run and prove a Tyk upgrade - component order, backups, mixed-version windows, migrations, rollback, breaking changes, Helm chart upgrades and a before-and-after snapshot diff of APIs, policies, keys and Portal products. Use when someone asks to upgrade, patch or roll back Tyk Gateway, Dashboard, Pump, Portal, MDCB, Hybrid, Operator or the Helm charts, or what breaks between releases. Not for choosing which version or LTS line to run, which the tyk-facts skill owns.
---

# Tyk Upgrades

An upgrade is safe when you can say what will change, take the stack back if it goes wrong,
and show afterwards that nothing else changed. This skill does those three things. Its
commands and output cover Docker Compose: a patch upgrade (Gateway and Dashboard 5.15.0 to
5.15.1, Portal 1.20.0 to 1.20.1), a minor upgrade (Gateway and Dashboard 5.15.1 to 5.16.0, Pump
1.17.0 to 1.18.0, Portal 1.19.0 to 1.20.0), both mixed-version windows, a full restore back to
5.15.1, and Helm chart 5.4.1 to 5.5.0 on Kubernetes. Hybrid and MDCB upgrades are given from
the public docs, with their links.

The Dashboard, Developer Portal, MDCB and Operator are licensed; the Gateway and Pump are open
source. Licence and packaging questions go to the `tyk-facts` skill.

## Clarify first

Ask only what the request does not answer. Defaults in brackets.

1. Current and target versions of each component? [read the current ones from the stack;
   target is the newest stable release of each line, resolved live]
2. Deployment: Docker Compose, Helm on Kubernetes, Linux packages, Hybrid, MDCB? [Compose]
3. How many Gateways behind a load balancer? [one: plan for a short outage]
4. Is a restored copy of production available to rehearse on? [no: build one from backups]
5. Which databases and versions (PostgreSQL, MongoDB, Redis)? [PostgreSQL and Redis; check
   them against the target's supported list]
6. Custom Go plugins, a custom Portal theme, scripts that call the Portal admin API? [ask; each
   adds a step]

## References

| File | Use it for |
| --- | --- |
| `references/order-and-mixed-versions.md` | Documented order per deployment, a Compose upgrade step by step, both mixed windows, `/hello` versus `/ready` |
| `references/backups-and-rollback.md` | What to back up, exact backup and restore commands, the migrations each upgrade runs, what rolls back |
| `references/breaking-changes-5-15-to-5-16.md` | Every breaking change from 5.15 / 1.19 to 5.16 / 1.20.1, what each does to a running stack, dependency floors |
| `references/helm-and-kubernetes.md` | Chart value paths, tag pinning, `--reuse-values`, API definitions in pods, readiness, rollback |
| `references/verification.md` | The snapshot script, example output, traffic probes, health endpoints, Portal checks |
| `scripts/tyk_state_snapshot.py` | `snapshot`, `diff`, `preflight`; tests in `scripts/test_tyk_state_snapshot.py` |

## Workflow

1. **Read the current versions from the stack.** Set these once; the snapshot script and the
   references use the same names.

   ```bash
   export GW=http://localhost:8080 GW_SECRET=<Gateway secret>
   export DASH=http://localhost:3000 DASHBOARD_API_KEY=<Dashboard user API key>
   export PORTAL_URL=http://localhost:3001 PORTAL_API_TOKEN=<Portal admin API token>
   KEY=<a key with no quota, for traffic probes>
   docker compose images                      # Dashboard, Portal and Pump tags
   curl -s "$GW/hello"                        # {"status":"pass","version":"5.15.1",...}
   ```

2. **Resolve the target versions live**, per release line, with the Docker Hub commands in the
   `tyk-facts` skill (`references/versions-and-images.md`). As of 2026-11-19 the 5.16 line holds
   `v5.16.0` and the Portal 1.20 line `v1.20.0` and `v1.20.1`. Never write a tag from memory.

3. **Read every release note between the two versions**, not only the target's: breaking
   changes ship in patch releases too (Portal 1.20.1). Some opt-out settings are named only in
   the changelog entries, not in the Breaking Changes section, so read both. Check the
   supported database versions: 5.16.0 drops PostgreSQL 13 and 14 (Dashboard) and Redis 6.2
   (Gateway and Dashboard), and Pump 1.18.0 drops MongoDB below 7.0.
   `references/breaking-changes-5-15-to-5-16.md` has the 5.15 to 5.16 list.

4. **Snapshot and preflight the current stack**, with the variables from step 1. The key
   section is optional: with hashed keys it needs `enable_hashed_keys_listing: true` on the
   Gateway, which you can set back after the last snapshot (`references/verification.md`).

   ```bash
   python3 scripts/tyk_state_snapshot.py snapshot --out before.json --label "before"
   python3 scripts/tyk_state_snapshot.py preflight before.json
   ```

   `preflight` lists APIs with IDs the 5.16.0 Dashboard refuses to update, URL rewrites whose
   upstream path changes, file templates to check against `template_path`, and APIs whose
   detailed logs will be redacted. It does not read the Gateway configuration file: check
   `analytics_config.enable_detailed_recording` (detail for every API), `template_path` and
   `apply_strip_listen_path_to_url_rewrite` there yourself. Fix or accept each finding before
   going on.

5. **Back up**: Redis (`SAVE`, copy `dump.rdb`), the Dashboard, analytics and Portal
   databases (`pg_dump -Fc`), and the configuration files. Commands:
   `references/backups-and-rollback.md`. A Tyk Sync dump covers APIs and policies only.

6. **Rehearse on a copy** restored from those backups, with the same steps and checks. The
   upgrade guide asks for a tested restore before production.

7. **Upgrade one component at a time, in the documented order**
   (https://tyk.io/docs/developer-support/upgrading): Portal with or before the Dashboard, then
   Dashboard, Gateway, Pump. With MDCB: control plane (MDCB, Pump, Dashboard, Gateway), then
   each data plane (Go plugins, Pump, Gateway). On Compose, with the tag as a variable in
   `.env`:

   ```bash
   sed -i.bak 's/^DASHBOARD_VERSION=.*/DASHBOARD_VERSION=v5.16.0/' .env
   docker compose pull tyk-dashboard && docker compose up -d tyk-dashboard
   docker compose logs --since 2m tyk-dashboard | grep -i -E 'migration|error'
   ```

   After each component: Gateway `GET /ready` 200, traffic probes, a new snapshot and
   `diff before.json after.json --allow-new-fields`. Stop at the first unexpected difference.
   After a Portal move to 1.20.1, do step 8 first: until then the snapshot records
   `Portal products: HTTP 403 (Forbidden)`.

8. **After the Portal**: on 1.20.1, re-issue the admin API token from the Admin Portal profile
   page (the old one answers 403) and resend pending invitations; check the provider is `"Up"`
   (`PUT /portal-api/providers/{id}/synchronize` if not). The stored default theme is not
   upgraded in place, custom theme or not: the `tyk-developer-portal-branding` skill gets the
   new default and re-bases on it.

9. **On Helm**, pin every image tag in a values file at the subchart path
   (`tyk-gateway.gateway.image.tag`), pass it with `-f`, set readiness to `/ready`, and keep
   API definitions out of the pod: `references/helm-and-kubernetes.md`. Operator CRDs are not
   upgraded by Helm; the `tyk-gitops-sync-operator` skill has the command.

10. **If it goes wrong, restore**: stop the Tyk services, restore the databases and
    `dump.rdb`, put the old tags back, start, snapshot, diff against `before.json`. Commands:
    `references/backups-and-rollback.md`.

11. **Report** the versions before and after, each step's probe and diff result, every
    breaking change and what was done about it, and anything not verified.

## Pitfalls

1. **`/hello` passes before the Gateway serves.** A restarting Gateway answers `hello=200`
   while `ready=503` and the API `404`. Gate traffic and Kubernetes readiness on `/ready`; the
   `tyk-oss` chart's probes default to `/hello`.
2. **A single Gateway drops requests while it restarts**: about 10 s refused, then a 404.
   Zero-downtime upgrades need two or more Gateways behind a load balancer.
3. **The status code hides a behaviour change.** On 5.16.0 a URL rewrite that starts with
   the listen path still answers 200 but the upstream sees `/rewrite/target` instead of
   `/target`. Probe the upstream path, not only the status. Fix the rewrite target; as a
   stop-gap, `apply_strip_listen_path_to_url_rewrite: true` on the Gateway brings `/target`
   back for every API on it.
4. **Unsafe API IDs stop being editable.** On Dashboard 5.16.0 an API with ID
   `example:partner-a` keeps serving, but `PUT` returns `400 Invalid API ID: Allowed
   characters: ...`; `DELETE` still works. `TYK_DB_ALLOWUNSAFEAPIIDS=true` restores editing.
5. **A Portal patch release ends API tokens.** Portal 1.20.1 without `PORTAL_JWTSIGNINGKEY`
   generates a key at first start; the old admin API token gets `403 {"message": "Forbidden"}`.
   A key that is set but weak (under 32 characters, or a value published in Tyk docs) stops
   1.20.1 from starting (release notes); `PORTAL_ALLOWUNSAFEJWTSIGNINGKEY=true` starts it with a warning
   while you rotate the key.
6. **A chart upgrade changes the Tyk version.** Chart 5.5.0 with a values file that does not
   pin the tag moves the Gateway from `v5.15.1` to the chart's `v5.13.5`. A tag at the wrong
   path (`gateway.image.tag` instead of `tyk-gateway.gateway.image.tag`) is ignored silently.
7. **APIs created through the open source Gateway API live in the pod.** After `helm upgrade`
   replaces the pod, `GET /tyk/apis` returns `[]`.
8. **Restore order for Redis.** Redis writes `dump.rdb` when it stops, so copy the backup in
   after stopping it, then start. Re-create databases rather than `pg_restore --clean`, which
   leaves tables the upgrade added.
9. **The Portal provider shows `"Down"` after a restore or restart order change** until the next
   sync; synchronise it explicitly.
10. **Snapshot gaps.** Key counting needs `enable_hashed_keys_listing` on the Gateway;
    `GET /api/keys/` returns the HTML login page with 200, not JSON.
11. **Probe keys on a quota.** A probe loop can exhaust a policy quota (429, then 403) and look
    like an outage. Use a key without a quota for continuous probes.

## Limitations

- No down-migrations: the Portal docs say so, and the Dashboard migrates its schema on start.
  Rolling back means restoring the backups and losing what changed since they were taken.
- An old Dashboard (5.15.1) and Portal (1.19.0) start on databases the newer release has
  migrated, but this is not a supported rollback; do not plan on it.
- The snapshot covers APIs, policies, key hashes, Gateway-loaded APIs and Portal products,
  plans, catalogues, providers, organisations and user and app counts. It does not cover
  Dashboard users and settings, certificates, webhooks, MCP proxies, Portal pages and themes,
  or analytics.
- The steps for Hybrid, MDCB, Tyk Cloud, Linux packages and the Dashboard, Pump and Portal
  Helm charts come from the public docs; the references give commands and output for Docker
  Compose and the Gateway chart.
- The breaking-change list covers 5.15 / 1.19 to 5.16 / 1.20.1 only. For other versions, build
  the same table from the release notes and prove the changes that touch your APIs.

## Verification checklist

Run before reporting success.

- [ ] Each component reports the target version (`/hello`, `/ready`, image tags, Portal log).
- [ ] `diff before.json after.json --allow-new-fields` prints `no differences`, or each
      difference is explained and expected.
- [ ] Gateway `GET /ready` 200 and `loaded_api_ids` equals the active Dashboard APIs.
- [ ] Traffic probes give the same status and upstream path as before, apart from documented
      breaking changes you accepted.
- [ ] Every `preflight` finding is fixed or accepted in writing.
- [ ] Dashboard and Portal logs show no migration errors.
- [ ] Portal provider `"Up"`, admin API token re-issued if needed, theme renders and was
      re-based on the new release's default theme.
- [ ] Backups taken before the upgrade are kept, and a restore was rehearsed.

## Hand-offs

- Versions, LTS lines, release dates, licences: the `tyk-facts` skill.
- Installing a new environment or production deployment: the `tyk-deployment-planner` skill.
- A rehearsal stack from scratch: the `tyk-demo-builder` skill.
- Operator and Tyk Sync upgrades and CRDs: the `tyk-gitops-sync-operator` skill.
- Portal theme re-basing after an upgrade: the `tyk-developer-portal-branding` skill.
- Rebuilding Go plugins for the new version: the `tyk-custom-plugins` skill.
- Moving keys and policies to a re-created API: the `tyk-rate-limits-keys-policies` skill.
- Anything the upgrade broke that the checks above do not explain: the `tyk-troubleshooting`
  skill.
