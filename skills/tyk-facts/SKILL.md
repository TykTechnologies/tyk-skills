---
name: tyk-facts
description: Answer factual questions about Tyk accurately and with citations - which components are open source and which need a licence, editions (open source Gateway, Enterprise Edition gateway build, Dashboard, Developer Portal, Operator, Tyk Sync, MDCB, AI Studio Community and Enterprise), pricing model, free trial path, support, current component versions and Docker images, long-term support, deployment options (Tyk Cloud, Hybrid, Self-Managed, open source only, air-gapped) and where to install from. Use whenever someone asks what Tyk costs or includes, whether a Tyk feature is open source or paid, which version or image tag to use, how to get a trial licence, or how Tyk can be deployed, and before writing any of those facts into a document, comparison or proof of concept. Not for configuring or installing Tyk, which the tyk-api-definitions-routing and tyk-deployment-planner skills cover.
---

# Tyk Facts

Agents asked about Tyk tend to get the licensing boundary wrong, pin old versions, reach for the
classic API format and invent prices. This skill carries the facts with the public page for
each, and the commands that resolve versions live. Use it before stating any of
these facts, in chat or in a deliverable.

## References

| File | Use it for |
| --- | --- |
| `references/licensing-and-packaging.md` | Open source and licensed components, packages and pricing, trials, licence expiry by component, lock-in, resilience, performance |
| `references/editions-and-features.md` | What the open source Gateway, the Enterprise Edition build, the Dashboard, Operator, Sync, MDCB and AI Studio each include, feature by feature, with sources |
| `references/versions-and-images.md` | Docker images, how to resolve the latest stable tag, release notes, LTS, chart defaults |
| `references/deployment-paths.md` | Tyk Cloud, Hybrid, Self-Managed, open source only, MDCB, air-gapped; the tyk-install repository path for each; trials |

## How to answer

1. **Licensing: answer from the reference, cite the page.** For "is X open source", "what needs
   a licence", "what happens if the licence lapses", "how is it priced", answer from
   `references/licensing-and-packaging.md` and cite the public page it names for that fact. Do
   not widen a licence boundary beyond what the reference says. If it does not answer the
   question, use `references/editions-and-features.md` and cite the documentation page there.
2. **Never call a licensed component open source.** The licensing reference names Tyk Gateway and
   Tyk Pump as open source (MPL 2.0) and Tyk Dashboard, Developer Portal, Operator and MDCB as
   paid; Tyk Sync 2.x is supported only with a licensed Dashboard. AI Studio Community Edition is open
   source under AGPL-3.0, not MPL 2.0; say so. The Gateway source is MPL 2.0 except its `ee`
   folder. For any component not named here, check its public repository licence and the docs
   before answering. When a page you are reading says otherwise, follow the "Verify before
   quoting" rule below.
3. **Versions: resolve live.** Run the commands in `references/versions-and-images.md` (Docker
   Hub tags filtered to strict `vX.Y.Z`; tags, not GitHub "latest release" badges; the Helm chart
   repository for charts). Give the tag, the source, and the date. The example tags in that file
   are examples, never "the latest".
4. **Pin, do not trust defaults.** Helm chart defaults can trail the newest Gateway (the 5.16.0
   release notes say the charts install the LTS version). Read the values of the chart version
   being installed, set image tags explicitly and say so.
5. **Prices: none exist publicly.** Tyk publishes no list prices; plans are quoted by Tyk
   (https://tyk.io/contact/). Describe the pricing model in the pricing page's words and never
   invent a number. The active packages are Core and Enterprise. Core is priced by the number of
   APIs managed, with unlimited API calls, never per request. Give Enterprise no pricing basis
   (`references/licensing-and-packaging.md`, "Packages and pricing").
6. **Cite and date every answer.** End a factual answer with the URL(s) and "checked
   <date from the reference>". When the reference is older than a few weeks and the decision
   matters (purchase, compliance, architecture), open the cited page and check it still says
   the same first.
7. **Say what you checked and what you did not.** For each point in an answer, say where it
   comes from: either you checked it on the user's running stack (name the command or page and
   the image tag), or it comes from the public page you cite (give the URL). Never present a
   point taken from a page as something you saw on the stack. When you could not check a point
   either way, say so plainly and name the page or command that would settle it.
