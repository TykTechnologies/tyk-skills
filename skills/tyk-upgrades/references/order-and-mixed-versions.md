# Upgrade order and mixed-version windows

Last verified: 2026-11-19 against Tyk Gateway (`tykio/tyk-gateway-ee`) and Dashboard 5.15.0, 5.15.1 and 5.16.0, Tyk Pump 1.17.0 and 1.18.0, Tyk Developer Portal 1.19.0, 1.20.0 and 1.20.1, on Docker Compose with Redis 7.2 and PostgreSQL 16

## The documented order

From the upgrade guide, https://tyk.io/docs/developer-support/upgrading:

| Deployment | Order |
| --- | --- |
| Self-managed (Dashboard, Gateway, Pump on separate machines) | 1. Dashboard, 2. Gateway, 3. Pump |
| Self-managed with MDCB, control plane first | 1. MDCB, 2. Pump, 3. Dashboard, 4. Gateway |
| Self-managed with MDCB, then each data plane | 1. Go plugins, 2. Pump, 3. Gateway |
| Hybrid (control plane on Tyk Cloud) | control plane on Tyk Cloud first, then the data plane: 1. Go plugins, 2. Hybrid Pump, 3. Hybrid Gateways |
| Open source Gateway | 1. Gateway, 2. Pump |
| Developer Portal (MCP section) | "before or at the same time as Tyk Dashboard, and definitely before creating any MCP Proxy policies in the Dashboard" |
| Tyk Operator managing MCP proxies (MCP section) | after the Dashboard and Gateway run the new version |

The rows marked "MCP section" and the `enable_mcp_aggregation` rule below come from the
guide's "MCP Gateway (v5.13+)" section, written for deployments that use MCP Gateway when they
move onto 5.13 or later; its reasons (the `/api/mcps` endpoint and the MCP aggregation call
first shipped in 5.13) do not arise again between 5.15 and 5.16.

The same page gives the reason for control plane first: it avoids forward-compatibility
problems of a new Gateway against an old MDCB, and Hybrid gateways keep working while
disconnected from their control plane. With MDCB and MCP analytics, keep
`enable_mcp_aggregation` set to `false` on Hybrid Pumps until every component runs the new
version.

The page also warns that during a rolling or blue-green upgrade every Dashboard instance that
shares Redis or the database must use the same licence; otherwise gateway registration fails
with `GetSpareNode returned node not allowed by this dashboard's license`.

Tyk Cloud control planes are upgraded by the customer from the Cloud console, manually or by
the scheduled Auto Upgrade, which also upgrades the data planes that belong to that control
plane: https://tyk.io/docs/tyk-cloud/environments-deployments/managing-control-planes.

## What each step does on Docker Compose

One Gateway, one Dashboard, one Pump and one Portal on Docker Compose, with a loop calling two
APIs every 0.2 s during each step and a state snapshot (`verification.md`) after each step:

| Step | Traffic during the step | State after the step |
| --- | --- | --- |
| Dashboard 5.15.0 -> 5.15.1 | every call answers 200 | unchanged |
| Gateway 5.15.0 -> 5.15.1 | about 10 s refused, then one 404, then 200 | unchanged |
| Portal 1.19.0 -> 1.20.0 | not affected | new field on Portal products only |
| Dashboard 5.15.1 -> 5.16.0 | every call answers 200 | new field on a classic API readback only |
| Gateway 5.15.1 -> 5.16.0 | same pattern as the 5.15.1 Gateway restart | unchanged; two behaviour changes (`breaking-changes-5-15-to-5-16.md`) |
| Pump 1.17.0 -> 1.18.0 | not affected | analytics records keep arriving |
| Portal 1.20.0 -> 1.20.1 | not affected | old Portal admin API token refused with 403 |

Going straight from Portal 1.19.0 to 1.20.1 and from Gateway and Dashboard 5.15.1 to 5.16.0 in
the same order gives the same results, and an empty diff once the Portal token is re-issued.

The Gateway keeps serving from memory while the Dashboard restarts. A single Gateway does not:
plan a load balancer and at least two Gateways for a rolling upgrade, as the upgrade guide says.

### A restarting Gateway answers `/hello` before it can serve

Status codes every 0.2 s while a 5.16.0 Gateway container is recreated:

```text
 8 x  hello=200 ready=200 api=200
37 x  hello=000 ready=000 api=000      <- container restarting
 1 x  hello=200 ready=503 api=404      <- process up, APIs not loaded yet
27 x  hello=200 ready=200 api=200
```

`/ready` answers `503` with `{"status":"error","message":"A successful API reload did not
happen"}` until the first API load, then `200` with
`{"status":"pass","version":"5.16.0","description":"Tyk GW Ready",...}`. Use `/ready` (the
`readiness_check_endpoint_name` setting, default `ready`) for load balancer and Kubernetes
readiness; `/hello` is a liveness check. The same window exists on 5.15.1.

## Mixed-version windows

### New Dashboard, old Gateway (the documented intermediate state)

Dashboard 5.16.0 with Gateway 5.15.1:

- Traffic through the Gateway: all 200.
- An API created on the 5.16.0 Dashboard (`POST /api/apis/oas`) is loaded and served by the
  5.15.1 Gateway: `{"Status":"OK","Message":"API created",...}`, then `GET /mixed-a/items` 200.
- Gateway behaviour stays old until the Gateway is upgraded: the URL rewrite of
  `breaking-changes-5-15-to-5-16.md` still sends `/target` upstream.
- Dashboard rules change at once: updating an API whose ID has unsafe characters is refused
  with 400 as soon as the Dashboard runs 5.16.0, before the Gateway moves.
- The Dashboard node list (`GET /api/system/nodes`) keeps the Gateway registered.

### Old Dashboard, new Gateway (the reverse)

Dashboard 5.15.1 with Gateway 5.16.0 (a stack on 5.15.1 with only the Gateway upgraded):

- Traffic: all 200; `/ready` 200 with `"version":"5.16.0"`.
- An API created on the 5.15.1 Dashboard is served by the 5.16.0 Gateway (200).
- The new Gateway behaviour applies at once (the rewrite sends `/rewrite/target`).
- Updating the API with the unsafe ID is accepted (`{"Status":"OK","Message":"Api updated"}`):
  the check is in the Dashboard, and the Gateway does not check IDs of APIs it loads from the
  Dashboard, as the Gateway 5.16.0 release notes say.

So in both directions the stack keeps serving. Keep the window short anyway: behaviour that
depends on both sides (a field the old side does not know) is not covered by these checks.

## Which side owns which change

| Change between 5.15 and 5.16 | Takes effect when |
| --- | --- |
| URL rewrite target no longer stripped of the listen path | the Gateway is upgraded |
| Detailed analytics redact the `Authorization` header | the Gateway is upgraded |
| Unsafe custom API IDs refused on create and update | the Dashboard is upgraded (and the Gateway for its own API) |
| Portal admin API token re-issue (1.20.1) | the Portal is upgraded |
