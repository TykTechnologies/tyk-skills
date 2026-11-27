# Deployment paths

Last verified: 2026-11-20 against the tyk-install repository (README, tree, `.env.example` files, `docs/topology-matrix.md`, CI runs), the Tyk documentation, and Gateway `tykio/tyk-gateway:v5.16.0` (open source, file mode)

Which way Tyk can be deployed, what each needs, and where to install it from. Production and
long-lived installs go through the `tyk-deployment` skill, which plans, runs and reviews them on a
checkout of the public tyk-install repository (https://github.com/TykTechnologies/tyk-install); a time-boxed
proof goes through the `tyk-demo-builder` skill.

## The tyk-install repository

It holds the deployment files for every option below, the guides in `docs/` (topology matrix,
prerequisites, runtime, bootstrap, debugging) and the tools in `tools/` (preflight, deployment
verification, production-guidance audit), which also run without an agent. The `tyk-deployment`
skill uses the user's existing clone or clone it where the user chooses:

```bash
git clone --depth 1 https://github.com/TykTechnologies/tyk-install <dir>
```

## The options

| Option | What runs where | Licence needed (per the tyk-install README) | tyk-install path | Docs |
| --- | --- | --- | --- | --- |
| Open source only | Gateway, Redis (Pump optional), all yours; no Dashboard | "none" | `docker/oss/`, `kubernetes/helm-oss/` (`tyk-oss` chart), `baremetal/` (rpm/deb with systemd) | https://tyk.io/docs/tyk-open-source |
| Self-Managed | Dashboard, Gateway, Portal, Pump, Redis, PostgreSQL, all yours | "Dashboard license, Portal license (optional)" | `docker/self-managed/`, `docker/getting-started/` (sample APIs, policies and Portal content, `start.sh`), `kubernetes/helm-self-managed/` (`tyk-stack` chart) | https://tyk.io/docs/tyk-self-managed |
| Hybrid | Your Gateways, Pump and local Redis; Tyk Cloud hosts the control plane | "Tyk Cloud account with MDCB credentials" | `docker/hybrid/`, `kubernetes/helm-hybrid/` (`tyk-data-plane` chart) | https://tyk.io/docs/deployment-options/tyk-hybrid |
| Self-managed multi data centre (MDCB) | Your control plane plus remote data planes | "Dashboard license and MDCB license" | `docker/mdcb/`, `kubernetes/helm-mdcb/` (`tyk-control-plane` and `tyk-data-plane` charts) | https://tyk.io/docs/api-management/mdcb |
| Tyk Operator | Kubernetes controller reconciling Tyk resources | "Operator license" | `kubernetes/standalone-operator/` | https://tyk.io/docs/tyk-stack/tyk-operator/installing-tyk-operator |
| AI Studio | AI Studio, Edge Gateway, PostgreSQL | "none for Community Edition, AI license for Enterprise Edition" | `docker/ai-studio/`, `kubernetes/helm-ai-studio/` | https://tyk.io/docs/ai-management/ai-studio/installation/overview |
| Tyk Cloud | Fully managed SaaS | Tyk Cloud account | none (hosted) | https://tyk.io/docs/tyk-cloud |
| Air-gapped | Self-Managed or open source with no access to public registries | as for the topology chosen | mirror the images or packages of the chosen path; see the docs guide, and `docs/air-gapped.md` in tyk-install for each path | https://tyk.io/docs/api-management/air-gapped-deployment |

Notes, each from the cited source:

- Self-Managed and Hybrid "both run on Red Hat OpenShift from tyk-charts 5.4.0 by layering their
  `values-openshift.yaml` overlay" (tyk-install README).
- An optional observability overlay (OpenTelemetry Collector, Prometheus, Alertmanager, Tempo,
  Loki, Grafana) exists for Self-Managed and Hybrid: `docker/observability/`, `kubernetes/observability/`
  (tyk-install README).
- Bare metal: rpm/deb packages with systemd, no containers (tyk-install `baremetal/`), for every
  packaged component: Gateway, Dashboard, Pump, MDCB (control-plane side), Developer Portal and
  Tyk Sync; single sign-on uses the Identity Broker built into the Dashboard and Portal. The
  topology matrix (`docs/topology-matrix.md`) verifies the Gateway path end to end and marks the
  others `bundled` (artifacts exist). A data-plane or hybrid Gateway, AI Studio and Tyk Operator
  have no bare-metal path.
- MDCB: it serves its RPC service on port 9091 by default; when the control plane is unavailable
  the data planes "will continue to handle API requests, rejecting only new authorization tokens
  created on other Gateways"; the Synchroniser, which pushes keys, certificates and OAuth clients
  to the data planes in advance, "is disabled by default"
  (https://tyk.io/docs/api-management/mdcb). Planning a multi-site layout: the
  `tyk-deployment` skill.
- Hybrid: "you host the Gateways (the data plane) in your own environment, so all API traffic and
  the critical runtime parts stay with you. Tyk Cloud hosts the control plane: configuration,
  analytics, the Dashboard and identity management"
  (https://tyk.io/docs/deployment-options/tyk-hybrid). What keeps working when the
  control plane or Redis is unavailable is in `licensing-and-packaging.md`, "Resilience".
- Air-gapped: the guide covers "Kubernetes (Helm) — Mirroring container images and packaging Helm
  charts for offline installation" and "Bare Metal / VMs (Linux packages) — Creating a local
  mirror of Tyk's PackageCloud repositories"
  (https://tyk.io/docs/api-management/air-gapped-deployment). Self-Managed: the docs say "There
  is no calling home, and there are no usage limits" (https://tyk.io/docs/tyk-self-managed). Depending
  on the licence, the Dashboard can still send usage telemetry to Tyk by default; set `TYK_DB_DISABLETELEMETRY=true` to
  switch it off (https://tyk.io/docs/tyk-dashboard/configuration).
- The Self-Managed install methods on the product page: "Docker, Linux packages, Kubernetes Helm
  charts, Chef or Puppet, including air-gapped environments" (https://tyk.io/llms.txt, entry for
  https://tyk.io/self-managed/).
- tyk-install pins image tags in each `.env.example` and its history shows automated
  "sync component versions" updates; see `versions-and-images.md`.

## Trials and accounts

- Self-Managed (Dashboard, Portal, Operator, MDCB): https://tyk.io/self-managed-trial/ ("a 14-day
  trial"; "the license key arrives by email", https://tyk.io/docs/apim; "Tyk emails the license
  key within 5 minutes", https://tyk.io/pricing.md). The key goes in
  `TYK_LICENSE_KEY` for the Docker quick start; for Operator, `global.license.operator` in the
  Helm charts or `TYK_OPERATOR_LICENSEKEY` (https://tyk.io/pricing.md).
- Tyk Cloud: https://tyk.io/sign-up/ ("a free 48-hour trial with no credit card",
  https://tyk.io/pricing.md); account creation guide:
  https://tyk.io/docs/getting-started/create-account.
- AI Studio Enterprise: the overview page says to contact `support@tyk.io` to request a licence
  (https://tyk.io/docs/ai-management/ai-studio/overview). Community Edition needs none.
- Paid licences after the trial: https://tyk.io/contact/ ("no public list prices, by design").

These are the advertised trial terms. They do not say which products, licence scopes (API
governance, for example, is a separate scope enabled on request) or how many gateway nodes a
particular trial key enables. Check the key that arrives (the Dashboard shows its licence
details) before planning a proof that depends on a specific scope or node count.

## Quick starts outside the plugin

- Self-Managed with sample content: https://tyk.io/docs/getting-started/quick-start
- Open source Gateway in two minutes:
  https://tyk.io/docs/deployment-and-operations/tyk-open-source-api-gateway/quick-start
- Helm charts overview: https://tyk.io/docs/product-stack/tyk-charts/overview (repository
  `https://helm.tyk.io/public/helm/charts/`). Pin image tags; chart defaults trail the latest
  Gateway (`versions-and-images.md`).

## Open source only, file mode: how a Tyk OAS API is stored

With no Dashboard (`use_db_app_configs: false`) the Gateway loads API definitions from its `apps`
folder. This section describes `tykio/tyk-gateway:v5.16.0` with Redis, file-based policies
(`policy_source: file`, `policy_path`) and `hash_keys: true`. The `tyk-api-definitions-routing` skill covers the definitions themselves and the
`tyk-rate-limits-keys-policies` skill covers keys and policies.

- **A REST Tyk OAS API is a pair of files**: `<id>.json`, the classic wrapper with
  `"is_oas": true`, and `<id>-oas.json`, the OpenAPI document carrying `x-tyk-api-gateway`. An
  MCP proxy definition is stored the same way with `<id>-mcp.json` as its companion
  (https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/api_definition.go).
- **Produce the pair with the Gateway API.** `POST /tyk/apis/oas` with the OpenAPI document
  returns `{"key":"orders","status":"ok","action":"added"}`; after `GET /tyk/reload/group` the
  `apps` folder holds `orders.json` and `orders-oas.json` (the mount must be writable).
- **The wrapper is generated, not hand-written.** A stub wrapper with only ids, `org_id` and
  `"is_oas": true` next to a valid `-oas.json` is skipped at reload, with the log lines
  `Listen path is empty` and `Spec not valid, skipped!`, and the API answers `404`. A bare
  OpenAPI file with no `x-tyk-api-gateway` loads nothing either.
- **Keys.** `POST /tyk/keys` with `rate` and `per` and `access_rights` keyed by API id creates a
  key. With `rate: 3, per: 60` the fourth call gets `429 {"error": "Rate Limit Exceeded"}`. A
  per-operation limit in the OAS document
  (`x-tyk-api-gateway.middleware.operations.<operationId>.rateLimit` with `"rate": 2, "per": "1m"`)
  answers the third call with `429 {"error": "API Rate Limit Exceeded"}`. A call with no key gets
  `401 {"error": "Authorization field missing"}`.
- **File-based policies are looked up per organisation.** The policy's `org_id`, the key
  request's `org_id` and the API's `x-tyk-api-gateway.info.orgId` must all match (for example
  `default`). A key request with another `org_id` and `apply_policies` gets
  `{"status":"error","message":"Failed to create key, ensure security settings are correct."}` and
  the log `policy not found: "ten-per-minute"`. With matching key and policy but an API with no
  `orgId`, the request gets the same error and the log says `attempting to apply policy from
  different organisation to key, skipping`. With all three set to `default`, the key is created
  and a 10 requests per 60 seconds policy gives ten `200` and then `429`.

The public tyk-install repository has a repeatable equivalent of part of this: its `docker/oss`
stack ships a keyed example pair, a 10 requests per minute policy and `scripts/keyed-poc.sh`, and
its CI job "OSS Gateway, keyed API and rate limit" checks a keyed API, ten `200` then two `429`,
and an OAS import that writes the `apps/` file pair
(https://github.com/TykTechnologies/tyk-install/blob/main/.github/workflows/test-oss.yml). The
job runs on pull requests that change `docker/oss`.