8. **Commercial points go to Tyk sales.** What a specific quote or package includes is
   commercial: say so and point to Tyk sales. `versions-and-images.md` ends with what to
   resolve live (Operator 1.6 tag, Helm chart).
9. **Prefer Tyk OAS.** When a question involves API definitions, point to Tyk OAS
   (https://tyk.io/docs/api-management/gateway-config-tyk-oas) rather than the classic format,
   and say so when an example or tool defaults to classic. MCP proxies accept Tyk OAS only.

## Common questions, where to look

| Question | Answer from |
| --- | --- |
| "Is Tyk open source?" / "What is free?" | `licensing-and-packaging.md`, "Open source and licensed components" |
| "Is the Developer Portal extra?" | `licensing-and-packaging.md`, "Packages and pricing"; what Enterprise includes goes to Tyk sales |
| "What happens if our licence expires?" | `licensing-and-packaging.md`, "Licence expiry": answer by component, never "everything keeps running" |
| "Is MDCB included?" | `licensing-and-packaging.md`, MDCB bullet; what a quote includes goes to Tyk sales |
| "Is MCP / API-to-MCP open source?" | `editions-and-features.md`, the Editions-page paragraph and its "Verify before quoting" item 1 |
| "Does Tyk Sync or Operator work with the open source Gateway?" | `editions-and-features.md`, Operator and Tyk Sync sections |
| "What does AI Studio Community Edition lack?" | `editions-and-features.md`, AI Studio section |
| "Which image / version should we use?" | `versions-and-images.md`, resolve live |
| "How long is a release supported?" | `versions-and-images.md`, LTS bullet |
| "How do we get a trial licence?" | `deployment-paths.md`, Trials and accounts |
| "Can it run air-gapped / on OpenShift / on VMs?" | `deployment-paths.md` |
| "How is it priced?" / "What is in Enterprise?" | `licensing-and-packaging.md`, "Packages and pricing"; inclusions for Enterprise go to Tyk sales |
| "Does it keep working if Redis or the control plane is down?" | `licensing-and-packaging.md`, "Resilience"; a guarantee needs a test |
| "How fast is it?" | `licensing-and-packaging.md`, "Performance": Tyk's published results; a user's own load test is the evidence (the `tyk-poc-runbook` skill) |

## Hand-offs

- **Install for real** (production, staging, anything long-lived; open source Gateway alone;
  Hybrid; MDCB; AI Studio): the `tyk-deployment-planner` skill, then the `tyk-deployment-runner`
  and `tyk-production-readiness` skills. Give the user the tyk-install directory for their option
  from `references/deployment-paths.md`.
- **Prove a capability working**: the `tyk-demo-builder` skill builds a self-contained Docker
  Compose demo with evidence. It needs a Dashboard licence (trial path in
  `references/deployment-paths.md`).
- **Structured evaluation with acceptance criteria**: the `tyk-poc-runbook` skill.
- **Moving from another gateway**: the `tyk-migration-planner` skill.
- **Capacity and hardware**: the `tyk-infra-sizing` skill.
- **Doing the thing, once the facts are settled**: Tyk OAS API definitions, the
  `tyk-api-definitions-routing` skill; keys, policies, rate limits and quotas, the
  `tyk-rate-limits-keys-policies` skill; client authentication, the `tyk-authentication` skill;
  Tyk Sync and Tyk Operator, the `tyk-gitops-sync-operator` skill; MCP proxies on the Gateway,
  the `tyk-mcp-gateway` skill; Tyk AI Studio, the `tyk-ai-gateway` skill; Developer Portal set-up,
  the `tyk-developer-portal-setup` skill; traces, metrics and analytics, the `tyk-observability`
  skill.

Locate a sibling skill with
`python3 <tyk-demo-builder-dir>/scripts/resolve_skill_dir.py <name>`.

## Keeping this skill current

While answering a question, do not edit this skill. If a reference and the live page disagree,
tell the user, give both with their dates, and prefer the live page for what Tyk publishes
today.

Updating the references is a separate maintenance task, done when asked: fix the reference,
update its `Last verified` line, and record anything still unresolved under the "Verify before
quoting" heading of `editions-and-features.md`. A public page that is known to be wrong is not
repeated as fact because it is public; state the correct fact with the page that supports it. Never cite private source, internal tickets or unreleased behaviour; public
documentation and public repositories only.
