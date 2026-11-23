# Portkey (AI gateway) to Tyk

Last verified: 2026-11-19 against the Tyk AI Studio 2.3 and Tyk Gateway 5.16 documentation, the Portkey pages linked below, and AI Studio 2.3.0 as described in the `tyk-ai-gateway` skill

Mapping for teams moving LLM and agent traffic from Portkey, or another AI gateway with the same
concepts, onto Tyk. LLM traffic maps to Tyk AI Studio; MCP server traffic maps to the MCP Gateway
in Tyk Gateway. Each Tyk row cites the Tyk page that documents it and names
the AI Studio edition the page requires. Edition matters: several rows are Enterprise only (the
`tyk-facts` skill, `references/editions-and-features.md`; the call-by-call boundary is in the
`tyk-ai-gateway` skill).

**Record the incumbent's version and model first.** Portkey's own pages change the concepts:
"Virtual Keys have been migrated to Model Catalog"
(https://portkey.ai/docs/product/ai-gateway/virtual-keys), and the Model Catalog adds provider
integrations, model allow-lists, budgets and rate limits at organisation and workspace level,
called inline as `model="@provider/model_slug"`
(https://portkey.ai/docs/product/model-catalog). An estate may use virtual keys, the Model
Catalog, or both. Inventory which, from the incumbent's export and its documentation for the
version in use, before mapping.

## LLM traffic: Tyk AI Studio

| Portkey concept | Tyk AI Studio equivalent | Edition | Tyk docs | Status |
| --- | --- | --- | --- | --- |
| Providers / integrations (OpenAI, Anthropic, ...) | LLM configurations: vendor, credentials, allowed models (regular expressions), privacy level, pricing | Community and Enterprise | https://tyk.io/docs/ai-management/ai-studio/llm-management | verified |
| Gateway endpoint the apps call | Edge Gateway: "a single, consistent endpoint for applications to interact with various LLMs" | Community and Enterprise | https://tyk.io/docs/ai-management/ai-studio/proxy | verified |
| Virtual keys, or Model Catalog provider integrations (provider keys hidden from apps) | Apps with their own credentials and per-LLM grants, reviewed and activated by an administrator; provider keys held in AI Studio Secrets Management | Secrets: "Community & Enterprise" on the Secrets page; the Apps page states no edition; both work on Community and Enterprise 2.3.0 (the `tyk-ai-gateway` skill) | https://tyk.io/docs/ai-management/ai-studio/admin-apps, https://tyk.io/docs/ai-management/ai-studio/secrets | verified |
| Model allow-lists | Allowed models on an LLM ("Use regex patterns ... to whitelist specific models from a vendor") and on an App's grant | Community and Enterprise | https://tyk.io/docs/ai-management/ai-studio/llm-management | partial: Tyk sets them per LLM and per App, not at organisation and workspace level |
| Budget limits on keys | Budget management, enforcement and alerts; team budgets. Community Edition stores a budget and does not enforce it | Enterprise | https://tyk.io/docs/ai-management/ai-studio/budgeting | verified |
| Config: fallbacks | Failover waterfall on an LLM: "an ordered list of fallback LLM providers ... the gateway sends the same request to each fallback in turn". Scope: "Failover applies to the OpenAI-compatible chat endpoints" (per-LLM `/ai/{llmSlug}/v1/chat/completions`, the main `/v1/chat/completions`, Model Routers); "It does not apply to the vendor-native endpoints, the Anthropic Messages endpoint, or chat sessions"; "A streamed response can fail over only before the gateway sends the first token"; a `401` or `403` from the upstream never fails over | Community and Enterprise | https://tyk.io/docs/ai-management/ai-studio/llm-management | partial: compare each Portkey fallback config, its trigger codes and the endpoints the apps call |
| Config: load balancing across providers | Model Router, "Multi-Vendor Traffic Splitting" across a pool | Enterprise | https://tyk.io/docs/ai-management/ai-studio/model-router | verified |
| Conditional routing | Portkey routes on metadata, request parameters and the URL path (https://portkey.ai/docs/product/ai-gateway/conditional-routing). Tyk: the Model Router routes by model name pattern; the Semantic Router by prompt content. No Tyk page documents routing on caller-supplied metadata | Enterprise | https://tyk.io/docs/ai-management/ai-studio/model-router, https://tyk.io/docs/ai-management/ai-studio/semantic-router | partial |
| Guardrails (PII, secrets, prompt attacks) | Guardrail filters with detection providers; script filters. "Filter execution is an Enterprise Edition feature" | Enterprise. The filters page says Community can create filters but not run them; Community Edition 2.3.0 refuses `POST /api/v1/filters` with `403` "Filter scripting is an Enterprise feature." (the `tyk-ai-gateway` skill) | https://tyk.io/docs/ai-management/ai-studio/guardrails, https://tyk.io/docs/ai-management/ai-studio/filters | verified |
| Semantic cache | Semantic Caching (Advanced LLM Cache plugin), "off by default"; it looks for an exact match first | Enterprise | https://tyk.io/docs/ai-management/ai-studio/semantic-caching | verified |
| Request logs, cost and usage analytics | Analytics: usage, cost and performance of LLMs, tools and chat; proxy logs per LLM and App. "In the Community Edition, AI Studio records usage and cost data. Budget totals and the Compliance dashboard are Enterprise Edition features." On Community Edition 2.3.0 the token, cost and proxy-log analytics endpoints answer (the `tyk-ai-gateway` skill) | Community and Enterprise | https://tyk.io/docs/ai-management/ai-studio/analytics | verified |
| Audit of admin changes | Audit trail of actions on the management API (not a request log). "The Community Edition records nothing" | Enterprise | https://tyk.io/docs/ai-management/ai-studio/audit-trail | verified |
| SSO, fine-grained roles | Advanced SSO (SAML, OIDC), fine-grained RBAC | Enterprise | https://tyk.io/docs/ai-management/ai-studio/overview | verified |
| Prompt library / playground | Chat interface and AI Portal apps; no Tyk page documents a prompt library (https://tyk.io/docs/llms-full.txt) | Community and Enterprise | https://tyk.io/docs/ai-management/ai-studio/overview | no direct equivalent verified |

## MCP traffic: Tyk Gateway MCP Gateway

| Need | Tyk equivalent | Tyk docs | Status |
| --- | --- | --- | --- |
| Govern remote MCP servers (auth, rate limits, tool allow lists) | MCP proxies in Tyk Gateway (from v5.13), with per-tool, per-resource and per-prompt access rights and rate limits in policies | https://tyk.io/docs/ai-management/mcp-gateway/overview, https://tyk.io/docs/ai-management/mcp-gateway/policies | verified |
| Expose existing REST APIs as MCP tools | REST API to MCP proxies (from v5.15.0; Availability "Enterprise") | https://tyk.io/docs/ai-management/mcps/api-to-mcp | verified; licensing: see `tyk-facts` |
| OAuth 2.1 for MCP clients | Protected Resource Metadata on the MCP proxy | https://tyk.io/docs/ai-management/mcp-gateway/oauth-2-1 | verified |
| Use Gateway MCP proxies from AI Studio apps | Tyk Dashboard MCP integration: "It imports the MCP proxies of the Dashboard. An administrator reviews each one and publishes it in the AI Portal" | https://tyk.io/docs/ai-management/ai-studio/tyk-mcp-integration | verified (Enterprise) |
| Publish MCP servers to developers | Developer Portal MCP Products (Portal 1.19.0 or later) | https://tyk.io/docs/portal/api-products | verified |

Building the Gateway side: the `tyk-mcp-gateway` skill. Publishing in the Developer Portal: the
`tyk-developer-portal-setup` skill.

## Migration notes

- **Pick the edition first.** Budget enforcement, model and semantic routers, filter and
  guardrail execution, semantic caching and the audit trail need AI Studio Enterprise. Community
  Edition (AGPL-3.0, https://github.com/TykTechnologies/ai-studio) covers the proxy, failover,
  analytics, secrets and apps.
- **Cut over per application, and test what the app depends on.** Point one app at the AI Studio
  endpoint at a time. Before moving the next, compare with the same synthetic requests on both
  gateways: the SDK and headers the app sends, model names, streaming, error bodies and status
  codes, retries and which failures trigger fallback (the endpoint the app calls decides whether
  failover applies), and cost and budget attribution in AI Studio analytics. A Portkey config
  tree is not imported; each part becomes the Tyk feature in its row above.
- **Build a working proof first.** For LLM traffic, the `tyk-ai-gateway` skill stands up a
  Community Edition AI Studio evaluation stack with no licence and configures it through the
  admin API. For MCP servers and REST APIs as MCP tools on Tyk Gateway, use the
  `tyk-mcp-gateway` skill. The `tyk-demo-builder` skill's `ai-studio` module (with `mcp` for the
  Dashboard integration) generates an AI Studio companion stack beside a Tyk demo and needs an
  Enterprise licence; production installs use the `tyk-deployment-planner` skill (tyk-install repository
  `docker/ai-studio/`, `kubernetes/helm-ai-studio/`).
