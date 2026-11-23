# Multi-site

Last verified: 2026-11-20 against the Tyk MDCB documentation (https://tyk.io/docs/api-management/mdcb), the upgrade guide (https://tyk.io/docs/developer-support/upgrading) and the tyk-install `docker/mdcb/` and `kubernetes/helm-mdcb/` directories (MDCB `v2.14.0`, Gateway `v5.16.0`, tyk-charts `5.5.0`)

Read this before planning MDCB. The topology matrix routes "more than one data centre" here, and
that routing is right, but MDCB does not by itself deliver what most people mean by "either site
can fail", and the difference decides the design.

## What MDCB gives you

An asymmetric topology: one control plane (Dashboard, MDCB, its Redis and database) and any
number of data planes (Gateways in RPC mode, each data plane with its own Redis). See
https://tyk.io/docs/api-management/mdcb.

| If this fails | Then |
| --- | --- |
| A data-plane site | Its traffic stops. Other data planes and the control plane are unaffected. |
| The control-plane site | The MDCB page says the data planes "will continue to handle API requests, rejecting only new authorization tokens created on other Gateways". The Dashboard is unavailable, so no configuration or key change reaches any data plane until it returns. |

So a control-plane outage leaves every remaining site serving with frozen configuration. Whether
that is acceptable is a business decision, and it is the first thing to settle with the user.

## The synchroniser

Data planes need keys, certificates and OAuth clients to serve requests. Without the synchroniser
they pull these on demand from the control plane, so a resource a data plane has not yet pulled is
unavailable to it while the control plane is down. The synchroniser pushes them to the data planes
in advance. It is disabled by default (https://tyk.io/docs/api-management/mdcb, "MDCB
Synchroniser").

Both halves must be switched on; one alone does nothing:

- Control plane: `sync_worker_config.enabled` in MDCB (`TYK_MDCB_SYNCWORKER_ENABLED=true` in the
  tyk-install `docker/mdcb/control-plane/confs/mdcb.env`, `global.mdcbSynchronizer.enabled: true`
  in `kubernetes/helm-mdcb/`).
- Each data plane: `slave_options.synchroniser_enabled` on the Gateway
  (`TYK_GW_SLAVEOPTIONS_SYNCHRONISERENABLED=true`).

## What the tyk-install repository does not cover

Say plainly where the guidance stops; inventing a pattern is worse than saying there is none.

- **Two control planes that both accept writes.** Neither the MDCB documentation nor the
  tyk-install repository describes one. If the user needs it, involve Tyk directly.
- **Cross-site database replication for the control plane.** Its database holds API
  definitions, policies and users; replicating it and deciding what happens on failover is a
  database design the user owns.
- **Redis stretched across sites.** Each data plane has its own local Redis in the MDCB
  architecture; that is what lets it keep serving through a control-plane outage. A shared,
  cross-site Redis puts the dependency back and adds cross-site latency to every rate-limit check.
- **Traffic steering between sites.** DNS, GSLB, anycast or a global load balancer is where
  "either site serves traffic" is met. Tyk does not do it, and nothing in tyk-install configures
  it.
- **Backup and restore of the control plane**, which bounds a control-plane-site disaster. The
  `tyk-upgrades` skill covers backups and restores.

## Questions to settle before planning

Record the answers in the deployment plan; several change the topology, not just the values.

1. **Why two sites?** Latency, data residency or resilience? Residency usually forces separate
   data planes; resilience alone often does not (next question).
2. **Would two or more Gateways behind a load balancer in one site do?** The production guide
   recommends "Two or more Tyk Gateways, load balanced, each on a separate machine"
   (https://tyk.io/docs/planning-for-production); that is Self-Managed, with far fewer moving
   parts than MDCB.
3. **Which site owns the control plane?** Choose it deliberately.
4. **Is a period with frozen configuration acceptable** in the surviving sites while the
   control-plane site is down? If not, MDCB alone does not meet the requirement.
5. **What is the recovery time objective for the control plane?** It drives the backup and
   restore design.
6. **How does client traffic reach the right site?** Confirm something owns this.

## Planning notes

- **Start with one data plane.** Stand up the control plane and one data plane, prove the RPC link
  and a proxied request (the `tyk-deployment-runner` skill), then add the next site.
- **Port and protocol.** MDCB serves its RPC service on port 9091 by default
  (https://tyk.io/docs/api-management/mdcb). Every data-plane network must reach it. The
  tyk-install `kubernetes/helm-mdcb/README.md` exposes it with a LoadBalancer Service and notes
  that an OpenShift Route is HTTP only, so use a LoadBalancer or an ingress that can carry it.
- **TLS on the RPC link.** It crosses a network boundary. The tyk-install development defaults
  leave it off (`TYK_MDCB_USESSL=false`, and `TYK_GW_SLAVEOPTIONS_SSLINSECURESKIPVERIFY=true` on
  the data plane); their `# PROD:` markers say to enable TLS and validate certificates.
- **More than one MDCB instance.** Every data plane depends on MDCB to pick up changes; the MDCB
  page describes a health check for running it behind a load balancer, and the tyk-install
  `kubernetes/helm-mdcb/README.md` asks for two or more MDCB replicas.
- **Key hashing must agree.** `TYK_MDCB_HASHKEYS` on MDCB and `TYK_GW_HASHKEYS` on every Gateway
  must match, or keys do not resolve across the link (tyk-install `docker/mdcb/README.md`,
  "Troubleshooting").
- **Version order.** Upgrade the control plane before the data planes; the upgrade guide gives the
  reason as avoiding "forward-compatibility issues of the new Gateway using the old MDCB"
  (https://tyk.io/docs/developer-support/upgrading). Details: the `tyk-upgrades` skill.
- **Hybrid instead of MDCB.** When Tyk Cloud hosts the control plane, the customer runs only data
  planes (`docker/hybrid/`, `kubernetes/helm-hybrid/`); the same data-plane points apply.

## In the tyk-install checkout

- `docker/mdcb/README.md`: the two Compose stacks, the RPC troubleshooting table and "Before
  production".
- `kubernetes/helm-mdcb/README.md`: the Helm equivalent, exposing MDCB, OpenShift notes.
- `docs/topology-matrix.md`, "Choosing": when MDCB is the right row at all.
