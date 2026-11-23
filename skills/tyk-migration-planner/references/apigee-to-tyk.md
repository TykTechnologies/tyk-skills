# Apigee to Tyk

Last verified: 2026-11-19 against the Tyk documentation for Gateway and Dashboard 5.16 and Portal 1.20, and https://tyk.io/tyk-mcp-vs-apigee-mcp/

Mapping for an inventory taken from Apigee (X or hybrid). Every Tyk equivalent cites the Tyk
page that documents it; "verified" means the page documents the Tyk feature as described, not
that the behaviour is identical to Apigee. Prove the rows that
matter in a proof of concept (the `tyk-poc-runbook` skill). Tyk's comparison page:
https://tyk.io/tyk-vs-apigee/.

## Platform concepts

| Apigee | Tyk equivalent | Tyk docs | Status |
| --- | --- | --- | --- |
| API proxy (proxy endpoint + target endpoint) | Tyk OAS API definition: listen path and upstream URL in `x-tyk-api-gateway` | https://tyk.io/docs/api-management/gateway-config-tyk-oas | verified |
| API product (bundle of resources, quota, scopes) | Security policy (access rights, rate limit, quota) for enforcement; Developer Portal Product for publishing ("strategic packages of API & MCP resources") | https://tyk.io/docs/api-management/policies, https://tyk.io/docs/portal/api-products | verified |
| Developer app (credentials) | Portal Developer App: "containers for API access credentials" | https://tyk.io/docs/portal/developer-app | verified |
| Developer, company | Portal developers within organisations and teams | https://tyk.io/docs/portal/overview/concepts | verified |
| Existing app keys | Opaque keys whose values can be exported: import with their values through Portal custom credentials or the Gateway `POST /tyk/keys/{keyID}`. OAuth client secrets, tokens and other credential types need their own plan | https://tyk.io/docs/tyk-developer-portal/import-custom-credentials, https://tyk.io/docs/api-management/authentication/bearer-token | verified |
| Environments (test, prod) | Separate Tyk environments; promote APIs, keys and policies between them | https://tyk.io/docs/api-management/multiple-environments | verified |
| Shared flows | API Fragments (Dashboard 5.16.0): shared configuration referenced by many Tyk OAS APIs. Shares configuration, not a sequence of policies | https://tyk.io/docs/platform-management/api-fragments | partial |
| Analytics | Dashboard analytics, and Tyk Pump to external stores | https://tyk.io/docs/api-management/dashboard-analytics, https://tyk.io/docs/api-management/tyk-pump | verified |
| Apigee hybrid runtime | Tyk Hybrid (your Gateways, Tyk Cloud control plane) or self-managed MDCB | https://tyk.io/docs/deployment-options/tyk-hybrid, https://tyk.io/docs/api-management/mdcb | verified |

## Policies

| Apigee policy | Tyk equivalent | Tyk docs | Status |
| --- | --- | --- | --- |
| VerifyAPIKey | Auth token (bearer token) authentication | https://tyk.io/docs/api-management/authentication/bearer-token | verified |
| OAuthV2 (Apigee issues tokens) | Tyk OAuth 2.0 authorization server | https://tyk.io/docs/api-management/authentication/oauth-2 | verified |
| OAuthV2 / VerifyJWT with an external IdP | JWT authentication, or the `oauth2` security scheme for an external IdP | https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/json-web-tokens, https://tyk.io/docs/api-management/authentication/oauth2-authentication | verified |
| Quota | Quotas in a policy or key, tracked in Redis across Gateway nodes | https://tyk.io/docs/api-management/rate-limit | verified |
| SpikeArrest | Redis Rate Limiter, whose blocking behaviour the docs call "spike arrest"; optional Rate Limit Smoothing | https://tyk.io/docs/api-management/rate-limit | verified |
| AssignMessage | Request and response header and body transforms; URL rewriting | https://tyk.io/docs/api-management/traffic-transformation/request-headers, https://tyk.io/docs/api-management/traffic-transformation/request-body, https://tyk.io/docs/api-management/traffic-transformation/response-body, https://tyk.io/docs/transform-traffic/url-rewriting | verified |
| ExtractVariables | Request context variables, usable in later transforms | https://tyk.io/docs/api-management/traffic-transformation/request-context-variables | verified |
| JavaScript | JavaScript plugins (built-in JavaScript engine) | https://tyk.io/docs/api-management/plugins/javascript | verified |
| ServiceCallout | Virtual endpoints: "From a virtual endpoint, you can make calls out to other internal and upstream APIs". A virtual endpoint builds and returns the response itself. A ServiceCallout usually stores the called service's response in a flow variable and lets the proxy flow carry on to the target, so reproducing it means writing that logic (a virtual endpoint that also calls the target, or a plugin): a rewrite, not a setting | https://tyk.io/docs/api-management/traffic-transformation/virtual-endpoints | partial |
| KeyValueMapOperations (KVM) | Secrets Management: `env`, `inline`, `file`, HashiCorp Vault and Consul for all users; AWS Secrets Manager, Azure Key Vault and GCP Secret Manager in the Enterprise Edition. Read-only references, not a runtime read/write map | https://tyk.io/docs/tyk-configuration-reference/kv-store | partial |
| ResponseCache | Response caching | https://tyk.io/docs/api-management/response-caching | verified |
| CORS | CORS per API | https://tyk.io/docs/api-management/security-features | verified |
| OASValidation | Request validation against a defined request schema (non-matching requests get `HTTP 422`) | https://tyk.io/docs/api-management/traffic-transformation/request-validation | verified |
| RaiseFault | Mock response, or a virtual endpoint returning the error | https://tyk.io/docs/api-management/traffic-transformation/mock-response | verified |
| Java callout | No Java plugin type that runs inside the gateway; rewrite as a Go plugin, or as a gRPC plugin served by your own process (gRPC plugins "can be written in any language"; the rich plugins page has a tutorial for a gRPC-based Java plugin). | https://tyk.io/docs/api-management/plugins/overview, https://tyk.io/docs/api-management/plugins/rich-plugins | no direct equivalent |

