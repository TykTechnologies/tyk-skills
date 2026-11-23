# Products, plans and policy partitions

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 and 1.20.0 with Tyk Dashboard and Gateway 5.16.0

Public pages: https://tyk.io/docs/portal/api-products, https://tyk.io/docs/portal/api-plans,
https://tyk.io/docs/portal/api-provider (synchronisation). Policy objects and partitions in
general: the `tyk-rate-limits-keys-policies` skill.

## What a product and a plan are

- A **product** grants access to one or more APIs. It is a Dashboard policy with the `acl`
  partition.
- A **plan** carries the rate limit and quota. It is a Dashboard policy with the `rate_limit`
  and/or `quota` partition.
- An approved access request gives the developer one credential that applies both policies.
  For example, the issued key has `apply_policies` = [product policy, plan policy], `rate 5/60`
  and `quota 100` from the plan, and `meta_data`
  `{"ApplicationID":"1","DeveloperID":"3","OrganisationID":"1","TeamIDs":"1"}`.

## How synchronisation decides

| Dashboard policy `partitions` | Becomes |
| --- | --- |
| `{"acl": true}` (others false) | a product |
| `{"rate_limit": true, "quota": true}` (or one of them) | a plan |
| no `partitions` block, or every partition false | **nothing**, with no error |

Use `python3 scripts/portal_setup.py policies --tags <provider tags>` to list each policy as
`product`, `plan`, `both` or `neither`.

### When the block or the tag goes missing

On 1.20.0:

1. When a product policy is rewritten without `partitions` (the Dashboard stores every partition
   `false`) and the provider is synchronised, the sync answers 200,
   `"Action Synchronize: Executed successfully"`. The product disappears from `GET /products`,
   `GET /products/<id>` answers 404, and it leaves the catalogue.
2. When the partitions are restored and the provider synchronised again, a **new** product
   appears with a new ID. The old ID stays 404. The catalogue membership is gone.
3. Setting provider `PoliciesTags` to a tag no policy carries hides every product and plan.
   Tagging the policies and synchronising brings them back as **new** objects (new IDs) with
   empty `DisplayName`, `Description` and `Catalogues`.

The public page says a product that comes back is shown "with all of its previous
configuration"; on 1.20.0 it comes back as a new object. Treat a missing partition or tag as a
delete: keep `partitions` in every policy update, and after any tag or policy change run
`portal_setup.py inventory` and re-apply display names, descriptions, specifications and
catalogues.

## Creating them

From the Portal (the Portal writes the Dashboard policy):

```bash
curl -s -X POST "$PORTAL_URL/portal-api/products" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"Name":"Payments product","DisplayName":"Payments",
  "Description":"Take payments","ProviderID":1,"APIDetails":[{"APIID":"<API id>"}],"Catalogues":[1]}'
# 201 {"AuthType":"authToken","AuthTypes":["authToken"],"ID":2,"Path":"payments-product-1","ReferenceID":"<policy _id>",...}

curl -s -X POST "$PORTAL_URL/portal-api/plans" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"Name":"Gold plan","DisplayName":"Gold","ProviderID":1,
  "RateLimit":100,"Per":60,"Quota":10000,"QuotaRenewalRate":86400,"Catalogues":[1]}'
# 201 {"ID":2,"RateLimit":100,"Per":60,"Quota":10000,"QuotaRenewalRate":86400,"ReferenceID":"<policy _id>",...}
```

- The product policy the Portal writes has `partitions {"acl": true}`, `rate 1000/60` and no
  quota; the plan policy has `partitions {"quota": true, "rate_limit": true}`.
- `PUT /plans/{id}` changes the Dashboard policy at once (a new `RateLimit` reads back on the
  policy immediately).
- `DELETE` of a product or plan deletes its Dashboard policy.
- `AuthType` is derived from the APIs. One product cannot mix authentication methods:
  `APIDetails` with an auth-token API and a JWT API answers 422
  `{"errors":["invalid auth type combination"]}`.
- `Path` (for example `orders-product-1`) is the Live Portal address:
  `/portal/catalogue-products/<Path>`.
- Unlimited quota on a plan: `"UnlimitedQuota": true` (works on a JWT plan).

From the Dashboard: create a policy with the right partitions (and the provider tag), then
synchronise. The product name is the policy name; set `DisplayName` and `Description` in the
Portal afterwards.

## Documentation-only products

A product with no API access, only reference documentation (see
https://tyk.io/docs/portal/api-products/documentation-only-products):

```bash
curl -s -X POST "$PORTAL_URL/portal-api/products" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"Name":"Reference docs","IsDocumentationOnly":true,
  "Catalogues":[1],"SpecDetails":[{"SpecAlias":"orders-reference","Name":"Orders reference"}]}'
# 201 {"ID":9,"IsDocumentationOnly":true,"ProviderID":0,"ReferenceID":"","SpecDetails":[{"ID":1,...}],...}
```

It has no Dashboard policy, no provider and no request-access button; its page serves the
specification at `/portal/catalogue-products/<Path>/<SpecAlias>/docs` once one is uploaded
(`api-specifications.md`).
