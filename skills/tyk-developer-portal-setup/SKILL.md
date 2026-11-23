---
name: tyk-developer-portal-setup
description: Set up the Tyk Developer Portal through its admin API so developers can find APIs, sign up, request access and get working credentials - Dashboard provider, products and plans, catalogues, API specs, approval, DCR with Keycloak, key import, MCP products, SSO. Use when asked to set up, configure, publish APIs on or debug the Tyk Developer Portal, its catalogue, access requests, plans or DCR. Not for themes, page copy or emails: the `tyk-developer-portal-branding` skill.
---

# Tyk Developer Portal Setup

Takes a running Tyk Developer Portal (new or already bootstrapped) to the point where a
developer finds an API, signs up, requests access, gets a credential and calls the API. Everything is configured
through the Portal admin API, with a script for the parts that only exist as forms. The steps
apply to Developer Portal 1.20.1 with Dashboard and Gateway 5.16.0 (DCR, key import, MCP products
and SSO to 1.20.0; each reference names the release it applies to).
Public docs: https://tyk.io/docs/portal/overview/intro

The Portal and the Dashboard are licensed components, and the Portal needs a Dashboard (its
provider). For licensing and editions, use the `tyk-facts` skill. Installing the Portal is the
`tyk-deployment` skill; a disposable stack to try this on is the `tyk-demo-builder` skill.

## Ask first

Ask only what the request leaves open. Each question has a default.

| Question | Default |
| --- | --- |
| Portal URL, admin token (or bootstrap a new Portal), Dashboard URL and key? | ask; never print a token |
| Which APIs, grouped into which products, with which plans (rate, quota)? | one product per API, one free plan |
| Products and plans authored in the Dashboard (policies) or in the Portal? | in the Portal; it writes the policies |
| Who may see them: everybody, signed-in developers, named partner organisations? | public catalogue; one private catalogue per partner organisation |
| May anyone sign up, and are new accounts reviewed? | sign-up on, accounts reviewed (auto-approve off) |
| Are access requests approved by a person or automatically? | by a person; auto-approve only on a free plan |
| Credentials: auth token, or OAuth clients from an identity provider (DCR)? | auth token; DCR when the APIs use JWT |
| Existing consumer keys to keep? | none |
| Sign-in through the company identity provider? | no |
| Policies and APIs managed in Git? | no; if yes, the `tyk-gitops-sync-operator` skill owns them |

## Workflow

```bash
SKILL=<this skill's folder>
export PORTAL_URL=https://portal.example.com PORTAL_API_TOKEN=...      # header: Authorization: <token>
export DASHBOARD_URL=https://dashboard.example.com DASHBOARD_API_KEY=...   # read by the script
export GW=https://api.example.com                                      # Gateway, as developers call it
export PORTAL_ADMIN_EMAIL=... PORTAL_ADMIN_PASSWORD=...                # only for "settings"
P() { curl -s -X "$1" "$PORTAL_URL/portal-api$2" -H "Authorization: $PORTAL_API_TOKEN" -H 'Content-Type: application/json' "${@:3}"; }
```

1. **Reach the admin API.** A new Portal: `POST /portal-api/bootstrap` with `username`,
   `password`, `first_name`, `last_name` returns `data.api_token`; before that every other
   admin path is 404. Then wait until `P GET /providers` answers 200. Lists return 20 items
   unless you page with `per_page`/`page` (`references/admin-api.md`).

2. **Connect the Dashboard.** Create a dedicated Dashboard user with `apis`, `certs`, `keys`,
   `policies` (write), `analytics`, `users` (read), and `oauth` (write) if DCR is in scope:
   `POST $DASHBOARD_URL/api/users` with `user_permissions` answers 200
   `{"Status":"OK","Message":"User and session have been created","Meta":"<user id>"}`. Sign in
   to the Dashboard as that user, open **Edit Profile** from the user menu, and copy its key from
   **Tyk Dashboard API Access Credentials** (**Show secret**); the request body is in
   `references/provider-and-sync.md`. Create the provider with its `MetaData` as a JSON string
   (`URL` as the Portal reaches the Dashboard, `Secret`, `OrgID`, `Gateway` as developers reach
   it, `PoliciesTags`), then
   synchronise: `P PUT /providers/1/synchronize` answers 200
   `{"message":"Action Synchronize: Executed successfully","status":"ok"}`, and
   `P GET /providers` must show `"Status": "Up"` (`references/provider-and-sync.md`).

