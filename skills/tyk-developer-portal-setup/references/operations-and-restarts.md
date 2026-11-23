# What a restart, a key change and a reset keep

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0 (`tykio/portal:v1.20.0`) with Tyk Dashboard and Gateway 5.16.0

Public page: https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration.
Installing and scaling the Portal: the `tyk-deployment-planner` skill; upgrading it: the `tyk-upgrades` skill.

## Where the state lives

Everything this skill configures is in the Portal database: providers, products, plans,
catalogues, organisations, teams, users, apps, access requests, credentials, OAuth providers,
client types, SSO profiles, General settings, uploaded specifications (with
`PORTAL_STORAGE=db`) and sessions. The Dashboard holds the policies, keys and the Identity
Provider Registry; the identity provider holds the OAuth clients. The table below applies with
PostgreSQL storage and `PORTAL_STORAGE=db`.

## What each event keeps

| Event | Kept | Lost |
| --- | --- | --- |
| Portal container recreated (same database, same environment plus new variables) | every object above (`portal_setup.py inventory` is identical before and after on 1.20.1; on 1.20.0 also signed-in developer sessions), the admin API token | nothing |
| `PORTAL_JWTSIGNINGKEY` set to a new value, then restart | all data | every admin API token (403 `{"message": "Forbidden"}`) and every admin session (configuration page); on 1.20.0 also every admin and developer session (302 to `/auth/password/login`) |
| `PORTAL_JWTSIGNINGKEY` removed again, then restart | all data; tokens issued before the key was set work again (the stored key is used) | tokens issued under the key that was removed |
| `PORTAL_JWTSIGNINGKEY` set to a value under 32 characters | | the Portal does not start (see below) |
| upgrade 1.20.0 -> 1.20.1 on the same database, no `PORTAL_JWTSIGNINGKEY` | all data | every admin API token issued by 1.20.0 (403 `{"message": "Forbidden"}`), admin sessions (configuration page), password reset links mailed by 1.20.0 (`developers-and-access.md`) |
| `docker compose down -v` (database volume removed) | nothing | everything; bootstrap again |

## The signing key

The admin API tokens and sessions are signed with `PORTAL_JWTSIGNINGKEY`. On 1.20.1 (see also the public configuration page):

- Not set: at the first start the Portal generates a key, stores it in its database and logs
  `No JwtSigningKey configured: generated a new per-deployment key and stored it in the database`.
  Later starts log `No JwtSigningKey configured; using the per-deployment key stored in the
  database`. Every instance on the same database uses the same key.
- Set: the value you set is used and is not written to the database.
- Set to a value shorter than 32 characters, or to a value published in Tyk repositories or
  documentation: the Portal stops at start with `reading all settings: JwtSigningKey is shorter
  than 32 characters: set TYK_PORTAL_JWTSIGNINGKEY to a strong random secret of at least 32
  characters ...` and the container restarts in a loop. `PORTAL_ALLOWUNSAFEJWTSIGNINGKEY=true`
  lets it start anyway (configuration page); do not use it.

After any key change or the upgrade from 1.20.0, sign in to the admin UI and copy the new token
from **My profile** (`/admin/admin_profile`; the token shown there answers 200). To manage
the key yourself, set a random value of at least 32 characters (for example `openssl rand -base64
32`), the same on every Portal instance. Docs:
https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration#portal_jwtsigningkey

## Settings that need a restart

| Variable | What it changes |
| --- | --- |
| `TYK_PORTAL_ENABLEIDPREGISTRY=true` | DCR writes to the Dashboard's Identity Provider Registry (`dcr-keycloak.md`) |
| `PORTAL_TIB_ENABLED=true`, `PORTAL_API_SECRET`, `TYK_IB_SESSION_SECRET` | developer SSO (`sso.md`) |
| `PORTAL_DOCRENDERER=redoc` | OpenAPI pages rendered with Redoc instead of Stoplight Elements |
| `PORTAL_REFRESHINTERVAL` | minutes between automatic synchronisations (default 10, configuration page) |
| `PORTAL_JWTSIGNINGKEY` | the admin token and session signing key (above) |

## A health check

`GET /live` and `GET /ready` answer 200 `{"code":"OK","message":"Success"}` once the Portal is
up. Before bootstrap, and for a few seconds after it, the admin API answers 404; wait for
`GET /portal-api/providers` to answer 200 with the token.
