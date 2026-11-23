---
name: tyk-demo-builder
description: Generate, run and prove a self-managed Tyk demo or proof of concept as an isolated Docker Compose bundle built from modules (APIs, authentication, traffic controls, Developer Portal, MCP, OpenTelemetry, governance, plugins, Operator, Tyk Sync, AI Studio), with an executable proof for every requirement. Use when asked to build a Tyk demo, POC or evaluation stack from requirements, an RFP or OpenAPI files. Not for production installs, which the tyk-deployment skill owns.
---

# Tyk Demo Builder

Turns requirements into a generated Docker Compose bundle that runs self-managed Tyk (Dashboard,
Gateway, Pump, and the Portal or other components the selected modules need) and proves each
requirement with a recorded command and result. The bundle depends on nothing outside itself
except Docker, `jq`, `curl`, Python 3 and the licence values the user supplies.

It needs a Tyk Dashboard licence (the Portal, AI Studio and governance need theirs too; which
licence covers what, and trial routes, belong to the `tyk-facts` skill). For production or
long-lived installs, hand over to the `tyk-deployment` skill; for acceptance criteria on top of a
bundle, the `tyk-poc-runbook` skill.

## Ask First (Each Has A Default)

| Question | Default |
| --- | --- |
| Which requirements must be proved on the running stack (status `live`), and which are talk track? | every requirement the selected modules cover is proved on the running stack |
| Which modules? | the smallest set that proves the requirements (see **Modules**) |
| Where does the bundle go? | `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/demos/<slug>/`, never inside this plugin |
| Display name and slug? | the user's project or organisation name; slug in lower case |
| Which port block? | the first free block of 21 ports; `--port-base` to choose |
| Which versions? | the newest release of each component on Docker Hub, per release line |
| With the Portal: the organisation's brand kit? | the neutral placeholder kit (valid for a technical proof) |
| May the bundle be reset with data loss? | no: ask before every `reset-demo-stack.sh --purge` |

## Workflow

1. Read the requirements and diagrams. Write a requirement matrix with one row per testable
   behaviour and a status of `live`, `talk-track` or `blocked`.
2. Choose modules and generate:

   ```bash
   python3 <skill-dir>/scripts/init_demo_bundle.py --output <dir> --slug <slug> \
     --name "<display name>" --modules rest,api-key,portal,mcp,otel [--port-base <port>]
   ```

   Options: `--identity-mode local|external` (default `local`), `--<component>-version` or
   `--versions pinned` (the builder's default set, no network), `--brand-dir <kit>` with
   `portal`, `--force` to replace a previously generated bundle only.
3. Put the licence values in the bundle's private `.env` (`TYK_LICENSE_KEY`, and
   `TYK_PORTAL_LICENSE` with `portal`). Never print them.
4. Adapt the generated OAS, upstream stubs, policies and notes to the requirements.
5. Validate before starting anything: `python3 <skill-dir>/scripts/validate_bundle.py .`
6. Tell the user what will start and where, then start it. `./scripts/start-demo-stack.sh`
   prints every service and the host address and port it binds (all on `127.0.0.1` unless
   `BIND_ADDRESS` is changed) before it starts them.
7. Bootstrap, configure, verify:

   ```bash
   ./scripts/bootstrap-demo-stack.sh
   ./scripts/configure-demo.sh
   ./scripts/verify-demo.sh
   ```

8. Run the separate runners of the modules that have them (Tyk Sync promotion, Operator,
   AI Studio companion, Portal screenshots; table in `references/requirements-and-evidence.md`),
   then `./scripts/verify-demo.sh` again.
9. `./scripts/rehearse-demo.sh`: configures twice, promotes with Tyk Sync when selected,
   verifies, restarts the gateways and verifies again.
10. Before sharing, remove private files and run
    `python3 <skill-dir>/scripts/sanitize_bundle.py . --strict-runtime` (see **Sharing**).

## Modules

```text
rest, api-key, basic-auth, jwt, oauth, mtls, chained-auth,
routing, transforms, validation, error-overrides,
rate-limit, quota, cache, circuit-breaker,
websocket, graphql, streams,
portal, dcr, portal-branding, portal-catalogues,
otel, go-plugin, grpc-plugin, js-plugin,
operator, tyk-sync, cicd, governance, mcp, ai-studio,
gateway-sharding, key-import, custom-domain, versioning, vault, upstream-mtls
```

The generator adds dependencies (`cicd` brings `tyk-sync`, `portal` and `portal-branding` bring
each other), writes `demo.config.json` with the modules, ports, versions and their sources, and
generates one requirement row and proof id per module. Every bundle also proves `HEALTH-001`.
`validate_bundle.py` rejects an unknown module name; a component added by hand goes under
`custom_modules` in `demo.config.json`.

The generator refuses combinations it cannot prove, rather than build them half-working:

- `mcp` below Gateway or Dashboard 5.15.0, and `mcp,portal` below Portal 1.19.0;
- `governance` below Dashboard or Gateway 5.15.0, and with `operator`, an Operator below 1.5.0;
- `ai-studio` below AI Studio 2.3 (anything from the 2.2 line or older);
- `versioning` together with `tyk-sync` or `cicd` (Tyk Sync does not read the importer's
  version link);
- `--brand-dir` without `portal`, or a kit with missing files, non-SVG/PNG assets, or an SVG
  carrying scripts or event handlers.

The gateway listens on plain HTTP unless `mtls` is selected, which needs TLS; this is derived
from the modules and recorded as `gateway_tls`. `ai-studio` runs as its own Compose project in
`companion/ai-studio/` and adds nothing to the bundle's `compose.yaml`.

## Versions

