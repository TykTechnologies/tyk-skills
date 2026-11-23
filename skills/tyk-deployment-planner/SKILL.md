---
name: tyk-deployment-planner
description: Choose a Tyk deployment and write the plan before anything is created - edition (open source Gateway, Self-Managed, Hybrid, MDCB, AI Studio), platform (Docker Compose, Helm, OpenShift, bare metal), prerequisites, licences by variable name, pinned versions and the OpenShift overlay - from a checkout of the public tyk-install repository. Use when someone wants to install, deploy or stand up Tyk for production, staging or anything long-lived, or asks for a multi-site, multi-region or MDCB layout. Plans only. Not for a time-boxed demo, which is the `tyk-demo-builder` skill, or for capacity numbers, which the `tyk-infra-sizing` skill owns.
---

# Tyk Deployment Planner

Decide what to deploy, confirm the machine can run it, and write the plan down. Applying the plan
is the `tyk-deployment-runner` skill's job: this skill creates nothing and starts nothing.

Everything here works on a checkout of the public tyk-install repository
(https://github.com/TykTechnologies/tyk-install): its deployment directories, the guides in
`docs/` and the tools in `tools/`. This skill carries no copy of them.

## Get the tyk-install repository

1. Ask whether the user already has a clone. If so, use it and confirm it is the right one
   (`git -C <dir> remote get-url origin`). Updating it (`git -C <dir> pull --ff-only`) is the
   user's call; say which commit you planned against (`git -C <dir> rev-parse --short HEAD`).
2. Otherwise clone it where the user chooses, suggesting
   `${TYK_WORKSPACE:-~/tyk-workspace}/tyk-install`:

   ```bash
   git clone --depth 1 https://github.com/TykTechnologies/tyk-install <dir>
   ```

   Never clone it inside a customer repository, another project, or this plugin's folder.

Below, every path is relative to that checkout, and every command runs from its root.

## Workflow

1. Establish what the user needs: which edition, which platform, a local trial or a long-lived
   install, and which licences they hold. Ask rather than assume when the answer changes the
   target directory. For what each edition includes and which licence it needs, use the
   `tyk-facts` skill (`references/editions-and-features.md`, `references/deployment-paths.md`).
2. Read `docs/topology-matrix.md` and resolve the request to exactly one row. Its "Choosing"
   section is the order of questions; a licence the user does not hold rules a row out. If
   nothing matches, say so and stop; never improvise a deployment path.
3. More than one site, region or data centre, or "either site can fail": read
   `references/multi-site.md` before going further. MDCB is asymmetric, and most "we need HA"
   requests are met by two or more Gateways against one control plane.
4. Run the preflight for the chosen platform. On Docker pass `--target <the row's directory>` so
   only that topology's ports are checked; a port belonging to a component the row does not deploy
   is not a blocker. Report every real blocker with its remediation from `docs/prerequisites.md`.

   ```bash
   python3 tools/preflight.py --platform docker --target docker/self-managed \
     --licence TYK_LICENSE_KEY --env-file docker/self-managed/.env
   python3 tools/preflight.py --platform helm --namespace tyk
   ```

5. Read the version pins from the target directory, never from memory (see `## Versions`).
6. Decide which guidance profiles apply: `prod` and `performance` for anything that is not a
   laptop trial, `security` whenever the deployment outlives the session, `openshift` on
   OpenShift. Run the audit and record its findings in the plan; the `tyk-production-readiness`
   skill explains how to read and report them.

   ```bash
   python3 tools/profiles.py audit kubernetes/helm-self-managed/values.yaml \
     kubernetes/helm-self-managed/.env.example --profile prod --profile security
   ```

7. On OpenShift, prepare the overlay (`## Configuration overlays`). Never edit a tracked file.
8. Write `deployment-plan.md` (`## Where to put the plan`).

## Deployment plan

`deployment-plan.md` states:

- The chosen row, copied verbatim from `docs/topology-matrix.md`, including its status. The
  status values are defined at the top of that file; a `guidance-only` row has no artifact, and
  the plan says so first.
- The tyk-install commit planned against, the target directory, the components and the pinned
  versions read from the files.
- Licences required and which are present, by variable name only, never a value.
- Preflight results, including anything missing and how to install it.
- Profiles audited, their findings grouped as the `tyk-production-readiness` skill groups them,
  and the path of any OpenShift overlay.
- For multi-site plans, the answers to the questions in `references/multi-site.md`.
- The Docker context, or the Kubernetes context and namespace, the run will touch.
- The exact commands the `tyk-deployment-runner` skill will run, in order (from `docs/runtime.md`
  and the target directory's README).
- What will not be verified, and why (no licence, no cluster, no egress).

A plan that omits the status or the parts that cannot be verified is not finished.

## Where to put the plan

Plans and overlays are the user's files, not the repository's. In order of preference:

1. A path the user names.
2. The user's project workspace: `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/docs/`.
3. Inside the tyk-install checkout at `.tyk-deploy/`, which its `.gitignore` excludes; say so.

Never write a plan or overlay into a path git tracks (check with `git check-ignore -v <path>`),
and never into this plugin's folder. Say where you put it.

## Configuration overlays

`kubernetes/helm-self-managed/` and `kubernetes/helm-hybrid/` ship a committed
`values-openshift.yaml` (tyk-charts 5.4.0 or later). Prefer it, and pass it again on every later
`helm upgrade`. For the Helm paths without one (`helm-oss`, `helm-mdcb`), generate the overlay
from the `# Required for deploying on RedHat OpenShift` blocks, writing it outside tracked paths:

```bash
python3 tools/profiles.py apply kubernetes/helm-oss/values.yaml \
  --profile openshift -o <plan dir>/openshift-values.yaml
```

The OpenShift profile is the only one `apply` accepts. For production, performance and security,
record the `audit` findings in the plan for a person to make: a commented-out value is often the
current default shown for illustration rather than the recommendation, and a Helm overlay replaces
a list instead of merging it, so a generated `extraEnvs` would drop every entry the base file set.

Install with the base file first, then the overlay (`-f <dir>/values.yaml -f openshift-values.yaml`).
Validate every generated overlay, and confirm it removed nothing: `helm template` exits 0 on an
overlay that silently replaced a list.

```bash
V=kubernetes/helm-oss/values.yaml
helm template check tyk-helm/tyk-oss -f $V                                   | grep -c '\- name: TYK'
helm template check tyk-helm/tyk-oss -f $V -f <plan dir>/openshift-values.yaml | grep -c '\- name: TYK'
```

The two counts must match. Audit findings whose recommendation is prose rather than a value need
a human decision: carry them into the plan.

## Versions

- Docker and bare metal: `GATEWAY_VERSION`, `DASHBOARD_VERSION`, `PUMP_VERSION`,
  `PORTAL_VERSION`, `MDCB_VERSION` (and `AI_STUDIO_VERSION` for AI Studio) in the target
  directory's `.env.example`.
- Helm: the `image.tag` values in the directory's `values*.yaml`, and the chart version its README
  passes with `--version` (`helm-oss` and `helm-mdcb` also record it in `.env.example`). Pin both:
  the chart version and the image tags move independently, and chart defaults can trail the
  newest Gateway (the `tyk-facts` skill, `references/versions-and-images.md`).
- When the user wants a newer release than the pin, resolve it live as the `tyk-facts` skill
  describes, check the release notes for breaking changes
  (https://tyk.io/docs/developer-support/release-notes/overview), and set it in the target's
  `.env` or an overlay, never in `.env.example`.
- The order and the risks of moving between versions belong to the `tyk-upgrades` skill.

## References

- `references/multi-site.md`: read before planning MDCB or anything multi-region, multi-data
  centre or "either site can fail".
- In the tyk-install checkout: `docs/topology-matrix.md` (always first; the only authority on
  which paths exist), `docs/prerequisites.md` (what each platform needs and how to install it),
  `docs/runtime.md` (the commands the runner will use).

## Safety

- Plan only. Do not run `docker compose up`, `helm install`, `kubectl apply`, `oc apply` or a
  package manager from this skill.
- Never write a licence, token or password into the plan, a log or a chat message. Name the
  variable instead.
- Never read licence values out of an unrelated deployment's `.env` to fill a gap. Ask the user.
- Echo the Docker or Kubernetes context and namespace in the plan so the user sees the target
  before anything touches it.
- Keep generated files out of version control (`## Where to put the plan`).

## Hand-offs

- Applying the plan, bootstrapping and proving it: the `tyk-deployment-runner` skill.
- Reviewing a configuration for production: the `tyk-production-readiness` skill.
- Gateway count, Redis and analytics-store sizing, cost: the `tyk-infra-sizing` skill.
- The topology drawn: the `tyk-excalidraw-diagrams` skill.
- Licences, editions, versions and trials: the `tyk-facts` skill.
- A time-boxed demo or proof of a capability: the `tyk-demo-builder` skill.

## Completion contract

Finish when the plan names one matrix row with its status and the tyk-install commit, preflight
has passed or every blocker is reported with a remediation, any generated overlay validates with
an unchanged environment-variable count, and the runner's commands are written out in order. If
the request maps to a `guidance-only` row, say plainly that no artifact exists before the user
spends time on it.
