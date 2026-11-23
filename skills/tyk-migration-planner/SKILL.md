---
name: tyk-migration-planner
description: Plan a migration to Tyk from Kong, Apigee, Portkey or another API gateway or AI gateway - inventory what the incumbent runs, map each concept and plugin or policy to its Tyk equivalent with documentation links, prove the mapping in a proof of concept, plan the cut-over keeping existing URLs and, where the credential type allows it, existing API keys, and decommission. Also use to find the Tyk equivalent of Kong decK, Kong Ingress Controller, Apigee products and apps, or Portkey virtual keys and configs. Not for choosing between Tyk Sync and Tyk Operator outside a migration, which is the tyk-gitops-sync-operator skill.
---

# Tyk Migration Planner

Moving gateways fails on the details: a plugin with no equivalent, rate limits that behave
differently across replicas, consumers forced to re-issue keys, URLs that change. This skill
plans the move in five phases and maps each incumbent concept to a documented Tyk feature,
marking what has no direct equivalent.

## Where deliverables go

Keep everything generated outside this plugin's folder, in
`${TYK_WORKSPACE:-~/tyk-workspace}/<project>/` (`demos/<slug>/`, `sizing/`, `docs/`), unless the
user names another location. The inventory, mapping and migration plan go under `docs/`.
Exported incumbent configuration may contain credentials: keep it out of any public repository
and redact it before sharing.

## Dependencies

Resolve these at the start and write the result at the top of the migration plan: the path each
skill resolved to and the version of the plugin it came from.

| Dependency | Needed for | Where it comes from | If it is missing |
| --- | --- | --- | --- |
| `tyk-facts` skill | every licensing, edition and version statement | this plugin | stop; reinstall this plugin |
| `tyk-poc-runbook` and `tyk-demo-builder` skills | Phase 3 | this plugin | stop; reinstall this plugin |
| `tyk-api-definitions-routing`, `tyk-authentication`, `tyk-rate-limits-keys-policies`, `tyk-gitops-sync-operator`, `tyk-developer-portal-setup`, `tyk-observability`, `tyk-mcp-gateway`, `tyk-ai-gateway` skills | the Tyk side of each mapping row, as named below | this plugin | the row keeps its documentation link; mark its coverage `unassessed` |
| `tyk-infra-sizing` skill | sizing the target topology | this plugin | sizing stays an open point |
| `tyk-deployment` skill, with a checkout of the tyk-install repository | deploying the production topology in Phase 4 | this plugin; the skill clones https://github.com/TykTechnologies/tyk-install where the user chooses | install by hand from the public guides (https://tyk.io/docs/tyk-self-managed, https://tyk.io/docs/deployment-options/tyk-hybrid, https://tyk.io/docs/api-management/mdcb) and say so in the plan |

Locate a sibling skill with
`python3 <tyk-demo-builder-dir>/scripts/resolve_skill_dir.py <name>`. It looks in the Claude Code
personal skills folder first, then the Codex one, then beside the builder, and prints the first
match. If the printed path is not inside the same plugin installation as this skill, an older
personal copy is shadowing the bundled one: say so, and remove or update that copy first.

## References

| File | Use it for |
| --- | --- |
| `references/kong-to-tyk.md` | Kong services, routes, consumers, plugins, DB-less, Ingress Controller, decK |
| `references/apigee-to-tyk.md` | Apigee proxies, products, apps, developers, policies, Apigee MCP |
| `references/portkey-to-tyk.md` | AI gateway concepts onto Tyk AI Studio and the MCP Gateway |
| `references/gitops-tyk-sync-vs-operator.md` | Choosing Tyk Sync or Tyk Operator; decK command equivalents |

For a gateway not listed, build the same table: one row per incumbent concept, the Tyk
equivalent, the Tyk documentation URL, and a status of `verified`, `partial` or
`no direct equivalent`. Find pages through https://tyk.io/docs/llms.txt.

**What the status means, and what it does not.** `verified` in a reference table means the
cited Tyk page documents that Tyk feature. It is evidence that the capability exists. It is not
evidence that a particular incumbent configuration migrates to it. `partial` means the
documented Tyk feature covers only part of the incumbent concept; the row says which part.
Whether this project's configuration is covered is a separate question, and the migration plan
records both (Phase 2).

## Phase 1: Inventory

Export what the incumbent actually runs, not what its documentation offers:

- Kong: `deck gateway dump` (or the DB-less declarative file); list services, routes, consumers,
  credentials by type, plugins per scope (global, service, route, consumer), custom plugins.
- Apigee: proxies and their policy chains, shared flows, products, apps, developers, KVMs,
  environments, target servers.
