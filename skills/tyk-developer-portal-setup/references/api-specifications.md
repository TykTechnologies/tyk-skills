# API specifications on products

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 and 1.20.0 with Tyk Dashboard and Gateway 5.16.0

Public page: https://tyk.io/docs/portal/api-products (API reference documentation);
renderer setting: https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/deploy/configuration#portal_docrenderer

## Nothing is attached automatically

A product synchronised from, or created over, a Tyk OAS API has no specification:
`APIDetails[].OASDocument` and `OASUrl` are empty and the product page says
"There's no specifications attached". Attach one per API in the product.

## From the Tyk OAS definition (recommended: upload)

1. Export the plain OpenAPI document (no `x-tyk-api-gateway`) from the Dashboard:

   ```bash
   curl -s "$DASHBOARD_URL/api/apis/oas/<api id>/export?mode=public" -H "authorization: $DASHBOARD_API_KEY" -o orders.json
   ```

2. Check `servers`. The export carries the definition's own `servers`, which can include
   addresses only the Gateway's network knows (for example `http://tyk-gateway:8080/...`
   first). Developers try the API against these, so set `servers` to the public Gateway URL
   before uploading.
3. Upload it to the product's API:

   ```bash
   curl -s -X POST "$PORTAL_URL/portal-api/products/4/api-details/<api id>/oas" \
     -H "Authorization: $PORTAL_API_TOKEN" -F "file=@orders.json"
   # 200 {"status":"ok"};  GET /products/4/api-details -> "OASDocument": "orders.json"
   ```

The Portal stores the file and serves it itself, at
`/system/api_details/<api id>/oasdocument/<name>.<timestamp>.json`, so the Gateway and the
Dashboard need not be reachable from the developer's browser. The product page gets a
**DOWNLOAD SPEC** button and a docs page at `/portal/catalogue-products/<Path>/<api id>/docs`.
Upload size limit: `PORTAL_MAX_UPLOAD_SIZE`, at most 32 MB (see the configuration page above).

## By URL

```bash
curl -s -X PUT "$PORTAL_URL/portal-api/products/5/api-details/<api id>" -H "Authorization: $PORTAL_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"OASUrl":"https://specs.example.com/payments.json"}'
# 200, "OASUrl" reads back
```

The URL is fetched twice: by the Portal server when it renders the page, and by the
developer's browser, which the page tells to load the same URL. With
`http://localhost:48003/...` the page still answers 200 but the Portal logs
`failed to handle product documentation: failed to read oas doc: request failed: Get "http://localhost:48003/...": dial tcp [::1]:48003: connect: connection refused`;
with a container-only address the page embeds that address for the browser. The URL must
be reachable, with the same name, from the Portal and from every developer's browser (and
allow the browser's cross-origin request). Upload avoids all of this.

## Documentation-only products

Upload to the spec entry instead of an API:

```bash
curl -s -X POST "$PORTAL_URL/portal-api/products/9/spec-details/1/oas" -H "Authorization: $PORTAL_API_TOKEN" -F "file=@orders.json"
# 200 {"status":"ok"};  docs page /portal/catalogue-products/reference-docs-0/orders-reference/docs
```

## Which renderer shows them

`PORTAL_DOCRENDERER` chooses the OpenAPI renderer for the whole Portal (restart to change):

| Value | Docs page markup |
| --- | --- |
| unset or `stoplight` (default) | `<elements-api apiDescriptionUrl='/system/api_details/...json'>` (Stoplight Elements) |
| `redoc` | `<redoc spec-url='/system/api_details/...json'>` |

The theme loads both renderers from public CDNs; branding and pinning them is the
`tyk-developer-portal-branding` skill. MCP proxies show operator-written markdown instead
(`mcp-products.md`).