The KVM row is "partial": Apigee KVMs can be written at runtime by a policy; Tyk Secrets
Management references values held in a store. A runtime read/write store needs a plugin.

## MCP: Apigee MCP and Tyk MCP

From https://tyk.io/tyk-mcp-vs-apigee-mcp/: "Apigee hosts MCP proxies as a
managed Google Cloud service, transcoding OpenAPI specs into MCP tools and surfacing them through
API hub — all behind the Apigee subscription", while Tyk "runs anywhere — on-prem, hybrid,
multi-cloud". The page lists Tyk patterns: API-to-MCP from OpenAPI, remote MCP as upstream,
Dashboard and Docs as MCP, per-tool, per-resource and per-prompt policies, JSON-RPC method and
primitive rate limits, MCP-tagged analytics, Developer Portal listing of MCP tools, and a mock
MCP server.

Documentation for each: https://tyk.io/docs/ai-management/mcp-gateway/overview (remote MCP
proxies, policies, observability), https://tyk.io/docs/ai-management/mcps/api-to-mcp (REST API
to MCP). Portal MCP Products need Portal 1.19.0 or later
(https://tyk.io/docs/developer-support/release-notes/portal). Building them: the
`tyk-mcp-gateway` skill (Gateway side) and the `tyk-developer-portal-setup` skill (Portal side).

**Verify before quoting.** The comparison page says "Tyk's MCP support lives in the open source
gateway under MPL 2.0". The documentation says REST API to MCP "requires Tyk Enterprise Edition
for the proxy type itself" (https://tyk.io/docs/ai-management/mcp-gateway/overview). Present both;
the `tyk-facts` skill (`references/editions-and-features.md`) has the full wording.

## Apigee-specific migration notes

- **Products become two things.** Enforcement (access rights, limits, quota) lives in a Tyk
  policy (the `tyk-rate-limits-keys-policies` skill); the catalogue entry developers subscribe
  to is a Portal Product built on it (the `tyk-developer-portal-setup` skill).
- **Keep app keys where the credential allows it.** Import existing opaque key values (rows
  above). The `tyk-demo-builder` skill's `key-import` module shows the Dashboard custom-key route
  with keys minted for the bundle; the `tyk-developer-portal-setup` skill covers attaching
  imported keys to Portal apps. A key sent in a query parameter is usually configuration, not
  a plugin: set the token location on the API
  (https://tyk.io/docs/api-management/authentication/bearer-token); `js-plugin` is for what
  configuration cannot express.
- **Keep hostnames and base paths** with `customDomain` and the listen path
  (https://tyk.io/docs/api-management/gateway-config-tyk-oas); the builder's `custom-domain`
  module proves it.
- **Proxy bundles are not imported.** Start from each proxy's OpenAPI document as a Tyk OAS API,
  then re-create the policy chain with the rows above.