- AI gateways: providers, keys, budgets, routing and fallback configs, guardrails, logging sinks,
  MCP servers fronted.

Record per API: traffic volume, auth type, consumers, SLAs, custom code. Note the incumbent's
version.

Inventory credentials by class, not as one count, because each class migrates differently:
opaque API keys (can the plaintext value be exported, or only a hash?), Basic Auth passwords,
OAuth clients and their secrets, issued access and refresh tokens, JWT signing keys and trust
configuration, client certificates. For each class record how many, whether the secret is
recoverable, expiry and revocation state, and what the consumer is entitled to (products,
plans, quotas). A credential stored only as a one-way hash cannot be carried over with its
value; plan a re-issue for it.

## Phase 2: Mapping

1. For every inventory item, take the row from the matching reference. Copy the Tyk
   documentation URL and the reference's status into the mapping as **Tyk capability**.
2. Add a second field per row, **Coverage**, and start it at `unassessed`. Set it only after
   comparing how the incumbent is actually configured (its export, and its vendor documentation
   for the recorded version) with what the Tyk feature does:
   `compatible for this use`, `partial`, `rewrite`, or `unsupported`. Record the observable
   behaviour that must be preserved and the test that will show it (a proof ID or a PoC
   criterion). A `verified` capability with `unassessed` coverage is not a migrated item.
   Example: Kong's `openid-connect` plugin maps to JWT validation, which covers Kong acting as
   a resource server; if the incumbent uses the plugin's login redirects and sessions, coverage
   is `partial` or `rewrite`.
3. For every row whose capability is `partial` or `no direct equivalent`, or whose coverage is
   `partial`, `rewrite` or `unsupported`, decide: configuration change, custom plugin (Go, gRPC,
   JavaScript), accept the gap, or keep that API on the incumbent for now.
4. Check edition and licence for every Tyk feature used (the `tyk-facts` skill,
   `references/editions-and-features.md`): some features need the Enterprise Edition gateway
   build, a Dashboard licence scope, or AI Studio Enterprise.
5. Choose the GitOps tool with `references/gitops-tyk-sync-vs-operator.md`.
6. Write API definitions as Tyk OAS, from the incumbent's OpenAPI documents where they exist
   (the `tyk-api-definitions-routing` skill). The Tyk side of the other rows has its own skill: client
   authentication and credentials, the `tyk-authentication` skill; keys, policies, rate limits
   and quotas, the `tyk-rate-limits-keys-policies` skill; the Developer Portal (products, plans,
   imported credentials), the `tyk-developer-portal-setup` skill; logging, metrics and
   analytics sinks, the `tyk-observability` skill.

## Phase 3: Proof of concept

Prove the risky rows before committing: run the `tyk-poc-runbook` skill, which builds the stack
with the `tyk-demo-builder` skill. The builder's migration recipes:

| Module | What it shows, and its limit |
| --- | --- |
| `custom-domain` | Existing hostnames and legacy listen paths keep working (custom domains on every gateway, a legacy path stripped before the upstream). It does not test DNS, certificates, redirects or encoded paths on the real hosts |
| `key-import` | The mechanism: opaque key values imported through the Dashboard custom-key API, on their own policy; imported value, random key and missing key checked. It uses keys minted for the bundle, so it does not show that the incumbent's real export is recoverable, or that expiry, revocation and entitlements carry over |
| `js-plugin` | A legacy credential location (`?user_key=` or an `app_key` header) mapped into `Authorization` by a pre-authentication JavaScript plugin. Try native configuration first (see below) |
| `rate-limit`, `quota`, `gateway-sharding` | Limits enforced across more than one gateway on the bundle's policies; the project's own limits are tested in PoC criterion C2 |
| `tyk-sync` + `cicd`, or `operator` | The delivery mechanism and a smoke check. The Sync rollback re-applies earlier definitions and does not delete APIs the promotion created; the CI templates are checked statically, not run in a CI system. Delivery and rollback are proven only by running PoC criterion C1 on the project's own pipeline |

