#!/usr/bin/env python3
"""Send a burst of requests through one or more Tyk Gateways and count the results.

The requests go out one after another, round-robin across every --url, so a limit that is
meant to be shared by several Gateways is tested through all of them. The report gives the
status sequence, the count per Gateway, how long the burst took, the first rejection body and
the rate-limit headers, and it warns when the burst ran longer than the limit's window.

Standard library only.

    burst_test.py --url http://localhost:8080/orders/anything/orders \
                  --url http://localhost:8081/orders/anything/orders \
                  --header-env 'Authorization=PARTNER_A_KEY' \
                  --count 14 --window 60 --expect-ok 10 --expect-reject 429

Exit status: 0 when every expectation holds (or none was given), 1 when one fails,
2 on a usage or connection error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, OrderedDict

RATE_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After")


def parse_header(text: str) -> tuple[str, str]:
    name, sep, value = text.partition(":")
    if not sep or not name.strip():
        raise ValueError(f"header must look like 'Name: value', got {text!r}")
    return name.strip(), value.strip()


def parse_header_env(text: str, environ=os.environ) -> tuple[str, str]:
    name, sep, variable = text.partition("=")
    if not sep or not name.strip() or not variable.strip():
        raise ValueError(f"--header-env must look like 'Name=ENV_VARIABLE', got {text!r}")
    value = environ.get(variable.strip())
    if not value:
        raise ValueError(f"environment variable {variable.strip()} is not set")
    return name.strip(), value


def send(url: str, method: str, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, response_headers, body = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, response_headers, body = error.code, error.headers, error.read()
    return {
        "url": url,
        "status": status,
        "headers": {name: response_headers[name] for name in RATE_HEADERS if response_headers.get(name) is not None},
        "body": body.decode("utf-8", "replace"),
    }


def run_burst(urls, count, method="GET", headers=None, interval=0.0, timeout=10.0, sender=send, clock=time.monotonic, sleep=time.sleep):
    """Send `count` requests round-robin over `urls`; return the list of results and the elapsed seconds."""
    results = []
    started = clock()
    for index in range(count):
        if index and interval > 0:
            sleep(interval)
        results.append(sender(urls[index % len(urls)], method, headers or {}, timeout))
    return results, clock() - started


def summarise(results, elapsed, window=None):
    statuses = [result["status"] for result in results]
    per_url: "OrderedDict[str, Counter]" = OrderedDict()
    for result in results:
        per_url.setdefault(result["url"], Counter())[result["status"]] += 1
    ok = sum(1 for status in statuses if 200 <= status < 300)
    first_rejection = next((result for result in results if not 200 <= result["status"] < 300), None)
    warnings = []
    if window is not None and elapsed >= window:
        warnings.append(
            f"the burst took {elapsed:.1f} s, which is not shorter than the {window:g} s window: "
            "requests fell into more than one window, so the count does not test the limit"
        )
    return {
        "sequence": statuses,
        "ok": ok,
        "rejected": len(statuses) - ok,
        "per_url": {url: {str(status): number for status, number in sorted(counts.items())} for url, counts in per_url.items()},
        "elapsed_seconds": round(elapsed, 3),
        "first_response_headers": results[0]["headers"] if results else {},
        "last_response_headers": results[-1]["headers"] if results else {},
        "first_rejection": None
        if first_rejection is None
        else {
            "position": results.index(first_rejection) + 1,
            "status": first_rejection["status"],
            "body": " ".join(first_rejection["body"].split()),
            "headers": first_rejection["headers"],
        },
        "warnings": warnings,
    }


def check(summary, expect_ok=None, expect_reject=None, tolerance=0):
    """Return a list of failed expectations (empty when all hold)."""
    failures = []
    if expect_ok is not None and abs(summary["ok"] - expect_ok) > tolerance:
        allowed = f"{expect_ok}" if tolerance == 0 else f"{expect_ok} (+/- {tolerance})"
        failures.append(f"expected {allowed} successful responses, got {summary['ok']}")
    if expect_reject is not None:
        wrong = sorted({status for status in summary["sequence"] if not 200 <= status < 300 and status != expect_reject})
        if wrong:
            failures.append(f"expected every rejection to be {expect_reject}, also got {wrong}")
        if summary["rejected"] == 0:
            failures.append(f"expected at least one {expect_reject}, got none: send more requests than the limit")
    return failures


def render(summary, failures):
    lines = ["sequence: " + " ".join(str(status) for status in summary["sequence"])]
    for url, counts in summary["per_url"].items():
        lines.append(f"{url}: " + ", ".join(f"{number} x {status}" for status, number in counts.items()))
    lines.append(f"successful: {summary['ok']}   rejected: {summary['rejected']}   elapsed: {summary['elapsed_seconds']} s")
    if summary["first_response_headers"]:
        lines.append(f"headers on the first response: {summary['first_response_headers']}")
    rejection = summary["first_rejection"]
    if rejection:
        lines.append(f"first rejection: request {rejection['position']}, HTTP {rejection['status']}, body {rejection['body']}")
        if rejection["headers"]:
            lines.append(f"headers on the first rejection: {rejection['headers']}")
    lines += [f"WARNING: {warning}" for warning in summary["warnings"]]
    lines += [f"FAIL: {failure}" for failure in failures]
    if not failures:
        lines.append("PASS" if summary["warnings"] == [] else "PASS (with warnings)")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", action="append", required=True, help="request URL; repeat once per Gateway to alternate between them")
    parser.add_argument("--count", type=int, required=True, help="number of requests to send (more than the limit)")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--header", action="append", default=[], help="'Name: value'; repeatable")
    parser.add_argument("--header-env", action="append", default=[], help="'Name=ENV_VARIABLE': read the value from the environment so the key stays out of the command line; repeatable")
    parser.add_argument("--interval", type=float, default=0.0, help="seconds to wait between requests (default 0)")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--window", type=float, help="the limit's window in seconds (`per`); warns when the burst does not fit inside it")
    parser.add_argument("--expect-ok", type=int, help="number of 2xx responses expected")
    parser.add_argument("--tolerance", type=int, default=0, help="allowed difference from --expect-ok (default 0)")
    parser.add_argument("--expect-reject", type=int, help="status every non-2xx response must have, for example 429 or 403")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 2
    try:
        headers = dict(parse_header(text) for text in args.header)
        headers.update(parse_header_env(text) for text in args.header_env)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        results, elapsed = run_burst(args.url, args.count, args.method, headers, args.interval, args.timeout)
    except (urllib.error.URLError, OSError) as error:
        print(f"request failed: {error}", file=sys.stderr)
        return 2
    summary = summarise(results, elapsed, args.window)
    failures = check(summary, args.expect_ok, args.expect_reject, args.tolerance)
    if args.json:
        print(json.dumps({**summary, "failures": failures}, indent=2))
    else:
        print(render(summary, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
