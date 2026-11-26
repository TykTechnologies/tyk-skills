# Editions and features

Last verified: 2026-11-19 against the Tyk documentation for Gateway and Dashboard 5.16, Portal 1.20, Operator 1.6, Sync 2.3 and AI Studio 2.3, the Tyk Editions and Feature Availability page (https://tyk.io/docs/apim), and AI Studio 2.3.0 (Community and Enterprise images)

What runs where, and what needs a licence. Every row cites the public page it comes from, or
states the product's behaviour on the version it names. The top-level boundary is in
`licensing-and-packaging.md`; quote that first, then use this page for the detail.

## Open source Tyk Gateway (MPL 2.0, image `tykio/tyk-gateway`)

- Protocols and authentication: "Use any protocol: REST, SOAP, GraphQL, gRPC, and TCP" and
  "Industry Standard Authentication: OIDC, JWT, bearer Tokens, Basic Auth, Client Certificates
  and more" (https://tyk.io/docs/tyk-oss-gateway).
- Cluster-wide rate limits and quotas: "With Tyk's Redis Rate Limiter, every Gateway respects
  one cluster-wide rate limit through a shared counter ... This is part of the open-source Tyk
  Gateway" (https://tyk.io/docs/apim). The default limiter is the Distributed Rate Limiter,
  which keeps its counters in each gateway's memory; the Redis Rate Limiter is switched on with
  `enable_redis_rolling_limiter` (https://tyk.io/docs/api-management/rate-limit). The default is
  not always Redis-free: with more than one gateway, a limit at or below `drl_threshold` x the
  number of gateways, in requests per second, is enforced through Redis; `drl_threshold`
  defaults to 5 (https://tyk.io/docs/tyk-oss-gateway/configuration#drl_threshold, which gives
  the formula and the example "you have 2 Gateways, and your threshold is set to 5, if a user
  rate limit is larger than 10, it will use the distributed rate limiter algorithm"). The
  `tyk-rate-limits-keys-policies` skill covers counting in detail; the `tyk-poc-runbook` skill's
  criterion C2 tests it.
- Custom plugins in Go, gRPC (any language), Python and JavaScript
  (https://tyk.io/docs/api-management/plugins/overview; the Editions page lists all four in the
  Community Edition column).
- MCP proxies in front of a remote MCP server: "Proxies fronting a remote MCP server are
  available on all Tyk Gateway licenses" (https://tyk.io/docs/ai-management/mcp-gateway/overview),
  from Gateway v5.13 (same page: "v5.13 or later for pass-through MCP proxies").
- Secret stores `env`, `inline`, `file`, `hashicorp_vault`, `hashicorp_consul` are "All users"
  (https://tyk.io/docs/tyk-configuration-reference/kv-store).
- "The open-source Gateway already includes OIDC (via JWT validation), JWT, Basic Auth and
  client-certificate (mTLS) authentication, plus rate limiting and quotas"
  (https://tyk.io/docs/tyk-open-source). Without a Dashboard there is no GUI, no Tyk Sync and no
  Developer Portal (see "Licensed, not part of the open source Gateway" below).

## Enterprise Edition gateway build (image `tykio/tyk-gateway-ee`, licensed)

It is the gateway build every paid customer runs (`licensing-and-packaging.md`) and adds Tyk
Streams and advanced upstream authentication. The Editions page FAQ adds: "Some features, such as
upstream OAuth, token exchange and Tyk Streams, are only in the Enterprise build
(`tykio/tyk-gateway-ee`)" (https://tyk.io/docs/apim). Features the docs mark Enterprise:

| Feature | Since | What the page says | Source |
| --- | --- | --- | --- |
| Tyk Streams | Dashboard 5.7.0 ("Tyk Streams API support was introduced in Tyk Dashboard v5.7.0", https://tyk.io/docs/api-management/automations/sync) | "available as part of the Enterprise Edition"; the Dashboard licence "must include the `streams` scope" | https://tyk.io/docs/api-management/event-driven-apis |
| Upstream OAuth 2.0 | n/a | Availability: Gateway and Dashboard, "Enterprise" | https://tyk.io/docs/api-management/upstream-authentication/oauth |
| Upstream Basic Auth and OAuth | n/a | "only available to licensed users, via the Tyk Dashboard. These features are not available to open source users." | https://tyk.io/docs/api-management/upstream-authentication |
| Token exchange (RFC 8693) | 5.14.0 | Availability: "Enterprise"; "In open source deployments, token exchange does not execute at runtime" | https://tyk.io/docs/api-management/authentication/token-exchange |
| REST API to MCP proxies | 5.15.0 | Availability table: Gateway, "Enterprise". The MCP Gateway overview adds that it "requires Tyk Enterprise Edition for the proxy type itself" | https://tyk.io/docs/ai-management/mcps/api-to-mcp, https://tyk.io/docs/ai-management/mcp-gateway/overview |
| Secret stores AWS Secrets Manager, Azure Key Vault, GCP Secret Manager | n/a | Availability: "Enterprise Edition" | https://tyk.io/docs/tyk-configuration-reference/kv-store |

The Editions page (https://tyk.io/docs/apim, "Tyk Editions and Feature Availability") is the
canonical Community-versus-Enterprise table. Its columns are Community Edition and the three
Enterprise Edition deployment options (Self-Managed, Tyk Cloud, Hybrid). Its "MCP Gateway" rows:
MCP proxy for a remote MCP server and MCP client authorization with OAuth 2.0 and Protected
Resource Metadata are in the Community Edition; REST API to MCP proxy, upstream OAuth 2.0 for MCP
servers and token exchange for MCP servers are not. Its "Multi-Region and Automation" rows put
MDCB, Tyk Operator and Tyk Sync outside the Community Edition. A few rows also differ between the
Enterprise deployment options (for example gRPC, TCP and WebSocket proxying, certificate
authentication and gRPC plugins are marked unavailable on Tyk Cloud, and Kafka "On request");
read the row before promising a feature on Tyk Cloud.

The Gateway source tree states the split itself: MPL 2.0 "except for the 'ee' folder", which "is
subject to a separate commercial license" (`LICENSE.md` in https://github.com/TykTechnologies/tyk
at `v5.16.0`).

## Licensed, not part of the open source Gateway

The Editions page (https://tyk.io/docs/apim) marks these as not in the Community Edition: every
row of its "Tyk Dashboard" table (API Designer, API Design Governance, API traffic analytics,
user management with RBAC, local user login, single sign-on, user audit logs, Open Policy Agent
permission rules, multi-tenant with separated data, the Access Policy designer, certificate
store management), every row of its "Developer Portal" table, GraphQL Federation and Universal
Data Graph, the Identity Provider Registry, Kafka and Tyk Streams, upstream Basic, OAuth 2.0 and
token exchange, cloud secret stores, MDCB, Tyk Operator and Tyk Sync. Most of these are Dashboard
features or need the Dashboard. Tyk Operator is the exception: it is licensed, but it can manage
an open source Gateway directly (`TYK_MODE` `ce`; see "Tyk Operator" below). Its Kubernetes
Gateway API support needs the Dashboard. Also:

- **MCP proxies as a Dashboard resource.** "Tyk Dashboard 5.13.0 adds comprehensive support for
  the MCP ... Gateway. A dedicated `/api/mcps` endpoint set provides full CRUD operations for MCP
  Proxy definitions" (5.13.0 section,
  https://tyk.io/docs/developer-support/release-notes/dashboard). In 5.15.0 (same page): "You can
  now create an MCP proxy directly from an API already registered in Tyk". MCP proxy definitions
  "require Tyk OAS format" (https://tyk.io/docs/ai-management/mcp-gateway/overview). The
  `tyk-mcp-gateway` skill covers building them.
- **API governance.** Introduced in Dashboard 5.15.0 as "a new Enterprise Edition capability,
  built directly into the Dashboard" (https://tyk.io/docs/developer-support/release-notes/dashboard).
  "Tyk Governance is delivered as a license claim toggle on Tyk Dashboard. It is disabled by
  default and enabled per organization on request. It is not self-service." and "Without the
  governance scope on your license, every governance endpoint returns `403 Forbidden`"
  (https://tyk.io/docs/tyk-governance/overview).
- **Developer Portal MCP products.** Portal 1.19.0: "Tyk Developer Portal now provides
  first-class support for Tyk MCP Proxies. MCP Proxies now sync to Portal automatically, MCP
  Products can be created, complete with documentation and published in Catalogues"
  (https://tyk.io/docs/developer-support/release-notes/portal). The Editions page FAQ: "Tyk
  Developer Portal needs its own license and a connection to Tyk Dashboard."

## Tyk Operator (licensed)

- "Tyk Operator is a licensed, supported Tyk product (not open source): it needs a license key"
  (https://tyk.io/docs/tyk-stack/tyk-operator/installing-tyk-operator).
- It can target the open source Gateway, still with a licence: "although Tyk Operator is
  compatible with the Open Source Tyk Gateway, a valid license key is required for running Tyk
  Operator" (same page, "Open Source" tab, `TYK_MODE` `ce`).
- "Tyk Operator 1.6 adds Kubernetes Gateway API support: you can route traffic through your Tyk
  Gateways with the standard GatewayClass, Gateway and HTTPRoute resources (with Tyk Dashboard)"
  (https://tyk.io/docs/apim). Operator 1.6.0 was released on 6 November 2026
  (https://tyk.io/docs/developer-support/release-notes/operator), and `v1.6.0` is the newest
  strict tag of `tykio/tyk-operator` on Docker Hub; resolve it live with
  `versions-and-images.md`. The `tyk-gitops-sync-operator` skill covers using it.
- Custom resources include `TykOasApiDefinition`, `SecurityPolicy`, `TykStreamsApiDefinition`
  and `TykMcpProxyDefinition` (https://tyk.io/docs/api-management/automations/operator,
  https://tyk.io/docs/ai-management/mcps/api-to-mcp).

## Tyk Sync

The supported target for Tyk Sync 2.x is a licensed Tyk Dashboard. That is Tyk's support policy.
Say "not supported with the open source Gateway"; do not say it cannot technically work, and do
not recommend trying it.

- "From Tyk Sync v2.0, Tyk Sync will be closed source and we will only support use of Tyk Sync
  with licensed Tyk Dashboard" (2.0 section, https://tyk.io/docs/developer-support/release-notes/sync,
  which is headed "Licensed Protected Product").
- "Tyk Sync is no longer usable with the Open Source (OSS) version of the Tyk Gateway"
  (https://tyk.io/docs/api-management/automations/sync). No separate Sync licence key is
  described; it uses the Dashboard URL and a Dashboard API secret.

## MDCB (licensed)

"Tyk's Multi Data Center Bridge (MDCB) is a separately licensed extension to the Tyk control
plane" (https://tyk.io/docs/api-management/mdcb). The Editions page lists it as paid ("license
key required") and outside the Community Edition, available on Self-Managed, Tyk Cloud and
Hybrid (the Enterprise Edition, the licensed software, not the Enterprise package). What a given
quote includes is commercial: ask Tyk sales (`licensing-and-packaging.md`, MDCB bullet).

## Tyk AI Studio

- Two editions: "Tyk AI Studio is available in two editions: Community Edition (open source)
  and Enterprise Edition". Community Edition: "No license is required." Enterprise Edition: "A
  valid license key is required." (https://tyk.io/docs/ai-management/ai-studio/overview).
- Community Edition source: https://github.com/TykTechnologies/ai-studio, licensed under the
  GNU Affero General Public License v3 (`LICENSE.md`, and the README badge "License: AGPL v3").
  This is AGPL-3.0, not MPL 2.0 like the Gateway. The Editions page FAQ: "Tyk AI Studio
  Community Edition is open source under the GNU Affero General Public License v3.0 (AGPL 3.0).
  You can use it in production with no license."
- Images: Community Edition `tykio/tyk-ai-studio` with the Edge Gateway `tykio/tyk-microgateway`;
  Enterprise Edition `tykio/tyk-ai-studio-ent` with `tykio/tyk-microgateway-ent`. The
  Enterprise images refuse to start without a licence that covers AI Studio 2.3: with none, the
  control plane logs `license validation failed at boot: no license provided`; with a Tyk
  Dashboard licence, `license does not include the Tyk AI Studio SKU` (AI Studio 2.3.0). They
  are not a way to run the Community features without a licence.
- Enterprise-only per the feature table on the overview page (and the same table on
  https://tyk.io/docs/apim): budget management, enforcement and alerts, team budgets, model
  routers, semantic routers, filter scripts including tool calls, guardrail filters, advanced
  SSO (SAML, OIDC), fine-grained RBAC (roles and permissions), audit trail, governed metadata,
  outbound webhooks, Tyk Dashboard MCP integration, semantic caching (Advanced LLM Cache plugin),
  asset catalog plugin, priority support. In both editions: LLM proxy gateway, chat interface,
  tool integration, user management (admin and standard users), hub-and-spoke deployment, cost
  tracking and analytics, plugin system, main ingress and Anthropic Messages endpoint, LLM
  failover, embedders.
- AI Studio 2.3.0 behaves as that table says (Community images with no licence, Enterprise
  images with an AI Studio 2.3 licence). On the Community Edition, Enterprise-only admin
  endpoints answer `402`, or `403` with the title `Enterprise Feature`: for example
  `POST /api/v1/model-routers` answers `402` "model router is an Enterprise Edition feature
  ...", and `GET /api/v1/audit/records` answers `403` "the audit trail is an Enterprise Edition
  feature ...". Teams, catalogues, roles, Workspaces, SSO profiles and edge namespaces are also
  refused on the Community Edition. `GET /auth/features` reports `"edition":"community"` or
  `"enterprise"`. The full row-by-row boundary, with each call, is in the `tyk-ai-gateway`
  skill.
- Filters and guardrails, where the page and the product differ: "Filter execution is an
  Enterprise Edition feature. In the Community Edition, you can create filters and attach them,
  but AI Studio does not run them" (https://tyk.io/docs/ai-management/ai-studio/filters). On the
  Community Edition 2.3.0, `POST /api/v1/filters` is refused with `403` and "Filter scripting is
  an Enterprise feature.", so filters cannot be created there either, unlike the first half of
  that sentence. Either way no filter runs in the Community Edition.
- Budgets: the Community Edition stores an App's budget and does not enforce it: an App with a
  monthly budget of `0` is still served `200` on 2.3.0, where the Enterprise Edition answers
  `403 budget_exceeded`. The budgeting page says the same:
  "Budget enforcement (blocking requests when limits are exceeded) is an Enterprise Edition
  feature" (https://tyk.io/docs/ai-management/ai-studio/budgeting).
- Secrets (`$SECRET/NAME` references) work in both editions: the Secrets page lists "Community &
  Enterprise" (https://tyk.io/docs/ai-management/ai-studio/secrets), and 2.3.0 accepts them on
  both.
- Deployment modes are "Standalone" (single instance, "No external dependencies") and "Hub and
  Spoke (Control + Edge)" with Edge Gateways
  (https://tyk.io/docs/ai-management/ai-studio/installation/overview). The Tyk Dashboard MCP
  integration, which imports Dashboard MCP proxies into AI Studio, is Enterprise only (feature
  table above; on 2.3.0 Community, `GET /api/v1/tyk-connections` answers `403`).

## Verify before quoting

1. **Is REST API to MCP (OpenAPI to MCP) open source? Settled: no.** The Editions page
   (https://tyk.io/docs/apim) answers in its FAQ "Is the Tyk MCP Gateway Open
   Source?": "Yes, for MCP proxies to a remote MCP server. A REST API to MCP proxy, upstream OAuth
   and token exchange need the Enterprise Edition." The same page's "MCP Gateway" table and
   https://tyk.io/docs/ai-management/mcps/api-to-mcp (Availability "Enterprise", since v5.15.0)
   agree. Marketing pages (https://tyk.io/tyk-mcp-vs-kong-mcp/, https://tyk.io/tyk-mcp-vs-apigee-mcp/,
   both saying "Tyk's MCP support lives in the open source gateway under MPL 2.0", and the
   "Comparisons" part of https://tyk.io/llms.txt) still say API-to-MCP is in the open source
   Gateway; they are out of date, so quote the Editions page. Not the gateway feature: the
   separate MIT-licensed command-line tool https://github.com/TykTechnologies/api-to-mcp turns an
   OpenAPI spec into a standalone MCP server with "no gateway-level authentication, access
   control, or observability of its own" (https://tyk.io/docs/ai-management/mcp-gateway/faq).
2. **"No feature lockout" on the open source page.** https://tyk.io/docs/tyk-oss-gateway says
   "Tyk Gateway is provided ‘Batteries-included’, with no feature lockout", while the same page
   and https://tyk.io/docs/apim say the Enterprise Edition build adds Tyk Streams and advanced
   upstream authentication. Quote the edition tables above, not the slogan.
3. **Licence lapse wording varies by page.** https://tyk.io/docs/tyk-open-source adds "There is
   no grace period, but Tyk reminds you in advance" to its licence-lapse sentence; other pages do
   not. Answer licence-expiry questions by component (`licensing-and-packaging.md`, "Licence
   expiry"), not with a one-sentence promise.
