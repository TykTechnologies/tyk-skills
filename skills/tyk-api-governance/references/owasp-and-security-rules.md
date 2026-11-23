# OWASP-style API security rules

Last verified: 2026-11-19 against Tyk Dashboard 5.16.0, Tyk Gateway 5.16.0

Results below are what Dashboard 5.16.0 reports from
`POST /api/evaluations/sandbox/oas?ruleset_ids=spectral-owasp` and for saved APIs. Public pages:
https://tyk.io/docs/tyk-governance/ruleset-templates, https://owasp.org/API-Security/.

## The shipped template

`spectral-owasp` ("OWASP API Security", definition `{"extends":[["spectral:owasp","all"]]}`) is
present in every organisation whose licence has the `governance` scope. Use it by ID in a sandbox call, or copy
its definition into your own ruleset (`assets/rulesets/owasp-baseline.json` is that copy) and
link categories to the copy. Templates themselves carry no categories.

Rules that commonly fire, with the severity the Dashboard gives them:

| Rule | Severity | What triggers it |
| --- | --- | --- |
| `owasp-security-hosts-https-oas3` | error | a `servers` URL that is not `https://` (relative URLs too) |
| `owasp-no-http-basic` | error | an `http` security scheme with `scheme: basic` |
| `owasp-no-api-keys-in-url` | error | an `apiKey` scheme `in: query` |
| `owasp-protection-global-unsafe` | error | a `POST`, `PUT`, `PATCH` or `DELETE` with no security (`security: []`, no global security) |
| `owasp-protection-global-unsafe-strict` | info | the same, strict variant |
| `owasp-protection-global-safe` | info | a `GET` with no security |
| `owasp-rate-limit` | error | a 2xx or 4xx response without a rate-limit header (`X-RateLimit-Limit`, `X-Rate-Limit-Limit`, `RateLimit-Limit` with `RateLimit-Reset`, or `RateLimit`) |
| `owasp-rate-limit-retry-after` | error | a 429 response without a `Retry-After` header |
| `owasp-no-numeric-ids` | error | an integer `id` path parameter |
| `owasp-integer-format` | error | an integer schema without `format: int32` or `int64` |
| `owasp-integer-limit` | error | an integer schema without `minimum` and `maximum` |
| `owasp-string-restricted` | error | a string schema without `format`, `pattern`, `enum` or `const` |
| `owasp-define-error-responses-401`, `-429`, `-500` | warn | the operation has no such response, or it has no content schema |
| `owasp-define-error-validation` | warn | none of 400, 422 or 4XX defined |
| `owasp-no-additionalProperties`, `owasp-constrained-additionalProperties` | warn | an object schema with `additionalProperties: true` |

`assets/orders.oas.json` passes the whole template with 0 issues; it shows the shape each rule
wants (rate-limit and `Retry-After` headers in shared `components`, a typed error body,
bounded strings and integers, header-borne API key).

## What these rules cannot see

They read the OpenAPI document only:

- The OpenAPI `security` section is documentation. Whether Tyk authenticates is decided by
  `x-tyk-api-gateway.server.authentication`. A document can pass every OWASP security rule while
  the Gateway lets requests through. Add `tyk-authentication-enabled` from
  `assets/rulesets/tyk-gateway-baseline.json`.
- `owasp-rate-limit` fails until each 2xx and 4xx response **declares** a rate-limit header,
  even when the API has a Tyk rate limit (setting `x-tyk-api-gateway.upstream.rateLimit` does
  not satisfy it). Either declare the header, or switch the rule off and
  enforce `tyk-api-rate-limit` from the baseline instead.
- The upstream URL is in the extension; `tyk-upstream-https` covers it.

`assets/rulesets/tyk-gateway-baseline.json` (four rules: authentication enabled and every
operation has an `operationId` at `error`; HTTPS upstream and an API-level rate limit at `warn`)
reports:

| Document | Findings |
| --- | --- |
| no authentication block, no `operationId`, `http://` upstream, no rate limit | 2 errors, 2 warnings |
| authentication present with `enabled: false` | `tyk-authentication-enabled` (error), `tyk-upstream-https` (warn) |
| `assets/orders.oas.json` | none |

## The HTTPS rule and the servers the Dashboard writes

On save, the Dashboard rewrites `servers` in its stored copy (`concepts.md`). On 5.16.0:

| Dashboard setting and API | Stored `servers` | `owasp-security-hosts-https-oas3` |
| --- | --- | --- |
| defaults, no custom domain | `http://tyk-gateway:8080/orders-legacy`, `/orders-legacy`, then yours | error on each of the first two, even when yours is `https://` |
| defaults, custom domain `api.example.com` | `http://api.example.com/orders`, then yours | error on the first |
| `TYK_DB_HOSTCONFIG_GENERATEHTTPS=true`, no custom domain | `https://tyk-gateway:8080/orders-legacy`, `/orders-legacy`, then yours | error on the relative entry |
| `TYK_DB_HOSTCONFIG_GENERATEHTTPS=true`, custom domain `api.example.com` | `https://api.example.com/orders` only (yours, `https://api.example.com/orders/`, is merged) | passes |

So the sandbox can pass a file that fails once saved. The default fix: override the rule to
`warn` in the ruleset that Dashboard compliance uses (`assets/rulesets/owasp-dashboard.json`;
with it, `assets/orders.oas.json` goes from `non_compliant` to `compliant` once saved), and keep
it at `error` in CI, where the file's own `servers` are what you want to check: a gate that
resolves the category gets the `warn` copy, so add a call with `--ruleset-ids spectral-owasp`
(a file with an `http://` server passes the category call, exit 0, and fails the
`spectral-owasp` call with `owasp-security-hosts-https-oas3`, exit 2).

Where the APIs already use custom domains, setting `host_config.generate_secure_paths`
(environment `TYK_DB_HOSTCONFIG_GENERATEHTTPS`; documented at
https://tyk.io/docs/tyk-dashboard/configuration) to `true` also makes the stored `servers` pass
(last row of the table). Do not add a custom domain (`x-tyk-api-gateway.server.customDomain`)
only to quiet the rule: it changes routing. On Gateway 5.16.0 with
`enable_custom_domains: true`, an API with custom domain `api.example.com` answers `200` for
`Host: api.example.com` and `404 Not Found` for any other host. Routing and custom domains: the
`tyk-api-definitions-routing` skill.

## A sensible starting set

1. One category for every API (for example `baseline`) linked to a copy of the OWASP template
   with `action: warn`, so owners see the dialog, and to the Tyk gateway baseline.
2. While teams catch up, override the rules that need document work (`owasp-rate-limit`,
   `owasp-string-restricted`, `owasp-integer-limit`) to `warn` with string overrides, then raise
   them to `error` later. Raising severity re-evaluates every API in the category, and any API
   with a new error becomes non-compliant without its owner touching it.
3. Gate pull requests on the same rulesets (`ci-gates.md`); hard-stop Kubernetes delivery with
   a `block` `TykRuleset` (`operator-admission.md`).
