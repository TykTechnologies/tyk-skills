# Runtime, Ports, Lifecycle And Versions

Last verified: 2026-11-19 against Tyk Gateway and Dashboard 5.16.0, Pump 1.18.0, Developer Portal 1.20.1, Tyk Sync 2.3.0 and Tyk Operator 1.6.0.

This page covers how a generated bundle runs. For installing Tyk for production or long-lived
use, hand the user to the `tyk-deployment-planner` skill; for diagnosing a component, the
`tyk-troubleshooting` skill.

## Ownership

The demo slug is the Compose project name and the ownership marker. Stable API ids are
`<slug>-<module>`; policies carry a fixed custom `id` and `meta_data.stable_name`. Scripts update
or remove only objects carrying the bundle's marker, and never operate outside the bundle's
Compose project.

## What Starts, And Which Ports It Binds

`scripts/start-demo-stack.sh` prints every service of the project, and for each the host address
and port it publishes (or "no host port"), before it starts anything. For example, for
`rest,api-key,portal,mcp,otel` at `--port-base 57000`: the Dashboard on `127.0.0.1:57000`, the
Portal on `57001`, the Gateway on `57002`, the upstream on `57003`, the MCP mock and Inspector on
`57012`-`57014`, the Collector on `57008`-`57009`, Prometheus on `57010`, Tempo on `57011`; Redis,
PostgreSQL and Pump publish no host port. The services are those of the selected modules only
(Compose profiles); the bundle README lists the Dashboard, Gateway and Portal URLs.

Ports come from a block of 21 consecutive ports starting at `--port-base` (or the first free
block), recorded in `.env` and `demo.config.json`. The generator refuses a block in which any
port is occupied. New port names are appended, never inserted, so a port written down earlier
keeps its offset.

## Network Exposure

Every host-published port binds to `${BIND_ADDRESS:-127.0.0.1}`; `.env` sets
`BIND_ADDRESS=127.0.0.1`. The bundled identity service signs any claims it is sent, so it must
stay local: anyone who reaches it can mint tokens the bundle's JWT APIs accept.
`BIND_ADDRESS=0.0.0.0` is the explicit opt-in for presenting from another machine, on a trusted
network, for the session; the MCP Inspector, the Vault dev server and the AI Studio companion
stay on `127.0.0.1` even then. Services reach each other on the Compose network, never through
published ports.

