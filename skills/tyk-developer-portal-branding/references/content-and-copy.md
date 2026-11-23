# Content and copy

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and the public default theme at tag 1.20.1

Words live in two places: the theme (fixed text in templates, changed by the build) and the
Portal's database (page titles, content blocks, product and plan copy, menus, General settings,
changed by `scripts/apply_content.py`). A new theme does not change the database: with only the
theme applied, the About page still reads "About Tyk Portal" and "Tyk Pronunciation". Public docs: https://tyk.io/docs/portal/customization/pages and
https://tyk.io/docs/portal/customization/menus.

Paths such as `src/views/home.tmpl` cite the public default theme,
https://github.com/TykTechnologies/portal-default-theme at tag 1.20.1. Nothing needs to be fetched
from there: the same files are in the `default` theme your own Portal serves
(`python3 scripts/apply_theme.py download --output default.zip`).

## The content file

`assets/content.example.json` is a complete example for "Example Corp Developers". Start from
the Portal's current state:

```bash
python3 scripts/apply_content.py --export current.json     # what the Portal holds now
python3 scripts/apply_content.py --content content.json --dry-run
python3 scripts/apply_content.py --content content.json
```

Pages are matched by path, products, plans and catalogues by their current `Name` (the internal
name; the visitor sees `DisplayName`). Every write is read back; the script exits 1 when a value
did not persist. A second run with the same file changes nothing and reports, for example,
`0 change(s), 61 already as wanted` (1.20.0 and 1.20.1).

## Home page blocks (template `home`)

A fresh 1.20.0 or 1.20.1 Portal seeds 40 blocks on `/`. Set all of them, or the stock copy shows
through. In the page order of `src/views/home.tmpl`:

| Section | Blocks | Notes |
| --- | --- | --- |
| Header band | page `title`; `HeaderDescription`; `HeaderButtonLabel`, `HeaderButtonLink` | the button is always drawn |
| Use cases | `UseCasesTitle` (section shown only when set); `UseCasesDescription`; `UseCase1Title`..`UseCase3Title`; `UseCase1Description`..`UseCase3Description` | three fixed icons, recoloured by the build |
| Steps | `FlipFlopRightImageTitle1` (section guard); `FlipFlopRightImageSubTitle1`; `FlipFlopRightImageDescription1` (an `<ol>` of `<li class="step">` items); `FlipFlopRightImageInnerTitle1..6`, `FlipFlopRightImageInnerDescription1..6` | the inner card is hidden at 767 px and below |
| About | `FlipFlopLeftImageTitle1` (guard); `FlipFlopLeftImageDescription1`; `FlipFlopLeftImageButtonLabel1`, `FlipFlopLeftImageButtonLink1` | picture from `images.about` |
| Featured | `FeaturedProductsTitle`, `FeaturedProductsDescription`, `FeaturedProductsLinkLabel`, `FeaturedProductsLink` | shown only when a product has `featured: true` |
| Partners | `PartnersTitle`, `PartnersDescription` | the build removes the section; set both to "" |

Also seeded and not read by the 1.20.1 home template: `BlogsTitle`, `BlogsDescription`,
`BlogsLink`, `BlogsLinkLabel`. The example file sets them so an export carries no stock copy.
The image blocks `BackgroundImage` and `FlipFlopLeftImage1` are printed as text in the picture
columns (`theme-anatomy.md`).

## About page (template `flip_flop`, `/about-us`)

Seven seeded blocks: `HeaderDescription`, `NoImageSectionTitle`, `NoImageSectionDescription`,
`FlipFlopRightImageTitle`, `FlipFlopRightImageDescription`, `FlipFlopLeftImageTitle`,
`FlipFlopLeftImageDescription`. The template also reads `HeaderButtonLabel`/`HeaderButtonLink`
and `FlipFlopLeftImageButtonLabel`/`FlipFlopLeftImageButtonLink`; each button shows only when its
label is set (`src/views/flip_flop.tmpl`).

New pages: `"create": true` with `"template": "flip_flop"` (the example adds `/terms`). The
template must exist in the active theme.

## Products, plans, catalogues

| Field in the content file | Portal field | Shown |
| --- | --- | --- |
| `display_name` | `DisplayName` | catalogue card, product page, API reference title |
| `description` | `Description` | card and product header |
| `content` | `Content` (HTML) | product page body |
| `featured` | `Feature` | Home "Featured" section |
| `tags` | product tags | catalogue filter |
| `logo`, `preview` | product logo and card picture (PNG) | product header, catalogue card |

The Portal renames each uploaded picture, so the script uploads one only when the product has
none, or when `"replace_images": true`. Plans: `display_name`, `description`. Catalogues: `name`.
Products and plans themselves (rate limits, access, auth) are Portal setup, not branding.

## Menus

`menus.Primary` (nav) and `menus.Footer` (footer columns), each a list of `{title, path}`. There
is no menu API; the script edits the admin form. Sub-menus (a second
level) are set in the admin interface. The nav shows two levels; the footer shows each top item
as a column heading with its children as links (`src/partials/top_nav.tmpl`,
`src/partials/footer.tmpl`).

## Fixed copy in the theme

The build replaces the brand wording in templates: nav brand text, footer line, sign-in titles
("Log in to your <portal name> account", "Log in to <portal name>", "Create account"), mail
wording. Other fixed text stays as written in the default theme, for example the catalogue's
"Browse and discover APIs available in your organization", card buttons "Docs" and "More info",
the side menu ("Dashboard", "My apps", "Users", "Profile settings", "Log Out") and server flash
messages (`src/views/catalogue.tmpl`, `src/partials/catalogue_card.tmpl`,
`src/partials/side_menu.tmpl`). Changing those means editing the theme templates, which this
skill does not do by hand.

## robots.txt

`/robots.txt` serves the General settings text. A fresh Portal has the field empty and serves
`User-agent: *` / `Disallow: /`. Saving the General settings form with the field still empty
makes `/robots.txt` a single blank line, which lets every crawler in.
`apply_content.py` writes `User-agent: *` / `Disallow: /` into the field whenever it saves that
form and finds it empty; put the organisation's own rules there if the Portal should be indexed.