By default each component resolves to its newest strict `vX.Y.Z` tag on Docker Hub, read one
release line at a time (`name=vX.Y.`), because the anonymous tag listing stops at 1,000 tags. On
2026-11-19 that was Gateway and Dashboard 5.16.0, Pump 1.18.0, Portal 1.20.1, Operator 1.6.0 and
Tyk Sync 2.3.0; the AI Studio module is written for AI Studio 2.3.0. A component whose line has
no release yet, or that Docker Hub cannot answer for, falls back to the builder's default with a
warning. Tags are concrete but not digests: rehearse after any version change. Details in
`references/standalone-runtime.md`.

## References

| Read | When |
| --- | --- |
| `references/requirements-and-evidence.md` | statuses, proof shape, screenshots, separate runners, rehearsal gate |
| `references/standalone-runtime.md` | ownership, ports, network exposure, state, lifecycle and reset, sharding, transport, versions |
| `references/oas-and-traffic.md` | routing, transforms, validation, errors, limits, quota, cache, circuit breaker, versioning, custom domain |
| `references/authentication.md` | api-key, basic-auth, jwt, oauth, mtls, chained-auth, key-import, vault, upstream-mtls, identity service |
| `references/portal-and-dcr.md` | portal, portal-branding, portal-catalogues, dcr |
| `references/protocols-and-streams.md` | websocket, graphql, streams |
| `references/plugins-and-operator.md` | go-plugin, grpc-plugin, js-plugin, operator, tyk-sync, cicd |
| `references/observability.md` | `HEALTH-001` and otel |
| `references/governance.md` | governance, with or without operator |
| `references/mcp.md` | mcp |
| `references/ai-studio.md` | ai-studio, before selecting it |
| `references/debugging.md` | a bundle proof fails |

These pages describe what the modules generate and prove. How the Tyk features themselves work
belongs to the sibling skills named in each page.

## Evidence Rules

- A row is `live` only when every proof id it lists passed in the current run; a row whose
  proof did not run becomes `blocked`, with the ids that did not run. Generated configuration is
  never evidence that runtime behaviour works.
- For each answer, say where it comes from: checked on the user's running stack (name the
  command, the image tag and the date) or stated on a public https://tyk.io/docs/ page (give
  the URL). A link to a file in a public Tyk GitHub repository at a release tag is a citation,
  never a step for the user. Say when a point was not checked.
- When the stack and the documentation disagree, the stack wins for that version; say so and
  cite both. Never ask the user for Tyk source code, and never fetch it to answer.
- An internal behaviour inferred from probing a running build is labelled as inferred, with
  the version.

## Pitfalls

- `reset-demo-stack.sh --purge` deletes every volume of the project. It asks for the project
  name, or refuses without `--yes` when not interactive. Pass `--yes` only after the user agrees.
- `SYNC-001` checks content, not only ids: an API file edited after the last promotion keeps the
  row `blocked` until the next promotion.
- `PORTAL-UI-001` counts only screenshots registered with `scripts/record-screenshot.sh` for this
  stack and brand kit; a non-empty PNG alone is not evidence.
- Scripts list Dashboard objects with `?p=-1` and Portal objects with `per_page=100`; a lookup on
  the default page misses objects.
- A value left in your shell from an older `.runtime/runtime.env` makes configuration skip work;
  open a fresh terminal after a purge.
- `BIND_ADDRESS=0.0.0.0` publishes the local token issuer to the network: it signs any claims.

## Limitations

- CI templates are checked statically and their promote step runs locally; no CI runner runs.
- Upstreams, the identity service, the MCP server and the LLMs are local stubs. Say so in the
  runbook; a requirement about a real back end stays `talk-track` or `blocked` until that back
  end is wired and its run passes.
- `operator` needs a disposable kind cluster; without one `OPERATOR-001` stays `blocked`.
- Browser screenshots need a browser; without one the UI rows stay `blocked`.
- `gateway-sharding` needs a Dashboard licence that allows two gateway nodes.

## Visible Naming

Generated objects are named `<display name> <object>`, and generated e-mail addresses use
`<slug>.invalid`, so screenshots show the user's name, not the builder's. Internal markers live
in tags and `meta_data`. Proof scripts find their objects by a stable key (`meta_data.stable_name`,
a fixed custom `id`, a fixed e-mail) and update in place, so a rerun never creates a second copy.

## Sharing

Before sending a bundle, delete `.env`, `.runtime/`, generated certificates, compiled plugins,
`migration/imported-keys.json`, evidence bodies, and the AI Studio companion's `secret.env`,
`admin.env`, `.runtime/` and `data/`; then run
`python3 <skill-dir>/scripts/sanitize_bundle.py . --strict-runtime`, which fails while any
git-ignored file or symbolic link is still on disk. A clean result means these checks found
nothing, not that no secret exists. The sanitizer also reports a Portal brand kit that is not the
neutral placeholder; in the organisation's own bundle pass `--own-brand`.

## Safety

- Never read another bundle, never read a database directly, and never delete an object that
  does not carry the bundle's ownership marker.
- Show secrets only through `./scripts/show-credentials.sh`.
- Never commit, push or open a pull request without an explicit instruction from the user.

## Before Reporting Success

- [ ] `validate_bundle.py` passes.
- [ ] `verify-demo.sh` ran after every selected runner; no requested row is `failed`.
- [ ] `rehearse-demo.sh` passed (configuration twice, gateway restart, verification again).
- [ ] Each requirement has a status, asset, proof id, evidence location and evidence label.
- [ ] The user was told which services run, on which ports, and how to stop them
      (`./scripts/stop-demo-stack.sh`).
- [ ] Report the changed files, live results, talk-track items, blockers, URLs and the exact
      rerun commands.
