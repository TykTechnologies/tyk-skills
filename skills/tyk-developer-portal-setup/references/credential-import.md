# Importing existing API keys into a Portal app

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.0 with Tyk Dashboard and Gateway 5.16.0

Public page: https://tyk.io/docs/tyk-developer-portal/import-custom-credentials

Use this when consumers already hold API keys (for example from another gateway) and must keep
them. The Portal creates a Dashboard key with exactly the value you give, attached to a product
and plan, and the developer sees it on their app page.

## Recipe

```bash
# the developer must exist; create the app for them
curl -s -X POST "$PORTAL_URL/portal-api/apps" -H "Authorization: $PORTAL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"Name":"migrated-app","UserID":3,"Visibility":"personal"}'
# 201 {"ID":4,...}

curl -s -X POST "$PORTAL_URL/portal-api/apps/4/custom_credentials" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d "{\"Alias\":\"migrated-1\",\"Type\":\"CREDENTIAL_TYPE_TYK_MANAGED\",
  \"PlanID\":3,\"ProductIDs\":[4],\"AuthenticationMethod\":\"AUTH_METHOD_TOKEN\",
  \"AuthTokenType\":\"AUTH_TOKEN_TYPE_CUSTOM\",\"KeyID\":\"$EXISTING_KEY\"}" -o /dev/null -w '%{http_code}\n'
# 201

curl -s -o /dev/null -w '%{http_code}\n' "$GW/<listen path>/..." -H "Authorization: $EXISTING_KEY"   # 200
```

## What to watch

| Input | Result |
| --- | --- |
| `"AuthTokenType":"AUTH_TOKEN_TYPE_CUSTOM"` | the key has the supplied value: Gateway 200 |
| `"AuthTokenType":"AUTH_TOKEN_CUSTOM"` (the spelling in the public page's curl example) | 201 and echoed back, but the supplied value gets 403: a different key is created |
| `KeyID` of 4 characters | 422 `{"errors":["invalid credential key ID: key ID must be at least 5 characters long"]}` |
| the same `KeyID` imported twice | 201 both times; both credentials name one Dashboard key |
| deleting one of two credentials with the same `KeyID` (`DELETE /apps/4/custom_credentials/3`, 200) | the Dashboard key is deleted: the other credential's value answers 403 about 12 seconds later, while the Portal still lists that credential |
| wrong app ID in the path | 422 `{"errors":["getting client from context: getting client by id: record not found"]}` |

So: use `AUTH_TOKEN_TYPE_CUSTOM`, prove every imported value with a Gateway call, and check for
duplicates before importing (`GET /apps/{id}/custom_credentials` on every app, or the
Dashboard's key list). There is one call per key; loop over your list.

The imported key's limits come from the plan, like any Portal credential.
