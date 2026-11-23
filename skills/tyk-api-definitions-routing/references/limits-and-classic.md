# OpenAPI support, and what still needs the classic format

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`) and Tyk Dashboard 5.16.0

## OpenAPI versions

| `openapi` value | Result |
| --- | --- |
| `3.0.0`, `3.0.1`, `3.0.3`, `3.0.4` | accepted |
| `3.1.0`, `3.1.1`, `3.1.2`, `3.1` | accepted |
| `3.0`, `3` | `400 openapi: Does not match pattern '^3\.0\.\d(-.+)?$'` |
| `3.2.0` | `400 Schema not found for version "3.2.0"` |
| Swagger 2.0 (`"swagger": "2.0"`) | rejected by every Tyk OAS endpoint |

JSON and YAML are both accepted by the import endpoints.

OpenAPI 3.1 features that load: a type list such as `"type": ["string", "null"]` (and request
validation enforces it), `webhooks` with inline schemas, `info.summary`, `license.identifier`,
`jsonSchemaDialect`, `const` and `examples` in schemas. In a 3.0 document a type list is rejected
(`components.schemas.A.type: Invalid type. Expected: string, given: array`); use
`"nullable": true` there.

## What fails to load

| In the document | Message |
| --- | --- |
| `$ref` to a component that does not exist | `request malformed` |
| `$ref` to another file (`./schemas.json#/Order`) | `request malformed` |
| `$ref` to a URL | `request malformed` |
| A `$ref` anywhere inside `webhooks` | `invalid webhooks: webhook "evt": invalid operation POST: found unresolved ref: "#/components/schemas/E"` |
| `components.pathItems` | `invalid components: extra sibling fields: [pathItems]` |
| A 3.1 document with `webhooks` and no `paths` | `invalid paths: must be an object` |
| A path parameter in the path but not in `parameters` | `invalid paths: operation GET /u/{id} must define exactly all path parameters (missing: [id])` |
| The same `operationId` twice | `invalid paths: operations "GET /a" and "GET /b" have the same operation id "same"` |
| An operation without `responses` | `paths./a.get.responses: Invalid type. Expected: object, given: null` |
| A path without a leading slash | `paths: Additional property a is not allowed` |
| `security` naming a scheme when the document has no `components` | `No components or security schemes present in OAS` |
| A `mutualTLS` security scheme | see `versioning-and-authentication.md` |

What works: `$ref` to `#/components/schemas`, `#/components/parameters` and
`#/components/responses` from `paths`, and recursive schemas. So a document split across files
must be bundled into one file first, and a schema used by a webhook must be written inline.

Two paths that differ only in the parameter name (`/a/{x}` and `/a/{y}`) are accepted.

## Tyk OAS covers REST; these need the classic format

The documentation says Tyk OAS definitions "are used for REST and streaming use cases", that
classic definitions "are used for GraphQL, XML/SOAP and TCP services", and that Tyk 5.8 "reached
effective parity with Tyk Classic" for REST. See
https://tyk.io/docs/api-management/gateway-config-introduction. On 5.16.0:

| Need | In Tyk OAS | In the classic format |
| --- | --- | --- |
| **GraphQL** (validation of queries against a schema, and the other GraphQL features) | The extension has no GraphQL section. A `graphql` block is accepted and dropped; the readback holds only `info`, `server`, `upstream` | A classic API with `"graphql": {"enabled": true, "execution_mode": "proxyOnly", "version": "2", "schema": "type Query { hello: String }"}` proxies `{ hello }` and refuses `{ nope }` with `400 {"errors":[{"message":"field: nope not defined on type: Query","path":["query","nope"]}]}` |
| **TCP proxying** | `server.protocol` accepts only `http`, `https`, `h2c`; `"tcp"` is rejected (message in `definition-shape.md`) | A classic API with `"protocol": "tcp", "listen_port": 9100, "proxy": {"target_url": "tcp://orders.example.com:8080"}` carries a raw connection on port 9100. The Gateway must allow the port (`ports_whitelist` or `disable_ports_whitelist`) |

Classic definitions use their own endpoints: `POST /tyk/apis` on the Gateway API and
`/api/apis` on the Dashboard API. The two families do not mix; using an OAS endpoint on a
classic API, or the reverse, returns the errors in `definition-shape.md`. Docs:
https://tyk.io/docs/api-management/gateway-config-tyk-classic,
https://tyk.io/docs/api-management/graphql, https://tyk.io/docs/key-concepts/tcp-proxy

Things that look like they need classic and do not:

- **XML bodies.** The page above lists XML/SOAP under classic, but a Tyk OAS API proxies an XML
  request and transforms it with `"format": "xml"` (see `operation-middleware.md`). Proxying
  and transforming XML does not need the classic format.
- **Client certificates.** They are configured in the extension, not as an OpenAPI `mutualTLS`
  scheme (see `versioning-and-authentication.md`).
- **A regular expression for one path segment.** Not expressible in the path itself; use the
  parameter's schema with request validation (see `listen-paths-and-matching.md`).

To move an existing classic REST API to Tyk OAS, the Dashboard has a migration tool. See
https://tyk.io/docs/api-management/migrate-from-tyk-classic.

MCP proxies are also Tyk OAS documents with their own fields and endpoints; they belong to the
`tyk-mcp-gateway` skill.
