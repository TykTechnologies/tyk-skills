# Fonts and assets

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0, and the public default theme at tag 1.20.1

Paths such as `src/views/home.tmpl` cite the public default theme,
https://github.com/TykTechnologies/portal-default-theme at tag 1.20.1. Nothing needs to be fetched
from there: the same files are in the `default` theme your own Portal serves
(`python3 scripts/apply_theme.py download --output default.zip`).

## The brand kit is the only source of brand files

Use the organisation's own files, unmodified: logo (an SVG or PNG for light surfaces and, if they
have one, for dark surfaces), a favicon, the typeface files their licence lets them self-host,
pictures they own. Ask before downloading anything from a website, and record where each file
came from in `brand.json` `sources`. Never redraw, trace or recolour a logo; when only one logo
variant exists the build puts it on a white plate instead. The format of the kit is in
`brand-kit.md`.

## Fonts

The default theme names one font family per weight (`Inter-Regular`, `Inter-Medium`,
`Inter-SemiBold`, `Inter-Bold`, `OpenSans-Regular`, `OpenSans-Semibold`, `OpenSans-Bold`,
`Smoolthan-Regular`, `Smoolthan-Semibold`, `Smoolthan-Bold`) and uses those names directly in
53 rules across the stylesheets (`src/assets/stylesheets/fonts.css` and the rules that name
them). Changing `body { font-family }` alone does not reach them.

The build rewrites `fonts.css` so each of those family names is served by the brand's own files
(`fonts.files` in the kit, `.woff2`, `.woff`, `.ttf` or `.otf`, weights 400, 500, 600 and 700,
400 required; the nearest weight fills a missing one). The icon font `tykon` is kept: the theme's
icons are glyphs of it.

With a two-file kit (400 and 700) on 1.20.0, the build reports `typeface: 10`, and a browser on
the previewed theme downloads `/assets/fonts/brand/<file>-400.ttf` and `-700.ttf` and no stock
font file.

`fonts.local` names fonts installed on visitors' machines (`local("...")`). That is a fallback,
not branding: most visitors do not have the font. The build warns when the kit has no font files.

What fonts do not reach: the third-party API reference viewers (Stoplight Elements, Redoc,
AsyncAPI, GraphiQL) bring their own typography, and the admin interface is not themed.

## Images in the theme

| Kit slot | Replaces | Shown on |
| --- | --- | --- |
| `logo.on_dark` / `logo.on_light` | stock nav text, `dev-portal-logo.svg` (footer), `new-logo.svg` (sign-in pages) | every page |
| `favicon` | nothing (the stock theme has no favicon link) | every page |
| `images.hero` | `tyk-portal-silhouette.png` | Home and flip-flop page headers |
| `images.about` | `about-us.png` | Home About section, flip-flop page |
| `images.getting_started` | `request-access.png` (removed when not supplied) | Home steps card |
| `images.auth_background` | `login-background.jpg` (replaced by a plain colour when not supplied) | sign-in pages |
| `images.blog` | `blog.svg` | blog header |
| `images.error` | `not-found.svg` | not-found and 403 pages; without it they keep the stock illustration, recoloured (the build warns) |
| `images.card_placeholder` | `card-placeholder.svg` | products without a preview picture; without it the build draws a tinted plate |

The six sample partner logos on Home are removed with their section. The use-case icons, the
blog illustration and the not-found illustration are recoloured from the primary colour.
On 1.20.0, after the build, no page type shows a stock image in its `<img>` elements (screenshot
audit) or names one in its HTML (`verify.py`).

## Limits that apply to files

- Each file at most 5 MB, the archive at most 32 MB by default (`PORTAL_MAX_UPLOAD_SIZE`)
  (see https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration).
  A larger archive is refused with `File size exceed the maximum content file size limit:
  32.00 MB`. The build and `brand_kit.py` refuse any file over 5 MB.
- SVG files in the kit are refused when they carry script, event handlers, `javascript:` URLs
  or `foreignObject`: they are served from the Portal's own origin.
- Product logos and catalogue pictures are uploaded through the API, not the theme: PNG is
  accepted, SVG is refused with 400 `unsupported image format: .svg`.
- Email clients need a raster logo at an absolute address: `logo.email` (PNG or JPG) is shown in
  emails only when the kit also has `portal_url` (see `emails.md`).

## Third-party hosts

The default theme loads scripts, styles and fonts from public CDNs: `unpkg.com`,
`cdn.redoc.ly`, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `code.jquery.com`,
`stackpath.bootstrapcdn.com`; the screenshot audit lists the hosts each page contacts. The build keeps these hosts; it pins the three renderers (see `pitfalls.md`).

The two main layouts also carry a Google Tag Manager snippet that loads
`www.googletagmanager.com/gtm.js` with a fixed container ID, printed only when the template
function `IsFullstoryEnabled` is true (`src/layouts/portal_layout.tmpl` and
`src/layouts/portal_layout_private.tmpl`). The build removes both snippets: its report shows
`tag manager snippet removed: 2`, and the branded home page has no `googletagmanager`.
