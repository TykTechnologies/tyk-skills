# Upgrades and operations

Last verified: 2026-11-19 against Tyk Developer Portal 1.19.0, 1.20.0 and 1.20.1 (`tykio/portal:v1.19.0`, `v1.20.0`, `v1.20.1`) on PostgreSQL 16 and SQLite with `PORTAL_STORAGE=db`

## Theme lifecycle

```bash
python3 scripts/apply_theme.py status                       # themes and which is Current
python3 scripts/apply_theme.py apply --kit <kit> --work <dir> --no-activate   # upload only, print a preview link
python3 scripts/apply_theme.py activate <theme>
python3 scripts/apply_theme.py rollback                     # activate default again
python3 scripts/apply_theme.py delete <theme>               # only an inactive theme
```

Activation is immediate on the process that receives it. Re-uploading the active theme under the
same name applies at once on that process: the next page served already carries the new files.

## Preview before activating

`apply --no-activate` prints `<portal>/?preview-theme=<theme>`. Opening it starts a preview for
that browser session; `/?deactivate-preview=true` ends it. On 1.20.0, home,
CMS pages and the catalogue render through the preview theme; the API reference and sign-in
pages render with the active theme's templates but already load the preview theme's
stylesheets and fonts. Judge those pages after activation. Public docs describe the same preview
from the admin Themes screen: https://tyk.io/docs/portal/customization/themes.

## Re-basing on a new release

The build replaces the stock touchpoints of a given default theme and fails when one is missing
(`--allow-missing` builds anyway and lists them). Every touchpoint is present in the 1.19.0, 1.20.0
and 1.20.1 default themes.

After an in-place upgrade the Portal keeps the default theme it stored at first install:
1.19.0 -> 1.20.0 keeps the 1.19.0 files and 1.20.0 -> 1.20.1 keeps the 1.20.0 files, both logging
`same version of default theme already exists` (the stored default carries version `0.0.1` in
both releases). So do not build from the upgraded
Portal's `default`. Get the new release's default theme from a throwaway Portal of that version
with an empty database, which unpacks its own default theme at first start:

```bash
# portal.env (mode 600): PORTAL_LICENSEKEY=<licence>, PORTAL_DATABASE_DIALECT=sqlite3,
# PORTAL_DATABASE_CONNECTIONSTRING=/tmp/portal.db, PORTAL_STORAGE=db, PORTAL_THEMING_PATH=/themes
docker run -d --name portal-theme-source --env-file portal.env -p 127.0.0.1:3101:3001 tykio/portal:v1.20.1
curl -s -X POST http://localhost:3101/portal-api/bootstrap -H 'Content-Type: application/json' \
  -d '{"username":"admin@example.com","password":"<new>","first_name":"Ada","last_name":"Admin"}'
PORTAL_URL=http://localhost:3101 PORTAL_API_TOKEN=<api_token from the reply> \
  python3 scripts/apply_theme.py download --output default-1.20.1.zip
docker rm -f portal-theme-source
```

The log shows `unpacking default theme` / `saved default theme to storage` and bootstrap
answers 200 (1.20.0 and 1.20.1); the downloaded theme is byte for byte the default theme of a
fresh install of that release (270 files on 1.20.0). Then build from the downloaded archive with
a new version number; on 1.20.1 it builds, passes the pre-flight, uploads and activates as
`Current`:

```bash
python3 scripts/apply_theme.py apply --kit <kit> --work <dir> --version 1.1.0 \
  --source default-1.20.1.zip
```

On the upgraded Portal, a theme built from the 1.20.0 default theme makes the 403 page show the
1.20.0 forbidden view, which a 1.19.0-based theme cannot.

A fresh install stores the default theme of its own release: the `default` of a new 1.20.0
Portal carries `views/forbidden.tmpl`, which 1.20.0 added; the `default` of a new 1.20.1 Portal
holds the same 270 theme files as the public default theme at tag 1.20.1.

## Admin API token after the upgrade to 1.20.1

The scripts use an admin API token. Upgraded from 1.20.0 to 1.20.1 on the same database with no
`PORTAL_JWTSIGNINGKEY` set, the Portal logs `No JwtSigningKey configured: generated a new
per-deployment key and stored it in the database`, and the token issued by 1.20.0 answers 403
`{"message": "Forbidden"}`. Sign in to the admin UI and copy the new token
from **My profile** (`/admin/admin_profile`); it answers 200. Changing `PORTAL_JWTSIGNINGKEY`
later has the same effect, and the docs add that it also ends every admin session:
https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration#portal_jwtsigningkey

Password reset links sent before the upgrade stop working: a link mailed by 1.20.0, posted
after the upgrade to 1.20.1, answers 303 to `/auth/password/new`, the new password is not
accepted and the old one still is; a link requested after the upgrade works. Tell developers to request a new link from the forgot-password page.

## More than one Portal process

Two 1.20.0 processes sharing one PostgreSQL database:

| Change made on process A | Seen on process B |
| --- | --- |
| upload a theme under a new name | listed after about 10 s |
| activate a theme | B serves it after about 10 s |
| re-upload the active theme under the same name | **not seen**: B keeps the old files (still after 28 s) until the active theme changes |

So, with more than one replica, give each release its own theme name (for example
`theme_name` `example-corp-1-1-0`) and activate it, or activate another theme and then the
updated one again. The build allows only `a-z`, `0-9` and `-` in theme names.

## Restart behaviour

- A restart keeps the active theme: `3 themes loaded`, `Activated theme 'example-corp'` on
  1.20.0; on 1.20.1 `2 themes loaded`, `Activated theme 'example-corp'`.
- The startup theme setting `PORTAL_THEMING_THEME=default` does not override the theme activated
  through the API after a restart. The docs describe it as the theme used
  "when it starts for the first time"
  (https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration).
- Leftovers of a refused upload can stop the start: `pitfalls.md`, item 1 (activate `default`
  before restarting).
- Right after a restart or bootstrap the admin API answers 404 or resets the connection for a few
  seconds before answering 200.

## Storage

The behaviour on this page applies with `PORTAL_STORAGE=db`. The configuration reference lists
`fs`, `s3` and `db`:
https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration.
