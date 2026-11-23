#!/usr/bin/env python3
"""
Worked example: Tyk Cloud control plane + a self-managed data plane in Kubernetes.

Run it, then render and look at the result:

    python scripts/example_hybrid_dataplane.py /tmp/hybrid.excalidraw
    python scripts/render_excalidraw.py /tmp/hybrid.excalidraw /tmp/hybrid.png --scale 0.3

Read this alongside references/style-guide.md - it shows the band layout, the
control-plane channel routing, and the "Redis above the traffic lane" placement in
about 80 lines.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tyk_excalidraw import (Diagram, BLUE, GREEN, PURPLE, TEAL,  # noqa: E402
                            CLOUD, INTERNAL)

OUT = sys.argv[1] if len(sys.argv) > 1 else "hybrid.excalidraw"

d = Diagram()

d.text(40, -240, "Tyk hybrid: Cloud control plane, self-managed data plane", size=76)
d.text(40, -130, "The gateways run in your own cluster. API traffic stays inside it.",
       size=34, color="#495057")

# --- control plane (Tyk Cloud, outside your network) --------------------------
d.zone(80, 120, 780, 900, "Tyk Cloud", "SaaS control plane", kind=CLOUD, op=30)
d.icon("tyk-dashboard", 280, 420, 140, "Tyk-Dashboard")
d.icon("developer-portal", 660, 420, 140, "Developer Portal")
d.icon("mdcb", 280, 760, 140, "MDCB")
d.icon("postgres", 660, 760, 140, "Analytics store", sub="managed by Tyk Cloud")

# --- your Kubernetes cluster -------------------------------------------------
BX, BY, BW, BH = 1100, 120, 2100, 900
d.zone(BX, BY, BW, BH, "Your Kubernetes cluster", "internal / trusted", kind=INTERNAL)

LANE = BY + 520                     # the horizontal traffic lane
d.icon("api-consumer", BX + 130, LANE, 140, "API Consumer")
d.icon("load-balancer", BX + 420, LANE, 140, "Ingress / LB")

d.group_box(BX + 620, LANE - 200, 420, 400, "Tyk data plane (Helm)")
d.icon("tyk-gateway", BX + 730, LANE + 10, 130, "Tyk-Gateway")
d.icon("tyk-gateway", BX + 930, LANE + 10, 130, "Tyk-Gateway")

# Redis sits ABOVE the lane so its caption misses the flow arrow. The extra
# clearance (-200 rather than -165) keeps its sub-caption off the arrow's label.
d.icon("redis", BX + 1300, LANE - 200, 140, "Redis", sub="in-cluster",
       label_above=True)
d.icon("tyk-pump", BX + 1300, LANE + 190, 120, "Tyk-Pump", lsize=26)
d.icon("services", BX + 1650, LANE, 130, "Services (APIs)")
d.icon("services", BX + 1870, LANE, 130, "Services (APIs)")

# --- flows --------------------------------------------------------------------
d.arrow([(BX + 205, LANE), (BX + 345, LANE)], BLUE)
d.arrow([(BX + 495, LANE), (BX + 615, LANE)], BLUE)
d.arrow([(BX + 1000, LANE), (BX + 1570, LANE)], BLUE, label="proxied request")
d.arrow([(BX + 1010, LANE - 60), (BX + 1225, LANE - 165)], PURPLE)
d.arrow([(BX + 1240, LANE - 125), (BX + 1030, LANE - 40)], PURPLE)
d.arrow([(BX + 1300, LANE - 125), (BX + 1300, LANE + 125)], TEAL, style="dotted", sw=3)

# Control flow: out of the cluster, up the gutter, in over the zone's top edge.
CH = 960
d.arrow([(BX + 730, LANE - 210), (BX + 730, BY + 130), (CH, BY + 130), (CH, 570),
         (870, 570)], GREEN, style="dashed")
d.text(80, 1080, "green dashed = the data plane dials OUT to MDCB:\n"
                 "outbound RPC over TLS on :443,\n"
                 "no inbound firewall rules",
       size=28, color=GREEN)

d.legend(1100, 1100, [
    (BLUE, "solid", "Traffic flow - stays inside the cluster"),
    (GREEN, "dashed", "Control flow - outbound only, config and key sync"),
    (PURPLE, "solid", "Redis: keys, sessions, tokens, quota counters"),
    (TEAL, "dotted", "Analytics - the Hybrid Pump sends aggregates to Cloud"),
], w=2100)

d.note(80, 1500, 3120, 300, "What this buys",
       "API traffic, request bodies and Redis state stay in your cluster - aggregated analytics\n"
       "and configuration sync cross the boundary, over outbound connections the data plane opens.",
       tone="good")

d.save(OUT)
