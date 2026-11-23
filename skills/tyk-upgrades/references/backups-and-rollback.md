# Backups, database migrations and rollback

Last verified: 2026-11-19 against Tyk Dashboard 5.15.1 and 5.16.0, Tyk Developer Portal 1.19.0, 1.20.0 and 1.20.1, Tyk Gateway 5.15.1 and 5.16.0, Tyk Sync 2.3.0, Redis 7.2 and PostgreSQL 16 on Docker Compose

The upgrade guide asks for backups of the databases and the configuration files, and for a
test restore in a separate environment: https://tyk.io/docs/developer-support/upgrading
("Backups"); the backup guide it links is https://tyk.io/docs/developer-support/faq. It also
says upgrades do not overwrite configuration files.

## What holds what

| Store | Holds | Back up with |
| --- | --- | --- |
| Redis | keys (sessions), quotas and rate-limit counters, the analytics queue | `SAVE`, then copy `dump.rdb` |
| Dashboard database | APIs, policies, users, organisations, Dashboard settings | `pg_dump` (or `mongodump`) |
| Analytics database | request analytics written by Pump | `pg_dump`, if you need the history |
| Portal database | products, plans, catalogues, developers, apps, pages, and the theme when `PORTAL_STORAGE=db` | `pg_dump` (or the MySQL tool) |
| Files | `tyk.conf`, `tyk_analytics.conf`, `pump.conf`, `.env`, Compose or Helm values | copy into version control |

Tyk Sync `dump` exports APIs and policies only (same upgrade guide; commands below).
Keys stay in Redis and the Portal in its own database, so a Tyk Sync dump alone is not a
rollback.

## Commands (Docker Compose)

Run on the stack before the upgrade. Service and database names are the ones in your Compose
file; replace the example names below with yours.

```bash
B=backup-$(date +%Y%m%d-%H%M); mkdir -p "$B" && chmod 700 "$B"
docker compose exec -T tyk-redis redis-cli SAVE                     # -> OK
docker compose cp tyk-redis:/data/dump.rdb "$B/redis-dump.rdb"
for db in tyk_dashboard tyk_analytics portal; do
  docker compose exec -T tyk-postgres pg_dump -U tykuser -Fc "$db" > "$B/$db.dump"
done
cp .env compose.yaml "$B/" && cp -R config "$B/config"              # holds secrets: keep it private
```

`redis-cli CONFIG GET dir` shows where Redis keeps `dump.rdb` (`/data` in the official image).
`SAVE` blocks Redis while it writes; on a large production instance use `BGSAVE` and wait for
`LASTSAVE` to change, or your managed Redis service's snapshot feature.

Tyk Sync, run as a container on the stack's network (Dashboard 5.16.0):

```bash
docker run --rm --network <compose-network> -v "$PWD/$B/sync:/out" tykio/tyk-sync:v2.3.0 \
  dump -d http://tyk-dashboard:3000 -s "$TYK_DASHBOARD_API_KEY" -t /out
# --> Fetched 6 OAS APIs ... Creating spec file in: /out/.tyk.json  Done.
# files: oas-<id>.json per Tyk OAS API, api-<id>.json per classic API, policy-<id>.json
```

