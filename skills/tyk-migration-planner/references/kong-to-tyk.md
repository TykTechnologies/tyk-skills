# Kong to Tyk

Last verified: 2026-11-19 against the Tyk documentation for Gateway and Dashboard 5.16 and Operator 1.6 and `tykio/tyk-gateway:v5.16.0`

Concept-by-concept mapping for an inventory taken from Kong Gateway (open source or
Enterprise, DB or DB-less). Every Tyk equivalent cites the Tyk page that documents it.
"Verified" means the cited page documents the Tyk feature as described; it is not a claim of
drop-in compatibility. Prove the rows that matter in a
proof of concept (the `tyk-poc-runbook` skill). Tyk's own comparison page:
https://tyk.io/docs/compare/tyk-vs-kong-db-less (and https://tyk.io/tyk-vs-kong/).

## Platform concepts

| Kong | Tyk equivalent | Tyk docs | Status |
| --- | --- | --- | --- |
| Service + Route | One Tyk OAS API definition: `x-tyk-api-gateway.server.listenPath` (the route) and `x-tyk-api-gateway.upstream.url` (the service) | https://tyk.io/docs/api-management/gateway-config-tyk-oas | verified |
| Upstream with targets (load balancing) | Upstream load balancing across a target list: "Tyk supports round-robin load balancing across multiple upstream targets", with weights and uptime tests | https://tyk.io/docs/planning-for-production/ensure-high-availability/load-balancing | verified |
| Consumer + credential | A key (session) attached to one or more security policies | https://tyk.io/docs/api-management/policies | verified |
| Consumer groups / ACL groups | Policies; a key can carry several policies, merged by documented rules | https://tyk.io/docs/api-management/access-control/policies/applying-policies | verified |
| Kong Manager | Tyk Dashboard (licensed) | https://tyk.io/docs/api-management/dashboard-configuration | verified |
| Workspaces / RBAC | Dashboard organisations (multi-tenant), users, teams and RBAC (licensed) | https://tyk.io/docs/api-management/user-management | verified |
| Kong Dev Portal | Tyk Developer Portal (licensed; included in Core per https://tyk.io/pricing/; confirm inclusion for the package being bought with Tyk sales; see the `tyk-facts` skill) | https://tyk.io/docs/portal/overview/intro | verified |
| DB-less mode (declarative file per node) | Open source Gateway reading API definitions from its apps folder: "API definitions are stored in the apps folder ... scanned for .json files and re-scanned at startup or reload" (`app_path`); shared state in Redis | https://tyk.io/docs/tyk-oss-gateway/configuration | verified |
| Kong Ingress Controller | Tyk Operator (licensed): CRDs such as `TykOasApiDefinition` and `SecurityPolicy`; Operator 1.6.0 (released 6 November 2026) adds Kubernetes Gateway API support (`GatewayClass`, `Gateway`, `HTTPRoute`) with Tyk Dashboard. Setup and limits: the `tyk-gitops-sync-operator` skill | https://tyk.io/docs/api-management/automations/operator, https://tyk.io/docs/product-stack/tyk-operator/gateway-api/overview | verified |
| decK (`gateway dump`, `gateway sync`, `gateway diff`) | Tyk Sync 2.x (`dump`, `publish`, `update`, `sync`; licensed Dashboard required) and Operator CRDs | https://tyk.io/docs/api-management/automations/sync | verified; no `diff` command, see `gitops-tyk-sync-vs-operator.md` |

## Plugins

| Kong plugin | Tyk equivalent | Tyk docs | Status |
| --- | --- | --- | --- |
| key-auth | Auth token (bearer token). Existing key values can be imported: "import them to Tyk via `POST /tyk/keys/{keyID}`" | https://tyk.io/docs/api-management/authentication/bearer-token | verified |
| basic-auth | Basic authentication | https://tyk.io/docs/api-management/authentication/basic-authentication | verified |
| jwt | JWT authentication | https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/json-web-tokens | verified |
| oauth2 (Kong as authorization server) | Tyk OAuth 2.0 authorization server: "use Tyk Gateway as a built-in OAuth 2.0 authorization server to issue and manage access tokens" | https://tyk.io/docs/api-management/authentication/oauth-2 | verified |
| openid-connect | JWT validation of tokens from an external OIDC provider; or the `oauth2` security scheme for external IdPs (scopes, Protected Resource Metadata). This covers Kong acting as a resource server that validates JWTs. The Kong plugin can also act as an OpenID Connect relying party (authorization code flow, sessions; https://developer.konghq.com/plugins/openid-connect/); those uses are not covered by this row | https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/json-web-tokens, https://tyk.io/docs/api-management/authentication/oauth2-authentication | partial |
| hmac-auth | HMAC request signing: "Tyk currently implements the latest draft of the HMAC Request Signing standard" | https://tyk.io/docs/basic-config-and-security/security/authentication-authorization/hmac-signatures | verified |
| mtls-auth | Mutual TLS client authentication | https://tyk.io/docs/api-management/implement-tls | verified |
| acl | Policy access rights (which APIs, versions and paths a key may call) | https://tyk.io/docs/api-management/policies | verified |
| rate-limiting, rate-limiting-advanced | Rate limits and quotas at API, key and policy level; limiter choice (Distributed Rate Limiter by default, Redis Rate Limiter for one cluster-wide counter) | https://tyk.io/docs/api-management/rate-limit | verified |
| request-size-limiting | Request size limits | https://tyk.io/docs/api-management/traffic-transformation/request-size-limits | verified |
| proxy-cache | Response caching | https://tyk.io/docs/api-management/response-caching | verified |
| cors | CORS per API (`cors` in the Tyk OAS extension) | https://tyk.io/docs/api-management/security-features | verified |
| request-transformer | Request header and request body transforms | https://tyk.io/docs/api-management/traffic-transformation/request-headers, https://tyk.io/docs/api-management/traffic-transformation/request-body | verified |
| response-transformer | Response header and response body transforms | https://tyk.io/docs/api-management/traffic-transformation/response-headers, https://tyk.io/docs/api-management/traffic-transformation/response-body | verified |
| ip-restriction | IP access control per API (`ipAccessControl` in the Tyk OAS extension) | https://tyk.io/docs/api-management/gateway-config-tyk-oas | verified |
| bot-detection | No direct equivalent: the full documentation text (https://tyk.io/docs/llms-full.txt) documents no bot-detection feature. Options: IP access control, rate limits, or a custom plugin | n/a | no direct equivalent |
| pre-function, post-function, serverless functions | JavaScript plugins, Go plugins, gRPC plugins; virtual endpoints for gateway-side logic that answers the request | https://tyk.io/docs/api-management/plugins/overview, https://tyk.io/docs/api-management/traffic-transformation/virtual-endpoints | verified |
| prometheus | Gateway exports its own metrics over OpenTelemetry (OTLP); Tyk Pump also exposes "Prometheus, StatsD, or DogStatsD metrics derived from traffic logs" (a page the docs now title "Metrics Pumps (Legacy)"). Setup: the `tyk-observability` skill | https://tyk.io/docs/api-management/observability, https://tyk.io/docs/api-management/metrics/metrics-pumps | verified |
| datadog | Traces through the OpenTelemetry Collector to Datadog; analytics through Pump to Datadog | https://tyk.io/docs/api-management/traces/datadog, https://tyk.io/docs/api-management/tyk-pump | verified |
| opentelemetry | Native OpenTelemetry traces and metrics in the Gateway | https://tyk.io/docs/api-management/observability | verified |
| AI MCP Proxy | Tyk MCP Gateway (remote MCP proxies on all licences; REST API to MCP is Enterprise Edition per the docs) | https://tyk.io/docs/ai-management/mcp-gateway/overview | verified; licensing: see `tyk-facts` |

Anything not in these tables: search https://tyk.io/docs/llms.txt for the capability, cite the
page, and mark the row "verified" only after reading it. Mark it "no direct equivalent" when no
page documents it; never infer support from a marketing page alone.

## Kong-specific migration notes

- **Rate-limit semantics differ.** Tyk's default Distributed Rate Limiter "divides the configured
  rate limit between the number of addressable gateway instances"; switch on
  `enable_redis_rolling_limiter` for one shared counter
  (https://tyk.io/docs/api-management/rate-limit). With more than one gateway, a low limit goes
  through Redis even with the default limiter (`drl_threshold`). Re-test limits after migration
  (criterion C2 in the `tyk-poc-runbook` skill; counting in detail: the
  `tyk-rate-limits-keys-policies` skill).
- **Keep consumer keys where the credential allows it.** Opaque key values that can be exported
  in plaintext can be imported instead of re-issued (`POST /tyk/keys/{keyID}`, above; on Gateway
  5.16.0 a value shorter than `min_token_length`, 3 by default, was refused with `400`). Hashed
  secrets, OAuth clients and tokens, JWT trust and certificates each need their own plan. The
  `tyk-demo-builder` skill's `key-import` module shows the Dashboard custom-key route with keys
  minted for the bundle. For a key sent in a query parameter, a cookie or another header,
  configure the token location on the API first
  (https://tyk.io/docs/api-management/authentication/bearer-token; the planner's `SKILL.md`,
  "Credential location: native first", has the run); the builder's `js-plugin` module is for
  cases configuration cannot express. Credentials on the Tyk side: the `tyk-authentication`
  skill.
- **Keep URLs.** Bind APIs to the existing host with `customDomain` and keep the listen path
  (https://tyk.io/docs/api-management/gateway-config-tyk-oas); the builder's `custom-domain`
  module proves it.
- **Lua plugins.** Tyk documents custom plugins "in Go, JavaScript, Python, Lua and gRPC"
  (https://tyk.io/docs/compare/tyk-vs-kong-db-less), but Kong's Lua plugin interface is not Tyk's;
  plan a rewrite against the Tyk plugin API, not a copy.
- **OpenAPI first.** If Kong configuration was generated from OpenAPI documents, import those
  documents as Tyk OAS APIs rather than translating Kong YAML
  (https://tyk.io/docs/api-management/gateway-config-managing-oas).
