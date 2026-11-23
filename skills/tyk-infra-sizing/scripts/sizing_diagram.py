#!/usr/bin/env python3
"""Draw a sized Tyk environment as an editable, Tyk-styled .excalidraw diagram.

Input is the plain-dict result of sizing_engine.size_estate(brief). Every figure on the
diagram (gateway counts, vCPU, Redis GB and nodes, Pump instances, analytics storage,
traffic) is read from that result; nothing is estimated here.

    python3 sizing_diagram.py result.json out.excalidraw [--environment NAME]

Layout:

* Multi-plane environments (MDCB): the control plane sits in a left column; data planes
  form a grid to its right (one column up to two planes, two columns beyond). Each data
  plane dials OUT to MDCB: a green dashed route leaves its gateway group, joins the bus
  in the gutter above its row and runs along a shared vertical channel into MDCB.
* Single-plane environments: one zone holding the traffic lane and the control plane.
  No MDCB and no separate control-plane Redis; the Dashboard shares the plane's Redis.
* Shared control plane: a box naming the environment whose control plane is reused
  takes the place of the control plane.

The drawing library lives in the sibling tyk-excalidraw-diagrams skill. It is located in
the repository layout first, then through the tyk-demo-builder skill's resolve_skill_dir
helper (installed skills directories).
Render the output and look at it before shipping it:

    python3 <tyk-excalidraw-diagrams>/scripts/render_excalidraw.py out.excalidraw out.png --scale 0.2
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
LIBRARY_SKILL = "tyk-excalidraw-diagrams"

_LIBRARY: ModuleType | None = None

# ---------------------------------------------------------------------------- geometry
# Scene units. The style guide builds panels ~3000 wide with icons ~130-150.
TITLE_Y = 0
GRID_Y = 400             # top of the first row of zones
CP_X, CP_W = 0, 920      # control-plane column
CHANNEL_X = CP_X + CP_W + 150
GRID_X = CHANNEL_X + 150
ZONE_W, ZONE_H = 1820, 810
COL_GAP, ROW_GAP = 160, 170
LANE_DY = 440            # traffic lane, measured from the zone top
SINGLE_LANE_DY = 480     # single-plane zones need room for the Dashboard route
CP_BOX_W = 860           # control-plane group inside a single-plane zone
LEGEND_W = 1400
TITLE_CHARS = 56         # about 2,800 scene units at size 96

# Offsets from a data-plane zone's left edge.
X_CONSUMER, X_LB = 120, 370
X_GROUP, W_GROUP = 520, 480
X_REDIS, X_PUMP, X_SERVICES = 1230, 1400, 1660
X_EXIT = 950             # where the control route leaves the gateway group

DARK_GREEN = "#2b8a3e"
SUBTLE = "#495057"


# ---------------------------------------------------------------------------- library
def locate_library_dir() -> Path | None:
    """Return the tyk-excalidraw-diagrams scripts directory, or None when absent."""
    candidates: list[Path] = [SKILL_ROOT.parent / LIBRARY_SKILL]
    helper = SKILL_ROOT.parent / "tyk-demo-builder" / "scripts" / "resolve_skill_dir.py"
    if helper.is_file():
        spec = importlib.util.spec_from_file_location("_tyk_resolve_skill_dir", helper)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            resolved = module.resolve(LIBRARY_SKILL)
            if resolved is not None:
                candidates.append(Path(resolved))
    for skill_dir in candidates:
        scripts = skill_dir / "scripts"
        if (scripts / "tyk_excalidraw.py").is_file():
            return scripts.resolve()
    return None


def load_library() -> ModuleType | None:
    """Import tyk_excalidraw from the sibling skill, or return None when it is missing."""
    global _LIBRARY
    if _LIBRARY is None:
        scripts = locate_library_dir()
        if scripts is None:
            return None
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import tyk_excalidraw  # noqa: E402  (path resolved at runtime)

        _LIBRARY = tyk_excalidraw
    return _LIBRARY


def _require_library() -> ModuleType:
    lib = load_library()
    if lib is None:
        raise RuntimeError(
            f"Could not locate the {LIBRARY_SKILL} skill. Install the bundled skills with "
            "./scripts/install-skill.sh, or keep this skill next to it in the repository.")
    return lib


# ---------------------------------------------------------------------------- formatting
def fmt(value: float) -> str:
    """Round half up and group thousands, matching the sizing engine's reports."""
    return f"{int(math.floor(value + 0.5)):,}"