The upgrade guide says Tyk Sync also exports MCP APIs (https://tyk.io/docs/developer-support/upgrading);
the example output above comes from a stack without any.

## Restore (the rollback)

```bash
docker compose stop tyk-gateway tyk-pump tyk-dashboard tyk-portal
cp "$B/.env" .env; cp "$B/compose.yaml" compose.yaml; rm -rf config; cp -R "$B/config" config
for db in tyk_dashboard portal tyk_analytics; do
  docker compose exec -T tyk-postgres psql -U tykuser -d postgres -q \
    -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" -c "CREATE DATABASE $db OWNER tykuser"
  docker compose exec -T tyk-postgres pg_restore -U tykuser -d "$db" --no-owner < "$B/$db.dump"
done
docker compose stop tyk-redis            # Redis writes its own dump.rdb on shutdown
docker compose cp "$B/redis-dump.rdb" tyk-redis:/data/dump.rdb
docker compose up -d
```

Drop and re-create each database rather than `pg_restore --clean`: the upgrade adds tables
that are not in the dump, and `--clean` leaves them. `DROP DATABASE ... WITH (FORCE)` needs
PostgreSQL 13 or later.

Restoring a stack on Dashboard 5.16.0 and Portal 1.20.0 to its 5.15.1 / 1.19.0 backup brings
back every API, policy, key, Portal product, plan and catalogue as in the snapshot taken when
the backup was made. The one difference is the Portal provider `Status`, `"Up"` before and
`"Down"` after, when the Portal starts before the Dashboard answers. One synchronisation fixes
it:

```bash
curl -s -X PUT -H "Authorization: $PORTAL_API_TOKEN" "$PORTAL_URL/portal-api/providers/1/synchronize"
# {"message": "Action Synchronize: Executed successfully", "status": "ok"}   then Status "Up"
```

Anything created after the backup (new keys, developers, apps, edits) is lost by a restore.
The Portal upgrade guide says the same:
https://tyk.io/docs/portal/install/upgrade-guide.

## Database migrations

The Dashboard and the Portal change their schema on start; there is no separate migration
command.

- **Dashboard.** Every start logs its data migrations, on 5.15.x and 5.16.0 alike:
  `OAS Extensions migration successful`, `OAS storage migration successful`,
  `API assets orgs migration successful`, `API storage timestamp migration successful`,
  `JWT Source URL to JWKsURIs migration successful`, `OAS dots escaping migration successful`,
  `Orphaned APIs migration successful`. Read them after an upgrade; an error in their place is
  the first thing to look at. From 5.15.1 to 5.16.0 the PostgreSQL schema only grows: the
  `tyk_apis` table goes from 356 to 370 columns and `tyk_assets` from 8 to 9; no table or
  column is removed. 5.15.0 to 5.15.1 changes nothing.
- **Portal.** 1.19.0 to 1.20.0 adds the tables `callouts`, `catalogue_organisations` and
  `mcp_inspector_states` and one column each to `api_details`, `configuration_tables`, `pages`
  and `products`; 1.20.0 to 1.20.1 adds one column to `configuration_tables`. The log shows
  `Version: 1.20.0`, `ensuring default theme in storage` and
  `same version of default theme already exists`. That last line means the Portal keeps the
  default theme it stored at first install: the stored `default` theme is not upgraded in
  place, whether you use it or a custom theme (1.19.0 to 1.20.0 and 1.20.0 to 1.20.1). The
  `tyk-developer-portal-branding` skill gets the new release's default theme and re-bases on
  it. The Portal troubleshooting page says its
  migrations run forward only and down-migrations are not supported:
  https://tyk.io/docs/portal/troubleshooting/upgrade-failures.
- **Pump** writes into the analytics tables and needs nothing (1.17.0 to 1.18.0).

To see the change on your own stack, list the columns before and after:

```bash
docker compose exec -T tyk-postgres psql -U tykuser -d tyk_dashboard -tA -F' ' -c \
  "select table_name, count(*) from information_schema.columns where table_schema='public' group by 1 order by 1"
```

## What can be rolled back, and how

| Component | Roll back by | Notes |
| --- | --- | --- |
| Gateway | the previous image tag | holds no schema; Redis and the Dashboard are untouched by it |
| Pump | the previous image tag | no schema change from 1.17.0 to 1.18.0 |
| Dashboard | restore the database backup, then the previous tag | the restore above |
| Portal | restore the database backup, then the previous tag | the docs require the restore |
| Redis data | restore `dump.rdb` | only together with the databases, so keys and policies agree |

An old binary also starts on a database that the new one has migrated: Dashboard 5.15.1 on the
5.16.0 schema (APIs served, an API edit returns `Api updated`) and Portal 1.19.0 on the 1.20.0
schema (home page 200, products listed). The schema stays migrated, and data
written by features of the newer release is not visible to the older one. The Portal docs warn
that this "may cause startup panics or unpredictable behavior". Use the restore.
