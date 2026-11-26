# Licensing and packaging

Last verified: 2026-11-20 against https://tyk.io/pricing/, https://tyk.io/docs/apim (Tyk Editions and Feature Availability), the Operator release notes and Tyk Gateway 5.16.0

Answer licensing and packaging questions from this file, and cite the public page named with
each fact. When this file does not answer a question, say so and use
`editions-and-features.md`, which covers features one by one.

## Open source and licensed components

- **Open source (MPL 2.0, free):** Tyk Gateway and Tyk Pump. The Gateway source is MPL 2.0
  except its `ee` folder. https://tyk.io/docs/apim
- **Licensed (licence key required):** Tyk Dashboard, Tyk Developer Portal, Tyk Operator and
  MDCB. https://tyk.io/docs/apim
- **Enterprise Edition gateway build** (`tykio/tyk-gateway-ee`, licensed): the gateway every
  paid customer runs. It adds Tyk Streams and advanced upstream authentication; upstream OAuth,
  token exchange and Tyk Streams are only in this build. https://tyk.io/docs/apim
- **Tyk Operator** is a licensed product from v1.0. It needs a licence key
  (`TYK_OPERATOR_LICENSEKEY`, or `global.license.operator` in the Helm charts), and the earlier
  open source Operator repository is archived
  (https://github.com/TykTechnologies/tyk-operator, default branch `deprecated`).
  https://tyk.io/docs/apim
- **MDCB** is "a separately licensed extension to the Tyk control plane"
  (https://tyk.io/docs/api-management/mdcb), part of the Enterprise Edition software and
  available on Self-Managed, Tyk Cloud and Hybrid. Whether a given quote includes it is a
  question for Tyk sales.
- **Tyk Sync 2.x** works only with a licensed Dashboard
  (https://tyk.io/docs/api-management/automations/sync).
- **AI Studio Community Edition** is open source under AGPL-3.0, not MPL 2.0; AI Studio
  Enterprise is licensed (`editions-and-features.md`).
- **MCP:** MCP proxies to a remote MCP server are open source; a REST API to MCP proxy, upstream
  OAuth and token exchange need the Enterprise Edition (https://tyk.io/docs/apim, FAQ "Is the
  Tyk MCP Gateway Open Source?"). Detail in `editions-and-features.md`.

"Enterprise Edition" is the licensed software (the `-ee` build, the Dashboard, MDCB and the
rest). The Enterprise package is a commercial package. Never read "needs the Enterprise
Edition" as "only in the Enterprise package".

## Packages and pricing

- The active packages are **Core** and **Enterprise**. Name only these two. If a page you fetch,
  the pricing page included, names any other plan, still name only Core and Enterprise and send
  questions about plans to Tyk sales (https://tyk.io/contact/). This rule holds even where
  `SKILL.md` says to prefer the live page.
  - **Core:** usage-based, priced by the number of APIs managed, "with unlimited API calls"
    (https://tyk.io/pricing/). The usage that sets the price is how many APIs Tyk manages, not
    request or call volume: never describe Core as priced per request, per call or by traffic.
  - **Enterprise:** "Custom", "Contact Tyk"; "Enterprise adds premium support and custom SLAs".
    There is no public pricing basis for Enterprise; do not supply one.
- Tyk publishes no list prices; quotes come from Tyk sales (https://tyk.io/contact/). Do not
  state package sizes, tiers, discounts or what counts towards a quote.
- The Developer Portal is included in Core, not sold as a separate add-on, and needs a licence
  like the other paid components (https://tyk.io/pricing/). What Enterprise includes is a
  question for Tyk sales.
- Core includes Tyk's Enhanced success plan; Enterprise adds premium support and custom SLAs; the
  open source components have community support (https://tyk.io/pricing/).
- For peer-review ratings, link to https://www.gartner.com/reviews/product/tyk rather than
  repeating numbers, which differ from page to page.

## Trials

- Self-Managed: a 14-day trial; the licence key arrives by email
  (https://tyk.io/self-managed-trial/, https://tyk.io/docs/apim).
- Tyk Cloud: a free 48-hour trial with no credit card (https://tyk.io/pricing/).

`deployment-paths.md` has the details.

## Licence expiry

Answer per component. Never say that everything keeps running.

- **Gateway:** APIs already loaded keep serving traffic. The open source Gateway needs no
  licence at all.
- **Dashboard:** creating or updating APIs is blocked until the licence is renewed
  (https://tyk.io/docs/apim).
- **Operator:** "If the license is missing, invalid, or expired, Tyk Operator will exit with an
  error message" ("1.0.0 Release Notes",
  https://tyk.io/docs/developer-support/release-notes/operator). Changes made through Kubernetes
  resources stop reaching Tyk until the licence is renewed.
- **MDCB and other licensed components:** the public pages do not describe each one; ask Tyk how
  the version in use behaves.

Renew before expiry. Where a decision depends on expiry behaviour, test it in a non-production
environment (the `tyk-poc-runbook` skill).

## Lock-in

API definitions can be exported and run on the open source Gateway (MPL 2.0). That holds for
APIs that use only open source features: Tyk Streams, upstream authentication, token exchange and
REST API to MCP are Enterprise Edition features (`editions-and-features.md`), so check which
features an API uses before saying it can move.

## Resilience

Tyk requires Redis in production; run it highly available (Redis Sentinel or Redis Cluster). A
Redis or control-plane outage is a failure to recover from, not a way to run.
https://tyk.io/docs/deployment-options/tyk-hybrid

- **Control plane unavailable (Tyk Hybrid):** Gateways keep their last working configuration and
  keep serving traffic (https://tyk.io/docs/deployment-options/tyk-hybrid). Do not promise that
  analytics produced during the outage reach the control plane later; for a complete record, run
  an additional Pump to a store on the data plane.
- **Redis unavailable, Gateway 5.16.0** (open source or Dashboard-managed), in the same words as
  the `tyk-troubleshooting` skill: an API without authentication, and a JWT API whose policy has
  no quota, keep answering `200`; a JWT API whose policy has a quota answers
  `403 {"error": "Quota exceeded"}`, each after about 2 seconds; an auth-token key works only
  while the Gateway's local copy of its session lasts, at most 10 seconds after the Gateway last
  read it from Redis, then gets `403 {"error": "Access to this API has been disallowed"}`, and a
  key with a quota, or one not read in the last 10 seconds, is refused from the first request.
  Within 2 seconds of Redis returning, every key and token works again, with no restart.
- **Rate limits:** the default Distributed Rate Limiter keeps counters in each gateway's memory,
  but with more than one gateway a low limit goes through Redis anyway (`editions-and-features.md`,
  "Open source Tyk Gateway"); quotas always need Redis. Gateways that share one Redis share their
  limits; regions with separate Redis do not.
- **Tyk Cloud:** Tyk runs Redis and handles disaster recovery.

Anyone who needs a guarantee needs a test: the `tyk-poc-runbook` skill, criterion C3.

## Performance

Tyk load-tests releases and publishes the results at https://tyk.io/performance-benchmarks/
(AWS, GCP and Azure, four machine classes on each, multiple five-minute runs, shown as charts).
Present them as vendor-published results for that machine and workload, not as a capacity
figure for the user's hardware or a specific release. The evidence for a decision is the user's
own load test: the `tyk-poc-runbook` skill, criterion C4.
