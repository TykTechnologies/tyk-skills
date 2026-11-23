# Breaking changes and deprecations: 5.15 / 1.19 to 5.16 / 1.20

Last verified: 2026-11-19 against Tyk Gateway (`tykio/tyk-gateway-ee`) and Dashboard 5.15.0, 5.15.1 and 5.16.0, Tyk Pump 1.17.0 and 1.18.0, Tyk Developer Portal 1.19.0, 1.20.0 and 1.20.1

Read the release notes for every version between yours and the target, not only the target:
breaking changes ship in minor and patch releases (1.20.1 below is a patch).

- Gateway: https://tyk.io/docs/developer-support/release-notes/gateway
- Dashboard: https://tyk.io/docs/developer-support/release-notes/dashboard
- Pump: https://tyk.io/docs/developer-support/release-notes/pump
- Portal: https://tyk.io/docs/developer-support/release-notes/portal
- MDCB: https://tyk.io/docs/developer-support/release-notes/mdcb
- Helm charts: https://tyk.io/docs/developer-support/release-notes/helm-chart

`tyk_state_snapshot.py preflight <snapshot>` lists the APIs that the first four rows touch,
from a snapshot of the old stack (`verification.md`).

## Gateway and Dashboard 5.16.0

| Change (release notes, "Breaking Changes") | Opt-out setting | Details |
| --- | --- | --- |
| Detailed analytics redact the `Authorization` header and custom auth headers | `analytics_config.allow_unsafe_detailed_logs` (Gateway) | below |
| Strip Listen Path no longer rewrites URL Rewrite targets that start with the listen path | `apply_strip_listen_path_to_url_rewrite` (Gateway; named in the changelog entry, not in the Breaking Changes section) | below |
| Custom API IDs must use only letters, digits, `.`, `_`, `-`, `~` and hold a letter or digit | `allow_unsafe_api_ids` (Dashboard and Gateway) | below |
| Body transform templates in file mode must sit under the Gateway `template_path` | `allow_unsafe_body_transform_template_paths` | release notes |
| Webhook `template_path` values are validated (absolute paths and `..` refused) | `allow_unsafe_webhook_template_paths` | release notes |

Neither release lists deprecations. 5.15.1 lists no breaking changes, and the patch upgrade
from 5.15.0 changes nothing in the snapshot.

### Detailed analytics

An API with `server.detailedActivityLogs.enabled: true` and
`authentication.stripAuthorizationData: false`, one keyed call, then the raw request read back
from `GET /api/logs/?p=-1` (`RawRequest`, base64):

```text
Gateway 5.15.0 / 5.15.1: Authorization stored in clear
Gateway 5.16.0:          Authorization stored as: ***
```

Anything that reads raw requests from analytics to identify callers needs another field
after the upgrade; the record still carries the `APIKey` field (a key hash).

### URL rewrite with Strip Listen Path

Listen path `/rewrite/` with `strip: true`, operation rewrite
`"rewriteTo": "http://demo-upstream:8080/rewrite/target"`; the upstream echoes the path it
received:

```text
Gateway 5.15.1:                                          GET /rewrite/items -> 200, upstream sees /target
Gateway 5.16.0:                                          GET /rewrite/items -> 200, upstream sees /rewrite/target
Gateway 5.16.0, TYK_GW_APPLYSTRIPLISTENPATHTOURLREWRITE=true: GET /rewrite/items -> 200, upstream sees /target
```

The status stays 200, so only an upstream-side check catches it. Two ways out:

- **Lasting fix:** change the rewrite target to the exact upstream path you want
  (`http://demo-upstream:8080/target` here; on 5.16.0 the upstream then sees `/target`). The
  release notes say only targets that start with the listen path are affected, so this target
  behaves the same before and after, and the change can be made before the Gateway upgrade.
- **Stop-gap:** set `apply_strip_listen_path_to_url_rewrite: true` in `tyk.conf` (or
  `TYK_GW_APPLYSTRIPLISTENPATHTOURLREWRITE=true`) on every 5.16.0 Gateway. It restores the old
  stripping for every API on that Gateway; both forms give `/target` again, and a path
  with no rewrite (`GET /rewrite/other`) still reaches the upstream as `/other`. The default is
  `false`. The changelog entry "Fixed URL Rewrite targets being altered by Strip Listen Path"
  names it: https://tyk.io/docs/developer-support/release-notes/gateway.

### Unsafe custom API IDs

An API created on 5.15.0 with `x-tyk-api-gateway.info.id: "example:partner-a"` keeps loading
and serving after the upgrade (`GET /partner-a/items` 200). On Dashboard 5.16.0:

```text
PUT /api/apis/oas/example:partner-a  -> 400
{"Status":"Error","Message":"Invalid API ID: Allowed characters: a-z, A-Z, 0-9, ., _, -, ~ and must contain at least one letter or digit","Meta":null}
POST /api/apis/oas (new id example:partner-b) -> 400, same message
```

