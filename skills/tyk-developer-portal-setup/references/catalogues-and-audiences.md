# Catalogues, organisations, teams and visibility

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 and 1.20.0 with Tyk Dashboard and Gateway 5.16.0

Public pages: https://tyk.io/docs/tyk-stack/tyk-developer-portal/enterprise-developer-portal/managing-access/manage-catalogues,
https://tyk.io/docs/tyk-stack/tyk-developer-portal/enterprise-developer-portal/managing-access/manage-api-consumer-organisations,
https://tyk.io/docs/portal/api-consumer

## The pieces

- **Catalogue**: a list of products and plans with a visibility. A developer can request a
  product only with a plan from the same catalogue.
- **Organisation**: an API consumer company. Each has a default team, `<name> All users`.
  A new Portal has `Default Organisation` (ID 1) with team `Default Organisation All users` (ID 1).
- **Team**: a group of users inside one organisation.
- **Roles**: `consumer-admin` (manages their organisation's users and apps) and
  `consumer-team-member`. Self-registered developers get `consumer-team-member`; SSO-created
  developers get `consumer-admin` (`sso.md`).

A new Portal has two catalogues: `Public Catalogue` (ID 1, `Public`) and `Private Catalogue`
(ID 2, `Private`, no audience).

## Who sees what (signed in through the Live Portal)

| Catalogue | Anonymous visitor | Signed-in developer outside the audience | Developer in the audience |
| --- | --- | --- | --- |
| `Public` | sees it | sees it | sees it |
| `Private`, no audience | no | **sees it** | sees it |
| `Private`, `Organisations: [<org id>]` | no | no | sees it |

## Recipe

```bash
# an organisation; its default team is created with it
curl -s -X POST "$PORTAL_URL/portal-api/organisations" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"Name":"Partner A"}'
# 201 {"ID":24,...};  GET /organisations/24/teams -> [{"ID":24,"Name":"Partner A All users","Default":true}]

# a private catalogue: NameWithSlug is required
curl -s -X POST "$PORTAL_URL/portal-api/catalogues" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"Name":"Partners","NameWithSlug":"partners","VisibilityStatus":"Private"}'
# 201;  without NameWithSlug: 422 {"errors":["Name's slug can't be blank"]}

# products, plans and the audience (the whole organisation)
curl -s -X PUT "$PORTAL_URL/portal-api/catalogues/4" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"Name":"Partners","NameWithSlug":"partners","VisibilityStatus":"Private","Products":[5],"Plans":[4],"Organisations":[24]}'
# 200 {"Organisations":["Partner A"],"Products":["Payments product"],"Plans":["Gold plan"],...}

# a developer in that organisation
curl -s -X POST "$PORTAL_URL/portal-api/users" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"Email":"dev@partner-a.example.com","First":"Alex","Last":"Partner",
  "Role":"consumer-team-member","OrganisationID":24,"Active":true,"Provider":"password","Password":"<password>"}'
# 201 {"ID":4,"Organisation":"Partner A","Teams":["Partner A All users"],...}
```

- `Organisations` is not in the published API schema but is accepted and read back (1.20.0 and 1.20.1).
  Send it as a list of IDs: `[{"ID":24}]` clears it to `null`. A later `PUT` without the field
  keeps it.
- `PUT /catalogues/{id}` with `Products` or `Plans` replaces the whole list.
- `PUT /users/{id}` with only `{"Active": true}` or `{"First": "..."}` keeps the user's teams.

## Team audiences through the API

On 1.20.0 and 1.20.1, `POST /catalogues/{id}/audiences {"OrganisationID":24,"TeamID":24}`
answers 201, but the audience reads back with `"OrganisationID": 0`, and a developer in that team
(signed in again) does not see the catalogue. Creating the catalogue with
`"OrgCatalogues":[{"OrganisationID":24,"TeamID":24}]` gives the same result. Grant visibility through the admin API with `Organisations`
(the whole organisation). For a narrower audience than an organisation, use a separate
organisation, or set the team in the admin UI and prove it by signing in as a team member.
`portal_setup.py inventory` reports every team audience stored without an organisation.
Re-check this after each Portal upgrade.

## Proving visibility

Sign in as a developer inside the audience and as one outside it, and read
`/portal/catalogue-products`: the catalogue filter lists the catalogues that developer sees
(`<option value="4" >Partners</option>`) and the cards show the products. Do not rely on the
admin readback alone. `portal_setup.py verify` fails at the cart ("the cart has no request
form") when the developer cannot see the product's catalogue.
