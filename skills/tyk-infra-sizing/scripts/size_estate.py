#!/usr/bin/env python3
"""Size a Tyk estate from a brief and print a Markdown summary.

Fast iteration loop while settling the inputs: change the brief, rerun, read the
summary. build_sizing_pack.py produces the full sizing pack from the same brief.

Usage:
    python3 size_estate.py brief.json [--json result.json] [--markdown summary.md]

Briefs may be JSON, or YAML when PyYAML is installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sizing_engine as E  # noqa: E402
from sizing_engine import fmt, gb_text  # noqa: E402


def load_brief(path: str | Path) -> dict:
    """Read a JSON (or, with PyYAML, YAML) brief. Unreadable or malformed files raise
    BriefError with one line saying what is wrong, never a traceback."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise E.BriefError(f"cannot read the brief {path}: {exc.strerror or exc}") from exc
    if str(path).endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on the interpreter
            raise SystemExit("This brief is YAML but PyYAML is not installed. Use JSON, or install PyYAML.") from exc
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise E.BriefError(f"{path} is not valid YAML: {' '.join(str(exc).split())}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise E.BriefError(f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}") from exc


def money(value: float) -> str:
    return f"${fmt(value)}"


def markdown(result: dict) -> str:
    out: list[str] = []
    title = result.get("organisation") or "Tyk estate"
    t = result["traffic"]
    out.append(f"# {title}: infrastructure sizing")
    out.append("")
    out.append(f"Traffic: {fmt(t['peak_rps'])} peak / {fmt(t['avg_rps'])} average RPS. " + " ".join(f"{d}." for d in t["derivation"]))
    for env in result["environments"]:
        out.append("")
        out.append(f"## {env['name']} ({env['posture']}, {env['resilience']})")
        out.append("")
        out.append("| Data plane | Hosting | Peak RPS | Gateways | Redis | Pump | N-1 |")
        out.append("| --- | --- | --- | --- | --- | --- | --- |")
        for p in env["planes"]:
            g, r = p["gateways"], p["redis"]
            if g["count"] == 1:
                n1 = "**fails**: single instance" if env["posture"] == "production" else "n/a (single instance)"
            else:
                n1 = "holds" if g["n1_ok"] else "**fails**"
            pump = str(p["pump"]["instances"]) if p["pump"] else "none"
            out.append(f"| {p['label']} | {p['cloud_name']}, {p['target']} | {fmt(p['peak_rps'])} | {g['count']} x {g['shape']} | {gb_text(r['gb'])}: {r['tier']} | {pump} | {n1} |")
        cp = env["control_plane"]
        if cp["mode"] == "shared":
            out.append(f"\nControl plane: shared with {cp['shared_with']}.")
        else:
            out.append(f"\nControl plane: {cp['nodes']} x {cp['node_shape']} ({', '.join(cp['components'])}). Analytics store: {cp['db']['line']}.")
            if cp["mdcb"]:
                out.append(f"Control-plane Redis: {gb_text(cp['cp_redis_gb'])}, persistent.")
        if env["cost_rows"]:
            out.append("")
            out.append("| Item | Shape | USD/month |")
            out.append("| --- | --- | --- |")
            for row in env["cost_rows"]:
                out.append(f"| {row['item']} | {row['shape']} | {fmt(row['monthly'])} |")
            out.append(f"| **Total** | | **{money(env['total_monthly'])}** |")
        if env["unpriced_items"]:
            out.append(f"\nNot priced (self-hosted): {', '.join(env['unpriced_items'])}.")
        if env["notes"]:
            out.append("")
            for note in env["notes"]:
                out.append(f"- **{note['tone'].capitalize()}:** {note['text']}")
    for s in result["scenarios"]:
        out.append("")
        out.append(f"## Scenario: {s['name']} ({s['environment']})")
        out.append("")
        out.append("| Metric | Base | Scenario | Change |")
        out.append("| --- | --- | --- | --- |")
        for c in s["comparison"]:
            out.append(f"| {c['metric']} | {fmt(c['base'])} | {fmt(c['variant'])} | {c['delta']:+,} |")
    out.append("")
    out.append(f"Estate total (priced items, approximate list prices): **{money(result['estate_total_monthly'])} per month**. "
               f"Price snapshot {result['price_snapshot']}; {result.get('price_recheck', '')}.")
    if result["questions"]:
        out.append("")
        out.append("## Questions to confirm")
        out.append("")
        for i, q in enumerate(result["questions"], 1):
            out.append(f"{i}. {q['question']}")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("brief")
    parser.add_argument("--json", help="write the full result JSON here")
    parser.add_argument("--markdown", help="write the Markdown summary here instead of stdout")
    args = parser.parse_args()
    try:
        result = E.size_estate(load_brief(args.brief))
    except E.BriefError as exc:
        print(f"brief error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, default=str))
    summary = markdown(result)
    if args.markdown:
        Path(args.markdown).write_text(summary)
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
