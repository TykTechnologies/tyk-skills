# Developer single sign-on (OpenID Connect)

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.0 (embedded Tyk Identity Broker) and Keycloak 26.8.1

Public page: https://tyk.io/docs/tyk-stack/tyk-developer-portal/enterprise-developer-portal/managing-access/enable-sso

This page covers developers signing in to the Live Portal with an OpenID Connect provider
(Keycloak), with new accounts created on first sign-in and placed in a team.

## Portal settings (restart)

| Variable | Value |
| --- | --- |
| `PORTAL_TIB_ENABLED` | `true` (embedded broker; no separate TIB service) |
| `PORTAL_API_SECRET` | a strong random value; the profile's `IdentityHandlerConfig.DashboardCredential` must equal it |
| `TYK_IB_SESSION_SECRET` | a strong random value; signs the broker's redirect session cookie |

## Identity provider

A confidential OpenID Connect client (here `developer-portal`) with redirect URI
`<portal>/tib/auth/<profile id>/openid-connect/callback` (a wildcard `<portal>/tib/auth/*` also
works), standard flow on.

## Profile

```json
{
  "ID": "developer-sso", "Name": "developer-sso", "OrgID": "0",
  "ActionType": "GenerateOrLoginDeveloperProfile",
  "Type": "redirect", "ProviderName": "SocialProvider",
  "ProviderConfig": {
    "CallbackBaseURL": "https://portal.example.com/tib",
    "FailureRedirect": "https://portal.example.com/?fail=true",
    "UseProviders": [{"Name": "openid-connect", "Key": "developer-portal", "Secret": "<client secret>",
      "DiscoverURL": "http://keycloak:8080/realms/example/.well-known/openid-configuration",
      "Scopes": ["openid", "email", "profile"]}]
  },
  "IdentityHandlerConfig": {"DashboardCredential": "<PORTAL_API_SECRET>"},
  "DefaultUserGroupID": "24",
  "ReturnURL": "https://portal.example.com/sso"
}
```

`POST /portal-api/sso_profiles` with this body answers 201 with an empty body; a body the
Portal cannot parse answers 400 `{"errors":"error decoding profile: EOF"}`. `DiscoverURL` is
read by the Portal server; the browser goes to the provider's `authorization_endpoint` from
that document, so the document must name an address browsers reach (`dcr-keycloak.md`,
"Network").

## The sign-in

1. `GET /tib/auth/developer-sso/openid-connect` -> 302 to the provider's sign-in page.
2. After the user signs in -> 302 `/tib/auth/developer-sso/openid-connect/callback?...` -> 301
   `/sso?nonce=...` -> 302 `/`; `/portal/private/dashboard` answers 200.
3. A first-time user is created: `"Provider": "tib"`, `"Role": "consumer-admin"`, active, in
   the organisation of team 24 (`DefaultUserGroupID`), and sees that organisation's private
   catalogue at once.

Publish the sign-in link (`/tib/auth/<profile id>/openid-connect`) on the Live Portal, or set
`PORTAL_SSO_CUSTOM_LOGIN_URL` to send every sign-in there (registration and password reset
pages keep working, per the public page). Per the public page, any user who authenticates gets an account; a
`DefaultUserGroupID` that names a missing team makes sign-in fail. Without mail settings the
Portal logs "cannot send email notification ... default from email not set" and the sign-in
still succeeds.
