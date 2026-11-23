# Changelog

All notable changes to this plugin are recorded here. Versions follow
[Semantic Versioning](https://semver.org/), and each release is tagged `v<version>` in this
repository. The version in `plugin.json` and every other manifest matches the newest entry.

## [Unreleased]

### Skills

- `tyk-deployment-planner`: choose an edition, platform and topology from the public tyk-install
  repository's topology matrix, run its preflight, and write a deployment plan with pinned
  versions, licences by name and the OpenShift overlay, including multi-site and MDCB questions.
- `tyk-deployment-runner`: start, bootstrap and prove a tyk-install deployment with a real request
  through the gateway, idempotency and persistence evidence.
- `tyk-production-readiness`: audit a Tyk configuration against tyk-install's production,
  performance, security and OpenShift guidance, reported as already correct, needs changing and
  needs a decision.

### Changed

- Every hand-off to the separate `tyk-install` plugin now names the deployment skills above; the
  tyk-install repository keeps the deployment files, guides and tools they run.
- `tyk-facts`: `references/deployment-paths.md` describes the tyk-install repository instead of
  its former plugin, and adds the MDCB port, data-plane behaviour without the control plane and
  the Synchroniser default.

## [0.1.0] - unreleased

First public release.

### Skills

- `tyk-facts`: licensing, editions, pricing model, trial path, versions, images and deployment
  options, quoted from Tyk's public pages with the date they were last reviewed.
- `tyk-demo-builder`: an isolated Docker Compose demo or proof of concept built from your
  requirements, with executable evidence for every requirement and a rehearsal script.
- `tyk-poc-runbook`: a proof-of-concept plan with acceptance criteria, tests and the evidence to
  keep.
- `tyk-migration-planner`: concept and plugin mapping from Kong, Apigee, Portkey or another
  gateway, cut-over recipes, and a choice between Tyk Sync and Tyk Operator.
- `tyk-infra-sizing`: gateway, Redis, Pump and analytics-store sizing with N-1 checks, a cost
  estimate and an editable topology diagram.
- `tyk-excalidraw-diagrams`: editable Excalidraw deployment diagrams in the Tyk house style.
- `tyk-api-definitions-routing`: Tyk OAS API definitions that load on the first try, path-matching rules,
  and a checker for mistakes that fail silently.
- `tyk-rate-limits-keys-policies`: per-consumer rate limits and quotas with keys and policies, and
  how the limit is counted with one Gateway and with several.
- `tyk-gitops-sync-operator`: Tyk configuration in Git, delivered by CI with Tyk Sync, Tyk
  Operator or plain files.
- `tyk-mcp-gateway`: MCP servers behind Tyk Gateway and REST APIs exposed as MCP tools, with
  authentication, per-tool access and limits.
- `tyk-ai-gateway`: Tyk AI Studio as an AI gateway, configured through its admin API.
- `tyk-developer-portal-branding`: a Tyk Developer Portal theme built from a brand kit, with every
  page type and email checked.
- `tyk-developer-portal-setup`: the Developer Portal connected to the Dashboard, with products,
  plans, catalogues, developer access, apps and credentials.
- `tyk-authentication`: client authentication methods in Tyk OAS and how each one fails.
- `tyk-observability`: OpenTelemetry traces and metrics, Tyk Pump, access logs and Dashboard
  analytics.
- `tyk-troubleshooting`: a symptom decoder and a diagnostic bundle script.
- `tyk-custom-plugins`: Go, JavaScript, Python and gRPC plugins and plugin bundles.
- `tyk-websockets-streams-webhooks`: WebSocket and SSE proxying, Tyk Streams and Gateway event webhooks.
- `tyk-api-governance`: API design rulesets in the Dashboard, in CI and at Kubernetes admission.
- `tyk-upgrades`: upgrade order, backups, breaking changes and post-upgrade checks.

### Repository

- Every statement in a skill is checked on a running Tyk stack or against a public page; no skill
  needs Tyk source code.
- `SECURITY.md`, `CONTRIBUTING.md`, a pull request template and issue forms.
- One source for the version and description (`plugin.json`, copied by
  `scripts/sync_manifests.py`), with support, privacy and terms links in the Claude Code and Codex
  manifests.
- A release workflow that validates each version tag and publishes it as a GitHub release.