**Credential location: native first.** Tyk reads an auth token from a header, a query
parameter or a cookie, and an API can accept more than one of them
(https://tyk.io/docs/api-management/authentication/bearer-token). A legacy `user_key` query
parameter is configuration: on Gateway 5.16.0, an `authToken` scheme with
`"query": {"enabled": true, "name": "user_key"}` accepts `?user_key=<key>` with `200`, still
accepts the key in the `Authorization` header, and answers a call with neither
`401 {"error": "Authorization field missing"}`. The parameter reaches the upstream unless
`server.authentication.stripAuthorizationData` is `true`; then the upstream sees neither the
parameter nor the header. Keep the JavaScript plugin for what
configuration cannot express, such as an alias or a rewrite of the value, and compare it with
the native setting on the same requests before choosing.

Add the modules for the rest of the requirement set (auth types, Portal, MCP, AI Studio and so
on) from the builder's `SKILL.md`.

For an AI gateway or MCP migration, prove the target with the `tyk-ai-gateway` skill for LLM
traffic (a Community Edition AI Studio stack that needs no licence) and the `tyk-mcp-gateway`
skill for MCP servers and REST APIs as MCP tools on Tyk Gateway. Per-consumer limits and the
delivery pipeline have their own skills: `tyk-rate-limits-keys-policies` and
`tyk-gitops-sync-operator`.

## Phase 4: Cut-over

1. Plan, deploy and review the production topology with the `tyk-deployment` skill (Docker
   Compose, Helm, OpenShift, bare metal; Self-Managed, Hybrid, MDCB, AI Studio). Size it with the
   `tyk-infra-sizing` skill. Pin image tags (`tyk-facts`, `references/versions-and-images.md`).
2. Import API definitions and policies through the chosen GitOps path, never by hand.
3. Carry credentials over by class, following the Phase 1 inventory. Opaque API keys whose
   values are recoverable can be imported with their existing values (`POST /tyk/keys/{keyID}`
   on the Gateway, the Dashboard custom-key API, or Portal custom credentials; see the
   references). On Gateway 5.16.0 `POST /tyk/keys/legacy-partner-a-0001` creates the key and a
   call with that value gets `200`, while `POST /tyk/keys/ab` gets `400` with "custom key ID length
   2 is less than min_token_length (3)". Each key must belong to the
   right organisation and policy. Every other class (hashed secrets, OAuth clients and tokens, JWT
   trust, certificates) needs its own decision: re-issue, re-register, or reconfigure trust.
   Test a valid, an invalid, an expired or revoked, and an out-of-scope credential per class.
4. Keep existing URLs: bind each API to its current host with `customDomain` and keep listen
   paths; move DNS or the load balancer per API or per consumer group, not all at once.
5. Run both gateways in parallel; compare status codes, latency, limits and access rights per
   API before moving the next group.
6. Plan the rollback as more than a traffic switch. Moving DNS or the load balancer back
   restores routing only. Decide and write down, before cut-over:
   - who owns keys and policies while both gateways run (freeze changes, or one system of
     record), because keys created or revoked on one side after the split do not exist on the
     other;
   - what happens to quota and rate counters (they do not transfer; agree a reset or accept the
     difference);
   - how a consumer group is routed when groups share a hostname (DNS cannot split by path or
     consumer; that needs the load balancer or the client);
   - the thresholds that trigger a rollback, the capacity kept on the incumbent, and how
     long-lived connections and streams drain.
   Rehearse the rollback once after changing or revoking a credential, and check routing,
   access rights and counters afterwards.
7. Re-test rate limits and quotas on the production topology; the limiter choice and the number
   of gateways change how a limit is counted (the `tyk-rate-limits-keys-policies` skill;
   `references/kong-to-tyk.md`, migration notes).
8. Watch both gateways during the parallel run with the same signals: status codes, latency
   and errors per API (the `tyk-observability` skill for Tyk's side).

## Phase 5: Decommission

When no traffic reaches the incumbent for the agreed period: export its final configuration
for the record, then retire what only the incumbent used. Before revoking an upstream
credential or removing a certificate or DNS entry, confirm Tyk or another service does not use
the same one; "no traffic through the old gateway" does not show that. Stop its licence
renewal. Record what was retired and when.

## Rules

- Every mapping row cites a Tyk documentation page. A row without one is `not verified`: say
  that no page was found, not that Tyk has no equivalent. Use `no direct equivalent` only when
  the documentation was read and the feature is absent.
- Never state licensing, pricing or versions from memory; use the `tyk-facts` skill.
- Describe the incumbent's behaviour from its export and from its vendor's own documentation
  for the recorded version, and cite the page. An export shows configuration, not defaults or
  runtime behaviour. Never invent behaviour; where neither source answers, record the question.
- Run each sibling skill from the paths recorded under "Dependencies".

## Verification before reporting

- Every mapping row has a Tyk documentation link, a capability status and a coverage value;
  no row with `unassessed` coverage is reported as migrated.
- Every risky row names the PoC criterion or proof that will test it.
- Every licensing, edition and version statement came through the `tyk-facts` skill, with its
  date.
- The plan's dependency list names the resolved path and plugin version of each skill used.
