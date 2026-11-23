#!/usr/bin/env python3
"""Compare the latency of several Gateway URLs, one request at a time, interleaved.

Point it at the same upstream path through an API without a plugin and through the same API
with the plugin. Requests go round-robin over one keep-alive connection per URL, so noise on a
busy machine hits every URL alike; the difference between medians is the plugin's cost.

    measure_latency.py --requests 1500 \
        none=http://localhost:8080/perf-none/status/200 \
        go=http://localhost:8080/perf-go/status/200

Prints the median and 90th percentile per URL and the median difference from the first one.
Standard library only. Exit status 1 when any request does not answer 200.
"""
from __future__ import annotations

import argparse
import http.client
import statistics
import sys
import time
from urllib.parse import urlsplit


def parse_target(text: str) -> tuple[str, str, int, str, bool]:
    if "=" not in text:
        raise ValueError(f"expected NAME=URL, got {text!r}")
    name, url = text.split("=", 1)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return name, parts.hostname, port, path, parts.scheme == "https"


def percentile(sorted_values: list[float], fraction: float) -> float:
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


def measure(targets, requests: int, warmup: int, headers: dict[str, str]):
    conns = {}
    for name, host, port, _, tls in targets:
        cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
        conns[name] = cls(host, port, timeout=30)
    samples = {t[0]: [] for t in targets}
    failures = {t[0]: 0 for t in targets}
    for i in range(requests + warmup):
        for name, _, _, path, _ in targets:
            conn = conns[name]
            start = time.perf_counter()
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            response.read()
            elapsed = (time.perf_counter() - start) * 1000
            if response.status != 200:
                failures[name] += 1
                continue
            if i >= warmup:
                samples[name].append(elapsed)
    for conn in conns.values():
        conn.close()
    return samples, failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("targets", nargs="+", metavar="NAME=URL")
    parser.add_argument("--requests", type=int, default=1000, help="measured requests per URL (default 1000)")
    parser.add_argument("--warmup", type=int, default=50, help="unmeasured requests per URL first (default 50)")
    parser.add_argument("--header", action="append", default=[], metavar="NAME:VALUE",
                        help="send this header with every request (for example an API key)")
    args = parser.parse_args(argv)
    try:
        targets = [parse_target(t) for t in args.targets]
    except ValueError as exc:
        parser.error(str(exc))
    headers = {}
    for item in args.header:
        if ":" not in item:
            parser.error(f"--header needs NAME:VALUE, got {item!r}")
        key, value = item.split(":", 1)
        headers[key.strip()] = value.strip()
    samples, failures = measure(targets, args.requests, args.warmup, headers)
    baseline = None
    for name, *_ in targets:
        values = sorted(samples[name])
        if not values:
            print(f"{name:16} no successful requests ({failures[name]} not 200)")
            continue
        median = statistics.median(values)
        if baseline is None:
            baseline = median
        print(f"{name:16} median={median:.2f}ms p90={percentile(values, 0.9):.2f}ms "
              f"diff={median - baseline:+.2f}ms n={len(values)} non200={failures[name]}")
    return 1 if any(failures.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
