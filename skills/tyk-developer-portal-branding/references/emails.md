# Emails

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0, with Mailpit `axllent/mailpit:v1.34.0` as the SMTP catcher

Public docs: https://tyk.io/docs/portal/customization/email-notifications.

Paths such as `src/views/home.tmpl` cite the public default theme,
https://github.com/TykTechnologies/portal-default-theme at tag 1.20.1. Nothing needs to be fetched
from there: the same files are in the `default` theme your own Portal serves
(`python3 scripts/apply_theme.py download --output default.zip`).

## Where each part comes from

| Part | Source | Changed by |
| --- | --- | --- |
| body (HTML and text) | the active theme, `mailers/*.html.tmpl`, `*.text.tmpl` | the build |
| frame (logo band, footer) | `mailers/layouts/emails.html.tmpl`; stock content is only `{{ yield }}` | the build |
| reset-password and invite bodies | `mailers/auth/reset_password.*`, `targeted_invite.*`, sent without the layout | the build adds the frame to these files |
| subject of each notification | General settings (13 subject fields) | `apply_content.py`, section `email.subjects` |
| sender, admin address, SMTP host, port, user | General settings | `apply_content.py`, section `email` (password from `PORTAL_SMTP_PASSWORD`) |
| the address in mail links | not set by the theme | not brandable |

The template files are under `src/mailers/` in the default theme (tag 1.20.1).

## What the build changes

- A frame on every HTML mail: a dark band with the logo (or the brand name as text), the body,
  a footer line with `footer_text` and the support address. Email clients need inline styles,
  absolute image addresses and a raster logo, so the logo appears only when the kit has both
  `logo.email` (PNG or JPG) and `portal_url`; the image address is
  `<portal_url>/assets/images/brand/<file>`, which must be reachable from the reader's mail client.
- Wording: "Welcome to the Developer portal" -> "Welcome to <portal name>"; the placeholder
  `https://portal.com/admin` -> `<portal_url>/admin`; `customer@support.com` -> `support_email`
  (the sentence is dropped when the kit has none); sign-offs "The Team" / "The Teams" / "The team"
  -> "The <name> team"; the typos "ahs" and "thr" in the stock "submitted" mails.
- Plain-text parts: four stock text templates contain `<p>`, `<strong>` and `<br/>`, which arrive
  as literal tags in the text part of the reset mail. The build removes them.

## Subjects

Thirteen subjects are fields of General settings and are empty on a fresh Portal. The content
file keys and the form fields they set:

| Key | Field |
| --- | --- |
| `reset_password` | `EmailResetSubject` |
| `access_approved` / `access_rejected` | `EmailApproveSubject` / `EmailRejectSubject` |
| `signup_request` | `EmailSignupSubject` |
| `invite_admin` / `invite_user` | `EmailInviteSubject` / `EmailInviteConsumerSubject` |
| `account_activated` / `account_deactivated` | `EmailActivateSubject` / `EmailDeactivateSubject` |
| `organisation_request` / `organisation_approved` / `organisation_rejected` | `EmailOrganisationRequestSubject` / `EmailOrganisationApproveSubject` / `EmailOrganisationRejectSubject` |
| `welcome_admin` / `welcome_developer` | `EmailWelcomeAdminSubject` / `EmailWelcomeDevSubject` |

## What arrives

With SMTP host `mailpit`, port 1025, From `developers@example.com` and the subjects from the
example file, these mails arrive (rows marked 1.20.0 are for that release):

| Trigger | Subject | Notes |
| --- | --- | --- |
| forgot-password form (`POST /auth/password/recover`) | "Reset your Example Corp Developers password" | framed, logo from `https://developers.example.com/...`, link `<Portal address>/auth/password/edit?token=<uuid>`, no HTML tags in the text part (1.20.0 and 1.20.1); on 1.20.1 the link sets a password once |
| `POST /portal-api/users` with an active user | "Welcome to Example Corp Developers" | framed, branded wording, support address (1.20.0 and 1.20.1) |
| admin account welcome | "Welcome to Example Corp Developers" | admin link `https://developers.example.com/admin` from `portal_url` (1.20.0) |
| account activated | "Your Example Corp Developers account is active" | (1.20.0) |

## How to test mails

Point General settings at a mail catcher on the Portal's network (for example Mailpit,
`docker run axllent/mailpit`, SMTP on port 1025, web UI and API on 8025), trigger a reset from
the forgot-password page, then read `GET /api/v1/messages` and `GET /api/v1/message/<id>` from
the catcher. Trigger test mails through the Portal's public address and check that the link
uses it. Check also: subject, sender, the frame (`role="presentation"` in the HTML part), the
logo address, and no HTML tags in the text part. Restore the real SMTP settings afterwards.

## Limits

- The admin interface itself and its own emails are not themed.
- The SMTP password is write-only: the General settings form always shows it empty, and a save
  that leaves it empty keeps the stored one (on 1.20.1, re-saving the form to change a sign-up
  setting leaves the stored password in place). `apply_content.py` sets it from
  `PORTAL_SMTP_PASSWORD`, never reads it and never prints it.
- With a password stored, changing the SMTP host, port or user without entering the password
  again is refused: on 1.20.1 the form answers 422 with "Re-enter the SMTP password after
  changing the host, port, or user." Set `PORTAL_SMTP_PASSWORD` whenever the content file
  changes `smtp_host`, `smtp_port` or `smtp_user`; without it `apply_content.py` exits 1 and
  says so.
