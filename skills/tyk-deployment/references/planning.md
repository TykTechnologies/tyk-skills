# Planning a deployment

Decide what to deploy, confirm the machine can run it, and write the plan down. Planning creates
nothing and starts nothing; `running.md` applies the plan.

Every path is relative to the tyk-install checkout (`SKILL.md`, "Get the tyk-install
repository"), and every command runs from its root.

## Workflow

1. Establish what the user needs: which edition, which platform, a local trial or a long-lived
   install, and which licences they hold. Ask rather than assume when the answer changes the
   target directory. For what each edition includes and which licence it needs, use the
   `tyk-facts` skill (`references/editions-and-features.md`, `references/deployment-paths.md`).
2. Read `docs/topology-matrix.md` and resolve the request to exactly one row. Its "Choosing"
   section is the order of questions; a licence the user does not hold rules a row out. If
   nothing matches, say so and stop; never improvise a deployment path.
3. More than one site, region or data centre, or "either site can fail": read `multi-site.md`
   before going further. MDCB is asymmetric, and most "we need HA" requests are met by two or
   more Gateways against one control plane.
4. Run the preflight for the chosen platform. On Docker pass `--target <the row's directory>` so
   only that topology's ports are checked; a port belonging to a component the row does not deploy
   is not a blocker. Report every real blocker with its remediation from `docs/prerequisites.md`.

   ```bash
   python3 tools/preflight.py --platform docker --target docker/self-managed \
     --licence TYK_LICENSE_KEY --env-file docker/self-managed/.env
   python3 tools/preflight.py --platform helm --namespace tyk
   ```

5. Read the version pins from the target directory, never from memory (`## Versions`).
6. Decide which guidance profiles apply: `prod` and `performance` for anything that is not a
   laptop trial, `security` whenever the deployment outlives the session, `openshift` on
   OpenShift. Run the audit and record its findings in the plan, grouped as
   `production-readiness.md` describes.

   ```bash
   python3 tools/profiles.py audit kubernetes/helm-self-managed/values.yaml \
     kubernetes/helm-self-managed/.env.example --profile prod --profile security
   ```

7. On OpenShift, prepare the overlay (`openshift-overlay.md`). Never edit a tracked file.
8. Write `deployment-plan.md` where `SKILL.md`, "Where generated files go", says.

## Deployment plan

`deployment-plan.md` states:

- The chosen row, copied verbatim from `docs/topology-matrix.md`, including its status. The
  status values are defined at the top of that file; a `guidance-only` row has no artifact, and
  the plan says so first.
- The tyk-install commit planned against, the target directory, the components and the pinned
  versions read from the files.
- Licences required and which are present, by variable name only, never a value.
- Preflight results, including anything missing and how to install it.
- Profiles audited, their findings in the three buckets of `production-readiness.md`, and the
  path of any OpenShift overlay.
- For multi-site plans, the answers to the questions in `multi-site.md`.
- The Docker context, or the Kubernetes context and namespace, the run will touch.
- The exact commands the run will use, in order (from `docs/runtime.md` and the target
  directory's README).
- What will not be verified, and why (no licence, no cluster, no egress).

A plan that omits the status or the parts that cannot be verified is not finished.

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

## In the tyk-install checkout

- `docs/topology-matrix.md`: always first; the only authority on which paths exist.
- `docs/prerequisites.md`: what each platform needs and how to install it.
- `docs/runtime.md`: the commands the run will use.

## Done when

The plan names one matrix row with its status and the tyk-install commit, preflight has passed or
every blocker is reported with a remediation, any generated overlay validates with an unchanged
environment-variable count, and the run's commands are written out in order. If the request maps
to a `guidance-only` row, say plainly that no artifact exists before the user spends time on it.
