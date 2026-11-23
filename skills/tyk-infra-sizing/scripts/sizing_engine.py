#!/usr/bin/env python3
"""Tyk self-managed infrastructure sizing engine.

Takes a sizing brief (see references/brief-schema.md) and returns a plain-dict result that
the Markdown, Excalidraw and DOCX renderers consume. The method combines Tyk's published
figures (benchmarks, record and key sizes, MongoDB index guidance) and labelled planning
assumptions; sizing_data.SOURCES says which is which. It
covers:

* several environments (production and non-production) in one estate,
* data planes with uneven traffic shares, different clouds, regions and targets,
* a control plane beside one data plane, or remote from all of them (MDCB),
* on-premises or private-cloud planes (sized, never priced),
* an analytics-disabled mode and scenarios recomputed across the whole estate.

fixtures/regression_cases.json holds twelve compatibility cases that test_sizing_engine.py
replays; they record what the engine produced after a reviewed change, not an independent
oracle. The formula and boundary tests in the same file check the method itself.

The module has no third-party dependencies.
"""

from __future__ import annotations

import copy
import math
from datetime import date
from typing import Any

import sizing_data as D

# ----------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------


class BriefError(ValueError):
    """The brief is malformed or internally inconsistent."""


def js_round(value: float) -> int:
    """Round half up, as JavaScript's Math.round does for the values used here."""
    return int(math.floor(value + 0.5))


def ceil(value: float) -> int:
    """Math.ceil semantics on IEEE doubles, as in the regression cases."""
    return int(math.ceil(value))


def fmt(value: float) -> str:
    return f"{js_round(value):,}"


def gb_text(value: float) -> str:
    if value >= 100:
        return f"{fmt(value)} GB"
    return f"{max(0.1, js_round(value * 10) / 10):g} GB"


def egress_cost(gb_total: float, free_gb: float, tiers: list[tuple[float, float]]) -> float:
    remaining = max(0.0, gb_total - free_gb)
    cost = 0.0
    prev = 0.0
    for cap, rate in tiers:
        band = min(remaining, cap - prev)
        if band <= 0:
            break
        cost += band * rate
        remaining -= band
        prev = cap
    return cost


def aws_redis_node(need_gb: float, allow_burstable: bool = False) -> tuple[float, int, str]:
    ladder = (D.AWS_REDIS_DEV_NODES + D.AWS_REDIS_NODES) if allow_burstable else D.AWS_REDIS_NODES
    for gib, monthly, name in ladder:
        if gib * D.AWS_REDIS_USABLE >= need_gb:
            return gib, monthly, name
    return D.AWS_REDIS_NODES[-1]


def redis_plan(need_gb: float, cloud: str, prod: bool, nodes: int, region_factor: float) -> dict[str, Any]:
    """The one Redis topology and price for a requirement.

    Used for the planned Redis, the outage-tolerance sensitivity and the control-plane Redis,
    so the same memory always gets the same shards, node and price. Above
    REDIS_SINGLE_NODE_MAX_GB the requirement is split into equal shards.
    """
    shards = max(1, ceil(need_gb / D.REDIS_SINGLE_NODE_MAX_GB))
    plan: dict[str, Any] = {"gb": need_gb, "shards": shards, "nodes": nodes, "tier_name": "", "tier_gib": None, "cost": 0}
    if cloud == "aws":
        gib, monthly, name = aws_redis_node(need_gb / shards, not prod)
        plan.update(tier_name=name, tier_gib=gib, cost=js_round(monthly * nodes * shards * region_factor))
        plan["tier"] = f"{nodes} x {name}" if shards == 1 else f"{shards} shards x {nodes} x {name} (cluster mode)"
    elif cloud in D.CLOUDS:
        plan["cost"] = js_round(need_gb * D.CLOUDS[cloud]["redis_per_gb"] * (nodes / 2) * region_factor)
        plan["tier"] = f"{gb_text(need_gb)} provisioned" + (f", {nodes} nodes" if nodes > 2 else ", replicated" if nodes == 2 else "")
        if shards > 1:
            plan["tier"] += f", {shards} shards"
    else:
        plan["tier"] = ""
    plan["topology"] = (shards, nodes, plan["tier_name"])
    return plan


def self_hosted_ram(dataset_gb: float, minimum: int = 4) -> int:
    """VM RAM for a self-hosted Redis: dataset x2 for fork-on-persist headroom, stepped."""
    need = max(minimum, dataset_gb * 2)
    for step in (4, 8, 16, 32, 64, 128, 256, 512):
        if step >= need:
            return step
    return ceil(need)


def detail_kb(w: dict[str, Any]) -> float:
    """Extra KB a detailed analytics record carries: the raw request and response, headers
    included, base64-encoded (4 bytes for every 3)."""
    if not w["detailed_analytics"]:
        return 0.0
    return D.BASE64_FACTOR * (w["avg_request_kb"] + w["avg_response_kb"] + D.DETAILED_HEADERS_KB)


def analytics_record_kb(w: dict[str, Any]) -> float:
    """One analytics record buffered in Redis or sent between planes."""
    return D.ANALYTICS_BASE_KB + detail_kb(w)


# ----------------------------------------------------------------------------------------
# Brief normalisation
# ----------------------------------------------------------------------------------------

WORKLOAD_DEFAULTS: dict[str, Any] = {
    "auth_profile": "full-middleware",
    "response_caching": True,
    "detailed_analytics": False,
    "analytics": "enabled",
    "request_log_retention_days": 7,
    "aggregate_retention_days": 90,
    "active_api_keys": 10000,
    "api_definitions": 100,
    "policies": 200,
    "analytics_buffer_seconds": 30,
    "peak_duration_seconds": None,
    "developer_portal": "none",
    "storage_engine": "postgresql",
    "multi_dc_analytics": "hybrid-aggregate",
    "internet_egress_share": 1.0,
    "upstream_latency_ms": 50,
    "avg_request_kb": 2,
    "avg_response_kb": 8,
    "throughput_factor": 1.0,
}
# Inputs whose default is "not known": None stays None and changes how the engine sizes.
NULLABLE_KEYS = ("peak_duration_seconds",)

ENUMS = {
    "auth_profile": D.AUTH_PROFILES,
    "analytics": ("enabled", "disabled"),
    "developer_portal": ("none", "internal", "partner-facing"),
    "storage_engine": ("postgresql", "mongodb"),
    "multi_dc_analytics": ("control-plane", "hybrid-aggregate", "edge-local"),
    "posture": ("production", "non-production"),
    "resilience": ("ha", "single-node"),
}

RANGES = {
    "request_log_retention_days": (1, 90),
    "aggregate_retention_days": (7, 365),
    "analytics_buffer_seconds": (10, 86400),
    "peak_duration_seconds": (60, 86400),
    "internet_egress_share": (0, 1),
    "upstream_latency_ms": (1, 10000),
    "avg_request_kb": (0.1, 10240),
    "avg_response_kb": (0.1, 10240),
    "active_api_keys": (0, 10**9),
    "api_definitions": (1, 10**6),
    "policies": (0, 10**6),
    "throughput_factor": (0.05, 1),
}

# Every key a brief object may carry. Anything else is a typo or an unsupported input, and
# a typo that silently falls back to a default produces a plausible but wrong sizing.
TOP_KEYS = {"organisation", "title", "prepared_by", "date", "context", "traffic", "workload",
            "cloud", "region", "target", "environments", "scenarios"}
CONTEXT_KEYS = {"summary", "drivers"}
VOLUME_KEYS = ("annual_requests", "monthly_requests", "daily_requests")
TRAFFIC_KEYS = {"peak_rps", "avg_rps", *VOLUME_KEYS, "peak_multiplier"}
PAYLOAD_KEYS = ("avg_request_kb", "avg_response_kb", "upstream_latency_ms")
ENV_KEYS = {"name", "posture", "resilience", "description", "traffic", "workload", "cloud", "region",
            "target", "gateway_vcpu", "data_planes", "control_plane"}
PLANE_KEYS = {"name", "label", "cloud", "region", "target", "traffic_share", "hosts_control_plane",
              "gateway_vcpu", "min_gateways"}
CONTROL_PLANE_KEYS = {"mode", "shared_with", "cloud", "region", "target", "label"}
SCENARIO_KEYS = {"name", "environment", "overrides", "description"}
# Two figures for the same thing must agree this closely, or the brief contradicts itself.
TRAFFIC_AGREEMENT = 0.05

# Inputs whose default materially changes the answer, and the question to settle it. Every
# workload default has one (test_sizing_engine.py enforces it).
QUESTION_FOR_DEFAULT = {
    "peak_multiplier": "What is the real peak-to-average ratio? The peak sizes the gateways and the Redis analytics buffer.",
    "peak_duration_seconds": "How long does the peak last? Pump was sized to keep up with the full peak; with a known peak duration it can be sized for the backlog the Redis buffer absorbs.",
    "active_api_keys": "How many active API keys, tokens or sessions exist at once? Per-consumer tokens at retail scale make keys the largest Redis term.",
    "avg_response_kb": "What are typical response payload sizes? They drive egress cost, the response cache and detailed analytics.",
    "avg_request_kb": "What are typical request payload sizes? They matter when detailed analytics records full bodies.",
    "request_log_retention_days": "How many days of per-request logs must stay queryable in the Dashboard? This is usually the largest storage term.",
    "aggregate_retention_days": "How many days of aggregated analytics must the Dashboard keep? Aggregates grow with active keys and APIs times retention days.",
    "analytics_buffer_seconds": "How long a Pump or database outage must be survived without losing analytics? This is a direct Redis cost decision.",
    "internet_egress_share": "What share of response traffic leaves the cloud to the public internet, rather than private links or same-cloud consumers?",
    "upstream_latency_ms": "What is the typical upstream response time? It sets the gateway connection pool size.",
    "auth_profile": "Which gateway features run on the hot path: authentication only, quotas and rate limits, or full middleware?",
    "throughput_factor": "Do custom plugins, heavy transformations or JWT signing run on the hot path? The benchmark rate was used unchanged.",
    "traffic_shares": "How is traffic split between the data planes? An even split was assumed.",
    "storage_engine": "Is PostgreSQL acceptable for the analytics store, or is MongoDB a requirement?",
    "api_definitions": "Roughly how many API definitions and versions will the platform hold?",
    "policies": "Roughly how many security policies will the platform hold?",
    "response_caching": "Will gateway response caching be used, and on which APIs?",
    "developer_portal": "Is a Developer Portal needed, and is it internal or partner-facing? None was sized.",
    "analytics": "Is Dashboard analytics wanted, or does an external observability platform take over? Analytics was sized as enabled.",
    "multi_dc_analytics": "Must per-request logs from every data plane be browsable centrally in the Dashboard, or are aggregated reports enough? Central raw logs cost the most transfer and storage.",
    "detailed_analytics": "Must analytics record full request and response bodies? That multiplies analytics storage by the payload sizes.",
}


