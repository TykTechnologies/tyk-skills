# Theme anatomy

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0, and the public default theme at tag 1.20.1

What a Developer Portal theme is made of, and which file paints which page. Public docs:
https://tyk.io/docs/portal/customization/themes. Paths such as `src/views/home.tmpl` cite the public default theme,
https://github.com/TykTechnologies/portal-default-theme at tag 1.20.1. Nothing needs to be fetched
from there: the same files are in the `default` theme your own Portal serves
(`python3 scripts/apply_theme.py download --output default.zip`).

## Folders

| Folder | Holds | Needed for upload |
| --- | --- | --- |
| `theme.json` | `name`, `version`, `author`, `templates` (the page templates admins can pick) | yes, at the archive root |
| `layouts/` | full HTML documents (`<html>`, `<head>`, `<title>`, nav, footer) | yes |
| `views/` | the body of each page type | yes (the manifest's templates point here) |
| `partials/` | snippets: `top_nav`, `footer`, `side_menu`, `catalogue_card`, `product_doc_hero` and more | yes |
| `auth/` | sign-in, forgot-password and set-new-password fragments | yes |
| `mailers/` | email bodies and the email layout | yes |
| `assets/` | stylesheets, scripts, images, fonts, served under `/assets/` | no |

On Portal 1.20.0, an archive without `layouts/`, `partials/`, `auth/` or
`mailers/` is refused with `loading theme: Failed to initialize theme: registering path
/themes/<name>/<folder>: not found`; an archive without `assets/` is accepted.

## Layouts and the pages that use them

The layouts are in `src/layouts/`; this mapping holds on 1.20.0 and 1.20.1.

| Layout | Pages | Nav | Footer |
| --- | --- | --- | --- |
| `portal_layout` | home and other CMS pages, catalogue, product detail, cart and checkout, sign-in, sign-up, forgot and reset password, not-found, 403 | yes, except on sign-in pages | yes, except on sign-in pages |
| `portal_layout_private` | dashboard, profile, analytics, users, organisation (side menu shown except under `/portal/private/apps`) | yes | yes |
| `blog_layout` | `/blog` and posts | yes | yes |
| `product_layout_doc_stoplight` | API reference of a REST product (default renderer) | yes | yes |
| `product_layout_doc_redoc` | API reference when the renderer setting is `redoc` | yes | yes |
| `product_layout_doc_asyncapi` | API reference of an AsyncAPI document | yes | yes |
| `product_layout_doc_graphql` | GraphQL playground | yes | **no footer** |
| `product_layout_doc_mcp` | MCP product reference | yes | yes |

Notes:

- Sign-in pages (`/auth/password/*`) render inside `portal_layout` with no nav and no footer
  (no `<nav>` in the HTML). The footer partial also loads jQuery, Bootstrap's script and
  the theme's `main.js` (`src/partials/footer.tmpl`), so those pages run without them.
- The GraphQL layout has no footer.
- The MCP layout exists in the 1.20.1 default theme (`src/layouts/`).

## Which file serves each sign-in URL

On 1.20.0:

| URL | File |
| --- | --- |
| `/auth/password/login` | `auth/password/login.tmpl` |
| `/auth/password/register` | `layouts/auth/register.tmpl` (shows "Create an account") |
| `/auth/password/new` (forgot password) | `auth/password/new.tmpl` |
| `/auth/password/edit?token=...` (set a new password, also the invite landing) | `auth/password/edit.tmpl`; any token value renders the form, no token redirects (303) to `/auth/password/new`. On 1.20.1 a link sets a password once: a second post of the form with the same token goes back to `/auth/password/new` with "invalid recovery link" |

The theme carries more sign-in fragments in an older design (`layouts/auth/login.tmpl`,
`reset.tmpl`, `forgot_password.tmpl`, `generic_invite.tmpl`) with an inline grey gradient
background. The build removes that inline background so every sign-in page
takes the brand rule.

## Page routes worth knowing

| Route | Template | Status code |
| --- | --- | --- |
| `/` and every CMS page path | the page's template (`home`, `flip_flop`, ...) | 200 |
| `/portal/catalogue-products` | `catalogue` | 200 |
| `/portal/catalogue-products/<product path>` | `portal_product_detail` | 200 |
| `/portal/catalogue-products/<product path>/<API id or spec alias>/docs` | one of the doc layouts | 200 |
| `/portal/private/dashboard` (title "Dashboard"), `/apps`, `/profile`, `/analytics`, `/users`, `/organisation`, `/cart/provision` | private views | 200 signed in, 302 to sign-in otherwise |
| an unknown path | `not_found` | **200** |
| a page the role may not open (for example `/portal/private/users/invite` for a `consumer-team-member`) | `forbidden` | 403 |
| `/robots.txt` | text from General settings | 200 |

These hold on 1.20.0 and 1.20.1. The not-found page answers 200, so status codes alone do
not show a missing page; the verify step looks for the not-found text instead.

## Browser titles

The stock layouts print "Developer Portal" when a page has no title, the product's internal name
on API reference pages, and nothing on the blog (`src/layouts/*.tmpl`). The
sign-in and forgot-password titles come from the Portal itself: "Developer portal login" and
"Password reset". The build rewrites every layout's `<title>` to
`<page title> - <portal name>`; the two server titles keep their words with the portal name
after them, for example `Developer portal login - Example Corp Developers`.

## How a template reads content

- `{{ .page.Title }}` is the page title; `{{ .blocks.<Name>.Content }}` a content block; long
  blocks test `.blocks.<Name>.MarkdownEnabled` and print `MarkdownContent` instead
  (`src/views/home.tmpl`).
- Menus come from `GetMenus.Primary` (nav, two levels) and `GetMenus.Footer` (footer columns)
  (`src/partials/top_nav.tmpl`, `src/partials/footer.tmpl`).
- The side menu of the private pages is fixed in `partials/side_menu.tmpl` (Dashboard, My apps,
  Users, Profile settings, Log Out); it is not a menu admins can edit.
- Image blocks (`BackgroundImage`, `FlipFlopLeftImage1`) are printed as text inside the picture
  column of the stock home page; the visible pictures are fixed files in the template
  (`src/views/home.tmpl`). The build replaces those fixed files with the
  kit's `hero` and `about` images.
- The two main layouts carry a Google Tag Manager snippet, which the build removes
  (`fonts-and-assets.md`, "Third-party hosts").

## Template functions

Theme templates are Go templates with the Sprig functions. From 1.20.1 the Portal no longer
provides `env`, `expandenv` and `getHostByName` to templates. On 1.20.1 a
theme whose `views/home.tmpl` calls `{{ env "HOME" }}` uploads (200) and its preview of `/`
answers 500, the log showing `function "env" not defined`. The default theme calls none of them.
`python3 scripts/build_theme.py --check <archive>.zip` refuses an archive whose templates call
one, so a custom theme carried over from an older release is caught before upload.
