# Pitfalls and limitations

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 and 1.20.0 with Tyk Dashboard and Gateway 5.16.0

The file named after each pitfall has the request, the response and the release it applies to.

## Pitfalls

1. **A policy without partitions becomes nothing.** No product, no plan, no error, and
   synchronisation reports success. (`products-plans-partitions.md`)
2. **A product that drops out of synchronisation comes back as a new product.** Removing the
   partitions or the provider tag deletes it; restoring them creates a new ID with no display
   name, description or catalogue. (`products-plans-partitions.md`)
3. **Provider policy tags filter everything.** With `PoliciesTags` set, a policy created in
   the Dashboard without the tag is ignored. (`provider-and-sync.md`)
4. **The Dashboard user needs `oauth: write` for DCR.** The docs' permission list omits it;
   without it approval fails with "Not authorised", after an IdP client was already created.
   (`provider-and-sync.md`, `dcr-keycloak.md`)
5. **`certs`, not `certificates`,** in Dashboard user permissions. (`provider-and-sync.md`)
6. **Provider `MetaData` is a JSON string** inside the JSON body. (`provider-and-sync.md`)
7. **No specification is attached for you.** Upload one per API; check its `servers`.
   (`api-specifications.md`)
8. **A specification URL is read by the Portal server and by the browser.** `localhost` and
   container-only names fail one of them. Upload instead. (`api-specifications.md`)
9. **Team audiences added through the admin API do not grant visibility (1.20.0 and 1.20.1).**
   They store organisation 0. Use `Organisations` on the catalogue, and re-check after each
   Portal upgrade. (`catalogues-and-audiences.md`)
10. **A private catalogue with no audience is visible to every signed-in developer.**
    (`catalogues-and-audiences.md`)
11. **New Portals auto-approve sign-ups.** Turn `auto-approve-users` off if accounts need
    review. (`developers-and-access.md`)
12. **Saving General settings with an empty robots box blanks `/robots.txt`** and then blocks
    the next save. `portal_setup.py settings` fills it. (`developers-and-access.md`)
13. **`AUTH_TOKEN_CUSTOM` (the public example's spelling) silently creates a random key.** Use
    `AUTH_TOKEN_TYPE_CUSTOM`. (`credential-import.md`)
14. **Importing the same key value twice is accepted;** deleting either credential deletes the
    key behind both. (`credential-import.md`)
15. **OAuth provider `Type` is case sensitive:** `Keycloak`, `Okta`, `Gluu`, `Other`.
    (`dcr-keycloak.md`)
16. **A DCR token must ask for the scopes.** Without `scope=<product scope> <plan scope>` the
    Gateway answers 403 "no matching policy found in scope claim". (`dcr-keycloak.md`)
17. **Scripts posting Live Portal forms need the CSRF token and `Sec-Fetch-Site: same-origin`.**
    Over plain HTTP an `http://` Origin alone gets 400. (`developers-and-access.md`)
18. **A session follows the client address.** Behind proxies, keep the forwarded client
    address stable between requests, or users are signed out. (`developers-and-access.md`)
19. **Changing `PORTAL_JWTSIGNINGKEY` invalidates every admin API token** and admin session,
    and so does the upgrade from 1.20.0 to 1.20.1 when no key is set (1.20.1 generates its own).
    A key under 32 characters, or a value published in Tyk docs or sample files, stops 1.20.1 at
    start. (`operations-and-restarts.md`)
20. **Some admin API fields answer 200 and are not stored** (MCP documentation fields). Read
    back every write. (`admin-api.md`, `mcp-products.md`)
21. **Lists stop at 20 items** unless you page. (`admin-api.md`)
22. **The first DCR credential for a product can be refused for a few seconds** (403 "Key not
    authorized") until the Gateway reloads the new scope mapping. (`dcr-keycloak.md`)

## Limitations (say these plainly)

- Products and plans are Dashboard policies; the Portal cannot offer an API that its provider's
  Dashboard does not manage.
- One product uses one authentication method: mixing an auth-token API and a JWT API in one
  product is refused ("invalid auth type combination").
- The General settings (registration, auto-approve, access flow, mail) have no admin API; they
  are an admin form.
- On 1.20.1 the admin API cannot scope a catalogue to one team of an organisation (pitfall 9).
- MCP product documentation (markdown) is written in the admin UI; on 1.20.0 the admin API does not
  store it.
- The OpenAPI renderer (Stoplight Elements or Redoc) is one setting for the whole Portal.
- Approval, rejection and provisioning are per request; there is no bulk call. Credential
  import is one call per key.