BOOL_KEYS = ("response_caching", "detailed_analytics")
FARGATE_VCPU = (1, 2, 4, 8, 16)


def _check_enum(key: str, value: Any, where: str) -> None:
    allowed = ENUMS.get(key)
    if allowed and value not in allowed:
        raise BriefError(f"{where}: {key}={value!r} is not one of {', '.join(allowed)}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_range(key: str, value: Any, where: str) -> None:
    bounds = RANGES.get(key)
    if bounds is None:
        return
    if not _is_number(value):
        raise BriefError(f"{where}: {key} must be a number, got {value!r}")
    low, high = bounds
    if not low <= value <= high:
        raise BriefError(f"{where}: {key}={value} is outside {low}..{high}")


INTEGER_KEYS = ("active_api_keys", "api_definitions", "policies")


def _check_workload_value(key: str, value: Any, where: str) -> None:
    if key in NULLABLE_KEYS and value is None:
        return
    if key in INTEGER_KEYS and _is_number(value) and value != int(value):
        raise BriefError(f"{where}: {key} must be a whole number, got {value!r}")
    if key in BOOL_KEYS and not isinstance(value, bool):
        raise BriefError(f"{where}: {key} must be true or false, got {value!r}")
    _check_enum(key, value, where)
    _check_range(key, value, where)


def _require_object(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise BriefError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _optional_object(container: dict, key: str, where: str) -> dict:
    """An object field that may be absent. Present but empty is fine; present with another
    type (a list, a string, false) is an error, not a silent default."""
    if key not in container or container[key] is None:
        return {}
    return _require_object(container[key], where)


def _reject_unknown(obj: dict, allowed: set[str], where: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise BriefError(f"{where}: unknown keys {', '.join(sorted(map(str, unknown)))} (allowed: {', '.join(sorted(allowed))})")


def _number(value: Any, where: str, low: float = 0, high: float = float("inf"), integer: bool = False) -> float:
    if not _is_number(value) or (integer and not isinstance(value, int)):
        raise BriefError(f"{where} must be {'an integer' if integer else 'a number'}, got {value!r}")
    if not low <= value <= high:
        raise BriefError(f"{where}={value} is outside {low:g}..{high:g}")
    return value


def _normalise_target(value: Any, where: str) -> str:
    if value is not None and not isinstance(value, str):
        raise BriefError(f"{where}: target must be a string, got {value!r}")
    target = (value or "kubernetes").lower()
    target = D.TARGET_ALIASES.get(target, target)
    if target not in D.DEPLOYMENT_TARGETS:
        raise BriefError(f"{where}: target {value!r} is not one of {', '.join(D.DEPLOYMENT_TARGETS)} (or an alias such as eks, podman)")
    return target


def _check_target_cloud(target: str, cloud: str, where: str) -> None:
    if target in D.AWS_ONLY_TARGETS and cloud != "aws":
        raise BriefError(f"{where}: {target} is an AWS target, but the cloud is {cloud}")


def _normalise_cloud(value: Any, where: str) -> str:
    if not value:
        raise BriefError(f"{where}: cloud is required (one of {', '.join(D.CLOUD_KEYS + D.SELF_HOSTED_CLOUDS)})")
    if not isinstance(value, str):
        raise BriefError(f"{where}: cloud must be a string, got {value!r}")
    cloud = value.lower()
    if cloud not in D.CLOUD_KEYS and cloud not in D.SELF_HOSTED_CLOUDS:
        raise BriefError(f"{where}: cloud {value!r} is not one of {', '.join(D.CLOUD_KEYS + D.SELF_HOSTED_CLOUDS)}")
    return cloud


def _normalise_region(value: Any, where: str) -> str:
    if value not in ("us", "eu"):
        raise BriefError(f"{where}: region must be us or eu (the price basis), got {value!r}")
    return value


def derive_traffic(traffic: dict[str, Any], defaulted: set[str], where: str = "traffic") -> dict[str, Any]:
    """Resolve peak and average RPS from whichever figures the brief supplies.

    Every supplied figure is validated, and figures that describe the same quantity must
    agree within TRAFFIC_AGREEMENT: an average RPS next to a monthly volume that implies
    something else is a contradiction to settle, not a choice for the engine to make.
    Records where each figure came from: avg_source is supplied, volume or assumed;
    peak_source is supplied or derived.
    """
    traffic = _require_object(traffic, where)
    multiplier = traffic.get("peak_multiplier")
    multiplier_defaulted = multiplier is None
    if multiplier_defaulted:
        multiplier = 3
    else:
        _number(multiplier, f"{where}.peak_multiplier", 1, 1000)
    seconds_month = D.DAYS_PER_MONTH * 86400
    period = {"annual_requests": 365 * 86400, "monthly_requests": seconds_month, "daily_requests": 86400}
    derivation = []
    implied: list[tuple[str, float]] = []
    avg = traffic.get("avg_rps")
    if avg is not None:
        _number(avg, f"{where}.avg_rps", 0, 1e8)
        if avg <= 0:
            raise BriefError(f"{where}.avg_rps must be positive")
        implied.append(("avg_rps", float(avg)))
    volume_source = None
    for key in VOLUME_KEYS:
        if traffic.get(key) is None:
            continue
        volume = _number(traffic[key], f"{where}.{key}", 1, 1e16)
        implied.append((key, volume / period[key]))
        if volume_source is None:
            volume_source = key
            if avg is None:
                derivation.append(f"{fmt(volume)} {key.split('_')[0]} requests / {fmt(period[key])} s = {fmt(volume / period[key])} average RPS")
    if len(implied) > 1:
        values = [v for _, v in implied]
        low, high = min(values), max(values)
        if high > 0 and (low <= 0 or high / low > 1 + TRAFFIC_AGREEMENT):
            listed = "; ".join(f"{k} implies {fmt(v)} average RPS" for k, v in implied)
            raise BriefError(f"{where}: the traffic figures disagree ({listed}). Keep one, or make them agree.")
    avg_source = None
    if avg is not None:
        avg_source = "supplied"
        volume_source = volume_source if len(implied) > 1 else None
    elif implied:
        avg = implied[0][1]
        avg_source = "volume"
    peak = traffic.get("peak_rps")
    peak_source = "supplied" if peak is not None else None
    if peak is not None:
        _number(peak, f"{where}.peak_rps", 0, 1e8)
    if peak is None and avg is None:
        raise BriefError(f"{where}: give peak_rps, avg_rps, or a request volume (annual_requests, monthly_requests, daily_requests)")
    if peak is not None and avg is not None and not multiplier_defaulted and avg > 0:
        if abs(peak / avg - multiplier) / multiplier > TRAFFIC_AGREEMENT:
            raise BriefError(f"{where}: peak_rps / average = {peak / avg:.2f}, but peak_multiplier is {multiplier:g}. Keep one, or make them agree.")
    if peak is None:
        peak = avg * multiplier
        peak_source = "derived"
        derivation.append(f"{fmt(avg)} average RPS x {multiplier:g} peak multiplier = {fmt(peak)} peak RPS")
        if multiplier_defaulted:
            defaulted.add("peak_multiplier")
    if avg is None and not multiplier_defaulted:
        avg = peak / multiplier
        avg_source = "derived"
        derivation.append(f"{fmt(peak)} peak RPS / {multiplier:g} peak multiplier = {fmt(avg)} average RPS")
    if avg is None:
        # No average: a third of peak (a 3x peak-to-average ratio), flagged as an assumption.
        avg = max(1, js_round(peak / 3))
        avg_source = "assumed"
        derivation.append(f"Average RPS not supplied: assumed peak / 3 = {fmt(avg)}")
        defaulted.add("peak_multiplier")
    if peak <= 0:
        raise BriefError(f"{where}: peak traffic must be positive")
    if avg > peak:
        raise BriefError(f"{where}: average RPS ({fmt(avg)}) cannot exceed peak RPS ({fmt(peak)})")
    return {
        "peak_rps": float(peak),
        "avg_rps": float(avg),
        "peak_multiplier": peak / avg if avg else None,
        "volume_source": volume_source,
        "avg_source": avg_source,
        "peak_source": peak_source,
        "monthly_requests": avg * seconds_month,
        "derivation": derivation,
    }


def _locate_control_plane(cp_in: dict, planes: list[dict], where: str) -> tuple[dict | None, bool]:
    """Find the plane the dedicated control plane sits beside, or decide it is remote.

    The control plane is beside a plane when that plane is flagged hosts_control_plane (or,
    with no flag, it is the first plane matching the cloud, region and label given, else the
    first plane) and the control plane's cloud, region and label do not say otherwise. A control plane placed in another cloud, region or site is remote:
    every data plane then reaches it through MDCB.
    """
    flagged = [p for p in planes if p["hosts_control_plane"]]
    if len(flagged) > 1:
        raise BriefError(f"{where}: only one data plane can host the control plane")
    if not flagged:
        # No flag: the first plane whose cloud, region and label match the placement given.
        def matches(p: dict) -> bool:
            return (("cloud" not in cp_in or str(cp_in["cloud"]).lower() == p["cloud"])
                    and ("region" not in cp_in or cp_in["region"] == p["region"])
                    and ("label" not in cp_in or str(cp_in["label"]) in (p["label"], p["name"])))
        candidate = next((p for p in planes if matches(p)), planes[0])
    else:
        candidate = flagged[0]
    differs = []
    if "cloud" in cp_in and _normalise_cloud(cp_in["cloud"], f"{where}.control_plane") != candidate["cloud"]:
        differs.append("cloud")
    if "region" in cp_in and _normalise_region(cp_in["region"], f"{where}.control_plane") != candidate["region"]:
        differs.append("region")
    if "label" in cp_in and str(cp_in["label"]) not in (candidate["label"], candidate["name"]):
        differs.append("label")
    if differs and flagged:
        raise BriefError(f"{where}: data plane {candidate['name']!r} hosts the control plane, but control_plane sets a different "
                         f"{' and '.join(differs)}. Drop hosts_control_plane to place the control plane remotely, or drop the override.")
    if differs:
        return None, True
    return candidate, False


def normalise_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Validate the brief and fill defaults, recording which inputs were defaulted."""
    if not isinstance(brief, dict):
        raise BriefError("the brief must be a JSON or YAML object")
    brief = copy.deepcopy(brief)
    _reject_unknown(brief, TOP_KEYS, "brief")
    estate_defaulted: set[str] = set()

    workload = dict(WORKLOAD_DEFAULTS)
    supplied = _optional_object(brief, "workload", "workload")
    _reject_unknown(supplied, set(WORKLOAD_DEFAULTS), "workload")
    for key in WORKLOAD_DEFAULTS:
        if key in supplied:
            workload[key] = supplied[key]
        else:
            estate_defaulted.add(key)
    if "traffic" not in brief:
        raise BriefError("traffic: give peak_rps, avg_rps, or a request volume (annual_requests, monthly_requests, daily_requests)")
    traffic_in = _require_object(brief["traffic"], "traffic")
    _reject_unknown(traffic_in, TRAFFIC_KEYS | set(PAYLOAD_KEYS), "traffic")
    for key in PAYLOAD_KEYS:
        # Accept payload and latency figures under traffic too; that is where people put them.
        if key in traffic_in:
            if key in supplied and supplied[key] != traffic_in[key]:
                raise BriefError(f"traffic.{key}={traffic_in[key]!r} and workload.{key}={supplied[key]!r} disagree; give it once")
            workload[key] = traffic_in[key]
            estate_defaulted.discard(key)
    for key, value in workload.items():
        _check_workload_value(key, value, "workload")
    traffic_defaulted: set[str] = set()
    traffic = derive_traffic({k: v for k, v in traffic_in.items() if k in TRAFFIC_KEYS}, traffic_defaulted)

    context = _optional_object(brief, "context", "context")
    _reject_unknown(context, CONTEXT_KEYS, "context")
    if "summary" in context and not isinstance(context["summary"], str):
        raise BriefError("context.summary must be a string")
    if "drivers" in context and not (isinstance(context["drivers"], list) and all(isinstance(d, str) for d in context["drivers"])):
        raise BriefError("context.drivers must be a list of strings")

    default_cloud = brief.get("cloud")
    default_region = brief.get("region", "us")
    default_target = brief.get("target", "kubernetes")

    if "environments" in brief and brief["environments"] is not None:
        envs_in = brief["environments"]
        if not isinstance(envs_in, list):
            raise BriefError("environments must be a list")
        if not envs_in:
            raise BriefError("environments is empty; leave it out for one production environment, or list at least one")
    else:
        envs_in = [{"name": "Production", "posture": "production"}]
    for index, env in enumerate(envs_in):
        _require_object(env, f"environments[{index}]")
        _reject_unknown(env, ENV_KEYS, f"environments[{index}]")

    names = set()
    environments = []
    has_production = any(e.get("posture", "production" if i == 0 else "non-production") == "production" for i, e in enumerate(envs_in))
    for index, env in enumerate(envs_in):
        where = f"environments[{index}]"
        name = env.get("name") or f"Environment {index + 1}"
        if not isinstance(name, str):
            raise BriefError(f"{where}: name must be a string")
        if name in names:
            raise BriefError(f"{where}: duplicate environment name {name!r}")
        names.add(name)
        posture = env.get("posture", "production" if index == 0 else "non-production")
        _check_enum("posture", posture, where)
        resilience = env.get("resilience", "ha" if posture == "production" else "single-node")
        _check_enum("resilience", resilience, where)
        overrides = _optional_object(env, "workload", f"{where}.workload")
        _reject_unknown(overrides, set(WORKLOAD_DEFAULTS), f"{where}.workload")
        env_workload = {**workload, **overrides}
        for key, value in overrides.items():
            _check_workload_value(key, value, f"{where}.workload")

        planes_in = env.get("data_planes", 1)
        if isinstance(planes_in, int) and not isinstance(planes_in, bool):
            if not 1 <= planes_in <= 50:
                raise BriefError(f"{where}: data_planes must be 1..50")
            planes_in = [{"name": f"DP{i + 1}" if planes_in > 1 else "Data plane"} for i in range(planes_in)]
        if not isinstance(planes_in, list) or not planes_in:
            raise BriefError(f"{where}: data_planes must be a count or a non-empty list")
        for p_index, plane in enumerate(planes_in):
            _require_object(plane, f"{where}.data_planes[{p_index}]")
            _reject_unknown(plane, PLANE_KEYS, f"{where}.data_planes[{p_index}]")
        shares = [p.get("traffic_share") for p in planes_in]
        for p_index, share in enumerate(shares):
            if share is not None:
                _number(share, f"{where}.data_planes[{p_index}].traffic_share", 0, 1)
        shares_defaulted = False
        if all(s is None for s in shares):
            shares = [1 / len(planes_in)] * len(planes_in)
            shares_defaulted = len(planes_in) > 1
        elif any(s is None for s in shares):
            given = sum(s for s in shares if s is not None)
            missing = sum(1 for s in shares if s is None)
            if given > 1 + 1e-6:
                raise BriefError(f"{where}: traffic_share values exceed 1")
            fill = (1 - given) / missing
            shares = [fill if s is None else s for s in shares]
        total_share = sum(shares)
        if abs(total_share - 1) > 0.01:
            raise BriefError(f"{where}: traffic_share values must sum to 1 (got {total_share:.3f})")
        shares = [s / total_share for s in shares]

        planes = []
        plane_names = set()
        for p_index, (plane, share) in enumerate(zip(planes_in, shares)):
            p_where = f"{where}.data_planes[{p_index}]"
            p_name = plane.get("name") or f"DP{p_index + 1}"
            if not isinstance(p_name, str) or not isinstance(plane.get("label", p_name), str):
                raise BriefError(f"{p_where}: name and label must be strings")
            if p_name in plane_names:
                raise BriefError(f"{p_where}: duplicate plane name {p_name!r}")
            plane_names.add(p_name)
            cloud = _normalise_cloud(plane.get("cloud", env.get("cloud", default_cloud)), p_where)
            region = _normalise_region(plane.get("region", env.get("region", default_region)), p_where)
            target = _normalise_target(plane.get("target", env.get("target", default_target)), p_where)
            _check_target_cloud(target, cloud, p_where)
            vcpu = plane.get("gateway_vcpu", env.get("gateway_vcpu"))
            if vcpu is not None:
                _number(vcpu, f"{p_where}.gateway_vcpu", 1, 64, integer=True)
                if target == "fargate" and vcpu not in FARGATE_VCPU:
                    raise BriefError(f"{p_where}: Fargate tasks take {', '.join(map(str, FARGATE_VCPU))} vCPU, got {vcpu}")
            min_gateways = _number(plane.get("min_gateways", 0), f"{p_where}.min_gateways", 0, 1000, integer=True)
            hosts = plane.get("hosts_control_plane", False)
            if not isinstance(hosts, bool):
                raise BriefError(f"{p_where}: hosts_control_plane must be true or false")
            planes.append({
                "name": p_name,
                "label": str(plane.get("label", p_name)),
                "cloud": cloud,
                "region": region,
                "target": target,
                "share": share,
                "gateway_vcpu": vcpu,
                "min_gateways": int(min_gateways),
                "hosts_control_plane": hosts,
            })

        cp_in = _optional_object(env, "control_plane", f"{where}.control_plane")
        _reject_unknown(cp_in, CONTROL_PLANE_KEYS, f"{where}.control_plane")
        mode = cp_in.get("mode", "dedicated")
        if mode not in ("dedicated", "shared"):
            raise BriefError(f"{where}.control_plane: mode must be dedicated or shared")
        if mode == "shared":
            if not isinstance(cp_in.get("shared_with"), str) or not cp_in.get("shared_with"):
                raise BriefError(f"{where}.control_plane: mode shared needs shared_with (an environment name)")
            extra = set(cp_in) & {"cloud", "region", "target", "label"}
            if extra:
                raise BriefError(f"{where}.control_plane: a shared control plane takes its {', '.join(sorted(extra))} from {cp_in['shared_with']}; drop them here")
            if any(p["hosts_control_plane"] for p in planes):
                raise BriefError(f"{where}: a plane cannot host the control plane when the environment shares another's")
            cp = {"mode": "shared", "shared_with": cp_in["shared_with"], "cloud": None, "region": None,
                  "target": None, "location_label": None, "remote": True}
        else:
            if "shared_with" in cp_in:
                raise BriefError(f"{where}.control_plane: shared_with needs mode shared")
            cp_where = f"{where}.control_plane"
            host, remote = _locate_control_plane(cp_in, planes, where)
            if host is not None:
                host["hosts_control_plane"] = True
            basis = host or planes[0]
            cp_cloud = _normalise_cloud(cp_in.get("cloud", basis["cloud"]), cp_where)
            if "target" in cp_in:
                cp_target = _normalise_target(cp_in["target"], cp_where)
            else:
                # Inherit the plane's target only where it exists on the control plane's cloud.
                cp_target = basis["target"] if not (basis["target"] in D.AWS_ONLY_TARGETS and cp_cloud != "aws") else "kubernetes"
            _check_target_cloud(cp_target, cp_cloud, cp_where)
            label = cp_in.get("label")
            if label is not None and not isinstance(label, str):
                raise BriefError(f"{cp_where}: label must be a string")
            cp = {
                "mode": "dedicated",
                "shared_with": None,
                "cloud": cp_cloud,
                "region": _normalise_region(cp_in.get("region", basis["region"]), cp_where),
                "target": cp_target,
                "location_label": label or (basis["label"] if host else "Control plane site"),
                "remote": remote,
            }

        # Inputs still on their default in this environment. Workload overrides and the
        # environment's own traffic figures replace the estate's defaults.
        env_defaulted = estate_defaulted - set(overrides)
        if shares_defaulted:
            env_defaulted.add("traffic_shares")
        env_traffic_in = env.get("traffic")
        assumed_traffic = False
        if env_traffic_in is not None:
            _require_object(env_traffic_in, f"{where}.traffic")
            if not env_traffic_in:
                raise BriefError(f"{where}.traffic is empty; leave it out or give fraction, peak_rps, avg_rps or a volume")
            if "fraction" in env_traffic_in:
                if len(env_traffic_in) > 1:
                    raise BriefError(f"{where}.traffic: fraction cannot be combined with {', '.join(sorted(set(env_traffic_in) - {'fraction'}))}")
                fraction = _number(env_traffic_in["fraction"], f"{where}.traffic.fraction", 1e-6, 1)
                env_traffic = {
                    "peak_rps": traffic["peak_rps"] * fraction,
                    "avg_rps": traffic["avg_rps"] * fraction,
                    "derivation": [f"{fraction:.0%} of the estate traffic"],
                }
                env_defaulted |= traffic_defaulted
            else:
                _reject_unknown(env_traffic_in, TRAFFIC_KEYS, f"{where}.traffic")
                own_defaulted: set[str] = set()
                t = derive_traffic(env_traffic_in, own_defaulted, f"{where}.traffic")
                env_traffic = {"peak_rps": t["peak_rps"], "avg_rps": t["avg_rps"], "derivation": t["derivation"]}
                env_defaulted |= own_defaulted
        elif posture == "production" or not has_production:
            env_traffic = {"peak_rps": traffic["peak_rps"], "avg_rps": traffic["avg_rps"], "derivation": ["Estate traffic"]}
            env_defaulted |= traffic_defaulted
        else:
            # Functional-test load: 5% of the estate with a 10 RPS floor. Flagged as an assumption.
            peak = max(10.0, traffic["peak_rps"] * 0.05)
            env_traffic = {"peak_rps": peak, "avg_rps": max(1.0, js_round(peak / 3)),
                           "derivation": ["Not supplied: assumed functional-test load of 5% of estate peak (10 RPS floor)"],
                           "assumed": True}
            assumed_traffic = True
        environments.append({
            "name": name,
            "posture": posture,
            "resilience": resilience,
            "workload": env_workload,
            "defaulted": sorted(env_defaulted),
            "planes": planes,
            "control_plane": cp,
            "traffic": env_traffic,
            "traffic_assumed": assumed_traffic,
            "description": str(env.get("description", "")),
            "guests": [],
        })

    by_name = {e["name"]: e for e in environments}
    for env in environments:
        cp = env["control_plane"]
        if cp["mode"] != "shared":
            continue
        host = by_name.get(cp["shared_with"])
        if host is None or host is env:
            raise BriefError(f"{env['name']}: control_plane.shared_with must name another environment")
        if host["control_plane"]["mode"] != "dedicated":
            raise BriefError(f"{env['name']}: control_plane.shared_with must name an environment with a dedicated control plane")
        if env["posture"] == "production" and host["posture"] != "production":
            raise BriefError(f"{env['name']}: a production environment cannot borrow the control plane of non-production {host['name']}")
        # The guest's planes connect to the host's control plane through MDCB.
        for key in ("cloud", "region", "target", "location_label"):
            cp[key] = host["control_plane"][key]
        host["guests"].append(env["name"])

    scenarios = []
    if brief.get("scenarios") is not None and not isinstance(brief["scenarios"], list):
        raise BriefError("scenarios must be a list")
    for index, scenario in enumerate(brief.get("scenarios") or []):
        where = f"scenarios[{index}]"
        _require_object(scenario, where)
        _reject_unknown(scenario, SCENARIO_KEYS, where)
        target_env = scenario.get("environment", environments[0]["name"])
        if not isinstance(target_env, str) or target_env not in by_name:
            raise BriefError(f"{where}: unknown environment {target_env!r}")
        overrides = _optional_object(scenario, "overrides", f"{where}.overrides")
        if not overrides:
            raise BriefError(f"{where}.overrides is empty; a scenario changes at least one workload field")
        _reject_unknown(overrides, set(WORKLOAD_DEFAULTS), f"{where}.overrides")
        for key, value in overrides.items():
            _check_workload_value(key, value, where)
        scenarios.append({"name": str(scenario.get("name") or f"Scenario {index + 1}"),
                          "environment": target_env, "overrides": overrides,
                          "description": str(scenario.get("description", ""))})

    # An input is an open question when a production environment (or, with none, any
    # environment) still runs on its default.
    askers = [e for e in environments if e["posture"] == "production"] or environments
    defaulted = sorted(set().union(*(set(e["defaulted"]) for e in askers)))

    return {
        "organisation": str(brief.get("organisation", "")),
        "title": str(brief.get("title", "")),
        "context": context,
        "traffic": traffic,
        "workload": workload,
        "environments": environments,
        "scenarios": scenarios,
        "defaulted": defaulted,
        "prepared_by": str(brief.get("prepared_by", "")),
        "date": str(brief.get("date") or date.today().isoformat()),
    }


# ----------------------------------------------------------------------------------------
# Sizing
# ----------------------------------------------------------------------------------------


def _rate_cloud(cloud: str) -> str:
    return cloud if cloud in D.CLOUDS else D.BENCHMARK_PROXY_FOR_UNPUBLISHED


def gateway_rate(plane: dict, env: dict) -> tuple[float, str, str]:
    """Benchmarked RPS per vCPU for the plane's hardware, its source and a short basis.

    Non-production planes on the default burstable node use the published 2-vCPU burstable
    run when it is lower than the 16-vCPU rate: burstable machines sustain far less.
    """
    profile = env["workload"]["auth_profile"]
    cloud = plane["cloud"]
    rate_cloud = _rate_cloud(cloud)
    rate = D.RPS_PER_VCPU[rate_cloud][profile]
    source = "published" if cloud in ("aws", "gcp", "azure") else "assumed"
    basis = "16-vCPU benchmark machine"
    burstable = (env["posture"] != "production" and cloud in ("aws", "gcp", "azure")
                 and plane["target"] != "fargate" and plane["gateway_vcpu"] is None)
    if burstable:
        rate = min(rate, D.RPS_PER_VCPU_BURSTABLE[cloud][profile])
        basis = "2-vCPU burstable benchmark machine"
        if cloud == "aws":  # t4g.medium has no run of its own; t2.medium is the nearest
            source = "assumed"
    return rate, source, basis


def size_pump(avg: float, peak: float, w: dict[str, Any], record_kb: float) -> dict[str, Any]:
    """Pump instances that keep the Redis analytics buffer from overflowing.

    With no peak duration the Pump keeps up with the full peak. With a peak duration the
    backlog that builds while the peak exceeds Pump capacity must fit the records the Redis
    buffer holds (peak x analytics_buffer_seconds), and capacity must exceed the average so
    the backlog drains afterwards.
    """
    rate = D.PUMP_RECORDS_PER_S_PER_INSTANCE
    drain_floor = int(avg // rate) + 1  # capacity strictly above the average
    duration = w["peak_duration_seconds"]
    allowance = peak * w["analytics_buffer_seconds"]
    if duration is None:
        instances = max(drain_floor, ceil(peak / rate))
        basis = "peak"
    else:
        instances = drain_floor
        while max(0.0, peak - instances * rate) * duration > allowance:
            instances += 1
        basis = "backlog"
    capacity = instances * rate
    backlog = max(0.0, peak - capacity) * (duration or 0)
    drain_s = backlog / (capacity - avg) if backlog else 0.0
    return {
        "instances": instances,
        "basis": basis,
        "records_per_s": avg,
        "peak_records_per_s": peak,
        "capacity_records_per_s": capacity,
        "backlog_records": backlog,
        "backlog_gb": backlog * record_kb * D.KB,
        "buffer_records": allowance,
        "drain_seconds": drain_s,
        "mb_per_s": avg * record_kb / 1024,
    }


def size_plane(plane: dict, env: dict, multi_dc: bool, extra_keys: int = 0) -> dict[str, Any]:
    w = env["workload"]
    prod = env["posture"] == "production"
    ha = env["resilience"] == "ha"
    cloud = plane["cloud"]
    priced = cloud in D.CLOUDS
    C = D.CLOUDS.get(cloud)
    R = D.EU_FACTOR if plane["region"] == "eu" else 1
    analytics_on = w["analytics"] == "enabled"

    peak = env["traffic"]["peak_rps"] * plane["share"]
    avg = env["traffic"]["avg_rps"] * plane["share"]

    # ---- gateways --------------------------------------------------------------------
    benchmark_rate, rate_source, rate_basis = gateway_rate(plane, env)
    rate = benchmark_rate * w["throughput_factor"]
    vcpu = plane["gateway_vcpu"] or (4 if prod else 2)
    base = max(1, ceil(peak / (rate * D.PLANNING_UTIL) / vcpu))
    count = base + 1 if ha else base
    count = max(count, plane["min_gateways"])
    per_gw = peak / count
    conn_pool = max(D.MIN_CONN_POOL, ceil(per_gw / (1000 / w["upstream_latency_ms"])))
    planned_capacity = js_round(count * vcpu * rate * D.PLANNING_UTIL)
    n1_capacity = js_round((count - 1) * vcpu * rate * D.PLANNING_UTIL)
    hpa_max = max(count + 1, ceil(peak * D.HPA_BURST_FACTOR / (rate * D.PLANNING_UTIL) / vcpu) + (1 if ha else 0))
    ram = vcpu * D.GATEWAY_RAM_GB_PER_VCPU
    node_ram = ram
    if plane["target"] == "fargate":
        shape = f"{vcpu} vCPU / {ram} GB task"
    elif priced and plane["gateway_vcpu"] is None:
        shape = C["node_shape"] if prod else C["dev_node_shape"]
        node_ram = C["node_ram_gb"] if prod else C["dev_node_ram_gb"]
    else:
        shape = f"{vcpu} vCPU / {ram} GB"
    gateways = {
        "count": count,
        "base": base,
        "vcpu": vcpu,
        "ram_gb": ram,  # what each gateway container or VM requests
        "node_ram_gb": node_ram,  # memory of the purchased node or task
        "shape": shape,
        "rate_rps_per_vcpu": rate,
        "benchmark_rps_per_vcpu": benchmark_rate,
        "rate_source": rate_source,
        "rate_basis": rate_basis,
        "planned_capacity_rps": planned_capacity,
        "n1_capacity_rps": n1_capacity,
        "n1_ok": n1_capacity >= peak,  # a single instance has no N-1 capacity at all
        "per_gateway_peak_rps": per_gw,
        "conn_pool": conn_pool,
        "hpa": {"min": count, "max": hpa_max, "target_cpu": D.HPA_TARGET_CPU},
    }

    # ---- Redis -----------------------------------------------------------------------
    record_kb = analytics_record_kb(w)
    cache_rps = avg * D.CACHE_SHARE if w["response_caching"] else 0
    parts = {
        "keys": (w["active_api_keys"] + extra_keys) * D.API_KEY_KB * D.KB,
        "cache": cache_rps * w["avg_response_kb"] * D.CACHE_TTL_S * D.KB,
        "analytics_buffer": (peak * record_kb * w["analytics_buffer_seconds"] * D.KB) if analytics_on else 0.0,
        "emergency_config": ((w["api_definitions"] * D.API_DEF_KB + w["policies"] * D.POLICY_KB) * D.KB) if multi_dc else 0.0,
    }
    steady = (parts["keys"] + parts["cache"] + parts["emergency_config"]) / D.UTIL

    def plan_for(buffer_gb: float) -> dict[str, Any]:
        need = max(1, ceil(steady + buffer_gb))
        nodes = 3 if buffer_gb > D.OUTAGE_CLASS_BUFFER_GB else (2 if ha else 1)
        return redis_plan(need, cloud, prod, nodes, R)

    plan = plan_for(parts["analytics_buffer"])
    redis_gb = plan["gb"]
    outage_class = parts["analytics_buffer"] > D.OUTAGE_CLASS_BUFFER_GB
    nodes = plan["nodes"]
    shards = plan["shards"]

    def buffer_cost_at(seconds: int) -> int | None:
        # The same plan_for as the base case, so the same duration gives the same price.
        if not priced or not analytics_on:
            return None
        return plan_for(peak * record_kb * seconds * D.KB)["cost"]

    tier_note = ""
    if cloud == "aws":
        tier_note = (f"{plan['tier_name']} has {plan['tier_gib']} GiB per node; about 85% is usable with reserved memory "
                     "tuned to about 15%. At the 25% default, step up one tier.")
        tier = plan["tier"]
    elif priced:
        tier = plan["tier"]
    else:
        vm_qty = 3 if ha or outage_class else 1
        tier = f"{vm_qty} x VM, {self_hosted_ram(redis_gb / shards)} GB RAM" + (" (Redis Sentinel)" if vm_qty == 3 else "")
        if shards > 1:
            tier = f"{shards} shards x " + tier
    redis = {
        "gb": redis_gb,
        "parts": parts,
        "record_kb": record_kb,
        "cache_rps": cache_rps,
        "steady_gb": steady,
        "nodes": nodes if priced else (3 if ha or outage_class else 1),
        "tier": tier,
        "tier_name": plan["tier_name"],
        "tier_note": tier_note,
        "tier_gib": plan["tier_gib"],
        "persistent": not multi_dc,
        "outage_class": outage_class,
        "serverless_fit": priced and peak < D.REDIS_SERVERLESS_FIT_RPS,
        "service": C["redis_svc"] if priced else "Redis or Valkey on your own VMs",
        "sensitivity": {"30s": buffer_cost_at(30), "5min": buffer_cost_at(300), "1h": buffer_cost_at(3600)},
        "shards": shards,
        "expiry_needed_s": w["analytics_buffer_seconds"] if analytics_on else 0,
    }

    # ---- Pump ------------------------------------------------------------------------
    pump = size_pump(avg, peak, w, record_kb) if analytics_on else None

    # ---- costs -----------------------------------------------------------------------
    is_k8s = plane["target"] == "kubernetes"
    k8s_fee = C["k8s_fee"] if priced and is_k8s else 0
    if not priced:
        gw_cost = 0
    elif plane["target"] == "fargate":
        gw_cost = js_round(count * (vcpu * D.FARGATE_VCPU_MONTH + vcpu * D.GATEWAY_RAM_GB_PER_VCPU * D.FARGATE_GB_MONTH) * R)
    else:
        unit = C["node"] if prod else C["dev_node"]
        if plane["gateway_vcpu"] not in (None, 4 if prod else 2):
            # Scale the benchmark-family node price linearly by vCPU.
            unit = (C["node"] if prod else C["dev_node"]) * vcpu / (4 if prod else 2)
        gw_cost = js_round(count * unit * R)
    # On Kubernetes Pump shares the gateway node pool; elsewhere each Pump instance is its
    # own 2 vCPU / 4 GB task or VM.
    pump_cost = 0
    if pump and priced and not is_k8s:
        if plane["target"] == "fargate":
            pump_cost = js_round(pump["instances"] * (2 * D.FARGATE_VCPU_MONTH + 4 * D.FARGATE_GB_MONTH) * R)
        else:
            pump_cost = js_round(pump["instances"] * (0.5 * C["node"] if prod else C["dev_node"]) * R)
    monthly_req = avg * 86400 * D.DAYS_PER_MONTH
    egress_gb = monthly_req * w["avg_response_kb"] * D.KB

    # ---- self-hosted bill of materials (useful for every VM-style target) ------------
    bom = [{"component": "Tyk Gateway", "vcpu": vcpu, "ram_gb": ram,
            "disk_gb": D.SELF_HOSTED_DISK_GB["gateway"], "qty": count, "stateful": False}]
    if pump:
        bom.append({"component": "Tyk Pump", "vcpu": 2, "ram_gb": 4, "disk_gb": D.SELF_HOSTED_DISK_GB["pump"],
                    "qty": pump["instances"], "stateful": False})
    redis_vm_qty = 3 if ha or outage_class else 1
    bom.append({"component": "Redis" + (" (Sentinel)" if redis_vm_qty == 3 else "") + (f", per shard x {shards}" if shards > 1 else ""),
                "vcpu": 4 if peak > 20000 else 2, "ram_gb": self_hosted_ram(redis_gb / shards),
                "disk_gb": max(20, ceil(redis_gb / shards * 3)), "qty": redis_vm_qty * shards, "stateful": not multi_dc})

    return {
        "name": plane["name"],
        "label": plane["label"],
        "cloud": cloud,
        "cloud_name": C["name"] if priced else ("On-premises" if cloud == "on-prem" else "Private cloud"),
        "priced": priced,
        "region": plane["region"],
        "target": plane["target"],
        "k8s_name": C["k8s"] if priced and is_k8s else ("Kubernetes" if is_k8s else {"ecs": "Amazon ECS", "fargate": "AWS Fargate", "vms": "VMs"}[plane["target"]]),
        "share": plane["share"],
        "peak_rps": peak,
        "avg_rps": avg,
        "hosts_control_plane": plane["hosts_control_plane"],
        "gateways": gateways,
        "redis": redis,
        "pump": pump,
        "monthly_requests": monthly_req,
        "egress_gb": egress_gb,
        "costs": {"k8s": k8s_fee, "gateways": gw_cost, "redis": redis_cost_of(plan, priced), "pump": pump_cost},
        "bom": bom,
    }


def redis_cost_of(plan: dict[str, Any], priced: bool) -> int:
    return plan["cost"] if priced else 0


def _guest_load(guests: list[dict]) -> dict[str, float]:
    """What environments that borrow a control plane add to it."""
    load = {"logs_gb": 0.0, "agg_gb": 0.0, "log_docs_m": 0.0, "keys": 0}
    for g in guests:
        db = g["control_plane"]["db"]
        load["logs_gb"] += db["nonagg_gb"]
        load["agg_gb"] += db["agg_gb"]
        load["log_docs_m"] += db["log_docs_m"]
        load["keys"] += g["workload"]["active_api_keys"]
    return load


def size_control_plane(env: dict, planes: list[dict], multi_dc: bool, guest: dict[str, float] | None = None,
                       mdcb: bool | None = None) -> dict[str, Any]:
    mdcb = multi_dc if mdcb is None else mdcb
    guest = guest or {"logs_gb": 0.0, "agg_gb": 0.0, "log_docs_m": 0.0, "keys": 0}
    w = env["workload"]
    cp = env["control_plane"]
    prod = env["posture"] == "production"
    ha = env["resilience"] == "ha"
    shared = cp["mode"] == "shared"
    cloud = cp["cloud"]
    priced = cloud in D.CLOUDS and not shared
    C = D.CLOUDS.get(cloud)
    R = D.EU_FACTOR if cp["region"] == "eu" else 1
    analytics_on = w["analytics"] == "enabled"
    avg = env["traffic"]["avg_rps"]
    fargate = cp["target"] == "fargate"

    def unit(vcpu: int, ram: int) -> float:
        """Monthly price of one control-plane instance of this size on the chosen target."""
        if fargate:
            return vcpu * D.FARGATE_VCPU_MONTH + ram * D.FARGATE_GB_MONTH
        return C["node"] * vcpu / D.CONTROL_PLANE_NODE_VCPU

    cp_nodes = 2 if ha else 1
    node_shape = (f"{D.CONTROL_PLANE_NODE_VCPU} vCPU / {D.CONTROL_PLANE_NODE_RAM_GB} GB task" if fargate
                  else (C["node_shape"] if C else f"{D.CONTROL_PLANE_NODE_VCPU} vCPU / {D.CONTROL_PLANE_NODE_RAM_GB} GB"))
    cp_cost = js_round(cp_nodes * unit(D.CONTROL_PLANE_NODE_VCPU, D.CONTROL_PLANE_NODE_RAM_GB) * R) if priced else 0
    # A control plane on Kubernetes needs a cluster of its own unless it runs in the
    # Kubernetes cluster of the data plane beside it.
    host = next((p for p in planes if p["hosts_control_plane"]), None)
    in_plane_cluster = host is not None and host["target"] == "kubernetes" and host["priced"] and host["cloud"] == cloud
    cp_k8s_fee = C["k8s_fee"] if priced and cp["target"] == "kubernetes" and not in_plane_cluster else 0

    portal = {"mode": w["developer_portal"], "line": "", "cost": 0, "db_cost": 0}
    if w["developer_portal"] == "internal":
        portal["line"] = "1 x 2 vCPU / 4 GB" + (" task" if fargate else " node") + ", sharing the analytics database"
        portal["cost"] = js_round(unit(2, 4) * R) if priced else 0
    elif w["developer_portal"] == "partner-facing":
        shape = node_shape.split(" (")[0]
        portal["line"] = f"2 x {shape} (HA) with its own small PostgreSQL HA pair"
        portal["cost"] = js_round(2 * unit(D.CONTROL_PLANE_NODE_VCPU, D.CONTROL_PLANE_NODE_RAM_GB) * R) if priced else 0
        portal["db_cost"] = js_round(2 * D.SMALL_DB_FACTOR * C["pg_node"] * R) if priced else 0

    # ---- analytics store ---------------------------------------------------------------
    raw_share = 1 if not multi_dc else (1 if w["multi_dc_analytics"] == "control-plane" else 0)
    if not analytics_on:
        raw_share = 0
    nonagg_per_day = avg * 86400 * (D.NONAGG_RECORD_KB + detail_kb(w)) * D.KB if analytics_on else 0.0
    nonagg_gb = nonagg_per_day * w["request_log_retention_days"] * raw_share
    active_items = w["active_api_keys"] + w["api_definitions"] * 2
    if not analytics_on or (w["multi_dc_analytics"] == "edge-local" and multi_dc):
        agg_gb = 0.0
    else:
        agg_gb = active_items * D.AGG_ITEM_KB * 24 * w["aggregate_retention_days"] * D.KB
    config_gb = (w["api_definitions"] * D.API_DEF_KB + w["policies"] * D.POLICY_KB) * D.KB
    log_docs_m = (avg * 86400 * w["request_log_retention_days"] * raw_share) / 1e6
    # A dedicated control plane also stores the analytics of environments that share it.
    hosted_logs = nonagg_gb + (0.0 if shared else guest["logs_gb"])
    hosted_agg = agg_gb + (0.0 if shared else guest["agg_gb"])
    hosted_docs = log_docs_m + (0.0 if shared else guest["log_docs_m"])
    storage_gb = max(10, ceil((hosted_logs + hosted_agg + config_gb) / D.UTIL))
    # Tyk's MongoDB sizing: the log index (about 30% of ~1KB records) plus the aggregates in
    # memory, doubled for MongoDB's own structures.
    index_gb = hosted_docs * 1e6 * D.MONGO_LOG_INDEX_KB * D.KB
    ram_gb = max(4, ceil(D.MONGO_RAM_FACTOR * (index_gb + hosted_agg)))

    db_cost = 0
    feasible = True
    if shared:
        db_line = f"Shared with the {cp['shared_with']} control plane (adds about {gb_text((nonagg_gb + agg_gb) / D.UTIL)} to its analytics store)"
    elif w["storage_engine"] == "mongodb":
        if priced:
            fits_ram = [t for t in D.ATLAS_TIERS if t[1] >= ram_gb]
            if not fits_ram:
                feasible = False
                largest = D.ATLAS_TIERS[-1]
                db_line = (f"No listed MongoDB Atlas tier fits: about {ram_gb} GB of RAM and {fmt(storage_gb)} GB of storage are needed, "
                           f"and the largest listed tier ({largest[0]}) has {largest[1]} GB of RAM")
            else:
                tier = next((t for t in fits_ram if t[2] >= storage_gb), fits_ram[0])
                over = js_round((storage_gb - tier[2]) * D.ATLAS_EXTRA_STORAGE_PER_GB) if storage_gb > tier[2] else 0
                db_cost = js_round((tier[3] + over) * R)
                name = "MongoDB Atlas" if cloud != "oci" else "MongoDB-compatible store sized like Atlas"
                db_line = f"{name} {tier[0]} ({tier[1]} GB RAM / {tier[2]} GB" + (", replica set built in)" if cloud != "oci" else ")") + (
                    f" + {fmt(storage_gb - tier[2])} GB extra storage" if over else "")
        else:
            db_line = f"MongoDB replica set: 3 x VM, {max(8, ram_gb)} GB RAM, {fmt(storage_gb)} GB disk"
    else:
        if priced:
            pg_unit = C["pg_node"] if prod else js_round(D.SMALL_DB_FACTOR * C["pg_node"])
            pg_name = C["pg_svc"] if prod else C["dev_pg_svc"]
            db_cost = js_round((pg_unit * (2 if ha else 1) + storage_gb * C["stor_per_gb"]) * R)
            db_line = f"{pg_name}{' x 2 (HA pair)' if ha else ''} + {fmt(storage_gb)} GB storage"
        else:
            db_line = f"PostgreSQL: {3 if ha else 1} x VM, 4 vCPU / 8 GB starting shape, {fmt(storage_gb)} GB disk"

    # Control-plane Redis: every key the control plane serves, its own and its guests'.
    cp_keys = w["active_api_keys"] + (0 if shared else guest["keys"])
    cp_redis_gb = max(1, ceil((cp_keys * D.API_KEY_KB * D.KB + 0.2) / D.UTIL)) if multi_dc and not shared else 0
    cp_redis_plan = redis_plan(cp_redis_gb, cloud, prod, 2 if ha else 1, R) if cp_redis_gb else None
    cp_redis_cost = cp_redis_plan["cost"] if cp_redis_plan and priced else 0

    components = [] if shared else ["Tyk Dashboard"]
    if not shared and mdcb:
        components += ["Tyk MDCB"] + (["Management gateway"] if multi_dc else [])
    if w["developer_portal"] != "none" and not shared:
        components.append("Tyk Developer Portal")

    bom = [{"component": "Control-plane node (" + ", ".join(c.replace("Tyk ", "") for c in components if c != "Tyk Developer Portal") + ")",
            "vcpu": D.CONTROL_PLANE_NODE_VCPU, "ram_gb": D.CONTROL_PLANE_NODE_RAM_GB, "disk_gb": D.SELF_HOSTED_DISK_GB["dashboard"], "qty": cp_nodes, "stateful": False}]
    if w["developer_portal"] == "internal":
        bom.append({"component": "Tyk Developer Portal", "vcpu": 2, "ram_gb": 4, "disk_gb": 20, "qty": 1, "stateful": False})
    elif w["developer_portal"] == "partner-facing":
        bom.append({"component": "Tyk Developer Portal", "vcpu": 4, "ram_gb": 8, "disk_gb": 20, "qty": 2, "stateful": False})
    db_vm_qty = 3 if ha else 1
    bom.append({"component": "MongoDB" if w["storage_engine"] == "mongodb" else "PostgreSQL (starting shape)",
                "vcpu": 4, "ram_gb": max(8, ram_gb) if w["storage_engine"] == "mongodb" else 8,
                "disk_gb": storage_gb, "qty": db_vm_qty, "stateful": True})
    if multi_dc:
        bom.append({"component": "Control-plane Redis" + (" (Sentinel)" if ha else ""), "vcpu": 2,
                    "ram_gb": self_hosted_ram(cp_redis_gb), "disk_gb": max(20, cp_redis_gb * 3), "qty": 3 if ha else 1, "stateful": True})

    return {
        "mode": cp["mode"],
        "shared_with": cp["shared_with"],
        "remote": cp.get("remote", False),
        "cloud": cloud,
        "cloud_name": C["name"] if C else ("On-premises" if cloud == "on-prem" else "Private cloud"),
        "priced": priced,
        "region": cp["region"],
        "target": cp["target"],
        "location_label": cp["location_label"],
        "nodes": cp_nodes,
        "node_shape": node_shape,
        "components": components,
        "mdcb": mdcb and not shared,
        "portal": portal,
        "cp_redis_gb": cp_redis_gb,
        "cp_redis_tier": cp_redis_plan["tier"] if cp_redis_plan else "",
        "k8s_name": C["k8s"] if cp_k8s_fee else "",
        "costs": {"control_plane": cp_cost, "portal": portal["cost"], "portal_db": portal["db_cost"],
                  "cp_redis": cp_redis_cost, "analytics_store": db_cost, "k8s": cp_k8s_fee},
        "guest_keys": 0 if shared else guest["keys"],
        "db": {
            "engine": w["storage_engine"],
            "line": db_line,
            "feasible": feasible,
            "storage_gb": storage_gb,
            "ram_gb": ram_gb,
            "index_gb": index_gb,
            "nonagg_per_day_gb": nonagg_per_day,
            "nonagg_gb": nonagg_gb,
            "agg_gb": agg_gb,
            "guest_gb": 0.0 if shared else guest["logs_gb"] + guest["agg_gb"],
            "log_docs_m": log_docs_m,
            "config_gb": config_gb,
            "raw_share": raw_share,
            "active_items": active_items,
            "writes_per_s": avg if raw_share else 0.0,
        },
        "bom": bom if not shared else [],
    }


def size_environment(env: dict, defaulted: frozenset[str] | set[str] | None = None,
                     guest: dict[str, float] | None = None) -> dict[str, Any]:
    w = env["workload"]
    if defaulted is None:
        defaulted = set(env.get("defaulted", []))
    guest = guest or {"logs_gb": 0.0, "agg_gb": 0.0, "log_docs_m": 0.0, "keys": 0}
    # multi_dc: this environment's own planes are MDCB workers: several planes, planes that
    # borrow another environment's control plane, or a control plane placed away from every
    # plane. A single-plane environment with its control plane beside it stays one install
    # with the Dashboard (persistent shared Redis, raw logs local) and only adds MDCB when
    # other environments borrow its control plane.
    remote_cp = env["control_plane"].get("remote", False)
    multi_dc = len(env["planes"]) > 1 or env["control_plane"]["mode"] == "shared" or remote_cp
    mdcb = multi_dc or bool(env.get("guests"))
    ha = env["resilience"] == "ha"
    analytics_on = w["analytics"] == "enabled"
    # A single-plane host whose Redis is also the control plane's holds its guests' keys.
    planes = [size_plane(p, env, multi_dc, 0 if multi_dc else guest["keys"]) for p in env["planes"]]
    cp = size_control_plane(env, planes, multi_dc, guest, mdcb)

    # ---- transfer ----------------------------------------------------------------------
    record_kb = analytics_record_kb(w)
    by_cloud: dict[str, float] = {}
    for p in planes:
        by_cloud[p["cloud"]] = by_cloud.get(p["cloud"], 0.0) + p["egress_gb"]
    egress_rows = []
    total_inet_cost = total_private_cost = 0.0
    for cloud, gb in by_cloud.items():
        inet = gb * w["internet_egress_share"]
        private = gb - inet
        if cloud in D.CLOUDS:
            C = D.CLOUDS[cloud]
            inet_cost = egress_cost(inet, C["egress_free_gb"], C["egress_tiers"])
            private_cost = private * D.PRIVATE_EGRESS_PER_GB
        else:
            inet_cost = private_cost = 0.0
        total_inet_cost += inet_cost
        total_private_cost += private_cost
        egress_rows.append({"cloud": cloud, "gb": gb, "internet_gb": inet, "private_gb": private,
                            "internet_cost": inet_cost, "private_cost": private_cost,
                            "effective_rate": inet_cost / inet if inet > 0 else 0.0,
                            "priced": cloud in D.CLOUDS})
    inter_dc_gb = inter_dc_cost = 0.0
    active_items = cp["db"]["active_items"]
    if multi_dc and analytics_on:
        for p in planes:
            if p["hosts_control_plane"]:
                continue  # beside the control plane: analytics stay local
            if w["multi_dc_analytics"] == "control-plane":
                gb = p["monthly_requests"] * record_kb * D.KB
            elif w["multi_dc_analytics"] == "hybrid-aggregate":
                gb = active_items * D.AGG_ITEM_KB * 24 * D.DAYS_PER_MONTH * p["share"] * D.KB
            else:
                gb = 0.0
            p["inter_plane_gb"] = gb
            inter_dc_gb += gb
            p["inter_plane_cross_cloud"] = p["cloud"] != cp["cloud"]
            if p["priced"]:
                PC = D.CLOUDS[p["cloud"]]
                # Same cloud: inter-region rate. Another cloud or your own DC: the
                # traffic leaves this cloud as internet egress (first paid tier).
                rate = PC["inter_region_per_gb"] if not p["inter_plane_cross_cloud"] else PC["egress_tiers"][0][1]
                inter_dc_cost += gb * rate
    client_egress_gb = sum(r["gb"] for r in egress_rows)
    transfer = {
        "client_egress_gb": client_egress_gb,
        "internet_gb": sum(r["internet_gb"] for r in egress_rows),
        "private_gb": sum(r["private_gb"] for r in egress_rows),
        "internet_cost": total_inet_cost,
        "private_cost": total_private_cost,
        "by_cloud": egress_rows,
        "inter_dc_gb": inter_dc_gb,
        "inter_dc_cost": inter_dc_cost,
        "monthly_requests": env["traffic"]["avg_rps"] * 86400 * D.DAYS_PER_MONTH,
    }

    # ---- cost rows -----------------------------------------------------------------------
    rows: list[dict[str, Any]] = []

    def add(item: str, shape: str, cost: float, group: str) -> None:
        rows.append({"item": item, "shape": shape, "monthly": js_round(cost), "group": group})

    priced_planes = [p for p in planes if p["priced"]]
    k8s_clusters = [p["k8s_name"] for p in planes if p["costs"]["k8s"]] + ([cp["k8s_name"]] if cp["costs"]["k8s"] else [])
    k8s_total = sum(p["costs"]["k8s"] for p in planes) + cp["costs"]["k8s"]
    if k8s_total:
        names = sorted(set(k8s_clusters))
        add("Managed Kubernetes", f"{' / '.join(names)} x {len(k8s_clusters)}" if len(k8s_clusters) > 1 else names[0], k8s_total, "platform")
    for p in priced_planes:
        prefix = f"{p['label']}: " if multi_dc else ""
        add(f"{prefix}Gateway {'tasks' if p['target'] == 'fargate' else 'nodes'}", f"{p['gateways']['count']} x {p['gateways']['shape']}", p["costs"]["gateways"], "gateway")
    for p in priced_planes:
        prefix = f"{p['label']}: " if multi_dc else ""
        kind = "Worker Redis (ephemeral)" if multi_dc else "Redis (persistent)"
        add(f"{prefix}{kind}", f"{p['redis']['tier']}, {p['redis']['service'].split(' (')[0]}", p["costs"]["redis"], "redis")
    for p in priced_planes:
        if p["costs"]["pump"]:
            prefix = f"{p['label']}: " if multi_dc else ""
            add(f"{prefix}Pump {'tasks' if p['target'] in ('fargate', 'ecs') else 'instances'}", f"{p['pump']['instances']} x 2 vCPU / 4 GB", p["costs"]["pump"], "pump")
    if cp["priced"]:
        add("Control plane", f"{cp['nodes']} x {cp['node_shape']}", cp["costs"]["control_plane"], "control-plane")
        if cp["costs"]["portal"]:
            add("Developer Portal", cp["portal"]["line"], cp["costs"]["portal"], "control-plane")
        if cp["costs"]["portal_db"]:
            add("Portal database", "small managed PostgreSQL, HA pair", cp["costs"]["portal_db"], "control-plane")
        if cp["cp_redis_gb"]:
            add("Control-plane Redis (persistent)", f"{gb_text(cp['cp_redis_gb'])}: {cp['cp_redis_tier']}", cp["costs"]["cp_redis"], "control-plane")
        if cp["db"]["feasible"]:
            add("Analytics store", cp["db"]["line"], cp["costs"]["analytics_store"], "control-plane")
    egress_cost_total = total_inet_cost + total_private_cost
    if any(r["priced"] for r in egress_rows):
        inet_priced = sum(r["internet_gb"] for r in egress_rows if r["priced"])
        private_priced = sum(r["private_gb"] for r in egress_rows if r["priced"])
        add("Client egress", f"{fmt(inet_priced)} GB internet (tiered)" + (f" + {fmt(private_priced)} GB private" if private_priced > 0 else ""), egress_cost_total, "transfer")
    if inter_dc_gb > 0.05 and js_round(inter_dc_cost) >= 1:
        add("Inter-plane transfer", gb_text(inter_dc_gb), inter_dc_cost, "transfer")

    # The total is the sum of the displayed rows, so a document never shows a total that
    # disagrees with its own table.
    total = sum(r["monthly"] for r in rows)

    unpriced = [p["label"] for p in planes if not p["priced"]]
    if not (cp["priced"] or cp["mode"] == "shared"):
        unpriced.append("control plane")
    if cp["priced"] and not cp["db"]["feasible"]:
        unpriced.append("analytics store (no listed tier fits)")
    if multi_dc and analytics_on and w["multi_dc_analytics"] == "edge-local":
        unpriced.append("data-plane analytics sinks (edge-local mode)")
    result = {
        "name": env["name"],
        "posture": env["posture"],
        "resilience": env["resilience"],
        "description": env["description"],
        "workload": w,
        "traffic": env["traffic"],
        "multi_dc": multi_dc,
        "defaulted": sorted(defaulted),
        "guests": list(env.get("guests", [])),
        "planes": planes,
        "control_plane": cp,
        "transfer": transfer,
        "cost_rows": rows,
        "total_monthly": js_round(total),
        "fully_priced": not unpriced,
        "unpriced_items": unpriced,
    }
    result["notes"] = env_notes(result, defaulted)
    return result


# ----------------------------------------------------------------------------------------
# Findings
# ----------------------------------------------------------------------------------------


def env_notes(env: dict, defaulted: frozenset[str] | set[str] = frozenset()) -> list[dict[str, str]]:
    """Structured findings. tone: risk | recommendation | decision | note."""
    notes: list[dict[str, str]] = []
    w = env["workload"]
    prod = env["posture"] == "production"
    analytics_on = w["analytics"] == "enabled"
    add = lambda tone, topic, text: notes.append({"tone": tone, "topic": topic, "text": text})

    for p in env["planes"]:
        g = p["gateways"]
        where = f"{p['label']}: " if env["multi_dc"] else ""
        if not g["n1_ok"] and prod:
            if g["count"] == 1:
                add("risk", "gateways", f"{where}a single gateway instance has no failover: any restart, upgrade or failure stops traffic. Production needs at least two.")
            else:
                add("risk", "gateways", f"{where}losing one gateway at peak leaves about {fmt(g['n1_capacity_rps'])} RPS of planned capacity against a {fmt(p['peak_rps'])} RPS peak. Add a gateway, and pre-scale ahead of known peak events.")
        if p["redis"]["shards"] > 1:
            add("risk", "redis", f"{where}Redis needs {gb_text(p['redis']['gb'])}, beyond the largest single node. Run Redis Cluster ({p['redis']['shards']} shards) or move the analytics buffer to a separate Redis, and shorten the outage tolerance if it drives the size.")
        if p.get("inter_plane_cross_cloud") and p.get("inter_plane_gb", 0) > 1:
            if p["priced"]:
                add("note", "cost", f"{where}analytics transfer to the control plane crosses clouds, so it is priced as internet egress rather than inter-region transfer.")
            else:
                add("note", "cost", f"{where}about {gb_text(p['inter_plane_gb'])} of analytics a month travels to the control plane over your own link, which is not priced here.")
        r = p["redis"]
        if r["outage_class"]:
            add("decision", "redis", f"{where}the analytics buffer alone is {gb_text(r['parts']['analytics_buffer'])}. Buffers that large are durability-critical during an outage, so plan a primary with two replicas.")
        pump = p["pump"]
        if pump and pump["basis"] == "backlog" and pump["backlog_records"] > 0:
            add("note", "analytics", f"{where}during a {fmt(w['peak_duration_seconds'])}-second peak, Pump falls about {fmt(pump['backlog_records'])} records behind "
                f"({gb_text(pump['backlog_gb'])} in Redis, within the {fmt(pump['buffer_records'])}-record outage buffer) and catches up about "
                f"{fmt(pump['drain_seconds'])} seconds after the peak. A Pump or database outage during the peak then has less buffer left.")
    for cloud_name in sorted({p["cloud_name"] for p in env["planes"] if p["gateways"]["rate_source"] == "assumed"}):
        labels = [p["label"] for p in env["planes"] if p["cloud_name"] == cloud_name and p["gateways"]["rate_source"] == "assumed"]
        scope = f"{', '.join(labels)}: " if env["multi_dc"] else ""
        basis = {p["gateways"]["rate_basis"] for p in env["planes"] if p["cloud_name"] == cloud_name}
        if cloud_name == "AWS":
            text = (f"{scope}the burstable t4g.medium node has no published Tyk benchmark; the gateway rate uses the published "
                    "t2.medium run. Load-test the node before relying on it for more than functional testing.")
        else:
            text = (f"{scope}no published Tyk benchmark exists for {cloud_name} hardware. The gateway rate assumes AWS c5-class "
                    "performance per vCPU. Load-test this hardware before committing.")
        if "2-vCPU burstable benchmark machine" in basis and cloud_name != "AWS":
            text += " Burstable nodes use the lower burstable rate."
        add("note", "gateways", text)
    floor = [p for p in env["planes"] if prod and p["cloud"] == "aws" and p["redis"]["tier_name"] == D.AWS_REDIS_NODES[0][2]]
    if floor:
        scope = f"{', '.join(p['label'] for p in floor)}: " if env["multi_dc"] else ""
        add("decision", "redis", f"{scope}{D.AWS_REDIS_NODES[0][2]} is the smallest steady-performance ElastiCache tier, so it is the floor even where less memory is needed. Burstable nodes are excluded from the production hot path, because the gateways use Redis for keys, quotas and analytics.")
    if prod and w["auth_profile"] != "keyless" and "active_api_keys" in defaulted:
        add("risk", "redis", f"Active API keys were not supplied, so {fmt(w['active_api_keys'])} was assumed. With {w['auth_profile']} the token store grows with consumers: millions of per-consumer tokens make keys the largest Redis term (5 million keys is about 5 GB). Confirm this before trusting the Redis sizing.")
    if w["throughput_factor"] < 1:
        add("decision", "gateways", f"Gateway throughput is derated to {w['throughput_factor']:.0%} of the published benchmark for custom plugins or heavy transformations on the hot path. Confirm the factor with a load test that runs those plugins.")
    if analytics_on:
        db = env["control_plane"]["db"]
        cp = env["control_plane"]
        if w["analytics_buffer_seconds"] > D.GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S:
            add("decision", "redis", f"Set analytics_config.storage_expiration_time to at least {fmt(w['analytics_buffer_seconds'])} seconds on every gateway. "
                f"With the default of {D.GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S} seconds the gateway deletes unprocessed analytics {D.GATEWAY_ANALYTICS_EXPIRY_DEFAULT_S} seconds "
                "after the first one is written, so a longer outage loses records whatever Redis holds.")
        if db["raw_share"] and db["nonagg_per_day_gb"] > 20:
            add("recommendation", "analytics", f"At about {gb_text(db['nonagg_per_day_gb'])} of raw request logs per day, keeping many days queryable makes the store large. Consider one to two days queryable, aggregates for trends, and raw logs archived to object storage.")
        if w["detailed_analytics"]:
            add("recommendation", "analytics", "Detailed analytics records full request and response bodies, base64-encoded, and dominates analytics storage. Enable it per API for debugging, not globally.")
        if env["multi_dc"] and w["multi_dc_analytics"] == "control-plane" and env["transfer"]["inter_dc_gb"] > 100:
            add("recommendation", "analytics", "Shipping every raw record to the control plane is the most expensive multi-plane analytics mode. The hybrid-aggregate mode forwards only aggregates, so individual request logs are not in the Dashboard log browser; GraphQL Activity by Graph is not populated, and MCP analytics need MCP aggregation enabled on the Hybrid Pump.")
        if env["multi_dc"] and w["multi_dc_analytics"] == "edge-local":
            add("note", "analytics", "Edge-local analytics keeps every record in its data plane. The sinks that hold them are not sized or priced here.")
        if cp["mode"] != "shared" and w["storage_engine"] == "postgresql" and db["writes_per_s"]:
            add("note", "analytics", f"PostgreSQL compute is not modelled: the instance shown is a starting shape sized for storage. Pump writes about {fmt(db['writes_per_s'])} records per second into it on average; load-test the database at the peak write rate before committing.")
        sink = "through the Hybrid Pump to MDCB" if env["multi_dc"] and w["multi_dc_analytics"] != "edge-local" else ("into MongoDB" if w["storage_engine"] == "mongodb" else "")
        if sink:
            add("note", "analytics", f"The Pump planning rate ({fmt(D.PUMP_RECORDS_PER_S_PER_INSTANCE)} records per second per instance) is for a PostgreSQL sink. No figure exists for throughput {sink}; confirm it in the load test.")
    else:
        add("decision", "analytics", "Analytics is disabled, so Dashboard activity reports are unavailable. Gateway access logs or OpenTelemetry traces can feed an external observability platform instead.")
    # The store can hold guests' analytics even when this environment records none.
    db, cp = env["control_plane"]["db"], env["control_plane"]
    if cp["mode"] != "shared" and not db["feasible"]:
        add("risk", "analytics", f"{db['line']}. Shorten request-log retention, keep raw logs out of MongoDB, or shard MongoDB; this estimate does not price the store.")
    elif w["storage_engine"] == "mongodb" and db["ram_gb"] > 64 and cp["mode"] != "shared":
        add("risk", "analytics", f"The MongoDB working set wants about {db['ram_gb']} GB of RAM. Shorten retention or keep raw logs elsewhere rather than buying that tier.")
    if env["multi_dc"]:
        add("note", "topology", "Route each client to one data plane with geographic or site affinity. Never load-balance one client across planes: quota and rate-limit counters are kept per plane, never shared between planes.")
    if env["control_plane"].get("remote") and env["control_plane"]["mode"] == "dedicated":
        add("note", "topology", f"The control plane runs in {env['control_plane']['location_label']}, away from every data plane, so each plane is an MDCB worker with its own Redis and ships its analytics across.")
    if env["control_plane"].get("guest_keys"):
        add("note", "redis", f"The control plane also holds the {fmt(env['control_plane']['guest_keys'])} active keys of the environments that share it.")
    if prod and w["internet_egress_share"] >= 1 and any(r["priced"] for r in env["transfer"]["by_cloud"]):
        add("note", "cost", "Egress assumes every response leaves the cloud to the public internet, the most pessimistic case. Private links and same-cloud consumers cut this line sharply.")
    if not prod:
        add("note", "posture", "Non-production posture uses burstable or smaller tiers sized for functional testing. Never promote this bill of materials to production.")
    return notes


# ----------------------------------------------------------------------------------------
# Estate
# ----------------------------------------------------------------------------------------


def _size_all(environments: list[dict]) -> dict[str, dict]:
    """Size every environment. Guests first: their analytics and keys land in the host."""
    sized: dict[str, dict] = {}
    for env in environments:
        if env["control_plane"]["mode"] == "shared":
            sized[env["name"]] = size_environment(env)
    for env in environments:
        if env["name"] not in sized:
            guest = _guest_load([sized[g] for g in env["guests"]])
            sized[env["name"]] = size_environment(env, guest=guest)
    return sized


def size_estate(brief: dict[str, Any]) -> dict[str, Any]:
    norm = normalise_brief(brief)
    sized = _size_all(norm["environments"])
    envs = [sized[e["name"]] for e in norm["environments"]]
    estate_total = sum(e["total_monthly"] for e in envs)

    scenarios = []
    for scenario in norm["scenarios"]:
        # Recompute the whole estate with the override, so a change in a guest environment
        # also moves its host's analytics store, Redis and cost.
        variant_envs = copy.deepcopy(norm["environments"])
        for env in variant_envs:
            if env["name"] == scenario["environment"]:
                env["workload"] = {**env["workload"], **scenario["overrides"]}
                env["defaulted"] = sorted(set(env["defaulted"]) - set(scenario["overrides"]))
        variant = _size_all(variant_envs)
        variant_total = sum(v["total_monthly"] for v in variant.values())
        scenarios.append({
            "name": scenario["name"],
            "description": scenario["description"],
            "environment": scenario["environment"],
            "overrides": scenario["overrides"],
            "result": variant[scenario["environment"]],
            "estate_total_monthly": variant_total,
            "comparison": compare(sized, variant, scenario["environment"], estate_total, variant_total),
        })

    multi = any(e["multi_dc"] for e in envs)
    questions = [{"input": key, "question": QUESTION_FOR_DEFAULT[key]} for key in norm["defaulted"]
                 if key in QUESTION_FOR_DEFAULT and (key != "multi_dc_analytics" or multi)
                 and (key != "peak_duration_seconds" or any(e["workload"]["analytics"] == "enabled" for e in envs))]
    if any(e.get("traffic_assumed") for e in norm["environments"]):
        questions.append({"input": "non_production_traffic", "question": "What load do non-production environments carry, including performance testing? A functional-test load was assumed."})

    uses_unpublished = sorted({p["cloud_name"] for e in envs for p in e["planes"] if p["gateways"]["rate_source"] == "assumed" and p["cloud"] != "aws"})
    return {
        "schema_version": 2,
        "engine_version": D.ENGINE_VERSION,
        "guidance_snapshot": D.GUIDANCE_SNAPSHOT,
        "price_snapshot": D.PRICE_SNAPSHOT,
        "price_recheck": D.PRICE_RECHECK,
        "organisation": norm["organisation"],
        "title": norm["title"],
        "date": norm["date"],
        "prepared_by": norm["prepared_by"],
        "context": norm["context"],
        "traffic": norm["traffic"],
        "workload": norm["workload"],
        "defaulted": norm["defaulted"],
        "environments": envs,
        "scenarios": scenarios,
        "estate_total_monthly": estate_total,
        "fully_priced": all(e["fully_priced"] for e in envs),
        "questions": questions,
        "benchmark_assumed_for": uses_unpublished,
        "clouds": sorted({p["cloud"] for e in envs for p in e["planes"]} | {e["control_plane"]["cloud"] for e in envs}),
    }


def compare(base: dict[str, dict], variant: dict[str, dict], name: str, base_total: int, variant_total: int) -> list[dict[str, Any]]:
    """Base against variant for one environment, plus the analytics store that holds its
    data (the host's, when the environment shares a control plane) and the estate total."""
    b, v = base[name], variant[name]
    store = b["control_plane"]["shared_with"] or name

    def redis_total(env: dict) -> int:
        return sum(p["redis"]["gb"] for p in env["planes"])

    def gateways(env: dict) -> int:
        return sum(p["gateways"]["count"] for p in env["planes"])

    def pumps(env: dict) -> int:
        return sum((p["pump"] or {}).get("instances", 0) for p in env["planes"])

    rows = [
        ("Gateways", gateways(b), gateways(v), ""),
        ("Pump instances", pumps(b), pumps(v), ""),
        ("Redis (GB, all planes)", redis_total(b), redis_total(v), "GB"),
        (f"Analytics store (GB){'' if store == name else ', ' + store}", base[store]["control_plane"]["db"]["storage_gb"],
         variant[store]["control_plane"]["db"]["storage_gb"], "GB"),
        (f"Monthly cost, {name} (USD)", b["total_monthly"], v["total_monthly"], "USD"),
        ("Monthly cost, whole estate (USD)", base_total, variant_total, "USD"),
    ]
    return [{"metric": m, "base": bv, "variant": vv, "delta": vv - bv, "unit": u} for m, bv, vv, u in rows]


if __name__ == "__main__":  # pragma: no cover - convenience only
    import json
    import sys

    print(json.dumps(size_estate(json.load(open(sys.argv[1]))), indent=2, default=str))
