# Limitations

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0

What branding cannot change on Portal 1.20.1, said plainly. Tell the user which of these apply
before they judge the result.

Paths such as `src/views/home.tmpl` cite the public default theme,
https://github.com/TykTechnologies/portal-default-theme at tag 1.20.1. Nothing needs to be fetched
from there: the same files are in the `default` theme your own Portal serves
(`python3 scripts/apply_theme.py download --output default.zip`).

| Cannot be branded | Why | Detail |
| --- | --- | --- |
| The admin interface (`/admin`) | it is not rendered by the theme | `/admin/general` loads only `/admin/assets/...` stylesheets, no theme stylesheet |
| The browser titles of the sign-in and forgot-password pages | the Portal sets "Developer portal login" and "Password reset"; the build can only add the portal name after them | the build rewrites each layout's `<title>` (`theme-anatomy.md`) |
| The address in email links | not set by the theme | trigger test mails through the Portal's public address and check the link (`emails.md`) |
| The API reference viewers' own look | Stoplight Elements, Redoc, AsyncAPI and GraphiQL style their own content; the theme frames them (nav, header, footer) | the audit lists viewer text apart (`verification.md`) |
| Which renderer shows REST API references | one Portal-wide setting, `PORTAL_DOCRENDERER` (`stoplight` or `redoc`), read at start | See https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration. After a restart with `PORTAL_DOCRENDERER=redoc`, `/portal-api/configs` shows `"ProductDocRenderer": "redoc"` and, on 1.20.1, REST references render in Redoc; AsyncAPI and GraphQL pages ignore it |
| Menus, mail settings, mail subjects, robots.txt by API | no API endpoint; the admin form is the only way | the public OpenAPI file has no such paths; the scripts drive the admin form |
| The side menu of the signed-in pages | fixed in the theme template, not an editable menu | `src/partials/side_menu.tmpl` |
| Fixed wording such as "Browse and discover APIs available in your organization", "Docs", "More info" | fixed in the theme templates; the build changes brand wording only | `src/views/catalogue.tmpl`, `src/partials/catalogue_card.tmpl` |
| A different theme per audience or per page | one theme is active for the whole Portal | See https://tyk.io/docs/portal/customization/themes ("only one of them is active") |

Things this skill does not do, and where they belong:

- Install the Portal, its database or storage: the `tyk-deployment-planner` skill; upgrade it: the
  `tyk-upgrades` skill.
- Create products, plans, catalogues, providers or dynamic client registration: the
  `tyk-developer-portal-setup` skill. The Portal must already run with its products.
- Plan a Portal upgrade: the `tyk-upgrades` skill (re-basing the theme afterwards is here).
- Licensing and what a Portal licence includes: the `tyk-facts` skill.
- A demo stack with a branded Portal: the `tyk-demo-builder` skill.
- Editing theme templates by hand. Every change goes through `build_theme.py` so it can be
  rebuilt on the next default theme.
