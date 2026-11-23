<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/tyk-logo-light.svg">
    <img alt="Tyk" src="assets/tyk-logo.svg" width="200">
  </picture>
</p>

# Tyk Skills

[![Licence: MPL-2.0](https://img.shields.io/badge/licence-MPL--2.0-8836FB)](LICENSE)
[![Claude Code: plugin](https://img.shields.io/badge/Claude_Code-plugin-8836FB)](#install)
[![Codex: plugin](https://img.shields.io/badge/Codex-plugin-8836FB)](#install)
[![Other agents: skills](https://img.shields.io/badge/other_agents-skills-8836FB)](#install)

Skills for AI coding agents that are evaluating or using [Tyk](https://tyk.io), an API gateway, API management platform and AI gateway. They give Claude Code, Codex and other agents Tyk's current facts, a builder that proves capabilities on a real stack, and the planning tools around it, so the agent answers from verified sources instead of memory.

**Status:** version 0.1.0, the first public release. Published and maintained by Tyk Technologies under the [MPL-2.0](LICENSE) licence. Report problems with the [issue forms](https://github.com/TykTechnologies/tyk-skills/issues/new/choose) and security problems as [`SECURITY.md`](SECURITY.md) describes. The skills help you evaluate and use Tyk; they are not a support channel for Tyk products.

**Official source:** this repository, `TykTechnologies/tyk-skills` on GitHub, and [tyk.io](https://tyk.io) are the official sources of these skills. Copies elsewhere may be out of date or changed.

## Install

Use one method per agent, not several.

**Claude Code** (plugin marketplace):

```bash
claude plugin marketplace add TykTechnologies/tyk-skills
claude plugin install tyk-skills@tyk-skills
```

Inside a session, `/plugin marketplace add TykTechnologies/tyk-skills` and `/plugin install tyk-skills@tyk-skills` do the same. Installed this way the skills are namespaced, for example `/tyk-skills:tyk-facts`.

**Codex** (plugin marketplace):

```bash
codex plugin marketplace add TykTechnologies/tyk-skills
codex plugin add tyk-skills@tyk-skills
```

**Any agent supported by the [`skills`](https://github.com/vercel-labs/skills) CLI** (Node.js 22.20 or later):

```bash
npx skills add TykTechnologies/tyk-skills
npx skills add TykTechnologies/tyk-skills --skill tyk-facts -a claude-code   # one skill, one agent
```

**From a local clone** (links the skills into `~/.claude/skills` or `~/.codex/skills`):

```bash
gh repo clone TykTechnologies/tyk-skills && cd tyk-skills
./scripts/install-skill.sh --engine claude    # or codex, or both
```

Either plugin CLI also accepts the clone's path in place of `TykTechnologies/tyk-skills`. The repository root is also an [Agent Plugin](https://agent-plugins.org) (`plugin.json` plus `skills/`).

### Install a specific version

Each release is a tag, `v<version>`, listed in [`CHANGELOG.md`](CHANGELOG.md) and on the repository's releases page. To stay on one:

```bash
# Codex: pin the marketplace to the tag
codex plugin marketplace add TykTechnologies/tyk-skills --ref v0.1.0
codex plugin add tyk-skills@tyk-skills

# Claude Code, or any agent through install-skill.sh: install from a clone at the tag
git clone --branch v0.1.0 --depth 1 https://github.com/TykTechnologies/tyk-skills.git
claude plugin marketplace add ./tyk-skills
claude plugin install tyk-skills@tyk-skills
```

A clone at a tag changes only when you check out another tag.

### How updates arrive

- **Claude Code** reads the marketplace from this repository's default branch. `claude plugin marketplace update tyk-skills` refreshes it and `claude plugin update tyk-skills@tyk-skills` installs the newer version (restart Claude Code to apply). Claude Code can also refresh marketplaces automatically; that is a per-marketplace setting under `/plugin`. Install from a clone at a tag if every update should be a decision you make.
- **Codex**: `codex plugin marketplace upgrade` refreshes Git marketplaces. A marketplace added with `--ref` stays on that ref.
- **`skills` CLI and `install-skill.sh`**: nothing changes until you run the install again or pull the clone. `install-skill.sh` links the skills by default, so `git pull` in the clone updates them in place; `--copy` installs a copy instead.

Read the [`CHANGELOG.md`](CHANGELOG.md) entry before updating.

## Skills

| Skill | Use it when | What you get |
| --- | --- | --- |
| `tyk-facts` | Any factual question: what is open source and what needs a licence, pricing model, trial path, versions and images, deployment options | Answers quoted from Tyk's current wording with the date it was last reviewed, and the commands to re-resolve versions live |
| `tyk-demo-builder` | You want to prove, not just read about, specific capabilities: authentication, traffic controls, transforms, Portal, observability, governance, MCP, AI Studio, plugins, Operator, CI/CD | An isolated Docker Compose bundle built from your requirements, with executable evidence for every requirement and a rehearsal script |
| `tyk-poc-runbook` | You are running a proof of concept or bake-off with acceptance criteria | A plan template and five acceptance criteria (GitOps delivery and rollback, limits across replicas, Redis and control-plane resilience, latency under load, a three-year cost frame) with tests and evidence to keep |
| `tyk-migration-planner` | You are moving from Kong, Apigee, Portkey or another gateway, or choosing between Tyk Sync and Tyk Operator | Concept and plugin mapping tables with documentation links, cut-over recipes that keep existing URLs and keys, and a GitOps decision guide |
| `tyk-infra-sizing` | You need gateway, Redis, Pump and analytics-store sizing for a traffic profile, with N-1 checks and a cost estimate | A traceable calculation, a Markdown sizing report and an editable Excalidraw topology from one brief |
| `tyk-excalidraw-diagrams` | You want the deployment drawn: data planes, control plane, MDCB, Redis placement, zones, multi-region | An editable `.excalidraw` file in the Tyk house style, plus a PNG |
| `tyk-api-definitions-routing` | You are writing, importing or fixing a Tyk OAS API definition, or a request gets a 404, 403 or an ignored setting | Definitions that load on the first try, the path-matching rules with real responses, and a checker for the mistakes that fail silently |
| `tyk-rate-limits-keys-policies` | Each partner, team or plan needs its own rate limit and quota, or a limit test gives odd counts | Keys and policies for the open source Gateway and the Dashboard, which limiter counts with one Gateway and with several, and a burst test |
| `tyk-gitops-sync-operator` | You want Tyk configuration in Git and delivered by CI, with promotion, drift correction and rollback | A choice between Tyk Sync, Tyk Operator and plain files, a working CI job, and the deletion and drift behaviour of each |
| `tyk-mcp-gateway` | You are putting MCP servers behind Tyk Gateway or turning a REST API into MCP tools | MCP proxy definitions, authentication including OAuth discovery and token exchange, per-tool access and limits, and the edition each part needs |
| `tyk-ai-gateway` | You need an AI gateway: one endpoint for several LLM providers, per-app keys, failover, cost tracking | Tyk AI Studio set up through its admin API, with a stub LLM for testing and the Community versus Enterprise boundary as observed |
| `tyk-developer-portal-branding` | The Tyk Developer Portal has to carry an organisation's brand, end to end | A theme built from a brand kit by script, every page type and email checked, and a list of what cannot be branded |
| `tyk-developer-portal-setup` | Developers need to find your APIs in the Tyk Developer Portal, sign up, request access and get credentials | The Portal connected to the Dashboard, products and plans from policies, catalogues and audiences, apps, credentials and dynamic client registration, proved with a real API call |
| `tyk-authentication` | You are choosing or fixing how clients authenticate: tokens, Basic, HMAC, JWT with an identity provider, OAuth, mutual TLS, or several methods at once | Each method set up in a Tyk OAS definition, the exact responses for missing, invalid and expired credentials, and the traps |
| `tyk-observability` | You want Tyk traffic in your own tracing, metrics and logging tools, or in the Dashboard | Native OpenTelemetry traces and metrics, Tyk Pump back ends, access logs, per-consumer attribution, and a Grafana dashboard |
| `tyk-troubleshooting` | A Tyk deployment returns errors you cannot explain, or something stopped working | A symptom-to-cause decoder built from reproduced failures, and a script that collects a redacted diagnostic bundle |
| `tyk-custom-plugins` | Configuration is not enough and you need custom code in the Gateway | Go, JavaScript, Python and gRPC plugins and plugin bundles, each proved with a working example, and where each hooks in |
| `tyk-websockets-streams-webhooks` | You are proxying WebSocket or Server-Sent Events, bridging to Kafka with Tyk Streams, or reacting to Gateway events with webhooks | Streams definitions, real-time proxying settings, event handlers and webhook payloads, and what a client sees when a broker is down |
| `tyk-api-governance` | You want API design rules enforced on Tyk APIs, in the Dashboard, in CI or at Kubernetes admission | Rulesets, warn versus block, CI linting with the same rules, and the Operator admission check |
| `tyk-upgrades` | You are planning or running a Tyk upgrade | Upgrade order, backups and rollback, the breaking changes between releases, and a script that snapshots and compares the stack before and after |
| `tyk-deployment` | You are installing Tyk for production, staging or anything long-lived: choosing the edition, platform and topology (including multi-site and MDCB), starting and proving the install, or checking a configuration is ready for real traffic | Built on the public [tyk-install](https://github.com/TykTechnologies/tyk-install) repository: a deployment plan (one topology-matrix row, preflight, pinned versions, licences by name), the stack started and proved with a real request through the gateway, a rerun that changes nothing and a restart that keeps state, and an audit grouped into already correct, needs changing (with `file:line`) and needs a decision |

Production and long-lived installs go through the `tyk-deployment` skill, which works on a checkout of the public [tyk-install](https://github.com/TykTechnologies/tyk-install) repository: its deployment files, guides and tools stay there, and the skill clones it where you choose.

## Example prompts

- "Is Tyk Gateway open source, and which parts need a licence?"
- "Build me a Tyk proof of concept with JWT auth, rate limiting and OpenTelemetry, and prove each one works."
- "Size a Tyk deployment for 2,000 requests per second across two regions."
- "We run Kong today. Map our plugins and workflow onto Tyk."
- "Give partner A 100 requests a second and partner B 10 on the same Tyk API."
- "Expose our Orders OpenAPI as MCP tools through Tyk, with OAuth."
- "Keep our Tyk APIs and policies in Git and deploy them from GitHub Actions."
- "Install the self-managed stack on our Kubernetes cluster, and tell us what to change before go-live."

The skill descriptions trigger them, so a slash command is optional. From a clone, `./scripts/build-demo.sh` runs the demo builder end to end; `--help` lists its options.

## Requirements

- Claude Code or Codex (or another agent the `skills` CLI supports), authenticated.
- Python 3. The skills use the standard library only, except Pillow for rasterising diagrams and sizing packs (`requirements-diagrams.txt`).
- For the demo builder: Docker with Docker Compose, `curl`, `jq` and `openssl`, and a Tyk Dashboard licence. The `tyk-facts` skill has the current free-trial path; the licence goes into the bundle's private `.env`, never into this repository. The open source Gateway alone needs no licence and is installed with the `tyk-deployment` skill.

No Tyk source code is needed. The skills check behaviour on your own running stack and cite the public documentation.

Generated work goes outside this repository, one folder per project: `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/` with `demos/<slug>/`, `sizing/` and `docs/`.

## What these skills run on your machine

Skills are instructions and scripts that your agent runs with your permissions. Nothing starts unless you ask for it, and nothing is installed system-wide. What happens is:

- **Docker stacks, when you ask for one.** The demo builder generates a Docker Compose bundle and starts it with the bundle's own `scripts/start-demo-stack.sh`. Several skills carry a small lab stack (`assets/` in `tyk-rate-limits-keys-policies`, `tyk-ai-gateway` and others) that you start the same way. Images come from Docker Hub: Tyk's `tykio/*` images plus Redis, PostgreSQL, Python and, for the modules that need them, OpenTelemetry, Prometheus, Tempo, Kafka, Vault and similar. The `operator` module installs cert-manager and the Tyk Operator Helm chart (from `helm.tyk.io`) into a disposable local kind cluster that its install script creates; the `mcp` module builds a mock MCP server from Tyk's public GitHub repository.
- **Deployments, when you ask for one.** The `tyk-deployment` skill starts the tyk-install repository's own deployments with Docker Compose, Helm (in the Kubernetes context and namespace it shows you first) or rpm/deb packages, once you have approved the plan it wrote. Those files are tyk-install's, not this plugin's: their Compose stacks publish ports on every interface as the files define them, so read the target directory's README before running one on a shared host.
- **Ports on 127.0.0.1 only for this plugin's stacks.** Every port the demo builder and the lab stacks publish binds to the loopback address by default. The builder picks a free block of host ports between 23000 and 49000 (or `--port-base`); the lab stacks use fixed defaults, such as 8080, that you can change in their `.env` files. Set `BIND_ADDRESS` in a bundle's `.env` only when you mean to present from another machine on a trusted network.
- **Files in your workspace.** Bundles, sizing packs, diagrams and briefs are written under `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/`, or where you say. Licences, generated credentials and certificates stay in that bundle's git-ignored `.env` and `.runtime/`. Nothing is written into this repository; `./scripts/build-demo.sh` refuses an output path inside it.
- **Network calls.** Docker Hub, for images and, unless you pass `--versions pinned`, for the tag list the builder reads to pick the latest stable versions; GitHub, helm.tyk.io and the cert-manager release only for the modules named above; GitHub to clone the tyk-install repository when the `tyk-deployment` skill needs one and you have no clone; and the public pages your agent reads to verify a fact, mainly https://tyk.io/docs/ and https://tyk.io/llms.txt. The Developer Portal branding steps download brand assets from your organisation's website only after you approve the list of files. The AI Studio stacks talk to stub LLMs unless you configure a real provider.
- **Nothing else is sent anywhere by these skills.** The Tyk components they start behave as the products do. Two of them can report usage to Tyk by default: the Tyk Dashboard, controlled by `TYK_DB_DISABLETELEMETRY` ([Dashboard configuration](https://tyk.io/docs/tyk-dashboard/configuration)), and Tyk AI Studio, controlled by `TELEMETRY_ENABLED`, which the `tyk-ai-gateway` stack sets to `false` and the demo builder's AI Studio companion leaves at its default. Add `TYK_DB_DISABLETELEMETRY=true` to a bundle's `config/dashboard.env` to switch the Dashboard's off.

## How the facts are kept current

- **Checked before it is written.** Every statement in a skill was checked on a running Tyk stack or against a public documentation page before it went in, and anything that could not be checked is left out. The skills state each fact plainly, scoped to the version it holds for, with a link to the public page that backs it; they do not narrate how a fact was checked. Where the product and its documentation differ, the skill says both. Licensed components whose code is not public are described only by their observed behaviour and their public documentation; no private code is quoted or identified. Licensing and packaging wording is quoted from [tyk.io/llms.txt](https://tyk.io/llms.txt), the [pricing page](https://tyk.io/pricing/) and the [Editions page](https://tyk.io/docs/apim).
- **Dated.** Reference files that record facts or dated findings carry a `Last verified:` line with the versions and date they were checked against. Versions are resolved live from Docker Hub and the release notes, never written down as "latest". Where two public sources disagree, the skills say so.
- **Re-verifying.** Updating references is a separate maintenance task: check the reference against the page or release it names, fix it, and update its `Last verified:` line. `skills/tyk-facts/references/licensing-and-packaging.md` lists the exact steps for the licensing block. Treat a conflict between a reference and the live stack or the documentation as a bug in the reference.

## Repository layout

```text
.
├── plugin.json                 Agent Plugins manifest; the root is the plugin root
├── .claude-plugin/             Claude Code plugin and marketplace manifests
├── .codex-plugin/, .agents/    Codex plugin and marketplace manifests
├── skills/<name>/              SKILL.md, references/, scripts/, agents/openai.yaml
├── scripts/                    install, demo launcher, manifest sync, validation
├── .github/                    CI and release workflows, issue forms
├── AGENTS.md                   the contract the skills follow
├── .claude/CLAUDE.md           Claude Code notes for contributors
├── CONTRIBUTING.md             how to propose a change or add a skill
├── SECURITY.md                 how to report a vulnerability
└── CHANGELOG.md                what changed in each release
```

## Validate before a change

```bash
./scripts/validate-repo.sh --skip-compose
```

It checks the repository contracts and residue rules, compiles and unit-tests the Python, lints the shell, generates and validates clean-room demo bundles, validates the skill packages, builds the example sizing pack and diagram, scans for runtime and secret residue, validates the plugin manifests for Agent Plugins, Claude Code and Codex (and that they all carry the version and description in `plugin.json`), and rejects any skill text that sends its reader to Tyk source. Without `--skip-compose` it also validates the generated Compose files with Docker. CI runs the same script on every pull request and every release tag.

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) explains how to propose a change and how to add a skill; [`AGENTS.md`](AGENTS.md) is the contract the skills follow and the one changes are reviewed against. Never add customer material, licences, credentials or generated bundles.

To report a wrong fact, use the "Wrong fact" issue form with the skill and reference file, the claim, and the public page or live result that contradicts it.

## Licence

[MPL-2.0](LICENSE).