3. **Make products and plans.** `P POST /products -d '{"Name":..,"DisplayName":..,"ProviderID":1,"APIDetails":[{"APIID":"<api id>"}]}'`
   and `P POST /plans -d '{"Name":..,"ProviderID":1,"RateLimit":5,"Per":60,"Quota":100,"QuotaRenewalRate":3600}'`
   (201 each; the Portal writes a policy with the `acl` or `rate_limit`/`quota` partition). For
   policies authored in the Dashboard, check them before synchronising:

   ```bash
   python3 "$SKILL/scripts/portal_setup.py" policies --tags portal
   # 6afe... 'Orders product': product
   # 6afe... 'No partitions block': neither (...) <- the Portal makes no product or plan of this
   ```

   Read `references/products-plans-partitions.md` before removing a partition or a tag.

4. **Attach a specification to every API in a product.** Export the plain OpenAPI from the
   Dashboard (`GET /api/apis/oas/<id>/export?mode=public`), set `servers` to the public Gateway
   URL, upload with `curl -X POST "$PORTAL_URL/portal-api/products/<id>/api-details/<api id>/oas" -H "Authorization: $PORTAL_API_TOKEN" -F file=@spec.json`
   (200 `{"status":"ok"}`). Prefer upload to `OASUrl` (`references/api-specifications.md`).

5. **Publish in catalogues.** `P PUT /catalogues/1 -d '{"Name":"Public Catalogue","VisibilityStatus":"Public","Products":[..],"Plans":[..]}'`.
   For a partner: an organisation (`P POST /organisations`), a private catalogue with
   `NameWithSlug`, and `"Organisations":[<org id>]` on it. Do not rely on
   `POST /catalogues/{id}/audiences` for a team (1.20.0 and 1.20.1)
   (`references/catalogues-and-audiences.md`).

6. **Set sign-up and review.** A new Portal accepts sign-ups and activates them at once.

   ```bash
   python3 "$SKILL/scripts/portal_setup.py" settings --set auto-approve-users=false
   ```

   Activate a waiting developer with `P PUT /users/<id> -d '{"Active":true}'`. Per plan,
   `"AutoApproveAccessRequests": true` approves requests automatically; otherwise approve with
   `P PUT /access_requests/<id>/approve` (`references/developers-and-access.md`).

7. **OAuth clients (only when the APIs use JWT).** Set `TYK_PORTAL_ENABLEIDPREGISTRY=true` on
   the Portal, prepare the identity provider (scopes, initial access token), create the OAuth
   provider (`Type` is `Keycloak`, `Okta`, `Gluu` or `Other`) and a client type, then a product
   with `DCREnabled`, `Scopes` and `Templates`, and plans with `JWTScope`
   (`references/dcr-keycloak.md`).

8. **Optional pieces.** Existing keys: `references/credential-import.md`. MCP proxies as
   products: `references/mcp-products.md`. Developer SSO: `references/sso.md`.

9. **Check the whole Portal, then prove the developer journey.**

   ```bash
   python3 "$SKILL/scripts/portal_setup.py" inventory          # lists everything; fix the PROBLEM lines (at the end)
   PORTAL_DEV_EMAIL=dev@example.com PORTAL_DEV_PASSWORD=... \
   python3 "$SKILL/scripts/portal_setup.py" verify --register --approve \
     --product <id> --plan <id> --catalogue <id> --call "$GW/<listen path>/<path>"
   # register: HTTP 200 / login: ok / access request 10: pending / approved through the admin API
   # credential: auth token shown on the developer's app page
   # API call ...: HTTP 200
   # PASS
   ```

   For a DCR product add `--token-url <IdP token endpoint> --scope "<product scope> <plan scope>"`.
   With account review on, the first run stops at login; activate the user and run it again
   without `--register`.

10. **Restart the Portal once** and repeat `inventory`; nothing may change
    (`references/operations-and-restarts.md`).

## References

