# Community Edition and Enterprise Edition

Last verified: 2026-11-19 against AI Studio 2.3.0 (`tykio/tyk-ai-studio`, `tykio/tyk-microgateway`, `tykio/tyk-ai-studio-ent`, `tykio/tyk-microgateway-ent`)

Both columns describe AI Studio 2.3.0: the Community images with no licence, the Enterprise
images with a licence issued for AI Studio 2.3. Cells that say "overview page" come from the
feature table on https://tyk.io/docs/ai-management/ai-studio/overview. The other rows name the
call that shows the behaviour; the full requests and answers are in the reference file given.

Licensing terms, how to get an Enterprise licence, pricing and what happens when a licence
lapses are not in this skill. Use the `tyk-facts` skill. The Community Edition source is
public: https://github.com/TykTechnologies/ai-studio. The public feature comparison is on the
overview page: https://tyk.io/docs/ai-management/ai-studio/overview.

Some AI Studio error messages tell the reader to visit https://tyk.io/ai-studio/pricing. That
URL returns `404`; send people to https://tyk.io/pricing/ instead.

## Which edition is running

```bash
curl -s http://localhost:8080/auth/features
```

`"edition":"community"` or `"enterprise"`, plus `version` and a `features` map. Community
images: `feature_model_router`, `feature_semantic_router`, `feature_rbac`, `feature_groups`,
`feature_webhooks`, `feature_tyk_mcp`, `hub_spoke_multi_tenant` and `feature_agent_tracing`
are `false`. Enterprise images with a licence: all of these are `true` except
`feature_agent_tracing`, which turns `true` only when the control plane runs with
`AGENT_TRACING_ENABLED=true`. The Enterprise answer also carries `license_days_left`.

## Feature boundary

