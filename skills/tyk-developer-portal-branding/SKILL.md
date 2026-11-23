---
name: tyk-developer-portal-branding
description: Brand a running Tyk Developer Portal: build a theme from the Portal's default theme and a brand kit, apply it through the admin API, set page copy, menus, email subjects and mail settings, and verify every page type. Use when someone asks to brand, theme, re-skin, white-label or restyle the Tyk Developer Portal, its sign-in pages or emails, set up its mail server, or re-base a custom theme after a Portal upgrade. Not for products, plans, catalogues, sign-up or credentials, which the tyk-developer-portal-setup skill owns.
---

# Tyk Developer Portal Branding

Turns a stock Tyk Developer Portal into one that carries an organisation's brand on every page
and email, without hand-editing theme files. The workflow applies to Portal 1.20.1 and 1.20.0, and the build
also works from the 1.19.0 default theme; the references say where behaviour differs between
releases. Public docs: https://tyk.io/docs/portal/customization/themes.

The Portal must already be installed and running with its products (installation is the
`tyk-deployment` skill; products, plans and catalogues are the `tyk-developer-portal-setup`
skill). The Developer Portal is a licensed component; for what a licence includes, use the
`tyk-facts` skill.

Scripts are Python 3 standard library only and live in `scripts/`; every command below runs
from this skill's folder. `screenshots.py` also needs a local Chrome, Chromium or Edge.

## Clarify first

Ask these, and use the default when the user has no preference:

| Question | Default |
| --- | --- |
| Where do the brand assets come from: files they hand over, brand guidelines, or their public website? | files they hand over; nothing is downloaded without asking |
| Dark or light navigation bar? | dark (`colors.nav_bg` defaults to a dark shade of the primary) |
| How exact: logo and colours only, or also fonts, pictures and copy? | logo, colours, favicon, pictures and copy; fonts only when they can self-host the files |
| Which pages matter most (public catalogue only, or the signed-in area too)? | every page type, signed out and signed in |
| Emails: brand them, and is there a test mail catcher? | brand them; test with a catcher before real SMTP |
| Which Portal: URL, version, an admin API token, a developer test account? | ask; never print tokens or passwords |

## Workflow

Set the connection once (values from the user; do not echo them):

```bash
export PORTAL_URL=https://portal.example.com PORTAL_API_TOKEN=... \
  PORTAL_ADMIN_EMAIL=... PORTAL_ADMIN_PASSWORD=... PORTAL_DEV_EMAIL=... PORTAL_DEV_PASSWORD=...
cd <this skill's folder>
```

1. **Collect the brand kit.** Copy `assets/brand-kit-neutral` (dark nav) or
   `assets/brand-kit-neutral-light` (light nav) to the user's workspace, replace every file with
   the organisation's own, unmodified files, and fill `brand.json` (`references/brand-kit.md`).
   Before downloading anything from a website, list the files and ask. Record each source in
   `sources` and remove `"placeholder": true`.
2. **Check the kit.** `python3 scripts/brand_kit.py <kit>` prints the completed palette and the contrast
   of every pair; exit 3 means a pair is under WCAG AA. Fix it by setting the role (for example
   a darker `link`), never by altering the brand colour.
3. **Take the baseline.** `python3 scripts/verify.py --portal-name "<portal name>" --theme <theme_name>`
   must fail on the stock Portal (it reports stock wording, images and titles). Optionally
   `python3 scripts/screenshots.py --output <dir>/before`.
4. **Get the default theme** of the running version: `python3 scripts/apply_theme.py download --output
   <dir>/default.zip`. On a Portal that was upgraded in place this is the old release's theme;
   download the new one from a throwaway Portal of the new version and pass it as `--source`
   (`references/upgrades-and-operations.md`).
5. **Build and pre-flight.** Never edit theme files by hand:

   ```bash
   python3 scripts/build_theme.py --source <dir>/default.zip --kit <kit> --output <dir>/<theme>.zip --version 1.0.0
   python3 scripts/build_theme.py --check <dir>/<theme>.zip
   ```

   The build fails when an expected stock touchpoint is missing or stock branding remains; read
   its warnings (missing pictures, single logo variant, no font files).
6. **Upload, preview, activate.** `python3 scripts/apply_theme.py apply --kit <kit> --work <dir>
   --no-activate` repeats steps 4 and 5 itself (add `--source <default theme>` on an upgraded
   Portal), uploads only after the pre-flight passes and prints a `?preview-theme=` link; look at
   it, then `python3 scripts/apply_theme.py activate <theme_name>`. (`apply` without `--no-activate`
   activates too and reads the theme back as `Current`.) With more than one Portal replica, use
   a new `theme_name` per release (`references/upgrades-and-operations.md`).
7. **Write the copy.** `python3 scripts/apply_content.py --export <dir>/current.json`, copy
   `assets/content.example.json` (and `assets/images/`, which it names) to the work folder as
   `content.json`, rewrite it for the organisation (every Home block, About, products and plans
   by their `Name` in the export, catalogues, menus, email), then `python3 scripts/apply_content.py
   --content <dir>/content.json --dry-run`, then the same without `--dry-run`. It reads every write back and is safe
   to run twice (`references/content-and-copy.md`).