`DELETE` of that API through the Dashboard API is not refused. With
`TYK_DB_ALLOWUNSAFEAPIIDS=true` on the Dashboard the same `PUT` returns
`{"Status":"OK","Message":"Api updated","Meta":null}`; removing it brings the 400 back. The
lasting fix is a new API with a safe ID, then moving keys and policies to it (the
`tyk-rate-limits-keys-policies` skill), because the ID is what keys and policies reference.

## Developer Portal

The rows marked "release notes" come from the 1.20.1 "Breaking Changes" and "Upgrade
instructions" (https://tyk.io/docs/developer-support/release-notes/portal) and the
configuration reference
(https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration).

| Release | Change | Details |
| --- | --- | --- |
| 1.20.0 | SMTP password and OAuth 2.0 provider registration token are write-only in the admin API (release notes, "Breaking Changes") | release notes |
| 1.20.1 | Admin API tokens and admin sessions issued before the upgrade stop working when no signing key is configured | below |
| 1.20.1 | A signing key that is set but weak (under 32 characters, or a value published in Tyk documentation or repositories, including the previous default) stops the Portal from starting; `PORTAL_ALLOWUNSAFEJWTSIGNINGKEY=true` starts it anyway, with a warning on every start | release notes |
| 1.20.1 | Password reset, invitation and confirmation links created before the upgrade stop working; resend pending invitations | release notes |
| 1.20.1 | Theme templates can no longer call `env`, `expandenv` or `getHostByName`; a custom theme that does fails to render | release notes |
| 1.20.1 | Without a configured Portal API secret (`PORTAL_PORTALAPISECRET`), the SSO API (`POST /api/sso`, `/api/portal/developers`) answers `401` to every external request; set one if you use a standalone Tyk Identity Broker or a custom SSO integration | release notes |
| 1.20.1 | `GET /portal-api/configs` returns `***` for every secret field | release notes |

### Portal 1.20.1: re-issue admin API tokens

Without `PORTAL_JWTSIGNINGKEY` set, the first start of 1.20.1 logs:

```text
No JwtSigningKey configured: generated a new per-deployment key and stored it in the database.
Admin API tokens and sessions signed with any previous key ... no longer work.
Set TYK_PORTAL_JWTSIGNINGKEY to manage the key yourself.
```

and the admin API token used until then is refused:

```text
GET /portal-api/products  (old token) -> 403 {"message": "Forbidden"}
GET /portal-api/products  (token from the admin profile page) -> 200
```

The new token is on the administrator's profile page in the Admin Portal (`/admin/admin_profile`
in 1.20.1). Every script, CI job and integration that calls the Portal admin API needs it.
To control the key yourself, set `PORTAL_JWTSIGNINGKEY` (also read as
`TYK_PORTAL_JWTSIGNINGKEY`) to a random value of at least 32 characters (`openssl rand -base64 32`),
the same on every Portal instance. The release notes say that changing the key, including the
automatic generation on upgrade, also ends active admin and Tyk Identity Broker sessions and the
long-lived admin token that integrations such as Tyk Operator's Portal client use: replace those
tokens before or right after the upgrade (links above).

## Pump 1.18.0

No breaking changes and no deprecations in the release notes. Analytics records keep arriving
in PostgreSQL after the switch, with a 5.16.0 Gateway.

## Supported dependency versions moved

From the "3rd Party Dependencies" tables in the release notes. Check the
databases before the upgrade, not after:

| Component | Old release | New release |
| --- | --- | --- |
| Gateway Redis | 5.15.1: 6.2.x, 7.x, 7.4.x, 8.x (up to 8.4) | 5.16.0: 7.x, 7.4.x, 8.x (up to 8.4) |
| Dashboard PostgreSQL | 5.15.1: 13.x to 17.x | 5.16.0: 15.x to 18.x |
| Dashboard Redis | 5.15.1: 6.2.x, 7.x, 7.4.x, 8.x (up to 8.4) | 5.16.0: 7.x, 7.4.x, 8.x (up to 8.4) |
| Dashboard MongoDB (compatible) | 5.15.1: 6.0.x, 7.0.x, 8.0.x | 5.16.0: 6.0.x to 9.0.x |
| Pump PostgreSQL | 1.17.0: 13.x to 17.x | 1.18.0: 15.x to 18.x |
| Pump Redis | 1.17.0: 6.x to 7.x, 8.x (up to 8.4) | 1.18.0: 7.x, 8.x (up to 8.4) |
| Pump MongoDB (compatible) | 1.17.0: 4.4.x, 5.x, 6.x, 7.0 | 1.18.0: 7.0.x, 8.0.x, 9.0.x |

Both 5.15.1 and 5.16.0 list Go 1.26 for Go plugins. The upgrade guide still asks for Go
plugins to be recompiled for the target version, also for patch releases that move the Go
version: https://tyk.io/docs/developer-support/upgrading (the `tyk-custom-plugins` skill
builds them).

## New fields after the upgrade

The new release returns fields the old one did not have, without anything being edited:

- Dashboard 5.16.0, classic API readback: `proxy.dns_discovery`.
- Portal 1.20.0, product readback: `APIDetails[].RequiredScopes`.

The snapshot diff reports them as `+field`. `diff --allow-new-fields` turns objects whose only
change is new fields into notes. A removed field or a changed value is always a difference.
