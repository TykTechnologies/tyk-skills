# Developer sign-up, access requests, approval and credentials

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 and 1.20.0 with Tyk Dashboard and Gateway 5.16.0

Public pages: https://tyk.io/docs/portal/api-consumer, https://tyk.io/docs/portal/api-owner,
https://tyk.io/docs/portal/request-access, https://tyk.io/docs/portal/developer-app,
https://tyk.io/docs/tyk-stack/tyk-developer-portal/enterprise-developer-portal/api-access/approve-requests

## Sign-up and the General settings

Developers register at `/auth/password/register` (fields `email`, `first`, `last`,
`password1`, `password2`, `invite_id`). The answer page says "You've successfully registered.
You can now login."

A new 1.20.0 Portal has these General settings:

| Setting (`portal_setup.py settings` name) | Default on a new Portal | Effect |
| --- | --- | --- |
| `registration` | true | the Register page accepts sign-ups |
| `auto-approve-users` | **true** | a new developer is active at once |
| `login` | true | developers can sign in |
| `access-flow` | `cart` | `cart` or `direct` request flow |
| `organisation-requests` | true | developers may ask to become an organisation |
| `auto-approve-organisations` | false | |

The public page describes self-registered users as inactive until approved; that is only true
once `auto-approve-users` is off. With it off, the user is created `"Active": false`,
sign-in is refused, and `PUT /portal-api/users/{id} {"Active": true}` approves them (their team
is kept).

These settings have no admin API call. They live in the admin form `/admin/general`:

```bash
export PORTAL_URL PORTAL_API_TOKEN PORTAL_ADMIN_EMAIL PORTAL_ADMIN_PASSWORD
python3 scripts/portal_setup.py settings                                   # read
python3 scripts/portal_setup.py settings --set auto-approve-users=false   # change, then reads back
```

The script signs in as the admin, re-sends every field exactly as the page holds it, changes
only what was asked and fails if the readback differs. Saving that form with the robots.txt
box empty publishes a blank `/robots.txt`, and the next save is refused with a "must contain at
least one user-agent group" error; the script writes `User-agent: *` / `Disallow: /` when the
box is empty. Robots content and mail settings belong to the `tyk-developer-portal-branding` skill.

## The access request (cart flow)

What a developer does, and what `portal_setup.py verify` repeats:

1. Opens the product page `/portal/catalogue-products/<Path>`, picks a plan and adds it to the
   cart (form fields `product_id`, `catalogue_id`, `plan_id`).
2. On `/portal/private/cart/provision` creates an app (`app-action=create`, `client_name`;
   the form also offers an existing app), for a DCR product also picks a client type
   (`app-action-template`), and submits. Answer: 303 to `/portal/private/apps/<id>`.
3. The request appears in `GET /portal-api/access_requests` with `Status` `pending` or
   `approved`.

Approval:

| Plan setting | Result |
| --- | --- |
| `AutoApproveAccessRequests: false` (default) | `pending`; an admin approves |
| `AutoApproveAccessRequests: true` | `approved` at once, credential issued |

```bash
curl -s -X PUT "$PORTAL_URL/portal-api/plans/4" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"AutoApproveAccessRequests":true}'
curl -s -X PUT "$PORTAL_URL/portal-api/access_requests/1/approve" -H "Authorization: $PORTAL_API_TOKEN"
# 200 {"message":"Action approve: Executed successfully","status":"ok"}
curl -s -X PUT "$PORTAL_URL/portal-api/access_requests/3/reject" -H "Authorization: $PORTAL_API_TOKEN"
# 200 {"message":"Action reject: Executed successfully","status":"ok"}; approving it afterwards:
# 422 {"errors":["only pending requests can be approved"],"status":"error"}
```

An admin can also create the app and the request for a developer:

```bash
curl -s -X POST "$PORTAL_URL/portal-api/apps" -H "Authorization: $PORTAL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"Name":"partner-a-orders","UserID":3,"Visibility":"personal","Description":"made by the admin"}'   # 201
curl -s -X PUT "$PORTAL_URL/portal-api/apps/3/provision" -H "Authorization: $PORTAL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"PlanID":3,"ProductIDs":[4]}'
# 200 {"message":"Action provision: Executed successfully","status":"ok"} -> a request, pending or auto-approved by the plan
# without ProductIDs: 422 {"errors":["at least one product is necessary to provision credentials"],...}
```

The admin path does not check the plan's catalogue: a plan from a private catalogue can be
provisioned with a public product.

## The credential

The developer sees it on the app page (`/portal/private/apps/<id>`): **Token** for an auth-token
product; **Client ID** and **Secret** for a DCR product (`dcr-keycloak.md`). Calls:

| Request | Answer |
| --- | --- |
| `GET $GW/<listen path>/...` with `Authorization: <token>` | 200 |
| no credential, auth-token API | 401 `{"error": "Authorization field missing"}` |
| beyond the plan's rate (5 per 60 s) | 429 `{"error": "Rate Limit Exceeded"}` |

Rotating (`PUT .../credentials/{c}/rotate`) and revoking (`DELETE .../credentials/{c}`) are on
the credential (`admin-api.md`). For limits and keys in general, see the
`tyk-rate-limits-keys-policies` skill.

## Driving the Live Portal from a script

The Live Portal forms are CSRF-protected. Two things are needed:

- the `csrf_token` field, from the page's `<meta name="csrf-token" content="...">`;
- a same-origin signal: `Sec-Fetch-Site: same-origin` (browsers send it). Over plain HTTP, an
  `Origin: http://<host>` header alone is refused with 400 `Bad Request`;
  `Origin: https://<host>` passes.

A missing token or signal answers 400 `Bad Request`. `scripts/portal_setup.py` sends both.
Never switch the check off (`PORTAL_DISABLE_CSRF_CHECK`) to make a script work.

## Sessions and the client address

A signed-in session is tied to the client address the Portal saw at sign-in. Behind proxies or
load balancers, keep the forwarded client address stable between requests, or users are signed
out.

## Password reset links

A reset link (`/auth/password/edit?token=<uuid>`, from the forgot-password form) sets a
password once. On 1.20.1 the first form post (`POST /auth/password/update`) answers 303 and the
developer can sign in with the new password; a second post with the same link answers 303 to
`/auth/password/new` with "invalid recovery link". The page still renders the form for a
used link, so only the post shows the result. Ask for a new link from the forgot-password page.

Links mailed before the upgrade to 1.20.1 stop working: a link sent by 1.20.0 and posted after
the upgrade answers 303 to `/auth/password/new`; the new password is not accepted and the old
one still is. A link requested after the upgrade works.