| Capability | Community Edition | Enterprise Edition | Where |
| --- | --- | --- | --- |
| LLM proxy: unified endpoint, per-LLM endpoints, streaming | Works | Works | `calling-models.md` |
| Hub and edges, pushes | Works | Works | `hub-and-edge.md` |
| Edge starts from its last configuration when no control plane answers | Works, but every key gets `503` until a control plane answers | Works; with full credential sync keys validate | `hub-and-edge.md` |
| Full credential sync (edge validates App keys with no control plane) | The edge logs `EDGE_CREDENTIAL_SYNC=full is an Enterprise option: this Community edge validates keys on demand` | Works: an App key the restarted edge has never seen gets `200` with the control plane stopped | `hub-and-edge.md` |
| LLM failover waterfall | Works | Works | `routing-failover-budgets.md` |
| Apps, credentials, per-LLM grants, allowed models | Works | Works | `admin-api.md` |
| Users: administrators and standard users, user API tokens | Works | Works | `admin-api.md` |
| Token and cost analytics, proxy logs, model prices | Works | Works | `guardrails-audit-analytics.md` |
| Prometheus metrics on hub and edge | Works | Works | `guardrails-audit-analytics.md` |
| Tools from OpenAPI, called over REST and MCP | Works | Works | `mcp-tools-and-plugins.md` |
| Secrets (`$SECRET/NAME` references) | Works | Works | `admin-api.md` |
| Budget enforcement | Stored and reported only: an App with budget `0` keeps getting `200` | `403 budget_exceeded` | `routing-failover-budgets.md` |
| Team budgets | `402 ... "team budgets are an Enterprise Edition feature ..."` | `GET /api/v1/groups/{id}/budget` answers | `routing-failover-budgets.md` |
| Budget alerts | Not included (overview page) | Included (overview page) | [Overview](https://tyk.io/docs/ai-management/ai-studio/overview) |
| Model Routers | `402 ... "model router is an Enterprise Edition feature ..."` | Routes by model pattern on the edge | `routing-failover-budgets.md` |
| Semantic Routers | `402 ... "semantic router is an Enterprise Edition feature ..."` | Routes by keyword, explicit route or default on the edge | `routing-failover-budgets.md` |
| Filter scripts and guardrail filters | `POST /api/v1/filters` -> `403 ... "Filter scripting is an Enterprise feature. ..."` | Block, redact and response block all work; five guardrails come ready, unattached | `guardrails-audit-analytics.md` |
| Compliance events | `403 ... "compliance monitoring is an Enterprise Edition feature ..."` | One event per guardrail match | `guardrails-audit-analytics.md` |
| Audit trail | `403 ... "the audit trail is an Enterprise Edition feature ..."` | One record per change, with a field diff and secrets redacted | `guardrails-audit-analytics.md` |
| Teams (create, membership) | `402 ... "Group management requires Enterprise Edition"`; one `Default` team | Works | `admin-api.md` |
| Catalogues | `402 ... "LLM catalogue management requires Enterprise Edition"` | Works | `admin-api.md` |
| Roles, role bindings | `GET /api/v1/rbac/roles` -> `402`, `"code":"enterprise_required"` | Five built-in roles; custom roles enforced (`403 missing permission llms:write`) | `admin-api.md` |
| Workspaces | `GET /api/v1/workspaces` -> `402 ... "Workspaces are an Enterprise Edition feature ..."`, `"code":"enterprise_required"` | Works | `admin-api.md` |
| Service accounts (API-key-only users for automation) | Works (`is_admin` sets its access) | Works (roles set its access) | `admin-api.md` |
| SSO profiles (SAML, OIDC) | `402 ... "SSO profile management is an Enterprise feature"` | `GET /api/v1/sso-profiles` answers `200` | [SSO](https://tyk.io/docs/ai-management/ai-studio/sso) |
| Namespaces for edges | `402 ... "Multi-tenant namespaces require Enterprise Edition"` | `GET /api/v1/namespaces` lists them | `hub-and-edge.md` |
| Outbound webhooks | `GET /api/v1/webhooks/targets` -> `403` | Answers; status `available: true` | [Webhooks](https://tyk.io/docs/ai-management/ai-studio/webhooks) |
| Governed metadata | `403 ... "governed metadata is an Enterprise Edition feature ..."` | Answers, with a seeded schema | [Governed metadata](https://tyk.io/docs/ai-management/ai-studio/governed-metadata) |
| Tyk Dashboard MCP integration | `GET /api/v1/tyk-connections` -> `403` | Connects to Tyk Dashboard 5.16.0 and imports its MCP proxies | `mcp-tools-and-plugins.md` |
| Plugin system | `GET /api/v1/plugins` answers (empty on a new installation) | In both editions per the overview page's feature table | `mcp-tools-and-plugins.md` |
| Semantic caching (Advanced LLM Cache plugin) | Not included (overview page) | Included (overview page) | [Semantic caching](https://tyk.io/docs/ai-management/ai-studio/semantic-caching) |
| Asset Catalog plugin | Not included (overview page) | Included (overview page) | [Asset catalog](https://tyk.io/docs/ai-management/ai-studio/asset-catalog) |

Two status codes mean "Enterprise only" on Community Edition: `402`, and `403` with the title
`Enterprise Feature`. Status endpoints stay readable and say `available: false`
(`/api/v1/audit/status`, `/api/v1/compliance/available`, `/api/v1/webhooks/status`), and
`GET /api/v1/team-budgets/settings` returns `{"enabled":false}`.

## What Community Edition is enough for

A working AI gateway: one endpoint in front of several LLM providers, per-application keys
with per-LLM and per-model grants, failover between providers, token and cost reporting,
metrics, OpenAPI tools served over MCP, and a control plane that runs as several replicas on
one PostgreSQL database (on the Community images too, `hub-and-edge.md`).

It cannot stop spending at a limit, send budget alerts, cache answers semantically, route by
model pattern or prompt, inspect or block prompt and answer content, keep an administrative
audit trail, split users into teams, roles or Workspaces, sign users in through an identity
provider, or let an edge validate keys while no control plane answers. Those need Enterprise Edition.

## Enterprise images and the licence

Started with no licence, both Enterprise images exit:

```text
FTL Failed to start AI Studio error="studio: start licensing: license validation failed at boot: no license provided"
Microgateway license validation failed at boot: no license key provided
```

Started with a licence that does not cover AI Studio 2.3 (for example a Tyk Dashboard licence):

```text
FTL Failed to start AI Studio error="studio: start licensing: license validation failed at boot: invalid license: license does not include the Tyk AI Studio SKU"
```

With an accepted licence the control plane logs `License validated successfully` with the
expiry date and a `License limits:` line, and the edge logs
`Microgateway license validated successfully`. The Enterprise images are not a way to run
Community features without a licence. Licence questions go to the `tyk-facts` skill.
