# Neutral Portal Automation

The Portal assets use only the Portal management API. They do not query or
modify the Portal database.

Set these values in the environment before the first run:

- `PORTAL_ADMIN_EMAIL`
- `PORTAL_ADMIN_PASSWORD`
- `PORTAL_ADMIN_FIRST_NAME` (optional)
- `PORTAL_ADMIN_LAST_NAME` (optional)

`configure-portal.sh` bootstraps the first API owner, stores the returned API
token in the bundle's protected runtime environment, creates or updates the
Dashboard provider, synchronizes it, and verifies that at least one product
and plan were imported. It creates a public catalogue when one is absent,
attaches the synchronized products and plans, and creates or updates a
published documentation page with nonempty Markdown content blocks. On an
already bootstrapped Portal, provide `PORTAL_API_TOKEN` if the runtime token
from the original bootstrap is unavailable.

Portal list endpoints return 20 items unless `per_page` is passed, so every
list a lookup depends on is read with `per_page=100`.

## Branding

The Portal is always shown in the brand kit in `brand/`:

- `brand.json`: `name`, `logo`, `logo_on_dark` (navigation bar, footer and
  sign-in pages, which sit on dark backgrounds), `logo_alt`, `nav_suffix`,
  `favicon`, `hero`, `footer_text`, `colors` (`primary`, `primary_dark`,
  `primary_darker`, `primary_light`, `on_primary`, `secondary`, `nav_bg`,
  `nav_link`, `link`, `text`, `hero_bg`, each `#rgb` or `#rrggbb`),
  `font_family`, and `sources` (where each asset and value came from). Assets
  are SVG or PNG files in `brand/`; an SVG may not carry script, event
  handlers, `javascript:` URLs or `foreignObject`.
- `content.json`: `{"pages": {"/": {"title": ..., "blocks": {"<block name>": "<content>"}}, "/about-us": {...}}}`.

`apply-neutral-theme.sh` (run by `configure-portal.sh`) downloads the
`default` theme from the running Portal, adds the overlay stylesheet, and
`build-theme.py --brand brand` replaces every stock touchpoint: the nav brand,
the footer logo and "Powered by" line, the logo and heading on the sign-in
pages, the mascot illustrations, the partner-logo strip, the favicon, and the
colours (as `--tdp-*` CSS variables, plus the Tyk palette colours hard-coded
in the theme CSS). It uploads and activates the `neutral-demo` archive and
verifies its status. Generated theme archives remain under
`.runtime/portal-theme`, private like the rest of `.runtime/`.

`customise-content.sh` (run by `scripts/configure-demo.sh`) rewrites the Home
and About copy from `content.json`, updating only blocks that differ and
reading every one back.

A generated kit is a neutral placeholder (`"placeholder": true`): it keeps stock
Tyk branding off the Portal and is enough for a technical proof. For a branded
presentation, replace it with your organisation's kit. After editing `brand/`, run:

```bash
./portal/apply-neutral-theme.sh
./portal/customise-content.sh
```

Run after the Dashboard and Portal are bootstrapped:

```bash
./portal/configure-portal.sh
```

`PORTAL-UI-001` also needs browser screenshots of the home, catalogue, API
specification and login views, saved as `evidence/screenshots/portal-theme.png`,
`portal-catalogue.png`, `portal-openapi-viewer.png` and `portal-login.png` and each
registered for this stack with
`./scripts/record-screenshot.sh <name> <portal-path> <marker>`.
