#!/usr/bin/env python3
"""Turn a sizing result into the structured content of the sizing report.

The content JSON (title block, executive summary, sections of typed blocks) is rendered
to Markdown by sizing_markdown.py. When a tyk-docx-theme skill is installed, the same JSON
follows that skill's content specification, so its renderer also produces a DOCX. Every
figure comes from the result dict; this module only arranges and explains it.

Reader-facing rule: nothing here may name tools, repositories or price sources that the
reader cannot check. Cite public Tyk documentation only.

Usage:
    python3 sizing_docx_content.py result.json content.json [--diagram diagram.png]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import sizing_data as D
from sizing_engine import fmt, gb_text

DOCS = {
    "planning": ("Planning for production", "https://tyk.io/docs/planning-for-production"),
    "redis": ("Redis in Tyk: memory, sizing and compatible engines", "https://tyk.io/docs/api-management/in-memory-database"),
    "mdcb": ("Multi Data Centre Bridge (MDCB)", "https://tyk.io/docs/api-management/mdcb"),
    "pump": ("Tyk Pump", "https://tyk.io/docs/api-management/tyk-pump"),
    "analytics_storage": ("Analytics storage management", "https://tyk.io/docs/api-management/dashboard-analytics/analytics-storage-management"),
    "dp_pump": ("Data plane Pump for distributed analytics", "https://tyk.io/docs/api-management/dashboard-analytics/data-plane-pump"),
    "database": ("Database management", "https://tyk.io/docs/planning-for-production/database-settings"),
    "install_methods": ("Self-managed installation methods", "https://tyk.io/docs/tyk-self-managed/install/methods"),
    "install_k8s": ("Install on Kubernetes", "https://tyk.io/docs/tyk-self-managed/install/kubernetes"),
    "install_linux": ("Install on Linux", "https://tyk.io/docs/tyk-self-managed/install/linux"),
    "operator": ("Tyk Operator", "https://tyk.io/docs/api-management/automations/operator"),
    "sync": ("Tyk Sync", "https://tyk.io/docs/product-stack/tyk-sync/installing-tyk-sync"),
    "persistent_db": ("Persistent database sizing", "https://tyk.io/docs/api-management/persistent-database"),
    "mongodb": ("Configure MongoDB (sizing)", "https://tyk.io/docs/tyk-configuration-reference/mongodb"),
    "gateway_config": ("Tyk Gateway configuration options", "https://tyk.io/docs/tyk-oss-gateway/configuration"),
    "benchmarks": ("Tyk performance benchmarks", "https://tyk.io/performance-benchmarks/"),
}

TARGET_NAMES = {"kubernetes": "Kubernetes", "ecs": "Amazon ECS", "fargate": "AWS Fargate", "vms": "Virtual machines"}
AUTH_NAMES = {
    "keyless": "keyless (no authentication)",
    "api-key-auth": "API key authentication",
    "auth-and-quota": "authentication with quotas and rate limits",
    "full-middleware": "full middleware (authentication, quotas, rate limits and analytics)",
}


LANDSCAPE_MAX_W, LANDSCAPE_MAX_H = 10.0, 5.4  # inches inside the theme's A4 landscape margins, leaving room for the caption


def png_size(path: str) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def figure_width(path: str) -> float:
    size = png_size(path)
    if not size or not size[1]:
        return LANDSCAPE_MAX_W
    return round(min(LANDSCAPE_MAX_W, LANDSCAPE_MAX_H * size[0] / size[1]), 2)


def money(value: float) -> str:
    return f"-${fmt(-value)}" if value < 0 else f"${fmt(value)}"


def pct(value: float) -> str:
    return f"{value * 100:.0f}%" if value * 100 >= 1 else f"{value * 100:.1f}%"


def rps(value: float) -> str:
    return f"{fmt(value)} RPS"


def para(text: str) -> dict:
    return {"type": "paragraph", "text": text}


def bullets(items: list[str]) -> dict:
    return {"type": "bullets", "items": [i for i in items if i]}


def table(headers: list[str], rows: list[list[Any]], widths: list[float] | None = None) -> dict:
    block = {"type": "table", "headers": headers, "rows": [[str(c) for c in r] for r in rows]}
    if widths:
        block["column_widths"] = widths
    return block


def callout(kind: str, title: str, text: str) -> dict:
    return {"type": "callout", "kind": kind, "title": title, "text": text}


def h2(text: str) -> dict:
    return {"type": "heading", "level": 2, "text": text}


def plane_place(p: dict) -> str:
    return f"{p['cloud_name']}, {TARGET_NAMES[p['target']]}"


def prod_envs(result: dict) -> list[dict]:
    """Production environments, those with their own control plane first, so [0] is always
    an environment the document can describe end to end."""
    envs = [e for e in result["environments"] if e["posture"] == "production"] or result["environments"][:1]
    return sorted(envs, key=lambda e: e["control_plane"]["mode"] == "shared")


def nonprod_envs(result: dict) -> list[dict]:
    return [e for e in result["environments"] if e["posture"] != "production" and e not in prod_envs(result)]


# ----------------------------------------------------------------------------------------
# Sections
# ----------------------------------------------------------------------------------------


def executive_summary(result: dict) -> list[dict]:
    blocks: list[dict] = []
    prod = prod_envs(result)[0]
    planes = prod["planes"]
    t = prod["traffic"]
    gw = sum(p["gateways"]["count"] for p in planes)
    vcpu = sum(p["gateways"]["count"] * p["gateways"]["vcpu"] for p in planes)
    n1_all = all(p["gateways"]["n1_ok"] for p in planes)
    shape = (f"{len(planes)} data planes managed from one control plane through MDCB" if prod["multi_dc"]
             else "a single data plane with the control plane alongside it")
    summary = [
        f"{prod['name']} is sized for {rps(t['peak_rps'])} at peak and {rps(t['avg_rps'])} on average, as {shape}.",
        f"It needs {gw} gateway instances ({vcpu} vCPU in total), planned at 40% of Tyk's published benchmark throughput so each plane keeps headroom for bursts and failures.",
        (f"Every data plane in {prod['name']} still carries its peak with one gateway lost, counting each gateway at its planning rate." if n1_all
         else f"At least one data plane in {prod['name']} cannot carry its peak with one gateway lost; the resilience section shows the fix."),
        f"Redis needs {', '.join(sorted({gb_text(p['redis']['gb']) for p in planes}, key=lambda s: float(s.split()[0].replace(',', ''))))} per data plane, built up from keys, cache, the analytics outage buffer and configuration backups."
        + (" At least one plane exceeds the largest single Redis node and needs cluster mode." if any(p["redis"]["shards"] > 1 for p in planes) else ""),
        f"The analytics store needs about {fmt(prod['control_plane']['db']['storage_gb'])} GB at the stated retention.",
    ]
    if result["fully_priced"] or any(e["total_monthly"] for e in result["environments"]):
        parts = [f"{e['name']} {money(e['total_monthly'])}" for e in result["environments"] if e["total_monthly"]]
        unpriced = sorted({i for e in result["environments"] for i in e["unpriced_items"]})
        cost = f"Cloud infrastructure at list prices comes to about {money(result['estate_total_monthly'])} per month ({'; '.join(parts)})"
        cost += f", excluding self-hosted infrastructure ({', '.join(unpriced)})." if unpriced else "."
        summary.append(cost)
    blocks.append(bullets(summary))
    top = [n for e in prod_envs(result) for n in e["notes"] if n["tone"] in ("risk", "recommendation")][:3]
    for note in top:
        blocks.append(callout(note["tone"], "Risk" if note["tone"] == "risk" else "Recommendation", note["text"]))
    return blocks


def scope_section(result: dict) -> dict:
    t = result["traffic"]
    prod = prod_envs(result)[0]
    w = prod["workload"]  # the inputs the primary environment was actually sized with
    ctx = result.get("context") or {}
    defaulted = set(prod.get("defaulted", result["defaulted"]))
    basis = lambda key: "Assumed" if key in defaulted else "Supplied"
    blocks: list[dict] = []
    if ctx.get("summary"):
        blocks.append(para(ctx["summary"]))
    if ctx.get("drivers"):
        blocks.append(para("The sizing reflects these requirements:"))
        blocks.append(bullets(ctx["drivers"]))
    blocks.append(h2("Traffic"))
    rows = []
    if t.get("volume_source"):
        rows.append(["Request volume", f"{fmt(t['monthly_requests'])} per month", "Supplied"])
    rows += [
        ["Average load", rps(t["avg_rps"]), {"supplied": "Supplied", "volume": "Derived", "derived": "Derived", "assumed": "Assumed"}[t["avg_source"]]],
        ["Peak design target", rps(t["peak_rps"]), "Supplied" if t["peak_source"] == "supplied" else ("Assumed" if "peak_multiplier" in defaulted else "Derived")],
        ["Peak-to-average ratio", f"{t['peak_rps'] / t['avg_rps']:.1f}x" if t["avg_rps"] else "n/a", basis("peak_multiplier")],
        ["Average request / response", f"{w['avg_request_kb']:g} KB / {w['avg_response_kb']:g} KB", basis("avg_response_kb")],
        ["Upstream response time", f"{w['upstream_latency_ms']:g} ms", basis("upstream_latency_ms")],
    ]
    blocks.append(table(["Metric", "Value", "Basis"], rows, [2.4, 2.6, 1.6]))
    if t["derivation"]:
        blocks.append(para("How the figures were derived:"))
        blocks.append(bullets(t["derivation"]))
    blocks.append(h2("Platform footprint"))
    rows = [
        ["Gateway features on the hot path", AUTH_NAMES[w["auth_profile"]], basis("auth_profile")],
        ["Active API keys and tokens", fmt(w["active_api_keys"]), basis("active_api_keys")],
        ["API definitions / policies", f"{fmt(w['api_definitions'])} / {fmt(w['policies'])}", basis("api_definitions")],
        ["Analytics", ("Enabled" + (", detailed (full bodies)" if w["detailed_analytics"] else ", standard records")) if w["analytics"] == "enabled" else "Disabled", basis("analytics")],
        ["Queryable request logs / aggregates", f"{w['request_log_retention_days']} days / {w['aggregate_retention_days']} days", basis("request_log_retention_days")],
        ["Analytics outage tolerance", f"{w['analytics_buffer_seconds']:,} seconds", basis("analytics_buffer_seconds")],
        ["Response caching", "On" if w["response_caching"] else "Off", basis("response_caching")],
        ["Developer Portal", {"none": "Not included", "internal": "Internal", "partner-facing": "Partner-facing (HA)"}[w["developer_portal"]], basis("developer_portal")],
        ["Analytics store engine", "PostgreSQL" if w["storage_engine"] == "postgresql" else "MongoDB", basis("storage_engine")],
        ["Egress to the public internet", pct(w["internet_egress_share"]), basis("internet_egress_share")],
    ]
    blocks.append(table(["Input", "Value", "Basis"], rows, [2.6, 2.6, 1.4]))
    blocks.append(para("\"Assumed\" values are defaults to confirm. The open questions section lists what to check and why each one matters."))
    return {"title": "Scope and inputs", "blocks": blocks}


def architecture_section(result: dict, diagram: str | None) -> dict:
    blocks: list[dict] = []
    prod = prod_envs(result)[0]
    cp = prod["control_plane"]
    if prod["multi_dc"]:
        blocks.append(para(
            f"The control plane runs once, in {cp['location_label']} ({cp['cloud_name']}). It hosts {', '.join(cp['components'])}, "
            f"the analytics store and a persistent control-plane Redis. Each of the {len(prod['planes'])} data planes runs its own gateways, "
            "Redis and Pump, and serves traffic on its own."))
        blocks.append(bullets([
            "Data-plane gateways make outbound RPC connections to MDCB (port 9091 by default for self-managed MDCB) to receive configuration and keys. Enable TLS on those connections as a deployment requirement. The control plane needs no inbound connection into a data plane; API consumer traffic still enters each data plane.",
            "If the link to the control plane drops, each data plane keeps serving traffic from its local Redis and configuration backups, rejecting only keys created elsewhere that it has not yet received. When the link returns, MDCB refreshes the plane with the changes it missed.",
            "Quota and rate-limit counters are kept per data plane and never shared between planes, so route each client to one plane with geographic or site affinity.",
            "Data-plane Redis is recoverable: sessions re-sync from the control plane and definition backups rebuild on the next reload. Control-plane Redis is the source of truth and needs persistence and high availability.",
        ]))
    elif cp["mdcb"] and prod.get("guests"):
        guests = ", ".join(prod["guests"])
        blocks.append(para(
            f"{prod['name']} is one install: its gateways and the Dashboard share one persistent Redis, which holds keys, OAuth clients and sessions. "
            f"The control plane also runs MDCB, so the {guests} data plane{'s' if len(prod['guests']) > 1 else ''} connect to it instead of running a control plane of their own. "
            "Their analytics land in this analytics store, which is sized to include them."))
    else:
        blocks.append(para(
            f"A single data plane needs no MDCB. The Dashboard and gateways share one persistent Redis, which holds keys, OAuth clients and sessions. "
            "Adding data planes later is additive: introduce MDCB and a local Redis per new plane, without re-architecting the first."))
    rows = []
    for env in result["environments"]:
        e_cp = env["control_plane"]
        cp_text = f"Shared with {e_cp['shared_with']}" if e_cp["mode"] == "shared" else f"{e_cp['location_label']} ({e_cp['cloud_name']})"
        rows.append([env["name"], env["posture"].replace("-", " ").capitalize(), "HA" if env["resilience"] == "ha" else "Single node",
                     str(len(env["planes"])), cp_text, rps(env["traffic"]["peak_rps"])])
    blocks.append(h2("Environments"))
    blocks.append(table(["Environment", "Posture", "Resilience", "Data planes", "Control plane", "Peak"], rows, [1.4, 1.1, 1.0, 1.0, 1.5, 0.9]))
    if diagram:
        # A multi-plane topology is genuinely wide: give it its own landscape page.
        blocks.append({"type": "section", "orientation": "landscape"})
        blocks.append({"type": "image", "path": diagram, "width_inches": figure_width(diagram),
                       "caption": f"Figure 1. {prod['name']} topology", "alt_text": f"{prod['name']} Tyk deployment topology"})
        blocks.append({"type": "section", "orientation": "portrait"})
    return {"title": "Recommended architecture", "blocks": blocks}


def plane_rows(env: dict) -> list[list[str]]:
    rows = []
    for p in env["planes"]:
        g, r = p["gateways"], p["redis"]
        if p["target"] in ("kubernetes", "ecs", "fargate"):
            scaling = f"Autoscale {g['hpa']['min']} to {g['hpa']['max']} at {g['hpa']['target_cpu']}% CPU"
        elif env["resilience"] == "ha":
            scaling = f"N+1 behind the load balancer"
        else:
            scaling = "Single instance"
        gateways = f"{g['count']} x {g['shape']}"
        pump = f"{p['pump']['instances']} x 2 vCPU / 4 GB" if p["pump"] else "None"
        redis = f"{gb_text(r['gb'])} needed: {r['tier']}"
        rows.append([p["label"] if env["multi_dc"] else "Data plane", gateways, scaling, pump, redis])
    return rows


def production_section(env: dict) -> dict:
    blocks: list[dict] = []
    planes = env["planes"]
    if env["multi_dc"]:
        blocks.append(h2("Traffic by data plane"))
        rows = [[p["label"], plane_place(p), pct(p["share"]), rps(p["avg_rps"]), rps(p["peak_rps"])] for p in planes]
        blocks.append(table(["Data plane", "Hosting", "Share", "Average", "Peak"], rows, [1.4, 2.0, 0.8, 1.3, 1.3]))
    blocks.append(h2("Data plane sizing"))
    blocks.append(table(["Data plane", "Gateways", "Scaling", "Pump", "Redis / Valkey"], plane_rows(env), [1.1, 1.9, 1.4, 1.1, 1.5]))
    blocks.append(bullets([
        "Gateway counts include one spare instance per plane (N+1) so a failure or rolling upgrade never removes peak capacity." if env["resilience"] == "ha" else "",
        (f"Pump is sized by analytics records per second, not by gateway count, at a planning rate of {fmt(D.PUMP_RECORDS_PER_S_PER_INSTANCE)} records per second per instance. "
         + ("It keeps up with the full peak, because the peak duration was not supplied." if any(p["pump"] and p["pump"]["basis"] == "peak" for p in planes)
            else "The backlog a peak builds must fit the Redis outage buffer and drain afterwards.")) if any(p["pump"] for p in planes) else "",
        "Data-plane Redis is a recoverable cache and is replicated for failover." if env["multi_dc"] else "The single Redis is persistent: it holds keys, OAuth clients and sessions.",
        ("Kubernetes gateway pods request " + "; ".join(sorted({f"{p['gateways']['vcpu']} vCPU / {p['gateways']['ram_gb']} GB" for p in planes if p["target"] == "kubernetes"}))
         + " each, with autoscaling on CPU; the node shapes above are what the cost assumes.") if any(p["target"] == "kubernetes" for p in planes) else "",
        "On Kubernetes, Pump runs in the gateway node pool inside the planning headroom and is not priced separately. On ECS, Fargate and virtual machines, each Pump instance is its own 2 vCPU / 4 GB task or VM and is priced." if any(p["pump"] for p in planes) else "",
    ]))
    cp = env["control_plane"]
    blocks.append(h2("Control plane"))
    rows = [["Control-plane nodes", str(cp["nodes"]), cp["node_shape"], ", ".join(c for c in cp["components"] if c != "Tyk Developer Portal")]]
    if cp["portal"]["mode"] != "none":
        rows.append(["Developer Portal", "", cp["portal"]["line"], "Public, variable traffic: autoscale it and give it its own database connection pool." if cp["portal"]["mode"] == "partner-facing" else "Internal use."])
    if cp["mdcb"]:
        rows.append(["Control-plane Redis", "HA" if env["resilience"] == "ha" else "1", f"{gb_text(cp['cp_redis_gb'])}" + (f": {cp['cp_redis_tier']}" if cp.get("cp_redis_tier") else ""),
                     "Persistent source of truth: keys, OAuth clients, sessions. Enable persistence and Sentinel, Cluster or managed replication."])
    rows.append(["Analytics store", "HA pair" if env["resilience"] == "ha" else "1", cp["db"]["line"],
                 f"{fmt(cp['db']['storage_gb'])} GB at 70% utilisation." + ("" if cp["db"]["feasible"] else " Not priced: no listed tier fits.")])
    blocks.append(table(["Component", "Instances", "Size", "Notes"], rows, [1.4, 1.1, 2.2, 2.2]))
    blocks.append(h2("Resilience check"))
    rows = []
    for p in planes:
        g = p["gateways"]
        if g["count"] == 1:
            verdict = "Fails: single instance"
        else:
            verdict = "Holds" if g["n1_ok"] else "Fails: add a gateway"
        rows.append([p["label"], str(g["count"]), rps(g["n1_capacity_rps"]), rps(p["peak_rps"]), verdict])
    blocks.append(table(["Data plane", "Gateways", "Capacity with one lost", "Peak", "Result"], rows, [1.3, 1.1, 1.7, 1.2, 1.6]))
    blocks.append(para("Capacity counts each gateway at 40% of its benchmarked throughput, so \"holds\" means the plane stays inside planning headroom, not merely alive. "
                       "The check is arithmetic: it does not show failover behaviour, Redis or database resilience, availability-zone placement or how fast autoscaling reacts. The validation plan tests those."))
    for note in env["notes"]:
        if note["topic"] in ("gateways", "topology"):
            blocks.append(callout(note["tone"], note["tone"].capitalize(), note["text"]))
    return {"title": f"{env['name']} sizing", "blocks": blocks}


def redis_section(env: dict) -> dict:
    blocks: list[dict] = []
    w = env["workload"]
    blocks.append(para(
        "Redis holds keys and sessions, quota counters (and rate-limit counters when the Redis rate limiter is in use), the response cache, the analytics buffer awaiting Pump and, "
        "in multi-plane estates, a local backup of API definitions and policies. Each term below is computed from the inputs; the steady terms are "
        "planned at 70% utilisation and the analytics buffer is taken at face value, since it is already a worst case. KB and GB are binary (KiB, GiB)."))
    rows = []
    for p in env["planes"]:
        parts = p["redis"]["parts"]
        rows.append([p["label"], gb_text(parts["keys"]), gb_text(parts["cache"]) if w["response_caching"] else "off",
                     gb_text(parts["analytics_buffer"]) if w["analytics"] == "enabled" else "off",
                     gb_text(parts["emergency_config"]) if env["multi_dc"] else "n/a", gb_text(p["redis"]["gb"])])
    blocks.append(table(["Data plane", "Keys", "Cache", "Analytics buffer", "Config backup", "Planned"], rows, [1.4, 1.0, 1.0, 1.3, 1.2, 1.1]))
    first = env["planes"][0]
    blocks.append(bullets([
        f"Keys: {fmt(w['active_api_keys'])} active keys at about 1 KB each.",
        f"Cache: about 10% of average traffic cached for 60 seconds at {w['avg_response_kb']:g} KB per response." if w["response_caching"] else "",
        (f"Analytics buffer: {w['analytics_buffer_seconds']:,} seconds of peak-rate records at {first['redis']['record_kb']:.3g} KB each"
         + (" (Tyk's documented size for a basic record)" if not w["detailed_analytics"] else " (a basic record plus the base64-encoded request and response, headers included)")
         + ". A Pump or database outage longer than this drops analytics.") if w["analytics"] == "enabled" else "",
        (f"Gateways delete unprocessed analytics {D.GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S} seconds after the first one is written unless analytics_config.storage_expiration_time is raised, "
         f"so set it to at least {w['analytics_buffer_seconds']:,} seconds for this buffer to hold.") if w["analytics"] == "enabled" and w["analytics_buffer_seconds"] > D.GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S else "",
        f"Configuration backup: {fmt(w['api_definitions'])} API definitions at about 50 KB and {fmt(w['policies'])} policies at about 2 KB." if env["multi_dc"] else "",
    ]))
    sens = [p for p in env["planes"] if p["redis"]["sensitivity"]["30s"] is not None]
    if sens:
        blocks.append(h2("Outage tolerance is a cost decision"))
        blocks.append(para("Redis tiers step, so choose the analytics outage tolerance deliberately. Monthly Redis cost per data plane:"))
        rows = [[p["label"], money(p["redis"]["sensitivity"]["30s"]), money(p["redis"]["sensitivity"]["5min"]), money(p["redis"]["sensitivity"]["1h"])] for p in sens]
        blocks.append(table(["Data plane", "30 seconds", "5 minutes", "1 hour"], rows, [2.0, 1.6, 1.6, 1.6]))
    extra = []
    if env["multi_dc"]:
        extra.append("Compress the API definition and policy backups held in data-plane Redis (storage.compress_api_definitions from Tyk 5.12, storage.compress_policies from 5.13). Compression typically saves about 75%.")
    if any(p["redis"]["outage_class"] for p in env["planes"]) or w["analytics_buffer_seconds"] >= 300:
        extra.append("Move the analytics buffer to its own Redis (enable_separate_analytics_store) so a backlog never competes with rate limiting and sessions for memory.")
    if w["response_caching"]:
        extra.append("Large or high-hit-rate caches can move to a separate cache Redis (enable_separate_cache_store).")
    tier_notes = sorted({p["redis"]["tier_note"] for p in env["planes"] if p["redis"].get("tier_note")})
    extra.extend(tier_notes)
    extra.append("Valkey is supported as a Redis-compatible engine for new environments. There is no migration path from an existing Redis deployment to Valkey.")
    extra.append("Keep each plane's Redis in the same network, ideally the same availability zone, as its gateways. Quota checks, the Redis rate limiter and analytics writes reach Redis while traffic flows, so cross-zone Redis adds latency.")
    blocks.append(h2("Recommendations"))
    blocks.append(bullets(extra))
    for note in env["notes"]:
        if note["topic"] == "redis":
            blocks.append(callout(note["tone"], note["tone"].capitalize(), note["text"]))
    return {"title": "Redis sizing", "blocks": blocks}


def analytics_section(env: dict) -> dict:
    w = env["workload"]
    db = env["control_plane"]["db"]
    blocks: list[dict] = []
    if w["analytics"] != "enabled":
        blocks.append(para("Analytics is disabled in this configuration, so there is no Pump and the store only holds Dashboard configuration."))
        blocks.append(callout("decision", "Decision", "Dashboard activity reports and the log browser are unavailable without analytics. Gateway access logs or OpenTelemetry traces can feed an external observability platform instead."))
        return {"title": "Analytics store sizing", "blocks": blocks}
    rows = []
    if db["raw_share"]:
        rows.append(["Per-request logs", f"{gb_text(db['nonagg_per_day_gb'])} per day x {w['request_log_retention_days']} days", gb_text(db["nonagg_gb"])])
    else:
        rows.append(["Per-request logs", "Not forwarded: data-plane Pumps send aggregates only", "0 GB"])
    if db["agg_gb"]:
        rows.append(["Hourly aggregates", f"{fmt(db['active_items'])} active items x 1 KB x 24 x {w['aggregate_retention_days']} days", gb_text(db["agg_gb"])])
    rows.append(["Configuration", "API definitions and policies", gb_text(db["config_gb"])])
    rows.append(["Planned storage", "At 70% utilisation", f"{fmt(db['storage_gb'])} GB"])
    blocks.append(table(["Term", "Basis", "Size"], rows, [1.8, 3.6, 1.4]))
    if w["storage_engine"] == "mongodb":
        blocks.append(para(f"MongoDB wants about {db['ram_gb']} GB of RAM for indexes (about 160 MB per million request documents) and working set."))
    blocks.append(para(f"Selected store: {db['line']}."))
    pumps = [p for p in env["planes"] if p["pump"]]
    if pumps:
        rows = [[p["label"], rps(p["pump"]["records_per_s"]), rps(p["pump"]["peak_records_per_s"]), str(p["pump"]["instances"]),
                 rps(p["pump"]["capacity_records_per_s"]),
                 "Keeps up with the peak" if p["pump"]["basis"] == "peak" else (f"{fmt(p['pump']['backlog_records'])} records, drains in about {fmt(p['pump']['drain_seconds'])} s" if p["pump"]["backlog_records"] else "No backlog")]
                for p in pumps]
        blocks.append(h2("Pump"))
        blocks.append(table(["Data plane", "Average", "Peak", "Instances", "Capacity", "Peak backlog"], rows, [1.3, 1.0, 1.0, 0.9, 1.0, 1.7]))
    if env["multi_dc"] and w["multi_dc_analytics"] == "hybrid-aggregate":
        blocks.append(para("Data-plane Pumps summarise traffic before sending it to the control plane, so individual request logs are not available in the Dashboard log browser. Tyk's data-plane Helm chart uses this mode by default. Individual logs can still go to a sink configured on each data-plane Pump, which is not sized here."))
    if env["multi_dc"]:
        blocks.append(h2("Where analytics are processed"))
        modes = [
            ("control-plane", "Every record crosses to the control plane", "Dashboard reports and the log browser", "Highest"),
            ("hybrid-aggregate", "Aggregates only; individual logs are not forwarded", "Traffic Analytics aggregates; no individual logs in the log browser, no GraphQL Activity by Graph, MCP needs aggregation enabled", "Minimal"),
            ("edge-local", "Nothing crosses", "No central Dashboard reporting; consolidate locally", "None"),
        ]
        rows = [[("Selected: " if m == w["multi_dc_analytics"] else "") + m.replace("-", " "), a, b, c] for m, a, b, c in modes]
        blocks.append(table(["Mode", "What crosses planes", "Dashboard reporting", "Transfer"], rows, [1.6, 2.1, 2.2, 1.0]))
    blocks.append(bullets([
        "Cap retention with TTL indexes or scheduled pruning, and archive old raw logs to object storage.",
        "Keep Pump in the same region as its Redis, and the analytics store in the control-plane region. Pump writes in batches, so every millisecond of round trip costs sink throughput.",
    ]))
    for note in env["notes"]:
        if note["topic"] == "analytics":
            blocks.append(callout(note["tone"], note["tone"].capitalize(), note["text"]))
    return {"title": "Analytics store sizing", "blocks": blocks}


def nonprod_section(result: dict) -> dict | None:
    envs = nonprod_envs(result)
    if not envs:
        return None
    blocks = [para("Non-production environments are sized for functional and integration testing, not production throughput. They use smaller or burstable tiers where a failure only affects that environment.")]
    for env in envs:
        blocks.append(h2(env["name"]))
        if env.get("description"):
            blocks.append(para(env["description"]))
        rows = []
        for p in env["planes"]:
            g, r = p["gateways"], p["redis"]
            rows.append([p["label"], plane_place(p), f"{g['count']} x {g['shape']}", f"{r['nodes']} x {gb_text(r['gb'])}" if p["priced"] else r["tier"],
                         str(p["pump"]["instances"]) if p["pump"] else "none"])
        blocks.append(table(["Data plane", "Hosting", "Gateways", "Redis", "Pump"], rows, [1.2, 1.6, 2.0, 1.3, 0.8]))
        cp = env["control_plane"]
        blocks.append(para(f"Control plane: {'shared with ' + cp['shared_with'] if cp['mode'] == 'shared' else str(cp['nodes']) + ' x ' + cp['node_shape'] + '. Analytics store: ' + cp['db']['line']}."))
        blocks.append(para(f"Load assumed: {rps(env['traffic']['peak_rps'])} peak ({'; '.join(env['traffic'].get('derivation', []))})."))
    blocks.append(callout("note", "Note", "Never promote a non-production bill of materials to production. Performance-test environments that must prove production throughput need the production posture."))
    return {"title": "Non-production environments", "blocks": blocks}


def scenario_section(result: dict) -> dict | None:
    if not result["scenarios"]:
        return None
    blocks: list[dict] = []
    for s in result["scenarios"]:
        blocks.append(h2(s["name"]))
        if s["description"]:
            blocks.append(para(s["description"]))
        rows = []
        for c in s["comparison"]:
            fmt_v = money if c["unit"] == "USD" else (lambda v: f"{fmt(v)}")
            delta = c["delta"]
            rows.append([c["metric"], fmt_v(c["base"]), fmt_v(c["variant"]), ("+" if delta > 0 else "") + (money(delta) if c["unit"] == "USD" else f"{delta:,}")])
        blocks.append(table(["Metric", s["environment"], s["name"], "Change"], rows, [2.2, 1.6, 1.6, 1.4]))
    return {"title": "Options compared", "blocks": blocks}


def deployment_section(result: dict) -> dict:
    targets = sorted({p["target"] for e in result["environments"] for p in e["planes"]})
    rows = [
        ["Kubernetes (EKS, GKE, AKS, OKE, OpenShift)", "Official Helm charts", "Tyk Operator (APIs and policies as CRDs) or Tyk Sync", "Horizontal Pod Autoscaler plus cluster autoscaling", "Estates standardised on Kubernetes"],
        ["Amazon ECS or Fargate", "Container images", "Tyk Sync or the Dashboard API; Tyk Operator is Kubernetes-only", "Service auto scaling on CPU", "No cluster or node management"],
        ["Virtual machines", "RPM and DEB packages signed with Tyk's GPG key, Ansible, or containers under Docker or Podman", "Tyk Sync or the Dashboard API", "Add instances behind the load balancer", "On-premises, or before Kubernetes is available"],
    ]
    blocks = [
        para("The sizing holds on any of these targets; they differ in how Tyk is installed, how configuration is promoted and how capacity scales."),
        table(["Target", "Install", "Configuration as code", "Scaling", "Best fit"], rows, [1.5, 1.4, 1.6, 1.4, 1.1]),
        para(f"Selected in this estate: {', '.join(TARGET_NAMES[t] for t in targets)}."),
        para("Whichever target is used, Tyk OAS API definitions are OpenAPI documents with a Tyk extension, the format the Dashboard API, Tyk Sync and Tyk Operator all accept."),
    ]
    return {"title": "Deployment options", "blocks": blocks}


def network_section(env: dict) -> dict:
    rows = [[p["label"], rps(p["gateways"]["per_gateway_peak_rps"]), f"{env['workload']['upstream_latency_ms']:g} ms", fmt(p["gateways"]["conn_pool"])] for p in env["planes"]]
    blocks = [
        para("One upstream connection serves about 1000 / latency requests per second, so the gateway's idle connection pool must cover each gateway's share of peak. "
             "Tyk recommends max_idle_connections_per_host of 500; left unset it falls back to the Go default of 2. Undersized pools show up as lost throughput, not errors."),
        table(["Data plane", "Peak per gateway", "Upstream latency", "max_idle_connections_per_host"], rows, [1.6, 1.6, 1.6, 2.0]),
        bullets([
            "Keep Redis local to its gateways; quota checks, the Redis rate limiter and analytics writes reach it while traffic flows.",
            "Co-locate Pump with its plane's Redis and the analytics store with the control plane.",
            "The MDCB link is the one connection designed for wide-area latency; everything else should stay local." if env["multi_dc"] else "",
        ]),
    ]
    return {"title": "Network and latency", "blocks": blocks}


def state_section(result: dict) -> dict:
    envs = result["environments"]
    workers = any(e["multi_dc"] for e in envs)
    cp_redis = any(e["control_plane"]["cp_redis_gb"] for e in envs)
    shared_redis = any(not e["multi_dc"] for e in envs)
    mdcb = any(e["control_plane"]["mdcb"] for e in envs)
    portal = any(e["workload"]["developer_portal"] != "none" for e in envs)
    analytics = any(e["workload"]["analytics"] == "enabled" for e in envs)
    persistent = ["Persistent, source of truth", "Persistence plus Sentinel, Cluster or managed snapshots", "Required"]
    rows = [["Tyk Gateway", "Data plane", "Stateless", "Configuration; custom plugin bundles unless served from a bundle server", "Required"]]
    if analytics:
        rows.append(["Tyk Pump", "Data plane", "Stateless", "Configuration", "Required for Dashboard analytics"])
    if workers:
        rows.append(["Redis (data plane)", "Data plane", "Recoverable", "Not needed: sessions re-sync and definition backups rebuild. Replicate to avoid disruption", "Required"])
    if cp_redis:
        rows.append(["Redis (control plane)", "Control plane"] + persistent)
    if shared_redis:
        rows.append(["Redis (shared with the Dashboard)", "Single install"] + persistent)
    if mdcb:
        rows.append(["Tyk MDCB", "Control plane", "Stateless", "Configuration", "Required when data planes connect remotely"])
    rows.append(["Tyk Dashboard", "Control plane", "Stateless", "Configuration", "Required"])
    rows.append(["Analytics store", "Control plane", "Persistent", "Backups with point-in-time recovery", "Required"])
    if portal:
        rows.append(["Tyk Developer Portal", "Control plane", "Stateless with its own database", "Configuration, theme customisations, database", "Optional"])
    blocks = [
        table(["Component", "Plane", "State", "Backup", "Required"], rows, [1.3, 1.0, 1.2, 2.1, 1.3]),
        para("Stateless components scale horizontally and recover by redeploying from configuration. Deploy order for an initial capability: analytics store and Redis, then Dashboard (and MDCB), then gateways, then Pump and the Developer Portal."),
    ]
    return {"title": "Component state and backup", "blocks": blocks}


def cost_section(result: dict) -> dict | None:
    if not any(e["cost_rows"] for e in result["environments"]):
        return None
    blocks: list[dict] = []
    clouds = [D.CLOUDS[c] for c in result["clouds"] if c in D.CLOUDS]
    for env in result["environments"]:
        if not env["cost_rows"]:
            continue
        blocks.append(h2(env["name"]))
        rows = [[r["item"], r["shape"], money(r["monthly"])] for r in env["cost_rows"]]
        rows.append(["Total", "", money(env["total_monthly"])])
        blocks.append(table(["Item", "Shape", "Per month"], rows, [1.9, 3.9, 1.0]))
        if env["unpriced_items"]:
            blocks.append(para(f"Not priced: {', '.join(env['unpriced_items'])} run on your own infrastructure. Their bill of materials is in the appendix."))
    seen = set()
    for env in result["environments"]:
        for note in env["notes"]:
            if note["topic"] == "cost" and note["text"] not in seen:
                seen.add(note["text"])
                blocks.append(callout(note["tone"], note["tone"].capitalize(), note["text"]))
    blocks.append(h2("Data transfer"))
    transfer_rows = []
    for env in result["environments"]:
        tr = env["transfer"]
        transfer_rows.append([env["name"], f"{fmt(tr['monthly_requests'] / 1e6)} million", f"{fmt(tr['internet_gb'])} GB", f"{fmt(tr['private_gb'])} GB",
                              gb_text(tr["inter_dc_gb"]) if env["multi_dc"] else "n/a"])
    blocks.append(table(["Environment", "Requests per month", "Internet egress", "Private egress", "Between planes"], transfer_rows, [1.4, 1.5, 1.4, 1.3, 1.2]))
    blocks.append(para("Ingress is free. Response traffic dominates transfer cost, so private links, same-cloud consumers and a CDN for cacheable responses move this line the most."))
    blocks.append(h2("Price basis"))
    blocks.append(bullets([
        f"Approximate list, on-demand prices for the primary US region from a snapshot taken on {result['price_snapshot']}; {result.get('price_recheck', '')}. "
        "EU regions carry an approximate 12% infrastructure premium; egress is unadjusted. These are planning estimates, not quotes.",
        "Committed-use and reserved pricing lowers these list prices; check the terms with each provider.",
        "Excluded: load balancers and their data-processing charges, NAT gateways (keep them off the egress path), snapshots, observability storage and support.",
        "Tyk licensing is separate from infrastructure and is not included.",
        "Confirm figures in " + ", ".join(f"the {c['name']} calculator ({c['calc']})" for c in clouds) + "." if clouds else "",
    ]))
    return {"title": "Estimated monthly infrastructure cost", "blocks": blocks}


def assumptions_section(result: dict) -> dict:
    pw = prod_envs(result)[0]["workload"]
    rates = sorted({(p["cloud_name"], p["gateways"]["benchmark_rps_per_vcpu"], p["gateways"]["rate_source"], p["gateways"]["rate_basis"])
                    for e in result["environments"] for p in e["planes"]})
    published = [
        "Gateway throughput per vCPU comes from Tyk's published benchmarks for each cloud ("
        + "; ".join(f"{c}{' burstable nodes' if basis.startswith('2-vCPU') else ''}: {fmt(r)} RPS per vCPU"
                    + (" (borrowed from another machine's run)" if s == "assumed" else "") for c, r, s, basis in rates)
        + f"), for the {AUTH_NAMES[pw['auth_profile']]} profile.",
        "About 1 KB per key or session and about 2 KB per basic analytics record in Redis, as Tyk's Redis documentation states. Detailed records add the request and response, headers included, base64-encoded.",
        "MongoDB memory follows Tyk's MongoDB sizing guidance: the request-log index (about 30% of the log data) plus the aggregates, doubled.",
        f"Policies at about 2 KB each, and unprocessed analytics kept {D.GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S} seconds by default (analytics_config.storage_expiration_time).",
    ]
    planning = [
        (f"Gateway throughput is derated to {pw['throughput_factor']:.0%} of the benchmark for custom plugins or transformations on the hot path." if pw["throughput_factor"] < 1 else ""),
        "Gateways are planned at 40% of benchmarked throughput with one spare instance per plane; data stores at 70% utilisation.",
        "About 2 KB per stored request log (Tyk documents about 1 KB), 50 KB per API definition and 1 KB per active key or API per hourly aggregate, which is an upper bound: real aggregate size depends on how many keys, APIs and endpoints are active each hour.",
        f"Pump is planned at {fmt(D.PUMP_RECORDS_PER_S_PER_INSTANCE)} records per second per instance writing to PostgreSQL, a conservative planning rate; throughput depends on the sink and its hardware, so confirm it in the load test.",
        "Response caching assumes about 10% of average traffic is served from cache with a 60-second TTL.",
        "Gateway-to-upstream traffic is assumed to stay inside the private network; ingress is free.",
    ]
    if result["benchmark_assumed_for"]:
        planning.append(f"No published benchmark covers {', '.join(result['benchmark_assumed_for'])}; those planes assume AWS c5-class performance per vCPU. Load-test that hardware before committing.")
    limits = [
        "PostgreSQL and MongoDB compute (CPU, IOPS) is not modelled beyond MongoDB memory: the database shown is a starting shape to load-test at the Pump write rate.",
        "Pump throughput into MongoDB, or through the Hybrid Pump to MDCB, has no figure behind it.",
        "The N-1 check is arithmetic on the planning rate. It does not show failover, zone placement or autoscaling speed.",
        "Aggregate storage and transfer scale with active keys and APIs, not with how many are active in each hour, so they can be far above or below the real figure.",
        "Prices are approximations for planning: load balancers, NAT, snapshots, observability storage, support and Tyk licensing are excluded, and self-hosted or edge-local items are sized but not priced.",
    ]
    blocks = [h2("Published figures"), bullets(published), h2("Planning assumptions"), bullets([i for i in planning if i]),
              h2("What this sizing does not model"), bullets(limits)]
    if result["questions"]:
        blocks.append(h2("Open questions"))
        blocks.append(para("These inputs were assumed. Each answer can change the sizing."))
        blocks.append({"type": "numbered", "items": [q["question"] for q in result["questions"]]})
    return {"title": "Assumptions and open questions", "blocks": blocks}


def validation_section(result: dict) -> dict:
    prod = prod_envs(result)[0]
    rows = []
    for p in prod["planes"]:
        rows.append([p["label"], rps(p["peak_rps"]), rps(p["peak_rps"] * 1.5), str(p["gateways"]["count"])])
    blocks = [
        para("Validate the sizing with a load test on the target platform before committing capacity. Use representative API definitions with the production middleware and analytics settings."),
        table(["Data plane", "Design peak", "Test to (1.5x)", "Gateways"], rows, [2.0, 1.6, 1.6, 1.4]),
        bullets([
            "Sustain the design peak for at least 30 minutes; gateway CPU should stay below the 60% autoscaling threshold and error rates flat.",
            "Remove one gateway at peak and confirm the remaining gateways hold latency targets (the N-1 check).",
            ("Stop Pump for the chosen outage tolerance, with analytics_config.storage_expiration_time at least that long, and confirm Redis absorbs the backlog without evicting keys and Pump catches up afterwards.") if prod["workload"]["analytics"] == "enabled" else "",
            "Measure Pump throughput into the chosen analytics store at the peak record rate." if prod["workload"]["analytics"] == "enabled" else "",
            "Measure p50, p95 and p99 latency with and without the gateway to isolate gateway overhead from upstream time.",
            "Load-test any hardware without a published Tyk benchmark before relying on its rate.",
        ]),
    ]
    return {"title": "Validation plan", "blocks": blocks}


def appendix_bom(result: dict) -> dict | None:
    rows = []
    for env in result["environments"]:
        for p in env["planes"]:
            if p["priced"] and p["target"] != "vms":
                continue
            for item in p["bom"]:
                rows.append([env["name"], p["label"], item["component"], str(item["vcpu"]), str(item["ram_gb"]), fmt(item["disk_gb"]), str(item["qty"])])
        cp = env["control_plane"]
        if cp["bom"] and (not cp["priced"] or cp["target"] == "vms"):
            for item in cp["bom"]:
                rows.append([env["name"], "Control plane", item["component"], str(item["vcpu"]), str(item["ram_gb"]), fmt(item["disk_gb"]), str(item["qty"])])
    if not rows:
        return None
    return {"title": "Appendix: virtual machine bill of materials", "blocks": [
        para("For planes hosted on your own infrastructure or on virtual machines. Redis RAM allows twice the dataset for persistence forks; three Redis and database nodes allow quorum-based failover. PostgreSQL is a starting shape: size its CPU and IOPS in the load test."),
        table(["Environment", "Plane", "Component", "vCPU", "RAM (GB)", "Disk (GB)", "Qty"], rows, [1.3, 1.1, 1.7, 0.6, 0.8, 0.8, 0.6]),
    ]}


def references_section() -> dict:
    keys = ["planning", "redis", "persistent_db", "mongodb", "gateway_config", "mdcb", "pump", "dp_pump", "analytics_storage", "database", "install_methods", "install_k8s", "install_linux", "operator", "sync", "benchmarks"]
    return {"title": "References", "blocks": [{"type": "references", "items": [{"label": DOCS[k][0], "url": DOCS[k][1]} for k in keys]}]}


def build_content(result: dict, diagram: str | None = None, classification: str = "Confidential", version: str = "1.0") -> dict:
    sections = [scope_section(result), architecture_section(result, diagram)]
    for env in prod_envs(result):
        sections.append(production_section(env))
    first = prod_envs(result)[0]
    many = len(prod_envs(result)) > 1
    for env in prod_envs(result):
        for section in (redis_section(env), analytics_section(env)):
            if many:
                section["title"] = f"{section['title']}: {env['name']}"
            sections.append(section)
    for optional in (nonprod_section(result), scenario_section(result)):
        if optional:
            sections.append(optional)
    sections += [deployment_section(result), network_section(first), state_section(result)]
    cost = cost_section(result)
    if cost:
        sections.append(cost)
    sections += [assumptions_section(result), validation_section(result)]
    bom = appendix_bom(result)
    if bom:
        sections.append(bom)
    sections.append(references_section())
    title = result.get("title") or "Infrastructure Sizing"
    content = {
        "title": title,
        "document_type": "Infrastructure sizing",
        "description": "Recommended Tyk self-managed topology, component sizing, cost estimate and validation plan.",
        "author": result.get("prepared_by") or "Tyk",
        "date": result.get("date", ""),
        "version": version,
        "classification": classification,
        "subject": "Tyk infrastructure sizing",
        "keywords": ["Tyk", "sizing", "architecture"],
        "toc": True,
        "executive_summary": executive_summary(result),
        "sections": sections,
    }
    if result.get("organisation"):
        content["client"] = result["organisation"]
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("result")
    parser.add_argument("content")
    parser.add_argument("--diagram", help="PNG of the production topology to embed")
    parser.add_argument("--classification", default="Confidential")
    parser.add_argument("--version", default="1.0")
    args = parser.parse_args()
    result = json.loads(Path(args.result).read_text())
    content = build_content(result, args.diagram, args.classification, args.version)
    Path(args.content).write_text(json.dumps(content, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
