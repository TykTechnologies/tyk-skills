# Verification

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0

Do not report the Portal as branded until every check below passes. Run the HTTP check on the
stock Portal first: it must report findings there (the baseline), or it is not looking at the
right things.

## Accounts the checks need

- `PORTAL_URL`, `PORTAL_API_TOKEN`: the admin API.
- `PORTAL_DEV_EMAIL`, `PORTAL_DEV_PASSWORD`: a developer account (role `consumer-admin`) for the
  signed-in pages. Without it those pages are "not checked" and the scripts exit 1.
- Optional `PORTAL_MEMBER_EMAIL`, `PORTAL_MEMBER_PASSWORD`: a `consumer-team-member`, to see the
  403 page.

Create test accounts in a test Portal only, and never print their passwords.

## 1. HTTP check, no browser

```bash
python3 scripts/verify.py --portal-name "Example Corp Developers" --theme example-corp
```

Checks the active theme, `/robots.txt`, and for every page type (home, CMS pages, catalogue,
each product and API reference page, blog, sign-in, sign-up, forgot and reset password, not-found,
dashboard, apps, profile, analytics, users, organisation, cart, and the 403 page): status code,
stock wording and stock images, tracking snippets, favicon link, brand stylesheet, browser title.

On the stock theme it exits 1 with findings on every page (stock wording, stock images, no
favicon, no brand stylesheet, titles without the portal name, "the default theme is active"):
for example 139 findings over 20 pages on 1.20.0 and 151 over 19 pages on 1.20.1. After branding
it exits 0 with 0 findings (for example 26 pages on 1.20.0, 21 on 1.20.1). The page count follows the Portal's products, API types and
test accounts.

## 2. Screenshots and rendered contrast

```bash
python3 scripts/screenshots.py --output shots/after
```

Needs a local Chrome, Chromium or Edge (set `CHROME` to its binary if it is elsewhere). Saves
`<page>-<width>.png` at 1440, 768 and 390 px and `audit.json`: rendered text under WCAG AA
contrast against what is painted behind it, stock wording and images, images that failed to
load, sideways overflow, fonts used, and the third-party hosts each page contacted. Exit 1 on
any finding. A branded Portal with the dark neutral kit has 0 findings on 1.20.0 and 1.20.1, with
Stoplight Elements and with Redoc.

Look at the screenshots too. The audit cannot judge a cropped logo, a picture that clashes, or
copy that reads badly, and it does not catch text running past the edge of its card: at 390 px
the sign-in heading runs past its card on the stock and on the branded theme (1.20.0 and 1.20.1). Look at the 390 px sign-in screenshots.

## 3. Emails

Trigger a password reset through the Portal's public address with a mail catcher configured
(`emails.md`). Check subject, sender, frame, logo address, the text part, and that the link uses
the public address.

## 4. Each API reference renderer the Portal uses

The page list includes one page per API type found on the Portal. If REST references use Redoc
(`PORTAL_DOCRENDERER=redoc`), open one: the viewer must show the operations, not an empty area.

## 5. Restart

Restart the Portal once after the final upload and confirm it comes back with the brand theme
active (`apply_theme.py status`): this catches leftovers of a refused upload (`pitfalls.md`).
The log shows `3 themes loaded` (1.20.0) or `2 themes loaded` (1.20.1), followed by
`Activated theme 'example-corp'`.

## Checklist to report

- [ ] `verify.py` failed on the stock Portal (baseline) and passes now.
- [ ] `screenshots.py` passes; screenshots looked at for every page type at three widths.
- [ ] A reset email arrived framed, with the right subject, sender and logo, and a link to the
      Portal's public address.
- [ ] Every API reference renderer in use shows content.
- [ ] The Portal restarted with the brand theme active.
- [ ] `/robots.txt` holds the intended rules.
- [ ] The report names what was branded, what was checked, and what cannot be branded
      (`limitations.md`), and lists any placeholder asset still in use.
