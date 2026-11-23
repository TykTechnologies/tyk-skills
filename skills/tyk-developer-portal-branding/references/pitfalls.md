# Pitfalls

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`)

Each item applies to Portal 1.20.0, and to 1.20.1 where it says so.

## 1. Pre-flight every archive: a refused upload is kept in storage

What the Portal does: an upload answered 400 with a message that starts `loading theme:`
(invalid manifest, missing `layouts/`, `partials/`, `auth/` or `mailers/`) has already stored
the archive's files. At the next start the Portal loads the stored theme folders, logs
`loading themes: Failed to initialize theme: ...` for that one and stops loading the rest. With
`default` active it starts. With a custom theme active that was stored after the refused one,
the start does not complete: the container restarts in a loop with
`panic: theme '<active theme>' not found` in its log (1.20.0 and 1.20.1).

The safe way to work: run `python3 scripts/build_theme.py --check <archive>.zip` before every
upload (`apply_theme.py` always does). It refuses everything the Portal refuses, plus files
over 5 MB, names outside `a-z`, `0-9`, `-`, and templates that call a function 1.20.1 removed
(item 14).

If an upload was refused anyway (both recoveries apply to 1.20.1):

- Before any restart: `python3 scripts/apply_theme.py rollback` (activates `default`). A restart
  then completes (`Activated theme 'default'`). Upload a valid archive under the **same theme
  name** as the refused one (200, "File uploaded successfully"), then activate the brand theme
  again; the next start loads every theme (for example `4 themes loaded`,
  `Activated theme '<name>'`).
- If the Portal was already restarted and does not start: restore the database backup taken
  before the upload (for example `pg_dump -Fc` before, `pg_restore --clean --if-exists` with the
  Portal stopped); it then starts with the brand theme `Current`. The restore also undoes
  every other change made after the backup, so take one before each theme upload.

## 2. The default theme is not upgraded in place

After moving a Portal from 1.19.0 to 1.20.0 on the same database, it logs `same version of
default theme already exists` and keeps serving the 1.19.0 default theme: downloading `default`
gives the 1.19.0 files (no `views/forbidden.tmpl`). A theme built from it lacks what the new
release added; on 1.20.0 the 403 page then falls back to the not-found view. The same applies
from 1.20.0 to 1.20.1: the stored `default` stays byte for byte the 1.20.0 files, while a fresh
1.20.1 install carries the 1.20.1 default theme. Get the new
release's default theme from a throwaway Portal of that version instead
(`upgrades-and-operations.md`).

## 3. Unpinned API reference renderers can break without any change on your side

The default theme loads Redoc as `https://cdn.redoc.ly/redoc/latest/...`, the AsyncAPI viewer as
`@asyncapi/react-component@latest` and Stoplight Elements with no version. Redoc 3.2.0
was published on 2026-11-19 and `latest` serves it; with the Redoc renderer selected the API
reference page stays empty and the console shows `Uncaught SyntaxError: Cannot use 'import.meta'
outside a module`. The build pins Redoc to v2.5.4, the AsyncAPI viewer to 3.2.3 and Stoplight
Elements to 9.0.29; with those pins all three render on 1.20.0, and Redoc and Stoplight Elements
render on 1.20.1. Re-check the pins when you re-base on a new default
theme.

## 4. One variable paints two places

`--tdp-nav-bg-color` paints the nav and the footer; `--tdp-nav-link-color` paints nav links,
footer text and the product header text; `--tdp-dark` paints the dark bands and breadcrumb text.
Setting one of them by hand recolours places nobody looked at. The build sets each surface by its
own rule (`colours-and-elements.md`).

## 5. A light primary colour makes white labels unreadable

Button labels, the active catalogue toggle and the active step are literal white in the default
theme. With an amber primary (`#F59E0B`) the toggle label has a contrast of 2.15:1. The build picks white
or near-black for every label on the primary.

## 6. Saving General settings with an empty robots.txt opens the Portal to crawlers

A fresh Portal serves `Disallow: /`. Saving the General settings form while its robots box is
empty makes `/robots.txt` one blank line, and the next save of the form is refused with a "must
contain at least one user-agent group" error. `apply_content.py` writes the default rules into
the box when it is empty; check `/robots.txt` after anyone saves that form in the admin
interface.

## 7. A re-upload under the same name reaches only one replica

With two Portal processes on one database, re-uploading the active theme under its own name
changes the pages served by the process that took the upload only; the other keeps the old files
until the active theme changes. See `upgrades-and-operations.md`.

## 8. Lookups that look like success

`GET /portal-api/themes/<unknown>` answers 200 with an empty theme, and every not-found page
answers 200. Look themes up in `GET /themes`, and check pages for the not-found text, as the
scripts do.

## 9. Zip mistakes

The theme must sit at the archive root: zipping the theme's folder instead of its contents gives
`nested theme "<folder>" is not allowed`. The downloaded default theme lists every file twice;
`unzip` asks to overwrite each one, Python's `zipfile` does not. Archive tools on macOS add
`__MACOSX/` entries; the pre-flight refuses them. Let `build_theme.py` write the archive.

## 10. Content outlives the theme

Page titles, content blocks, product copy, menus, mail subjects and robots.txt live in the
database. Switching back to the default theme keeps the branded copy; a new theme alone keeps the
stock copy ("About Tyk Portal" on the About page). Brand both.

## 11. Signed-in checks from a script need a browser-like request

The sign-in form is CSRF-protected: read the token from the page's `csrf-token` meta tag, send
it as `csrf_token`, and send `Sec-Fetch-Site: same-origin`. Without that header the Portal
answers 400 `Bad Request` even with a valid token. `portal_client.PortalSession` does this.

## 12. GraphQL specs uploaded by API have no address

`POST /products/<id>/spec-details/graphql/schema` creates the spec with an empty `SpecAlias`, so
its API reference page has no address until the alias is set with `PUT
/products/<id>/spec-details/<spec id>`.

## 13. An API reference page that shows the not-found page

The Portal fetches a product's specification itself when the docs page is opened. When the
product's `OASUrl` points at `http://localhost:<port>/...`, which the Portal's container cannot
reach, the docs page answers 200 with the not-found view and the Portal logs
`failed to handle product documentation: API/Spec not found`. `verify.py` reports it as "shows
the not-found page". Uploading the document instead (`POST
/portal-api/products/<id>/api-details/<API id>/oas`, multipart `file`) answers 200
`{"status":"ok"}`, replaces `OASUrl` with a stored document, and the page renders. This is
Portal setup, not branding, but it shows up during the branding checks.

## 14. A custom theme that calls `env`, `expandenv` or `getHostByName` breaks on 1.20.1

1.20.1 no longer provides these template functions. The upload still answers 200; every page
whose template calls one answers 500 (also through `?preview-theme=`) and the log shows
`function "env" not defined`. Pre-flight catches it: `build_theme.py --check` lists the
template and the function. Remove the call, or put the value into the template or a content
block. Themes built by this skill from the default theme call none of them.
