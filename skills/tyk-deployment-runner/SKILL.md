---
name: tyk-deployment-runner
description: Start a planned Tyk deployment from a checkout of the public tyk-install repository with Docker Compose, Helm or rpm/deb packages, bootstrap the organisation, admin user and first API, and prove it works with a real request through the gateway, a rerun that changes nothing and a restart that keeps state. Use when running, restarting or bootstrapping a Tyk install, or when a fresh install will not come up - gateway not starting, Dashboard licence errors, Redis or PostgreSQL connection failures, MDCB RPC login errors. Not for choosing the topology, which is the `tyk-deployment-planner` skill, or for diagnosing an established stack, which is the `tyk-troubleshooting` skill.
---

# Tyk Deployment Runner

Apply a deployment and prove it works. Start from a plan written by the `tyk-deployment-planner`
skill; without one, resolve the target to exactly one row of `docs/topology-matrix.md` in the
tyk-install checkout and confirm it with the user before starting anything.

Everything here works on a checkout of the public tyk-install repository
(https://github.com/TykTechnologies/tyk-install): its deployment directories, the guides in
`docs/` and the tools in `tools/`. This skill carries no copy of them.

## Get the tyk-install repository

Use the checkout the plan names. Otherwise use the user's existing clone (confirm it with
`git -C <dir> remote get-url origin`), or clone it where the user chooses, suggesting
`${TYK_WORKSPACE:-~/tyk-workspace}/tyk-install`:

```bash
git clone --depth 1 https://github.com/TykTechnologies/tyk-install <dir>
```

Never clone it inside a customer repository, another project, or this plugin's folder. Below,
every path is relative to that checkout, and every command runs from its root.

## Workflow

1. Read the plan if one exists. Otherwise resolve one matrix row and confirm it with the user.
2. Echo the target before touching it: the Docker context, or the Kubernetes context and
   namespace, and what will be created. For a cluster, confirm the namespace is one the user
   intends to use.
3. Prepare secrets: `cp .env.example .env` in the target directory, then have the user put the
   licence values in it. Never print them; `.env` is git-ignored and stays that way.
   `docker/getting-started/` is the exception: it has no `.env.example`, starts with its own
   `./start.sh`, and reads the licence from `docker/self-managed/.env`
   (`docs/topology-matrix.md`).
4. Start the stack with the commands in `docs/runtime.md` and the target directory's README.
5. Bootstrap the organisation, admin user, a test API and the Portal as `docs/bootstrap.md`
   describes (`utils/bootstrap/`, or the Dashboard API).
6. Verify with `tools/verify_deployment.sh` and include its real output (`## Evidence`).
7. Prove idempotency: re-run the configuration step and show the second run changes nothing.
8. Prove persistence: restart the stack and run the verification again.
9. Report the versions running (read back from the deployment), URLs, evidence, rerun commands
   and anything left unverified.

## Running

```bash
# Docker (not docker/getting-started, which starts with ./start.sh)
cd docker/<target> && cp .env.example .env   # the user fills in the licence values
docker compose up -d
docker compose ps

# Helm
helm repo add tyk-helm https://helm.tyk.io/public/helm/charts/ && helm repo update
helm upgrade --install tyk tyk-helm/<chart> --version <pinned chart version> \
  -n <namespace> --create-namespace \
  -f kubernetes/<target>/values.yaml -f <overlay>.yaml
```

Order matters: Redis and PostgreSQL ready before the Dashboard, the Dashboard before the Gateway,
and an MDCB control plane before any data plane. The Compose files encode this with healthchecks
and `depends_on`; on Kubernetes wait with `kubectl wait --for=condition=ready`, and install Redis
and PostgreSQL first with the commands each `kubernetes/*/README.md` gives. Bare metal follows
`baremetal/README.md`; it covers Gateway, Dashboard and Pump only.

## Evidence

Container status is not evidence: a Gateway can be running and route nothing. Before reporting
success, each of these has real command output behind it:

- Gateway `/hello` is healthy, with its Redis and Dashboard checks healthy where they apply.
- Dashboard `/hello` is healthy and the licence is valid, on licensed editions.
- **A real request reaches an upstream through the gateway** and returns the upstream's response.
- Idempotency: re-running configuration leaves the deployment unchanged.
- Persistence: after `docker compose restart` or a pod restart, the checks above still pass.

```bash
# Docker
tools/verify_deployment.sh --edition self-managed --stack self-managed --route /httpbin/get

# Kubernetes or OpenShift: --namespace switches component checks to kubectl
kubectl port-forward -n tyk svc/gateway-svc-tyk-tyk-gateway 8080:8080 &
tools/verify_deployment.sh --edition self-managed --namespace tyk --route /httpbin/get
```

Always pass `--stack` (Docker) or `--namespace` (Kubernetes). Without them the script inspects
the first matching container on the host, which on a machine running more than one Tyk stack can
report another deployment's Redis as evidence for this one. `--edition` takes `oss`,
`self-managed`, `hybrid`, `mdcb-control-plane`, `mdcb-data-plane` or `ai-studio`; `--help` lists
the rest.

Read the versions back from what is running, not from the files:

```bash
curl -s http://localhost:8080/hello | jq -r '.version'
docker compose images
kubectl get pods -n <namespace> -o jsonpath='{.items[*].spec.containers[*].image}'
```

Report what the commands returned. If a check could not run (no licence, no cluster, no egress),
name the check and the reason. An unverified deployment is reported as unverified.

## When a step fails

Read `docs/debugging.md` in the checkout before changing configuration, and report the actual
error rather than a summary of it. MDCB and Hybrid RPC errors have their own table in
`docker/mdcb/README.md`, "Troubleshooting". Once the stack has been up and something stops
working, the `tyk-troubleshooting` skill takes over.

## Safety

- Never delete, upgrade or reconfigure resources the user did not ask you to touch. Ask before
  reusing a namespace, project or Compose stack that already exists.
- Do not use direct database access for bootstrap or credential recovery; use the APIs
  (`docs/bootstrap.md`, `docs/debugging.md`).
- Never print or commit a licence, token, generated credential or certificate.
- Prefer an overlay over editing a tracked config file. If a tracked file must change, say so and
  show the diff.
- `docker compose down -v` destroys data. Confirm before running it, and never run it against a
  stack you did not create in this session.
- The PostgreSQL and Redis passwords, `TYK_GW_SECRET` and `TYK_GW_NODESECRET` committed in
  tyk-install are development defaults. Never call a deployment that uses them production-ready,
  and say why; the `tyk-production-readiness` skill lists them.

## Hand-offs

- Choosing the topology and writing the plan: the `tyk-deployment-planner` skill.
- Hardening the configuration before it carries real traffic: the `tyk-production-readiness`
  skill.
- An established stack that misbehaves: the `tyk-troubleshooting` skill.
- Upgrading what you deployed: the `tyk-upgrades` skill.
- APIs, keys and policies beyond the bootstrap test API: the `tyk-api-definitions-routing` and
  `tyk-rate-limits-keys-policies` skills.

## Completion contract

Finish when every evidence item above has real command output behind it, idempotency and
persistence are demonstrated, and the report lists the deployed versions read from the running
stack, URLs, the tyk-install commit, the exact rerun commands, and every check that could not be
performed with its reason.