8. **Emails.** The build frames and rewords the mail templates; the `email` section of the
   content file sets sender, admin address, SMTP server and the 13 subjects (set
   `PORTAL_SMTP_PASSWORD` whenever it changes the SMTP host, port or user). Trigger a password
   reset through the Portal's public address with a mail catcher and check the message and
   that its link uses that address (`references/emails.md`).
9. **Verify.** Run `scripts/verify.py` again (must pass), `python3 scripts/screenshots.py --output <dir>/after`
   (must pass; look at the images), open each API reference renderer in use, restart the Portal
   once and confirm the theme is still `Current` (`references/verification.md`).
10. **Report** what was branded, what was checked (commands and results), which placeholder
    assets remain, and what cannot be branded (`references/limitations.md`). Leave the work
    folder with the kit, `build-report.json`, archives and screenshots with the user.

Rollback at any time: `python3 scripts/apply_theme.py rollback` activates `default`; content stays.

## References

| Topic | File |
| --- | --- |
| Theme folders, layouts, which file serves each page, titles, status codes | `references/theme-anatomy.md` |
| Admin API calls, responses, pagination, auth; what has no API | `references/admin-api.md` |
| `brand.json` fields and how to collect a real kit | `references/brand-kit.md` |
| CSS variables, what each paints, hard-coded colours, light navigation | `references/colours-and-elements.md` |
| Fonts, pictures, favicon, file limits, third-party hosts | `references/fonts-and-assets.md` |
| Home and About blocks, products, plans, menus, robots.txt | `references/content-and-copy.md` |
| Email bodies, frame, subjects, testing with a catcher | `references/emails.md` |
| Known failures and their fixes | `references/pitfalls.md` |
| What cannot be branded | `references/limitations.md` |
| Checks and the checklist before reporting | `references/verification.md` |
| Preview, re-basing after an upgrade, replicas, restarts | `references/upgrades-and-operations.md` |

## Pitfalls

- A refused upload (`loading theme: ...`) is kept in storage, and a restart with a custom theme
  active then does not complete. Always pre-flight. If one was refused anyway: before any restart run
  `apply_theme.py rollback`, upload a valid archive under the refused name, then activate the
  brand theme again; if the Portal was already restarted and does not start, restore the
  database backup taken before the upload (`references/pitfalls.md`).
- After an in-place upgrade the stored `default` theme is the old release's (also from
  1.20.0 to 1.20.1). Build from the new release's default theme, taken from a throwaway Portal
  of that version. The upgrade to 1.20.1 also voids the admin API token when no
  `PORTAL_JWTSIGNINGKEY` is set; copy a new one from **My profile** in the admin UI.
- 1.20.1 removed the template functions `env`, `expandenv` and `getHostByName`: a theme that
  calls one uploads, then its pages answer 500. The pre-flight refuses it.
- The default theme loads Redoc, the AsyncAPI viewer and Stoplight Elements at their newest
  versions; Redoc 3.2.0 (published 2026-11-19) leaves the API reference page empty. The build pins them.
- `--tdp-nav-bg-color` paints nav and footer; `--tdp-dark` paints bands and breadcrumb text.
  Let the build set each surface.
- Saving General settings with an empty robots box makes `/robots.txt` blank; the scripts fill it.
- The not-found page and `GET /portal-api/themes/<unknown>` both answer 200.
- Content lives in the database: a theme alone leaves "About Tyk Portal" in place.

## Limitations

The admin interface, the Portal-set titles of the sign-in and forgot-password pages, the address
in email links, the API reference viewers' own styling, and the fixed side menu cannot be branded.
The REST renderer (Stoplight or Redoc) is one Portal-wide setting. Menus, mail settings and
robots.txt have no API (the scripts use the admin forms). One theme is active for the whole
Portal. Details and evidence: `references/limitations.md`.

## Verification checklist

- [ ] Baseline `verify.py` failed on the stock Portal; now it exits 0.
- [ ] `screenshots.py` exits 0; the screenshots were looked at for every page type at 1440, 768
      and 390 px, signed out and signed in.
- [ ] A reset email arrived with the branded subject, sender, frame and logo, and its link uses
      the Portal's public address.
- [ ] Each API reference renderer in use shows its content.
- [ ] After a restart the brand theme is still `Current`.
- [ ] `/robots.txt` holds the intended rules.
- [ ] No placeholder asset remains, or the report names each one.

## Hand-offs

- Installing or scaling the Portal: the `tyk-deployment` skill.
- Products, plans, catalogues, sign-up, access requests and credentials: the
  `tyk-developer-portal-setup` skill.
- Planning a Portal upgrade: the `tyk-upgrades` skill (re-basing the theme afterwards is here).
- A Portal that does not start or answers errors this skill does not explain: the
  `tyk-troubleshooting` skill.
- Licensing and what the Developer Portal includes: the `tyk-facts` skill.
- A demo stack with a branded Portal: the `tyk-demo-builder` skill.
