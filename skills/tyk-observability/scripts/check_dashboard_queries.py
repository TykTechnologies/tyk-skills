#!/usr/bin/env python3
"""Run every PromQL query in a Grafana dashboard against Prometheus and report the result.

A panel that returns no series usually means a metric or label name does not match what
the Gateway exports (for example a scrape without honor_labels, or custom api_metrics that
replaced the defaults). Python standard library only.

Usage:
  python3 check_dashboard_queries.py DASHBOARD.json --prometheus http://127.0.0.1:9090
  python3 check_dashboard_queries.py DASHBOARD.json --prometheus URL --var job=tyk-gateway

Exit status: 0 when every query returned at least one series, 1 otherwise, 2 on usage or
connection errors.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_VARS = {"__rate_interval": "1m", "__interval": "1m", "__range": "1h"}


def substitute(expr: str, variables: dict[str, str]) -> str:
    """Replace Grafana variables ($name and ${name}); unknown ones become '.*'."""
    out = expr
    # Longest names first so $api does not clobber $api_id.
    for name in sorted(variables, key=len, reverse=True):
        out = out.replace("${" + name + "}", variables[name]).replace("$" + name, variables[name])
    result = []
    i = 0
    while i < len(out):
        if out[i] == "$":
            j = i + 1
            if j < len(out) and out[j] == "{":
                k = out.find("}", j)
                if k != -1:
                    result.append(".*")
                    i = k + 1
                    continue
            while j < len(out) and (out[j].isalnum() or out[j] == "_"):
                j += 1
            if j > i + 1:
                result.append(".*")
                i = j
                continue
        result.append(out[i])
        i += 1
    return "".join(result)


def iter_queries(dashboard: dict):
    """Yield (panel id, panel title, expr) for every Prometheus target, rows included."""
    panels = list(dashboard.get("panels", []))
    while panels:
        panel = panels.pop(0)
        panels.extend(panel.get("panels", []))
        for target in panel.get("targets", []):
            expr = target.get("expr")
            if expr:
                yield panel.get("id"), panel.get("title", ""), expr


def query(prometheus: str, expr: str, timeout: float = 10.0) -> int:
    url = prometheus.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        raise RuntimeError(body.get("error", "query failed"))
    return len(body["data"]["result"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dashboard", help="Grafana dashboard JSON file")
    parser.add_argument("--prometheus", required=True, help="Prometheus base URL")
    parser.add_argument("--var", action="append", default=[], help="name=value for a dashboard variable")
    args = parser.parse_args(argv)

    variables = dict(DEFAULT_VARS)
    for item in args.var:
        if "=" not in item:
            parser.error(f"--var needs name=value, got {item!r}")
        name, value = item.split("=", 1)
        variables[name] = value

    try:
        with open(args.dashboard, encoding="utf-8") as fh:
            dashboard = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read dashboard: {exc}", file=sys.stderr)
        return 2
    dashboard = dashboard.get("dashboard", dashboard)

    empty = 0
    for panel_id, title, expr in iter_queries(dashboard):
        resolved = substitute(expr, variables)
        try:
            count = query(args.prometheus, resolved)
        except (urllib.error.URLError, RuntimeError, OSError) as exc:
            print(f"cannot query Prometheus: {exc}", file=sys.stderr)
            return 2
        status = "ok   " if count else "EMPTY"
        if not count:
            empty += 1
        print(f"{status} panel {panel_id} {title!r}: {count} series")
    return 1 if empty else 0


if __name__ == "__main__":
    sys.exit(main())