| File | Read it for |
| --- | --- |
| `references/admin-api.md` | Authentication, bootstrap, pagination, readback rules, the calls used |
| `references/provider-and-sync.md` | Dashboard user permissions, provider fields, sync, tags, provider status |
| `references/products-plans-partitions.md` | Partitions, what sync does when they or the tags go missing, creating from the Portal, documentation-only products |
| `references/catalogues-and-audiences.md` | Visibility table, organisations and teams, the `Organisations` field |
| `references/api-specifications.md` | Upload, URL, `servers`, Stoplight Elements or Redoc |
| `references/developers-and-access.md` | Sign-up settings, cart flow, approval, provisioning, CSRF for scripts, sessions |
| `references/dcr-keycloak.md` | DCR end to end with Keycloak and the Identity Provider Registry |
| `references/credential-import.md` | Keeping existing key values |
| `references/mcp-products.md` | MCP proxies as products |
| `references/sso.md` | Developer sign-in with OpenID Connect |
| `references/operations-and-restarts.md` | What restarts, key changes and resets keep |
| `references/pitfalls-and-limitations.md` | Twenty-two pitfalls and the limitations |

Script: `scripts/portal_setup.py` (Python standard library; `inventory`, `policies`,
`settings`, `verify`), tests in `scripts/test_portal_setup.py`.

## Pitfalls

The costly ones; all 22 are in `references/pitfalls-and-limitations.md`.

- A Dashboard policy without `partitions` becomes no product and no plan, and sync says success.
- A product that leaves synchronisation (lost partition or tag) returns as a new product without
  its name, description or catalogues.
- DCR approvals need the provider's Dashboard user to hold `oauth: write`.
- Nothing attaches a specification for you; a spec URL must be reachable by the Portal server
  and by browsers.
- Team audiences posted through the admin API do not grant visibility on 1.20.1; use
  `Organisations`. A private catalogue without an audience is open to every signed-in developer.
- New Portals auto-approve sign-ups.
- `AUTH_TOKEN_CUSTOM` imports a random key; use `AUTH_TOKEN_TYPE_CUSTOM`.
- Without `PORTAL_JWTSIGNINGKEY`, 1.20.1 generates a signing key and keeps it in its database.
  Setting a new key, or upgrading from 1.20.0 without one, voids every admin token and ends
  admin sessions; a key under 32 characters, or a value published in Tyk docs or sample files,
  stops the Portal at start. Reset links mailed before the upgrade stop working
  (`references/operations-and-restarts.md`).

## Limitations

- The Portal offers only APIs and policies of its provider Dashboards.
- One product, one authentication method.
- General settings (registration, review, access flow, mail) have no admin API, only a form.
- On 1.20.1 the admin API cannot limit a catalogue to one team; MCP documentation markdown is
  written in the admin UI.
- One OpenAPI renderer for the whole Portal.

## Before reporting success

- [ ] `P GET /providers` shows `"Status": "Up"`.
- [ ] `portal_setup.py policies` shows no `neither` for a policy that should be offered.
- [ ] `portal_setup.py inventory` prints no PROBLEM line you have not explained to the user.
- [ ] Each catalogue was checked signed in as a developer inside and outside its audience.
- [ ] `portal_setup.py verify` printed PASS: a developer signed up (or signed in), requested
      access, received the credential on their app page and called the Gateway with it.
- [ ] For DCR, the same with `--token-url` and `--scope`, and a token without the scopes got 403.
- [ ] After one Portal restart, `inventory` is unchanged.
- [ ] No token, key, secret or password is in a file you leave behind or in the report.
- [ ] The report names the Portal, Dashboard and Gateway versions.

## Hand-offs

- Themes, page copy, menus, robots.txt rules and emails: the `tyk-developer-portal-branding` skill.
- The API definitions (authentication, paths, servers): the `tyk-api-definitions-routing` skill; JWT
  and identity provider settings on the Gateway: the `tyk-authentication` skill.
- Rate limits, quotas and keys outside the Portal: the `tyk-rate-limits-keys-policies` skill.
- MCP proxies themselves: the `tyk-mcp-gateway` skill.
- Policies and APIs in Git or Kubernetes: the `tyk-gitops-sync-operator` skill.
- Installing or scaling the Portal: the `tyk-deployment` skill; upgrades: the `tyk-upgrades` skill.
- A demo stack with a Portal: the `tyk-demo-builder` skill. Licensing: the `tyk-facts` skill.
- Errors this skill does not explain: the `tyk-troubleshooting` skill.