Containers that reach the Dashboard through `host.docker.internal` (`ci/tyk-sync-docker.sh`, the
kind cluster of `operator`, the AI Studio companion's MCP import) reach it with the loopback
bind on OrbStack. On a Docker engine where `host.docker.internal` does not reach
the host's loopback, connect the container to the bundle's network and use the Dashboard's
service name instead of widening `BIND_ADDRESS`.

## Runtime State

Generated ids and secrets live in `.runtime/runtime.env` (mode `600`), sourced only by the
bundle's scripts through `scripts/common.sh`, which first clears every key a script records so a
value inherited from an older stack in your shell is never mistaken for work already done.
Everything under `.runtime/` is private, and `validate_bundle.py` fails on any file there that
grants group or other access. Keep `.runtime/`, `.env`, generated certificates, compiled plugins
and evidence bodies out of version control.

Bootstrap uses the Dashboard admin and management APIs and the Portal API; nothing reads a
database directly.

## Idempotency

Configuration is rerun on purpose (`rehearse-demo.sh` runs it twice). APIs are imported by stable
id and read back; policies are upserted by stable name after the APIs exist; Portal pages and
content blocks are matched by path or title and updated in place; every Dashboard list call
passes `?p=-1` and every Portal list call `per_page=100`, so a lookup never misses an object on a
later page.

## Lifecycle

- `scripts/stop-demo-stack.sh` stops the containers and keeps the data.
- `scripts/reset-demo-stack.sh` removes the containers and keeps the volumes.
- `scripts/reset-demo-stack.sh --purge` deletes every volume of the project (the Dashboard,
  Portal and analytics databases, Redis, and every key, policy and credential the stack issued),
  archives `.runtime/runtime.env`, and removes the Operator and Tyk Sync evidence. It first
  lists the volumes it will delete, then asks for the project name to be typed; a
  non-interactive caller must pass `--yes`, and gets a refusal without it
  ("Refusing to delete data without confirmation"). An agent passes `--yes` only after the user
  has agreed to lose that data.

## Gateway Sharding (`SHARD-001`)

`gateway-sharding` adds `tyk-gateway-external`, a second gateway with the same image and
settings plus `TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED=true` and
`TYK_GW_DBAPPCONFOPTIONS_TAGS=external`; the primary gateway stays unsegmented and loads every
API. `<display name> External Services` carries the tag `external` in `server.gatewayTags`,
`<display name> Internal Services` the tag `internal`. Every gateway gets a readable Compose
`hostname:` (`<slug>-gateway-internal`, `<slug>-gateway-external`), because the Dashboard node
list shows hostnames only.

`SHARD-001` asserts: the external API answers 200 on both gateways; the internal API 200 on the
primary and 404 on the external one; the external gateway's own `/tyk/apis` lists only
external-tagged APIs; and `GET /api/system/nodes` on the Dashboard lists both hostnames. The
Dashboard licence must allow two gateway nodes; read the remaining node count
on the Nodes & licenses page before the demo. Both gateways share one Redis, so keys, quotas and
rate-limit counters are shared: sharding places configuration, it does not isolate.

## Gateway Transport

The gateway listens on plain HTTP unless a selected module cannot be proved without TLS. The
generator derives this from the module set (`TLS_MODULES` in `init_demo_bundle.py`) and records
it as `gateway_tls` in `demo.config.json`; there is no flag. Today only `mtls` turns TLS on,
because a client certificate is only presented over TLS. A self-signed gateway certificate adds
`curl -k` to every command, browser and MCP-client trust warnings, and a demo CA to mount into
every in-network caller, and proves nothing else, so do not enable it to look production-like.

Everything the generator emits agrees with the chosen scheme through one set of tokens
(`tls_tokens()`): scripts call the gateway only through `gw_curl` and `${GATEWAY_URL}`, the
Dashboard's `TYK_DB_HOSTCONFIG_GENERATEHTTPS` matches the listener, and certificate generation,
the `server.pem` mount and the MCP Inspector's CA setting exist only in a TLS bundle. When TLS is
on, the generated README says so. The `mtls` bundle passes all its proofs over TLS.

## Component Versions

By default `init_demo_bundle.py` resolves each component to its newest strict `vX.Y.Z` tag on
Docker Hub (`tykio/tyk-dashboard`, `tykio/tyk-gateway-ee`, `tykio/tyk-pump-docker-pub`,
`tykio/portal`, `tykio/tyk-operator`, `tykio/tyk-sync`; with `ai-studio`,
`tykio/tyk-ai-studio-ent` on a tag `tykio/tyk-microgateway-ent` also carries). Pre-release
builds, `-fips`, architecture tags, floating tags and `latest` are never chosen, with one
exception: while no stable AI Studio 2.3 tag is on Docker Hub for both AI Studio images, the
resolver takes the newest 2.3 pre-release build both carry (never an `unstable` build) and
prints a note naming the tag; a stable 2.3 tag wins as soon as it is published.

Docker Hub's anonymous tag listing stops after 1,000 tags, so the resolver reads one release
line at a time with `name=vX.Y.`: it starts at the line of the builder's default version for that
component, moves forward while each line holds a release, and after the first line without one
tries the next major's `.0` line once. The Gateway starts at the Dashboard's resolved line and
takes the Dashboard's tag when it exists, otherwise the highest tag in that line, with a warning.
On 2026-11-19 this resolved Gateway and Dashboard v5.16.0, Pump v1.18.0, Portal v1.20.1,
Operator v1.6.0 and Tyk Sync v2.3.0. When a line holds no release yet, or Docker Hub cannot
answer, that component falls back to the builder's default with a warning naming it; generation
never fails for that reason.

- An explicit `--<component>-version` always wins. `--versions pinned` uses the builder's default
  set (`KNOWN_GOOD_VERSIONS`, mirrored in the starter `.env.example`) with no network access.
- `.env` and `demo.config.json` carry the concrete tags, and `demo.config.json` records each
  tag's source under `component_version_sources` (`user`, `latest (Docker Hub, <UTC date>)`,
  `known-good fallback`, `pinned`, or `matches dashboard`); the bundle README shows the same
  table. A tag is not an image digest, and the supporting images use floating tags, so
  `docker compose images` is the record of what actually ran.
- A newer release can change module behaviour: rehearse every selected module after any version
  change, and move the builder's default set only after such a rehearsal.
