# Brand kit

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and the default theme 1.20.1

A brand kit is a folder with `brand.json` and the files it names. `scripts/brand_kit.py <kit>`
validates it, fills in the colours it leaves out and prints the contrast of every text and
background pair (exit 0 all pass, 3 a pair fails, 1 the kit is invalid). Two neutral kits ship in
`assets/`: `brand-kit-neutral` (dark navigation, two logo variants, email logo) and
`brand-kit-neutral-light` (white navigation, one logo). Both are placeholders for "Example Corp"
and "Example Labs"; the build warns while `"placeholder": true`.

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | organisation name, used in sign-offs and footers |
| `portal_name` | no (default `<name> Developer Portal`) | the name in browser titles, sign-in pages and emails |
| `theme_name` | no (default from `name`) | 1-50 of `a-z`, `0-9`, `-`; not `default` |
| `nav_label` | no | short text after the logo in the nav, for example "Developers" |
| `lang` | no (default `en`) | `<html lang>` of every layout |
| `portal_url` | no | public address of the Portal; needed for the email logo and the admin link in mails |
| `support_email` | no | replaces the stock `customer@support.com` in mails; without it that sentence is removed |
| `logo.on_light`, `logo.on_dark` | at least one | SVG, PNG, JPG or WebP for light and dark surfaces |
| `logo.email` | no | PNG or JPG for emails |
| `logo.alt` | no (default `name`) | alternative text |
| `logo.height` | no (default 28) | 16 to 44 px; the nav bar is 55 px tall |
| `favicon` | no | SVG, PNG or ICO |
| `images.<slot>` | no | `hero`, `about`, `getting_started`, `auth_background`, `blog`, `error`, `card_placeholder` (see `fonts-and-assets.md`) |
| `colors.primary` | yes | the main brand colour |
| `colors.<role>` | no | `primary_hover`, `primary_pressed`, `on_primary`, `link`, `link_hover`, `nav_bg`, `nav_text`, `dark`, `on_dark`, `footer_bg`, `footer_text`, `footer_link`, `auth_bg`, `text`, `text_muted`, `tint`, `secondary`, `border` |
| `fonts.family` | with `fonts` | the typeface name |
| `fonts.files` | no | `{"400": "...", "500": ..., "600": ..., "700": ...}`, 400 required when any is given |
| `fonts.local` | no | installed font names, a fallback only |
| `footer_text` | no (default `© <name>`) | the line under the footer logo |
| `footer_links` | no | `[{"label", "href"}]`; href a path, an http(s) address or `mailto:` |
| `sources` | no | where each asset and colour came from; keep it filled |
| `placeholder` | no | `true` for a stand-in kit |

Every text value is plain text (no `<`, `>`, `{`, `}`); colours are `#rgb` or `#rrggbb`; files
are relative names inside the kit, at most 5 MB each; SVG files with script, event handlers,
`javascript:` or `foreignObject` are refused. These checks run before anything reaches a template.

## Collecting a real kit

1. Ask the user for the organisation's brand files or brand guidelines. Prefer files they hand
   over to anything found on a website.
2. If the only source is their public website, list what you would download (address, file
   name, size) and ask before downloading. Keep the files unmodified.
3. Take colours from the brand guidelines. When only a logo exists, take the primary colour from
   it and say so in `sources`.
4. Fonts: self-host only files the organisation's licence allows on a web server; otherwise
   leave `fonts.files` out and say the typeface could not be used.
5. Run `python3 scripts/brand_kit.py <kit>`; fix any failing contrast pair by setting the role
   explicitly (for example a darker `link`), not by changing the brand colour itself.
