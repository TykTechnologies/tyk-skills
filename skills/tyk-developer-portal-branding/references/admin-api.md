# Admin API for branding

Last verified: 2026-11-19 against Tyk Developer Portal 1.20.1 (`tykio/portal:v1.20.1`) and 1.20.0

The calls the branding workflow makes, with the responses Portal 1.20.0 and 1.20.1 return (the
workflow's calls to themes, pages, products, plans, catalogues, users, menus and General settings
answer the same on both). Public API
reference: https://tyk.io/docs/product-stack/tyk-enterprise-developer-portal/api-documentation/tyk-edp-api
(the OpenAPI file is `swagger/enterprise-developer-portal-swagger.yaml` in the public tyk-docs
repository). On 1.20.1 an
admin API token stops working after a signing-key change or the upgrade from 1.20.0
(`upgrades-and-operations.md`).

## Authentication

- Base path `/portal-api`. Header `Authorization: <token>` with the raw token, no `Bearer`.
  `Bearer <token>` and a missing header both answer 403 `{"message": "Forbidden"}`.
- A new Portal gives the first admin and its token through
  `POST /portal-api/bootstrap` with `{"username", "password", "first_name", "last_name"}`:
  200 `{"code":"OK","message":"Bootstrapped user successfully","data":{"api_token":"..."}}`. A
  second call answers 403. Right after bootstrap or a restart the API can answer 404 or drop the
  connection for a few seconds; `PortalAPI.wait_ready()` in `scripts/portal_client.py` waits for
  `GET /portal-api/themes` to answer 200.

## Lists and pagination

Lists are bare JSON arrays with no total. The default page is 20 items; `per_page` changes it
(`?per_page=100` returns all 40 home-page content blocks; `page_size` is ignored and returns
20). Page with `page=2,3,...` until a short page. `PortalAPI.list_all()` does this.

## Themes

| Call | Response on 1.20.0 |
| --- | --- |
| `GET /themes` | array of `{"ID","Name","Path","Status","Version","Author"}`; `Status` is `Current` or `Not in use` |
| `GET /themes/default/download` | 200 `application/zip`, `Content-Disposition: attachment; filename=default.zip`; every file is listed twice in the archive (identical copies), so `unzip` asks to overwrite; Python `zipfile` is fine |
| `POST /themes/upload`, multipart field `file` | 200 `File uploaded successfully` (plain text). The theme name is `name` in `theme.json`, not the file name |
| `PUT /themes/<name>/activate` | 200 `{"message": "Action activate: Executed successfully","status": "ok"}`; unknown name 422 `{"errors": ["theme 'nope' not found"],"status": "error"}` |
| `DELETE /themes/<name>` | inactive 200 `{"status": "ok"}`; active 422 `{"errors":["cannot delete active theme"]}`; unknown 404 `{"errors": ["theme not found: nope"]}` |
| `GET /themes/<unknown>` | **200** `{"ID": "","Name": "","Status": "Not in use"}`: look a theme up in the list instead |
| `GET /themes/<unknown>/download` | 500 |

Upload rejections (400, plain text):

| Archive | Response |
| --- | --- |
| not a zip | `unpacking notzip.zip: creating unpacker: creating zip reader: zip: not a valid zip file` |
| no `theme.json` | `unpacking no-manifest.zip: file not found: theme.json` |
| theme inside a top folder | `unpacking wrapped.zip: validating wrapped: nested theme "mytheme" is not allowed` |
| a second `theme.json` in a subfolder | `... nested theme "extra" is not allowed` |
| `theme.json` not JSON | `unpacking badjson.zip: invalid character 'n' looking for beginning of object key string` |
| name of 51 characters, or no name | `loading theme: Failed to initialize theme: parsing manifest ...: theme manifest is invalid` |
| a required folder missing | `loading theme: Failed to initialize theme: registering path /themes/<name>/<folder>: not found` |
| total over 32 MB | `File size exceed the maximum content file size limit: 32.00 MB` |

A `loading theme:` rejection has already stored the files: read `pitfalls.md` before uploading
anything that has not passed `build_theme.py --check`.

Accepted on 1.20.0: a 50-character name; a name with spaces (`Example Corp Theme`, then `%20` in
API paths; the build allows only `a-z`, `0-9` and `-`); an empty version; no `assets/` folder.

## Pages and content blocks

| Call | Notes |
| --- | --- |
| `GET /pages` | `ID`, `Title`, `Path`, `Template`, `Status` (`Published` or `Draft`) |
| `PUT /pages/<id>` | send the page object with the new `Title` |
| `POST /pages` | `{"Title","Path","Template","Status":"Published"}`; the template must exist in the active theme |
| `GET /pages/<id>/content-blocks?per_page=100` | `ID`, `Name`, `Content`, `MarkdownEnabled`, `MarkdownContent` |
| `PUT /pages/<id>/content-blocks/<block id>` | send the block with the new `Content` (and `MarkdownContent` when `MarkdownEnabled` is true) |
| `POST /pages/<id>/content-blocks` | `{"Name","Content"}` |

