#!/usr/bin/env python3
"""Gate an OpenAPI or Tyk OAS file on Tyk Governance rulesets, or export rulesets for offline lint.

check   Post the file to the Dashboard's sandbox endpoint (nothing is saved) and turn the result
        into an exit code a pipeline can trust.
export  Write the definition of each named ruleset to a file, so an open source linter can run
        the same rules without Dashboard access.
apply   Create a ruleset from a rules file, or replace it if the ID exists, always sending every
        field (a PUT that omits "active" deactivates the ruleset), then read it back and check.

Environment: TYK_DASHBOARD_URL (for example https://dashboard.example.com) and
TYK_DASHBOARD_TOKEN (a Dashboard user's API access key), the names the public CI page uses.
When they are unset, DASH and DASHBOARD_API_KEY (the names the other Tyk skills use) are read.
Python standard library only.

Exit codes for check:
  0  passed for the chosen strictness
  1  warnings only, and --strictness strict
  2  at least one error-severity issue (non-compliant)
  3  could not evaluate: HTTP or network failure, unreadable file, unparsable or truncated
     response, no ruleset matched, or (with --honor-action) no gating ruleset matched
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PASS, WARN_ONLY, NON_COMPLIANT, CANNOT_EVALUATE = 0, 1, 2, 3
MAX_IDS = 20


class GateError(Exception):
    """A condition that makes the result untrustworthy."""


def content_type_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix in (".yaml", ".yml"):
        return "application/yaml"
    raise GateError(f"unrecognised file extension on {path} (expected .json, .yaml or .yml)")


def split_ids(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise GateError("the list of categories or ruleset IDs is empty")
    if len(items) > MAX_IDS:
        raise GateError(f"{len(items)} values given; the sandbox endpoint accepts at most {MAX_IDS}")
    return items


def build_query(categories: str | None, ruleset_ids: str | None, api_id: str | None, api_name: str | None) -> str:
    if bool(categories) == bool(ruleset_ids):
        raise GateError("give exactly one of --categories or --ruleset-ids")
    params: list[tuple[str, str]] = []
    if categories:
        params.append(("categories", ",".join(split_ids(categories))))
    else:
        params.append(("ruleset_ids", ",".join(split_ids(ruleset_ids or ""))))
    if api_id:
        params.append(("api_id", api_id))
    if api_name:
        params.append(("api_name", api_name))
    return urllib.parse.urlencode(params, safe=",")


def http(method: str, url: str, token: str, body: bytes | None = None, ctype: str | None = None, timeout: float = 60.0):
    headers = {"Authorization": token}
    if ctype:
        headers["Content-Type"] = ctype
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GateError(f"cannot reach the Dashboard at {url.split('/api/')[0]}: {error}") from error


def evaluate(result: dict, strictness: str, honor_action: bool) -> tuple[int, list[str]]:
    """Map a sandbox response body to (exit code, report lines). Pure: no I/O."""
    if not isinstance(result, dict) or not isinstance(result.get("rulesets"), list):
        raise GateError("the response has no 'rulesets' list")
    if result.get("error"):
        raise GateError(f"the Dashboard reported an evaluation error: {result['error']}")
    if result.get("truncated") is True:
        raise GateError("the issue list was truncated; narrow the rulesets so every issue is visible")
    rulesets = result["rulesets"]
    if not rulesets:
        raise GateError(
            "no ruleset matched. An unknown or misspelt category returns 200 with an empty list; "
            "check the category or ruleset IDs"
        )
    gating = [r for r in rulesets if not honor_action or r.get("action", "none") != "none"]
    if not gating:
        raise GateError("every matched ruleset has action 'none' and --honor-action is set; nothing gates this build")
    gating_ids = {r.get("id") for r in gating}

    lines = []
    for r in rulesets:
        mark = "" if r.get("id") in gating_ids else "  (not gating: action none)"
        lines.append(
            f"ruleset {r.get('id')} '{r.get('name')}' action={r.get('action')}: "
            f"{r.get('error_count', 0)} error(s), {r.get('warn_count', 0)} warning(s), "
            f"{r.get('info_count', 0)} info{mark}"
        )
    order = {"error": 0, "warn": 1, "info": 2, "hint": 3}
    for issue in sorted(result.get("issues") or [], key=lambda i: (order.get(i.get("severity"), 9), i.get("rule_id", ""))):
        lines.append(f"  {issue.get('severity', '?'):5} {issue.get('ruleset_id')} {issue.get('rule_id')} at {issue.get('path')}: {issue.get('message')}")
        if issue.get("how_to_fix"):
            lines.append(f"        fix: {issue['how_to_fix']}")

    errors = sum(int(r.get("error_count", 0) or 0) for r in gating)
    warns = sum(int(r.get("warn_count", 0) or 0) for r in gating)
    if errors:
        verdict = NON_COMPLIANT
        lines.append(f"Result: non-compliant ({errors} error(s) in gating rulesets).")
    elif warns:
        verdict = WARN_ONLY
        lines.append(f"Result: compliant, with {warns} warning(s).")
    else:
        verdict = PASS
        lines.append("Result: compliant.")

    if strictness == "strict":
        code = verdict
    elif strictness == "standard":
        code = NON_COMPLIANT if verdict == NON_COMPLIANT else PASS
    elif strictness == "permissive":
        code = PASS
    else:
        raise GateError(f"unknown strictness {strictness!r}")
    return code, lines


def dashboard_env() -> tuple[str, str]:
    url = (os.environ.get("TYK_DASHBOARD_URL") or os.environ.get("DASH") or "").rstrip("/")
    token = os.environ.get("TYK_DASHBOARD_TOKEN") or os.environ.get("DASHBOARD_API_KEY") or ""
    if not url or not token:
        raise GateError("set TYK_DASHBOARD_URL and TYK_DASHBOARD_TOKEN (or DASH and DASHBOARD_API_KEY)")
    return url, token


def run_check(args: argparse.Namespace) -> int:
    ctype = content_type_for(args.spec)
    query = build_query(args.categories, args.ruleset_ids, args.api_id, args.api_name)
    try:
        body = Path(args.spec).read_bytes()
    except OSError as error:
        raise GateError(f"cannot read {args.spec}: {error}") from error
    url, token = dashboard_env()
    status, text = http("POST", f"{url}/api/evaluations/sandbox/oas?{query}", token, body, ctype)
    if status != 200:
        raise GateError(f"sandbox call returned HTTP {status}: {text.strip()[:300]}")
    try:
        result = json.loads(text)
    except json.JSONDecodeError as error:
        raise GateError(f"the response is not JSON: {error}") from error
    code, lines = evaluate(result, args.strictness, args.honor_action)
    print("\n".join(lines))
    return code


def run_export(args: argparse.Namespace) -> int:
    url, token = dashboard_env()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for ruleset_id in split_ids(args.ruleset_ids):
        status, text = http("GET", f"{url}/api/rulesets/{urllib.parse.quote(ruleset_id, safe='')}", token)
        if status != 200:
            raise GateError(f"GET /api/rulesets/{ruleset_id} returned HTTP {status}: {text.strip()[:200]}")
        definition = json.loads(text).get("definition", "")
        suffix = ".json" if definition.lstrip().startswith("{") else ".yaml"
        target = out / f"{ruleset_id}{suffix}"
        target.write_text(definition if definition.endswith("\n") else definition + "\n")
        print(f"wrote {target}")
    return PASS


def ruleset_body(args: argparse.Namespace, definition: str) -> dict:
    categories = [c.strip() for c in (args.categories or "").split(",") if c.strip()]
    return {
        "ruleset_id": args.ruleset_id,
        "name": args.name,
        "description": args.description or "",
        "active": not args.inactive,
        "resource_type": "oas-api",
        "action": args.action,
        "categories": categories,
        "definition": definition,
    }


def readback_problems(sent: dict, stored: dict) -> list[str]:
    """Fields the Dashboard did not store as sent. Pure: no I/O."""
    problems = []
    for field in ("name", "active", "action", "resource_type"):
        if stored.get(field) != sent[field]:
            problems.append(f"{field}: sent {sent[field]!r}, stored {stored.get(field)!r}")
    # The Dashboard stores the definition as compact JSON, also when YAML was sent.
    try:
        stored_rules = json.loads(stored.get("definition") or "")
    except json.JSONDecodeError:
        stored_rules = None
        problems.append("definition: the stored definition is not JSON")
    try:
        sent_rules = json.loads(sent["definition"])
    except json.JSONDecodeError:
        sent_rules = None  # YAML: the parse check above is all the standard library allows
    if sent_rules is not None and stored_rules is not None and sent_rules != stored_rules:
        problems.append("definition: the stored rules differ from the file")
    if sorted(stored.get("categories") or []) != sorted(sent["categories"]):
        problems.append(f"categories: sent {sent['categories']!r}, stored {stored.get('categories')!r}")
    return problems


def run_apply(args: argparse.Namespace) -> int:
    try:
        definition = Path(args.rules).read_text()
    except OSError as error:
        raise GateError(f"cannot read {args.rules}: {error}") from error
    url, token = dashboard_env()
    body = ruleset_body(args, definition)
    path = f"{url}/api/rulesets/{urllib.parse.quote(args.ruleset_id, safe='')}"
    status, _ = http("GET", path, token)
    if status == 200:
        status, text = http("PUT", path, token, json.dumps(body).encode(), "application/json")
        verb = "replaced"
    elif status == 404:
        status, text = http("POST", f"{url}/api/rulesets", token, json.dumps(body).encode(), "application/json")
        verb = "created"
    else:
        raise GateError(f"GET {path} returned HTTP {status}")
    if status not in (200, 201):
        raise GateError(f"ruleset write returned HTTP {status}: {text.strip()[:300]}")
    status, text = http("GET", path, token)
    if status != 200:
        raise GateError(f"read-back returned HTTP {status}: {text.strip()[:200]}")
    problems = readback_problems(body, json.loads(text))
    if problems:
        raise GateError("the stored ruleset differs from what was sent: " + "; ".join(problems))
    print(f"{verb} ruleset {args.ruleset_id}: active={body['active']} action={body['action']} categories={body['categories']}")
    return PASS


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="evaluate a spec file in the Dashboard sandbox")
    c.add_argument("spec")
    c.add_argument("--categories")
    c.add_argument("--ruleset-ids")
    c.add_argument("--api-id")
    c.add_argument("--api-name")
    c.add_argument("--strictness", choices=["strict", "standard", "permissive"], default="standard")
    c.add_argument(
        "--honor-action",
        action="store_true",
        help="count only rulesets whose action is not 'none' (the public example script's rule); "
        "by default every matched ruleset gates, as Dashboard compliance does",
    )
    e = sub.add_parser("export", help="write ruleset definitions to files for an offline linter")
    e.add_argument("--ruleset-ids", required=True)
    e.add_argument("--out", required=True)
    a = sub.add_parser("apply", help="create or replace a ruleset from a rules file, then verify it")
    a.add_argument("--ruleset-id", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--rules", required=True, help="JSON or YAML ruleset file, sent as the definition string")
    a.add_argument("--categories", default="", help="comma-separated; each must already be on some API")
    a.add_argument("--action", choices=["none", "warn"], default="none")
    a.add_argument("--description")
    a.add_argument("--inactive", action="store_true", help="store the ruleset inactive (evaluates nothing)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return {"check": run_check, "export": run_export, "apply": run_apply}[args.command](args)
    except GateError as error:
        verb = "could not evaluate" if args.command == "check" else "failed"
        print(f"governance_check {args.command}: {verb}: {error}", file=sys.stderr)
        return CANNOT_EVALUATE


if __name__ == "__main__":
    sys.exit(main())