def gb(value: float) -> str:
    if value >= 100:
        return f"{fmt(value)} GB"
    return f"{max(0.1, math.floor(value * 10 + 0.5) / 10):g} GB"


def pct(share: float) -> str:
    value = share * 100
    return f"{value:.0f}%" if abs(value - round(value)) < 0.05 else f"{value:.1f}%"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def short_shape(shape: str) -> str:
    """'c5.xlarge (4 vCPU / 8 GB, the benchmark family)' -> 'c5.xlarge (4 vCPU / 8 GB)'."""
    base, _, rest = shape.partition(" (")
    if not rest:
        return shape
    return f"{base} ({rest.rstrip(')').split(',')[0]})"


def self_hosted(cloud: str) -> bool:
    return cloud in ("on-prem", "private-cloud")


def select_environment(result: dict[str, Any], environment: str | None) -> dict[str, Any]:
    envs = result.get("environments") or []
    if not envs:
        raise ValueError("the sizing result has no environments")
    if environment is None:
        return next((e for e in envs if e.get("posture") == "production"), envs[0])
    for env in envs:
        if env["name"] == environment:
            return env
    raise ValueError(f"unknown environment {environment!r}; choose one of "
                     f"{', '.join(e['name'] for e in envs)}")


# ---------------------------------------------------------------------------- drawing
class _Builder:
    def __init__(self, lib: ModuleType, result: dict[str, Any], env: dict[str, Any]):
        self.lib = lib
        self.d = lib.Diagram()
        self.result = result
        self.env = env
        self.planes = env["planes"]
        self.cp = env["control_plane"]
        self.shared = self.cp["mode"] == "shared"
        self.multi = bool(env["multi_dc"])
        self.has_pump = any(p.get("pump") for p in self.planes)
        self.width = 0.0
        self.bottom = 0.0

    # -------------------------------------------------------------- header
    def header(self) -> None:
        organisation = (self.result.get("organisation") or "").strip()
        name = self.env["name"]
        title = f"{organisation} - {name} topology" if organisation else f"{name} topology"
        traffic = self.env["traffic"]
        n = len(self.planes)
        resilience = "HA" if self.env["resilience"] == "ha" else self.env["resilience"]
        subtitle = (f"Peak {fmt(traffic['peak_rps'])} RPS / average {fmt(traffic['avg_rps'])} RPS"
                    f"  -  {plural(n, 'data plane')}  -  {self.env['posture']}, {resilience}")
        # Long names wrap (and shrink) rather than stretch the canvas, which would shrink
        # every other label when the diagram is fitted to a page.
        size, lines = 96, textwrap.wrap(title, TITLE_CHARS) or [title]
        if len(lines) > 2:
            size, lines = 72, textwrap.wrap(title, int(TITLE_CHARS * 96 / 72))[:2]
        self.d.text(0, TITLE_Y, "\n".join(lines), size=size)
        self.d.text(0, TITLE_Y + len(lines) * size * 1.25 + 10, subtitle, size=38, color=SUBTLE)

    # -------------------------------------------------------------- one data plane
    def plane_zone(self, plane: dict[str, Any], x: float, y: float, w: float, h: float,
                   lane_dy: float, show_share: bool) -> dict[str, float]:
        """Draw a data-plane zone; return anchor points other routes attach to."""
        lib, d = self.lib, self.d
        onprem = self_hosted(plane["cloud"])
        kind = lib.TRUSTED if onprem else lib.INTERNAL
        where = f"{plane['cloud_name']} - {plane['k8s_name']}"
        facts = (f"{pct(plane['share'])} of traffic - " if show_share else "") + \
            f"{fmt(plane['peak_rps'])} peak RPS"
        subtitle = f"{where} - {facts}"
        if 24 + len(subtitle) * 26 * 0.52 > X_EXIT - 40:
            subtitle = f"{where}\n{facts}"
        d.zone(x, y, w, h, plane["label"], subtitle, kind=kind)

        lane = y + lane_dy
        gw_icon = "gateway-teal" if onprem else "tyk-gateway"
        redis_icon = "redis-gold" if onprem else "redis"
        pump_icon = "pump-teal" if onprem else "tyk-pump"

        d.icon("api-consumer", x + X_CONSUMER, lane, 120, "API consumers")
        d.icon("load-balancer", x + X_LB, lane, 120, "Load balancer")

        g = plane["gateways"]
        gx, gtop = x + X_GROUP, lane - 170
        d.group_box(gx, gtop, W_GROUP, 340, f"{g['count']} x Tyk Gateway")
        shown = min(g["count"], 3)
        step = 150
        first = gx + W_GROUP / 2 - step * (shown - 1) / 2
        for i in range(shown):
            d.icon(gw_icon, first + i * step, lane + 5, 110)
        d.text(gx + W_GROUP / 2, lane + 108, f"{g['vcpu']} vCPU / {g['ram_gb']:g} GB each",
               size=25, color=SUBTLE, center=True)

        r = plane["redis"]
        redis_y = lane - 210
        nodes = plural(r["nodes"], "node")
        d.icon(redis_icon, x + X_REDIS, redis_y, 120, f"Redis {gb(r['gb'])} needed",
               sub=nodes if r.get("shards", 1) == 1 else f"{r['shards']} shards x {nodes}", label_above=True)

        d.icon("services", x + X_SERVICES, lane, 130, "Upstream services")

        d.arrow([(x + X_CONSUMER + 70, lane), (x + X_LB - 70, lane)], lib.BLUE)
        d.arrow([(x + X_LB + 70, lane), (gx - 10, lane)], lib.BLUE)
        d.arrow([(gx + W_GROUP + 10, lane), (x + X_SERVICES - 75, lane)], lib.BLUE)
        d.arrow([(gx + W_GROUP + 5, lane - 120), (x + X_REDIS - 65, redis_y + 10)], lib.PURPLE)

        pump = plane.get("pump")
        pump_y = lane + 200
        if pump:
            d.icon(pump_icon, x + X_PUMP, pump_y, 110, "Tyk Pump",
                   sub=plural(pump["instances"], "instance") + self.pump_destination(plane), lsize=26)
            d.arrow([(x + X_REDIS + 60, redis_y + 62), (x + X_PUMP - 40, pump_y - 62)],
                    lib.TEAL, style="dotted", sw=3)
        return {"exit_x": x + X_EXIT, "group_top": gtop, "redis_x": x + X_REDIS,
                "redis_y": redis_y, "pump_x": x + X_PUMP, "pump_y": pump_y,
                "lane": lane, "right": x + w}

    def pump_destination(self, plane: dict[str, Any]) -> str:
        """Where this plane's Pump sends analytics, for planes that are MDCB workers."""
        if not (self.multi or self.shared) or plane.get("hosts_control_plane"):
            return ""
        mode = self.env["workload"].get("multi_dc_analytics", "hybrid-aggregate")
        return {"control-plane": "\nrecords to MDCB", "hybrid-aggregate": "\naggregates to MDCB",
                "edge-local": "\nlocal sink only"}.get(mode, "")

    # -------------------------------------------------------------- control plane (MDCB)
    def control_plane_zone(self, x: float, y: float) -> tuple[float, float, float]:
        """Draw the multi-plane control plane. Returns (mdcb_right_x, mdcb_y, bottom)."""
        lib, d, cp = self.lib, self.d, self.cp
        rows = [y + 300, y + 640, y + 980]
        left, right = x + 230, x + 660
        h = 1150
        where = f"in {cp['location_label']}" if cp.get("remote") else f"hosted with {cp['location_label']}"
        subtitle = (f"{where} - {cp['cloud_name']}\n"
                    f"{cp['nodes']} x {short_shape(cp['node_shape'])}")
        onprem = self_hosted(cp["cloud"])
        d.zone(x, y, CP_W, h, "Control plane", subtitle, kind=lib.CLOUD, op=30)

        comps = cp["components"]
        slots = {
            "Tyk Dashboard": ("dashboard-teal" if onprem else "tyk-dashboard", left, rows[0], "Tyk Dashboard", None),
            "Tyk MDCB": ("mdcb", right, rows[0], "Tyk MDCB", None),
            "Tyk Developer Portal": ("developer-portal", left, rows[1], "Developer Portal",
                                     cp["portal"]["mode"]),
            "Management gateway": ("gateway-teal" if onprem else "tyk-gateway", right, rows[1],
                                   "Management gateway", None),
        }
        for comp in comps:
            icon, cx, cy, label, sub = slots[comp]
            d.icon(icon, cx, cy, 130, label, sub=sub)
        db = cp["db"]
        engine = "MongoDB" if db["engine"] == "mongodb" else "PostgreSQL"
        db_icon = "mongodb" if db["engine"] == "mongodb" else "postgres"
        d.icon(db_icon, left, rows[2], 130, engine, sub=f"{gb(db['storage_gb'])} storage")
        d.icon("redis-gold" if onprem else "redis", right, rows[2], 130, "Control-plane Redis",
               sub=gb(cp["cp_redis_gb"]))
        if self.has_pump and self.env["workload"].get("multi_dc_analytics") != "edge-local":
            # Forwarded analytics: MDCB writes them into the control plane's store.
            d.arrow([(right - 60, rows[0] + 60), (left + 60, rows[2] - 60)], lib.TEAL, style="dotted", sw=3)
        return right + 80, rows[0], y + h

    def shared_box(self, x: float, y: float) -> tuple[float, float, float]:
        """A stand-in for a reused control plane. Returns (right_x, anchor_y, bottom)."""
        cp = self.cp
        h = 300
        body = (f"Uses the {cp['shared_with']} control plane.\n"
                "No Dashboard, MDCB or analytics store\n"
                "is deployed for this environment.\n"
                f"Adds about {gb(cp['db']['storage_gb'])} to its analytics store.")
        self.d.note(x, y, CP_W, h, "Shared control plane", body, tone="note")
        return x + CP_W + 10, y + h / 2, y + h

    # -------------------------------------------------------------- routes
    def route_out(self, anchor: dict[str, float], bus_y: float, target_x: float,
                  target_y: float) -> None:
        """Green dashed route from a data plane OUT to the control plane via the channel."""
        pts = [(anchor["exit_x"], anchor["group_top"]), (anchor["exit_x"], bus_y),
               (CHANNEL_X, bus_y)]
        if abs(target_y - bus_y) >= 1:
            pts.append((CHANNEL_X, target_y))
        pts.append((target_x, target_y))
        self.d.arrow(pts, self.lib.GREEN, style="dashed")

    # -------------------------------------------------------------- layouts
    def draw_multi(self) -> None:
        n = len(self.planes)
        cols = 1 if n <= 2 else 2
        rows = math.ceil(n / cols)
        if self.shared:
            target_x, target_y, left_bottom = self.shared_box(CP_X, GRID_Y + 150)
        else:
            target_x, target_y, left_bottom = self.control_plane_zone(CP_X, GRID_Y)
        for i, plane in enumerate(self.planes):
            r, c = divmod(i, cols)
            x = GRID_X + c * (ZONE_W + COL_GAP)
            y = GRID_Y + r * (ZONE_H + ROW_GAP)
            anchor = self.plane_zone(plane, x, y, ZONE_W, ZONE_H, LANE_DY, show_share=True)
            self.route_out(anchor, y - ROW_GAP / 2, target_x, target_y)
        grid_bottom = GRID_Y + rows * ZONE_H + (rows - 1) * ROW_GAP
        what = f"the {self.cp['shared_with']} control plane" if self.shared else "MDCB"
        caption = (f"Green dashed: each data plane dials OUT to {what}\n"
                   "over outbound RPC connections (MDCB port 9091\n"
                   "by default); enable TLS on them. The control\n"
                   "plane needs no inbound path into a data plane;\n"
                   "API consumer traffic still enters each one.\n\n"
                   "Quota and rate-limit counters are kept per\n"
                   "plane, never shared: route each client to one plane.")
        self.d.text(CP_X, left_bottom + 50, caption, size=26, color=DARK_GREEN)
        left_bottom += 50 + 26 * 1.25 * caption.count("\n") + 40
        self.width = GRID_X + cols * ZONE_W + (cols - 1) * COL_GAP
        self.bottom = max(grid_bottom, left_bottom)

    def draw_single(self) -> None:
        plane = self.planes[0]
        lib, d, cp = self.lib, self.d, self.cp
        h = ZONE_H + (SINGLE_LANE_DY - LANE_DY)
        if self.shared:
            target_x, target_y, left_bottom = self.shared_box(CP_X, GRID_Y + 150)
            anchor = self.plane_zone(plane, GRID_X, GRID_Y, ZONE_W, h, SINGLE_LANE_DY,
                                     show_share=False)
            self.route_out(anchor, GRID_Y - ROW_GAP / 2, target_x, target_y)
            self.width = GRID_X + ZONE_W
            self.bottom = max(GRID_Y + h, left_bottom)
            return

        # Dedicated control plane inside the same zone: no MDCB, one shared Redis.
        x, y = 0, GRID_Y
        w = ZONE_W + CP_BOX_W + 40
        anchor = self.plane_zone(plane, x, y, w, h, SINGLE_LANE_DY, show_share=False)
        lane = anchor["lane"]
        bx, btop = x + ZONE_W, y + 150
        d.group_box(bx, btop, CP_BOX_W, h - 180, "Control plane")
        onprem = self_hosted(cp["cloud"])
        dash_x, dash_y = bx + 230, anchor["redis_y"]
        d.icon("dashboard-teal" if onprem else "tyk-dashboard", dash_x, dash_y, 130, "Tyk Dashboard",
               sub=f"{cp['nodes']} x {short_shape(cp['node_shape'])}")
        if "Tyk Developer Portal" in cp["components"]:
            d.icon("developer-portal", bx + 620, dash_y, 130, "Developer Portal",
                   sub=cp["portal"]["mode"])
        db = cp["db"]
        engine = "MongoDB" if db["engine"] == "mongodb" else "PostgreSQL"
        db_icon = "mongodb" if db["engine"] == "mongodb" else "postgres"
        db_y = anchor["pump_y"] - 30
        d.icon(db_icon, dash_x, db_y, 130, engine, sub=f"{gb(db['storage_gb'])} storage")
        if "Tyk MDCB" in cp["components"]:
            # This control plane also serves other environments' planes through MDCB.
            guests = ", ".join(self.env.get("guests", [])) or "other environments"
            d.icon("mdcb", bx + 620, db_y, 130, "Tyk MDCB", sub=f"serves {guests}")

        # Dashboard shares the plane's Redis; gateways load APIs and policies from it.
        d.arrow([(dash_x - 80, dash_y), (anchor["redis_x"] + 70, dash_y)], lib.PURPLE)
        route_y = y + 130
        d.arrow([(anchor["exit_x"], anchor["group_top"]), (anchor["exit_x"], route_y),
                 (dash_x + 40, route_y), (dash_x + 40, dash_y - 75)], lib.GREEN, style="dashed")
        if plane.get("pump"):
            d.arrow([(anchor["pump_x"] + 65, anchor["pump_y"]), (dash_x - 80, db_y)], lib.TEAL,
                    style="dotted", sw=3)
        self.width = w
        self.bottom = y + h

    # -------------------------------------------------------------- legend and notes
    def legend_and_notes(self) -> None:
        lib, d = self.lib, self.d
        y = self.bottom + 150
        if self.multi or self.shared:
            control = "Control flow: each data plane dials out to the control plane"
        else:
            control = "Control flow: gateways load APIs and policies from the Dashboard"
        redis = "Redis: keys, sessions, quota counters, analytics buffer"
        if not self.multi and not self.shared:
            redis += " (shared with the Dashboard)"
        entries = [(lib.BLUE, "solid", "Traffic: consumers -> load balancer -> gateways -> services"),
                   (lib.GREEN, "dashed", control),
                   (lib.PURPLE, "solid", redis)]
        if self.has_pump:
            entries.append((lib.TEAL, "dotted", "Analytics: Pump moves records out of Redis" +
                            ("; MDCB writes forwarded analytics to the store" if self.multi and not self.shared else "")))
        width = max(self.width, LEGEND_W + 80 + 1500)
        d.legend(0, y, entries, w=LEGEND_W)
        legend_bottom = y + 76 + len(entries) * 48

        nx, nw = LEGEND_W + 80, width - LEGEND_W - 80
        ny = y
        info_lines = self.summary_lines()
        ny = self.note(nx, ny, nw, f"{self.env['name']}: sizing summary", info_lines, "info")
        shortfall = self.shortfall_lines()
        if shortfall:
            ny = self.note(nx, ny + 40, nw, "N-1 capacity shortfall", shortfall, "bad")
        self.bottom = max(legend_bottom, ny)

    def note(self, x: float, y: float, w: float, title: str, lines: list[str], tone: str) -> float:
        chars = max(40, int((w - 56) / (28 * 0.52)))
        wrapped: list[str] = []
        for line in lines:
            wrapped.extend(textwrap.wrap(line, chars, subsequent_indent="   ") or [""])
        h = 24 + 34 * 1.5 + len(wrapped) * 28 * 1.25 + 40
        self.d.note(x, y, w, h, title, "\n".join(wrapped), tone=tone)
        return y + h

    def summary_lines(self) -> list[str]:
        env, cp, planes = self.env, self.cp, self.planes
        lines = []
        total = sum(p["gateways"]["count"] for p in planes)
        if self.multi:
            per = ", ".join(f"{p['label']} {p['gateways']['count']} x {p['gateways']['vcpu']} vCPU"
                            for p in planes)
            lines.append(f"Gateways: {total} in total ({per}).")
            redis = ", ".join(f"{p['label']} {gb(p['redis']['gb'])} x {plural(p['redis']['nodes'], 'node')}"
                              for p in planes)
            kind = "persistent" if any(p["redis"]["persistent"] for p in planes) else "ephemeral"
            lines.append(f"Data-plane Redis ({kind}): {redis}.")
        else:
            p = planes[0]
            g = p["gateways"]
            lines.append(f"Gateways: {g['count']} x {short_shape(g['shape'])}, planned capacity "
                         f"{fmt(g['planned_capacity_rps'])} RPS against a {fmt(p['peak_rps'])} RPS peak.")
            lines.append(f"Redis: {gb(p['redis']['gb'])} needed, provisioned as {p['redis']['tier']}." if p["redis"]["tier"]
                         else f"Redis: {gb(p['redis']['gb'])} needed.")
        if not self.shared:
            if self.multi:
                lines.append(f"Control-plane Redis (persistent): {gb(cp['cp_redis_gb'])}.")
            lines.append(f"Analytics store: {cp['db']['line']}.")
        else:
            lines.append(f"Control plane and analytics store: {cp['db']['line']}.")
        pumps = [p["pump"]["instances"] for p in planes if p.get("pump")]
        if pumps:
            lines.append(f"Tyk Pump: {plural(sum(pumps), 'instance')}"
                         + (" across the data planes." if self.multi else "."))
        else:
            lines.append("Analytics disabled: no Tyk Pump is deployed.")
        return lines

    def shortfall_lines(self) -> list[str]:
        if self.env["posture"] != "production":
            return []
        lines = []
        for p in self.planes:
            g = p["gateways"]
            if g["n1_ok"] is False:
                where = f"{p['label']}: " if self.multi else ""
                lines.append(f"{where}losing one of {g['count']} gateways at peak leaves "
                             f"{fmt(g['n1_capacity_rps'])} RPS of planned capacity against a "
                             f"{fmt(p['peak_rps'])} RPS peak.")
        return lines

    def build(self) -> Any:
        self.header()
        if self.multi:
            self.draw_multi()
        else:
            self.draw_single()
        self.legend_and_notes()
        return self.d


def build_diagram(result: dict[str, Any], out_path: str, environment: str | None = None) -> str:
    """Draw one environment of a sizing result and save it as an .excalidraw file.

    `environment` defaults to the first production environment, else the first one.
    Returns the path written. Raises ValueError for an unknown environment and
    RuntimeError when the tyk-excalidraw-diagrams skill cannot be located.
    """
    lib = _require_library()
    env = select_environment(result, environment)
    diagram = _Builder(lib, result, env).build()
    return diagram.save(str(out_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw a Tyk sizing result as an .excalidraw diagram.")
    parser.add_argument("result", help="JSON produced by sizing_engine.size_estate")
    parser.add_argument("out", help="output .excalidraw path")
    parser.add_argument("--environment", help="environment name (default: first production environment)")
    args = parser.parse_args(argv)
    with open(args.result) as fh:
        result = json.load(fh)
    try:
        build_diagram(result, args.out, args.environment)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