A fresh 1.20.0 Portal has three pages: `/` (template `home`, 40 blocks), `/about-us`
(`flip_flop`, 7 blocks) and `/portal/catalogue-products` (`catalogue`, no blocks).

## Products, plans, catalogues, tags

| Call | Notes |
| --- | --- |
| `GET /products`, `GET /products/<id>` | `Name` (internal, synced from the Dashboard), `DisplayName`, `Description`, `Content`, `Feature`, `Path`, `Logo`, `Preview`, `APIDetails`, `SpecDetails` |
| `PUT /products/<id>` | partial body works: `{"DisplayName","Description","Content","Feature"}` |
| `POST /products/<id>/logo`, `/preview` | multipart field `file`; a PNG answers 200 `{"status":"ok"}`, an SVG 400 `unsupported image format: .svg`; the Portal renames the stored file (a timestamp is added) |
| `GET /tags`, `POST /tags {"Name"}`, `POST /products/<id>/tags {"Name"}` | ASCII letters, digits and `- _ . /` are accepted; a space, a comma or a non-ASCII letter gets 422 `a tag name should consist only of alphabetical characters, including some punctuation characters: ...` |
| `GET /plans`, `PUT /plans/<id>` | `DisplayName`, `Description` |
| `GET /catalogues`, `PUT /catalogues/<id>` | `Name`, `VisibilityStatus` |

A product's API reference is fetched by the Portal itself from `OASUrl`, or from a stored
document uploaded with `POST /products/<id>/api-details/<API id>/oas` (multipart `file`; 200
`{"status":"ok"}`, `OASUrl` cleared, `OASDocument` set). See `pitfalls.md`, item 13.

Documentation-only products (used to cover each API reference renderer):

- `POST /products` `{"Name","DisplayName","Description","IsDocumentationOnly":true,
  "Catalogues":[<id>],"SpecDetails":[{"SpecAlias":"order-events"}]}` -> 201.
- `POST /products/<id>/spec-details/<spec id>/oas` (multipart `file`) uploads an OpenAPI or
  AsyncAPI document -> 200 `{"status":"ok"}`. An AsyncAPI YAML file renders with the AsyncAPI
  layout; the same document in JSON goes to the OpenAPI renderer and fails to parse, so upload
  AsyncAPI as YAML.
- `POST /products/<id>/spec-details/graphql/schema` (multipart `file`, `.graphql`) creates a
  GraphQL spec with an empty `SpecAlias`; set one with `PUT /products/<id>/spec-details/<spec id>
  {"SpecAlias":"country-graph"}`, or the docs page has no address.
- The docs address is `/portal/catalogue-products/<product Path>/<SpecAlias>/docs`.

## Users (for the checks)

`POST /users` with `{"First","Last","Email","Role","OrganisationID","Active":true,"Password"}`
-> 201. Roles used here: `consumer-admin` (a developer who sees every private page) and
`consumer-team-member` (to see the 403 page). With SMTP configured, creating an active user this way
sends that user the "welcome" email.

## Things with no API

Menus, General settings (SMTP, sender, admin address, email subjects, robots.txt, portal
visibility) and the email templates have no admin API endpoint (the public OpenAPI file has no
such paths). Menus and General settings are changed through the admin interface forms;
`scripts/apply_content.py` does this with a signed-in session:

- Sign in: `GET /auth/password/login` (the page carries `<meta name="csrf-token" content="...">`),
  then `POST /auth/password/login` with `login`, `password` and `csrf_token`. The Portal answers
  400 `Bad Request` to a scripted post unless it also carried `Sec-Fetch-Site: same-origin`; with
  it, 302 to `/admin` for an admin.
- Menus: `GET /admin/menus/<id>/edit`, `POST /admin/menus/<id>` (multipart, `_method=PUT`,
  fields `QorResource.Name`, `QorResource.Path`, `QorResource.menus,menu_items[i].ID|Title|Path`).
- General settings: `GET /admin/general`, `POST /admin/general` (multipart, `_method=PUT`,
  47 fields such as `QorResource.EmailSMTPHost`, `EmailDefaultFrom`, `EmailAdmin`, the 13
  `Email*Subject` fields and `RobotsTxtContent`). The script reads the form and posts every
  field back with only the wanted ones changed, so nothing else changes. The
  form answers 200, not a redirect.

Templates (and so the email bodies) change only by uploading a theme.
