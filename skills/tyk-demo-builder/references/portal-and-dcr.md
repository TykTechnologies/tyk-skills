# Portal, Branding, Catalogue And DCR Modules

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 with Tyk Dashboard and Gateway 5.16.0.

This page covers what the Portal modules generate and what their proofs assert. For setting up
the Developer Portal in general (provider, products and plans, catalogues, specs, approval,
DCR with an identity provider), use the `tyk-developer-portal-setup` skill; for theming and page
copy, the `tyk-developer-portal-branding` skill. Public overview:
https://tyk.io/docs/portal/overview/intro

## portal (`PORTAL-001`)

`portal/configure-portal.sh` bootstraps the Portal through its admin API, creates or updates the
Dashboard provider, synchronises it, and publishes `<display name> Shared Services` (and, with
`dcr`, `<display name> Self-Service`) to `<display name> Public Catalogue`. Products are found by
the Dashboard `_id` of the policy that defines them (their `ReferenceID`), looked up from the
owned policy's `meta_data.stable_name`, never by display name. Only those products go to the
public catalogue, so a product restricted to an audience is never published publicly.

Each product gets its API specification through `APIDetails[].OASUrl`, asserted on readback. The
specification tab is rendered in the developer's browser, which fetches that URL itself, so it
must be reachable from the browser: the bundled upstream serves the definitions at
`/openapi/<file>`, and `PORTAL_SPEC_BASE_URL` (default `http://localhost:<UPSTREAM_PORT>`) must be
changed when the Portal is shown from another machine.

Every Portal list lookup in the bundle's scripts passes `per_page=100`, because a lookup on the
default page can miss an object on a longer list.

`PORTAL-001` asserts through the Portal API: the provider, the product, the plan, the public
catalogue, the developer user and the published documentation page.

## portal-branding (`PORTAL-UI-001`)

`portal` always brings `portal-branding`: the Portal is never shown in the stock Tyk theme.
`portal/apply-neutral-theme.sh` downloads the default theme from the running Portal, applies the
overlay and the brand kit with `portal/build-theme.py --brand`, uploads and activates the result;
`portal/customise-content.sh` replaces the stock Home and About copy from `content.json` and
reads every block back. Both run on every `configure-demo.sh`.

The brand kit lives in `portal/brand/`: `brand.json` (`name`, `logo`, `logo_on_dark`,
`logo_alt`, `nav_suffix`, `favicon`, `hero`, `footer_text`, `colors`, `font_family`, `sources`)
and `content.json` (`{"pages": {"/": {"title", "blocks"}, "/about-us": {...}}}`). Without
`--brand-dir` the generator writes a neutral placeholder (`"placeholder": true`: a text wordmark
of the display name, a slate palette, an abstract hero, neutral copy). That placeholder is a
valid technical proof that no stock Tyk branding remains. For a branded presentation, collect
your organisation's kit from the user, or, only after the user approves the download, from the
organisation's own public website; never invent a logo, and record each source in `brand.json`.
`brand.json` values reach CSS and templates, so the generator, the validator and
`build-theme.py` accept only `#rgb`/`#rrggbb` colours, a plain font list, and SVG or PNG files
inside the kit; an SVG may not carry `<script`, `on*=` handlers, `javascript:` or
`foreignObject`. `sanitize_bundle.py` reports a non-placeholder kit unless `--own-brand` is
passed, so one organisation's brand never travels in a shared bundle.

`PORTAL-UI-001`:

- fails when the theme is not active, or when `/`, `/about-us`, `/portal/catalogue-products`
  or `/auth/password/login` lacks the brand logo or favicon, still serves a stock Tyk image or
  string, or the overlay lacks `--tdp-primary` for the brand's primary colour;
- is `blocked` until the four browser screenshots
  `evidence/screenshots/portal-{theme,catalogue,openapi-viewer,login}.png` are captured from this
  stack and each is registered with `scripts/record-screenshot.sh <name> <portal-path> <marker>`.
  The script refuses a file that is not a PNG or a page this Portal does not serve with the
  marker now, and records the file's SHA-256, the URL, this stack's organisation and the brand
  kit's digest in `evidence/screenshots/manifest.json`. A screenshot whose file, Portal,
  organisation or brand kit has changed since is not counted;
- passes with either kit, and says which one the Portal shows.

With the placeholder kit every page check passes and the row stays `blocked` ("0/4 browser
screenshots registered") until four captures are registered; it then passes. Pages used: `/` (marker `Example Corp`), `/portal/catalogue-products`, the product page
`/portal/catalogue-products/<product path>` with its Service explorer tab open (marker
`elements-api`, the specification renderer) and `/auth/password/login` (marker `Log in`). A
marker the page did not show was refused, and replacing a registered file turned the row back
to `blocked` ("file changed since it was registered").

## portal-catalogues (`PORTAL-CAT-001`)

`portal/configure-catalogues.sh` creates the organisations `<display name> Internal Teams` and
`<display name> Partners`, one developer in each (created with a password through
`POST /portal-api/users`, so no e-mail flow is needed), and two private catalogues whose
audiences are those organisations' default teams. `<display name> Partner Services` (from its
own policy) and a documentation-only product (`IsDocumentationOnly: true`, with a
`spec-details` `OASUrl`) go to the partner catalogue only. Each organisation's default team is
re-synchronised with its full user list on every run.

On Portal 1.20.1 an audience shows its catalogue only when it carries both the organisation and
the team: a newly created audience reads back with `OrganisationID` 0, and the internal
developer then does not see the internal catalogue. The script therefore corrects the
audience in place (`PUT /portal-api/catalogues/<id>/audiences/<audience id>`) and asserts both
fields on readback.

`PORTAL-CAT-001` maps catalogues to products through the Portal API, then logs in as each
developer (`POST /auth/password/login`) and reads `/portal/catalogue-products`, asserting that
the internal developer sees the internal catalogue and not the partner product, and the partner
developer sees the partner catalogue and product.

## dcr (`DCR-001`, `PORTAL-DCR-AUD-001`)

`DCR-001` drives the bundled identity service's registration endpoint: create, read, update and
delete a client. With `portal`, `portal/configure-dcr.sh` also wires Portal DCR to the identity
service: an OAuth provider with `Type: "Other"` and a `WellKnownURL` on the Compose service
name, a client type (the DCR template), and `DCREnabled` plus `Templates` on
`<display name> Self-Service`. The Portal container calls the `registration_endpoint` it reads
from the discovery document, so that URL must be reachable inside the Compose network; the
identity service builds it from the caller's `Host` for this reason. With
`--identity-mode external` the script is not generated and the row stays `blocked`.

`PORTAL-DCR-AUD-001` checks that the two Portal events which rewrite a JWT API's security scheme
leave its validation intact: a product save with DCR enabled, and the approval of a DCR access
request (`PUT /portal-api/apps/<id>/provision`, then
`PUT /portal-api/access_requests/<id>/approve`). The script restores the JWT API from the
bundle before each event, snapshots `allowedAudiences`, `allowedIssuers` and the JWKS
`cacheTimeout` after it, and re-imports the API afterwards. A passing run reports: "Portal v1.20.1
+ Dashboard v5.16.0: ... allowedAudiences, allowedIssuers and the JWKS cacheTimeout survived
every event (product-save-with-dcr, access-request-approval) and the live definition is
intact".
