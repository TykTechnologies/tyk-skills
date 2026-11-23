# Colours and elements

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`, dark kit) and 1.20.0 (dark and light kits), and the public default theme at tag 1.20.1

Which colour paints what, the traps in the default theme, and how the build handles them. File
and line references are to the stylesheets under `assets/stylesheets/` of the 1.20.1 default theme
(https://github.com/TykTechnologies/portal-default-theme, tag 1.20.1, `src/`; the same
files are in the `default` theme your Portal serves). With the rules the build writes, the dark and
the light neutral kits leave no text under WCAG AA contrast outside the API reference viewers, on
every page type of 1.20.0 (26) and 1.20.1 (21) at 1440, 768 and 390 px.

## How the build applies colour

It does not edit the stock stylesheets. It writes one file, `assets/stylesheets/brand.css`, and
links it right after `main.css` in every layout, so its rules win at equal specificity. The file
is generated from `brand.json`; never edit it by hand (a rebuild overwrites it).

## The variables and what each one paints

The default theme defines about 113 custom properties in `main.css` (`:root`, lines 33-170).
These are the ones that carry brand colour, with the surfaces they reach:

| Variable (stock value) | Paints | Trap |
| --- | --- | --- |
| `--tdp-primary` (`#335FFD`) | links, primary buttons, nav hover underline, active tabs, date picker, toggles | none |
| `--tdp-brand-dark` (`#2A4DD0`) via `--border-brand-default` | primary button border **and** hover fill | set with the primary |
| `--tdp-brand-darker` (`#223EAC`) | primary button hover border | |
| `--tdp-primary-dark` (`#1A3088`) | tab hover fill, toast text, analytics chips | |
| `--tdp-nav-bg-color` (a three-stop gradient) | the nav bar **and the footer** | one value for both; the build sets nav and footer by their own rules |
| `--tdp-nav-link-color` | nav links, footer text, product header text, active tab text, modal bar | the build keeps it the on-dark text colour and sets nav and footer text separately |
| `--tdp-dark` (`#041837`) | the dark bands on Home and pages **and** breadcrumb and table text | the build keeps it a text colour and paints the bands by class |
| `--tdp-link-color` | every plain link | |
| `--link-default` (`#00A6ED`) | links on the sign-in pages | stock value is 2.74:1 on white |
| `--tdp-link-dark` (`#025281`) | blog card background | |
| `--tdp-light-purple` (`#0C95CF`) | product name in the catalogue card footer | |
| `--tdp-tab-active` (`#EFF3FF`) | side-menu highlight, inactive tabs, disabled inputs | |
| `--surface-brand-hover` (`#DEE5FE`) | outline button hover background | |
| `--tdp-text-color-extra-dark`, `--tdp-text-color` | body and secondary text | |
| `--tdb-text-color-gray`, `--tdb-text-color-gray-lighter` (`#808080`, `#868686`) | grey labels on analytics and forms | 3.95:1 and 3.64:1 on white, whatever the brand |
| `--tdp-login-form` | the panel behind the sign-in form | stock value is translucent; links on it reach only 3.55:1 |

## Colours no variable reaches

These are literals in the stylesheets or templates; a variable change cannot reach them, so
`brand.css` overrides the rule itself:

| Where | Stock | What the build does |
| --- | --- | --- |
| product and API reference header, `.api-detail-background-color` (`pages/api-product.css:351`) | `#181834` | paints it with `dark` |
| button labels `.btn`, `.btn-primary`, `.portal-primary` (`components/buttons.css:21,61,93,105`) | `white` | `on_primary`: white or near-black, whichever reads on the primary |
| text on the dark bands (`main.css:194,199`) | `white` | `on_dark` |
| side-menu links (`common/navigation.css:153,159`) | `#43435B` | `text_muted` |
| sign-in page background (`pages/login.css:2`) | photo `login-background.jpg` | `auth_bg` colour, or `images.auth_background` |
| older sign-in fragments | inline gradient `#36364C` to `#505071` | inline style removed |
| catalogue view toggle, active state | white label on the primary | `on_primary` (white on a light primary can fall to 2.15:1) |
| inline `code` | `#E83E8C` (3.82:1 on white) | `#A61E63` |
| stock illustrations (use-case icons, not-found, blog header) | teal and lavender fills | recoloured from the primary |

## Contrast

`scripts/colour.py` computes WCAG 2.2 contrast (https://www.w3.org/TR/WCAG22/#dfn-contrast-ratio):
4.5:1 for text, 3:1 for large text and interface parts. `brand_kit.py <kit>` prints every
text/background pair of the completed palette and exits 3 when one fails. The palette fills
roles the kit leaves out: `on_primary` and `nav_text` pick white or near-black, `link` darkens the
primary until it reaches 4.5:1 on white, `primary_hover` and `primary_pressed` darken the primary
by 14 and 28 per cent.

The screenshot audit then measures the rendered pages: each text node against the background
actually painted behind it. Text inside the third-party API reference viewers is listed apart
(`viewer_contrast` in `audit.json`); those viewers bring their own colours (for example the
Stoplight table of contents shows `#05B870` on `#EBEEF5`, 2.23:1, on the stock theme).

## Light navigation recipe

Set `colors.nav_bg` to a light colour (the light neutral kit uses `#FFFFFF`). The build then:

- paints the nav with that colour, adds a 1 px bottom border in `border`;
- sets nav text to near-black (`nav_text`), and the hover colour to the primary only when the
  primary reaches 3:1 on the bar, otherwise to the text colour;
- inverts the stock white toggler and cart icons (`filter: invert(1)`);
- keeps the footer dark (`footer_bg` defaults to `dark`) unless the kit sets it;
- shows the logo made for light surfaces in the nav; when the kit has only one logo variant, it is
  placed on a white plate wherever the surface does not suit it (the build warns).

With the light kit (`#F59E0B` primary, white nav), every page passes the contrast audit; the
toggle-label rule is what makes the catalogue view toggle pass.

## Elements and where to change them

| Element | Changed by |
| --- | --- |
| nav brand (stock: the text "DEVELOPER PORTAL") | build: logo plus optional `nav_label` |
| footer logo and "Powered by" line | build: logo, `footer_text`, `footer_links` |
| sign-in page logo and wording | build: logo; "Log in to your <portal name> account" |
| nav and footer menu items | content: `menus` (admin form) |
| home hero and About pictures | kit `images.hero`, `images.about` |
| sample partner logos on Home | removed by the build |
| favicon | kit `favicon` (stock theme has none; `/favicon.ico` answers 200 with an empty body) |
| product card placeholder | kit `images.card_placeholder`, else a tinted plate drawn by the build |
| product logo and catalogue card picture | content: product `logo` and `preview` (PNG) |
| side menu of the private pages | fixed in the theme; only its colours change |
