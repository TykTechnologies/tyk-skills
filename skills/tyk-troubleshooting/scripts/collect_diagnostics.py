#!/usr/bin/env python3
"""Collect a diagnostic bundle from a running Tyk stack, with secrets redacted.

It reads the health endpoints, versions, the number of APIs each Gateway has loaded, the
Dashboard's node and licence view, Redis reachability and the analytics backlog, and the
recent error and warning lines of each container, then matches what it found against the
known failure signatures and prints the likely causes.

Standard library only. Secrets are passed by the NAME of an environment variable, never on
the command line, and every value of those variables is removed from the output, as are
bearer tokens, JWTs, licence strings, connection-string passwords and long key-like strings.

    export TYK_GW_SECRET=...  TYK_DASHBOARD_API_KEY=...
    collect_diagnostics.py --gateway http://localhost:8080 --dashboard http://localhost:3000 \
        --portal http://localhost:3001 --redis localhost:6379 \
        --container tyk-gateway --container tyk-dashboard --container tyk-pump \
        --out ./tyk-diagnostics

Exit status: 0 when no finding was raised, 1 when at least one was, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

ANALYTICS_KEY = "analytics-tyk-system-analytics"
REDACTED = "<redacted>"

# Patterns removed from every string the bundle keeps, whatever their source.
SECRET_PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), REDACTED),  # JWT, licence
    (re.compile(r"(?i)((?:authorization|x-tyk-authorization|cookie|set-cookie)\s*[:=]\s*)(\"?)[^\s\",]+"), r"\1\2" + REDACTED),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(password|passwd|pwd|secret|token|api_key|apikey|license_key|licensekey)(\"?\s*[=:]\s*)(\"?)[^\s\",&]+"), r"\1\2\3" + REDACTED),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s@]*:)[^@\s/]+@"), r"\1" + REDACTED + "@"),
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/_]{32,}={0,2}(?![A-Za-z0-9])"), REDACTED),  # keys, hashes, base64
]

LEVEL_RE = re.compile(r'level=(error|fatal|panic|warning|warn)\b|"level":"(error|fatal|panic|warning|warn)"|\t(error|warn|fatal|panic)\t')
TIME_RE = re.compile(r'^time="[^"]*"\s*|^\d{4}-\d{2}-\d{2}T[0-9:.]+Z?\s+|"time":"[^"]*",?')


class Redactor:
    def __init__(self, secrets: list[str]):
        # Longest first, so a secret that contains another is removed whole.
        self.secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, REDACTED)
        for pattern, replacement in SECRET_PATTERNS:
            value = pattern.sub(replacement, value)
        return value

    def any(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.any(item) for item in value]
        if isinstance(value, dict):
            return {key: self.any(item) for key, item in value.items()}
        return value


def http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> dict:
    """GET a URL and return status, selected headers and body; never raises."""
    request = urllib.request.Request(url, headers=headers or {})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, response_headers, body = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, response_headers, body = error.code, error.headers, error.read()
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        return {"url": url, "error": str(reason), "elapsed_ms": round((time.monotonic() - started) * 1000)}
    text = body.decode("utf-8", "replace")
    result = {
        "url": url,
        "status": status,
        "content_type": response_headers.get("Content-Type", ""),
        "x_generator": response_headers.get("X-Generator", ""),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "body": text[:2000],
    }
    try:
        result["json"] = json.loads(text)
    except ValueError:
        pass
    return result


def redis_command(host: str, port: int, args: list[str], password: str | None, timeout: float = 3.0):
    """Send one command over RESP (after AUTH when a password is given) and return the reply."""
    def encode(parts):
        out = f"*{len(parts)}\r\n".encode()
        for part in parts:
            data = part.encode()
            out += f"${len(data)}\r\n".encode() + data + b"\r\n"
        return out

    def read_reply(stream):
        line = stream.readline()
        if not line:
            raise OSError("connection closed")
        kind, rest = line[:1], line[1:].strip().decode()
        if kind == b"+":
            return rest
        if kind == b"-":
            raise RuntimeError(rest)
        if kind == b":":
            return int(rest)
        if kind == b"$":
            size = int(rest)
            return None if size < 0 else stream.read(size + 2)[:-2].decode("utf-8", "replace")
        raise OSError(f"unexpected reply {line[:20]!r}")

    with socket.create_connection((host, port), timeout=timeout) as connection, connection.makefile("rb") as stream:
        if password:
            connection.sendall(encode(["AUTH", password]))
            read_reply(stream)
        connection.sendall(encode(args))
        return read_reply(stream)


def check_redis(target: str, password: str | None, sample_gap: float) -> dict:
    host, _, port = target.rpartition(":")
    result = {"target": target}
    try:
        started = time.monotonic()
        result["ping"] = redis_command(host or target, int(port or 6379), ["PING"], password)
        result["ping_ms"] = round((time.monotonic() - started) * 1000, 1)
        first = redis_command(host, int(port), ["LLEN", ANALYTICS_KEY], password)
        if sample_gap > 0:
            time.sleep(sample_gap)
            second = redis_command(host, int(port), ["LLEN", ANALYTICS_KEY], password)
        else:
            second = first
        result["analytics_backlog"] = [first, second]
    except (OSError, RuntimeError, ValueError) as error:
        result["error"] = str(error)
    return result


def docker_available() -> bool:
    return shutil.which("docker") is not None


def container_info(name: str, since: str, max_lines: int) -> dict:
    info = {"container": name}
    try:
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.Config.Image}}|{{.State.Status}}|{{.RestartCount}}|{{.State.StartedAt}}", name],
            capture_output=True, text=True, timeout=20)
        if inspect.returncode != 0:
            info["error"] = inspect.stderr.strip()[:300]
            return info
        image, status, restarts, started = inspect.stdout.strip().split("|")
        info.update(image=image, status=status, restarts=int(restarts), started_at=started)
        logs = subprocess.run(["docker", "logs", "--since", since, name], capture_output=True, text=True, timeout=60)
        info.update(summarise_log_lines((logs.stdout + logs.stderr).splitlines(), max_lines))
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        info["error"] = str(error)
    return info


def summarise_log_lines(lines: list[str], max_lines: int) -> dict:
    """Count error and warning lines with timestamps stripped, most frequent first."""
    counts: Counter[str] = Counter()
    for line in lines:
        if LEVEL_RE.search(line):
            counts[TIME_RE.sub("", line.strip())[:400]] += 1
    return {
        "lines_read": len(lines),
        "problem_lines": [{"count": count, "line": line} for line, count in counts.most_common(max_lines)],
    }


def collect(args, environ) -> dict:
    secret = environ.get(args.gateway_secret_env, "")
    dash_key = environ.get(args.dashboard_key_env, "")
    redis_password = environ.get(args.redis_password_env, "") if args.redis_password_env else ""
    bundle = {"collected_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "gateways": [], "dashboard": None, "portal": None, "pump": None, "redis": [],
              "containers": [], "probes": [], "log_files": []}
    for url in args.gateway:
        url = url.rstrip("/")
        gw = {"url": url, "hello": http_get(f"{url}/hello"), "ready": http_get(f"{url}/ready")}
        if secret:
            apis = http_get(f"{url}/tyk/apis", {"x-tyk-authorization": secret})
            loaded = apis.get("json")
            gw["loaded_apis"] = {"status": apis.get("status"), "error": apis.get("error"),
                                 "count": len(loaded) if isinstance(loaded, list) else None,
                                 "ids": [a.get("api_id") for a in loaded][:50] if isinstance(loaded, list) else None,
                                 "body": None if isinstance(loaded, list) else apis.get("body", "")[:300]}
        bundle["gateways"].append(gw)
    if args.dashboard:
        url = args.dashboard.rstrip("/")
        dash = {"url": url, "hello": http_get(f"{url}/hello")}
        if dash_key:
            nodes = http_get(f"{url}/api/system/nodes", {"authorization": dash_key})
            data = (nodes.get("json") or {}).get("data") if isinstance(nodes.get("json"), dict) else None
            dash["nodes"] = {"status": nodes.get("status"), "error": nodes.get("error"),
                             "body": None if data else nodes.get("body", "")[:300]}
            if isinstance(data, dict):
                valid_until = data.get("valid_until")
                dash["nodes"].update(
                    active_node_count=data.get("active_node_count"), nodes_available=data.get("nodes_available"),
                    nodes_remaining=data.get("nodes_remaining"),
                    hostnames=[n.get("hostname") for n in data.get("nodes") or []],
                    licence_valid_until=_dt.datetime.fromtimestamp(valid_until, _dt.timezone.utc).strftime("%Y-%m-%d")
                    if isinstance(valid_until, (int, float)) and valid_until > 0 else None)
            apis = http_get(f"{url}/api/apis?p=-1", {"authorization": dash_key})
            listed = (apis.get("json") or {}).get("apis") if isinstance(apis.get("json"), dict) else None
            dash["apis"] = {"status": apis.get("status"), "error": apis.get("error"),
                            "count": len(listed) if isinstance(listed, list) else None,
                            "active": sum(1 for a in listed if (a.get("api_definition") or {}).get("active"))
                            if isinstance(listed, list) else None}
        bundle["dashboard"] = dash
    if args.portal:
        url = args.portal.rstrip("/")
        bundle["portal"] = {"url": url, "live": http_get(f"{url}/live"), "ready": http_get(f"{url}/ready")}
    if args.pump_health:
        bundle["pump"] = {"health": http_get(args.pump_health)}
    for target in args.redis:
        bundle["redis"].append(check_redis(target, redis_password or None, args.backlog_gap))
    for probe in args.probe:
        bundle["probes"].append(http_get(probe))
    if args.container:
        if docker_available():
            for name in args.container:
                bundle["containers"].append(container_info(name, args.since, args.max_lines))
        else:
            bundle["containers"].append({"error": "docker is not on PATH; pass --log-file instead"})
    for path in args.log_file:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
            bundle["log_files"].append({"path": os.path.basename(path), **summarise_log_lines(lines, args.max_lines)})
        except OSError as error:
            bundle["log_files"].append({"path": os.path.basename(path), "error": str(error)})
    return bundle


def _all_log_text(bundle: dict) -> str:
    parts = []
    for source in bundle["containers"] + bundle["log_files"]:
        parts += [entry["line"] for entry in source.get("problem_lines", [])]
    return "\n".join(parts)


# (log substring, finding, reference file) for causes that only the logs reveal.
LOG_SIGNATURES = [
    ("Response failed with code 403", "A Gateway was refused by the Dashboard: its node secret does not match the Dashboard's.", "control-plane.md"),
    ("Response failed with code 404", "A Gateway was refused registration: no licence slot (node limit reached, or the Dashboard licence is invalid).", "licences.md"),
    ("/register/node", "A Gateway cannot reach the Dashboard URL it is configured with.", "control-plane.md"),
    ("Registration deferred (409", "Gateway registration is deferred: the Dashboard cannot use Redis.", "redis.md"),
    ("Redis is either down or was not configured", "A component cannot reach Redis.", "redis.md"),
    ("Is Temporal Storage down?", "Pump cannot reach Redis.", "analytics.md"),
    ("Pump init error (skipping)", "A pump failed to start and is skipped; its target receives nothing.", "analytics.md"),
    ("License key is invalid or has expired", "The Dashboard licence is invalid or expired.", "licences.md"),
    ("License is invalid", "The Developer Portal licence is invalid.", "licences.md"),
    ("Listen path collision", "Two APIs share a listen path; one was moved.", "api-not-loaded.md"),
    ("Couldn't unmarshal read file", "An API definition file on disk is not valid JSON and was skipped.", "api-not-loaded.md"),
    ("Attempted administrative access with invalid or missing key", "A caller (often the Dashboard) used the wrong Gateway secret.", "control-plane.md"),
    ("x509:", "TLS to an upstream failed certificate verification.", "upstream-errors.md"),
    ("proxy error: dial tcp", "The Gateway cannot connect to an upstream (refused, DNS or network).", "upstream-errors.md"),
    ("timeout awaiting response headers", "An upstream was slower than the Gateway's timeout.", "upstream-errors.md"),
]


def findings(bundle: dict) -> list[dict]:
    found = []

    def add(text, reference):
        found.append({"finding": text, "read": f"references/{reference}"})

    for gw in bundle["gateways"]:
        hello, ready = gw["hello"], gw["ready"]
        if "error" in hello:
            add(f"Gateway {gw['url']} does not answer: {hello['error']}.", "health-and-logs.md")
            continue
        ready_message = (ready.get("json") or {}).get("message", "") if isinstance(ready.get("json"), dict) else ""
        if ready.get("status") == 503:
            if "Redis" in ready_message:
                add(f"Gateway {gw['url']} is not ready: Redis is unreachable.", "redis.md")
            elif "reload" in ready_message:
                add(f"Gateway {gw['url']} has never loaded its APIs (no successful reload): check the "
                    "Dashboard URL, node secret, licence and node count, or Redis at start.", "control-plane.md")
            elif "shutting down" in ready_message:
                add(f"Gateway {gw['url']} is shutting down.", "health-and-logs.md")
        details = (hello.get("json") or {}).get("details") or {} if isinstance(hello.get("json"), dict) else {}
        for component, item in details.items():
            if isinstance(item, dict) and item.get("status") == "fail":
                add(f"Gateway {gw['url']} health check: {component} failing ({item.get('output', '')}).",
                    "redis.md" if component == "redis" else "control-plane.md")
        loaded = gw.get("loaded_apis")
        if loaded and loaded.get("status") == 403:
            add(f"Gateway {gw['url']} refused the secret: the secret variable does not hold this Gateway's secret.", "control-plane.md")
        elif loaded and loaded.get("count") == 0:
            add(f"Gateway {gw['url']} has loaded no APIs.", "api-not-loaded.md")
    dash = bundle["dashboard"]
    if dash:
        nodes = dash.get("nodes") or {}
        if nodes.get("status") == 402:
            add("Dashboard API answers 402: the Dashboard licence is invalid or expired.", "licences.md")
        elif nodes.get("status") == 401:
            add("Dashboard API answers 401: the API key is wrong, or the Dashboard cannot reach Redis.", "redis.md")
        if nodes.get("nodes_remaining") == 0:
            add("The Dashboard licence has no node slot left; another Gateway will not load APIs.", "licences.md")
        valid_until = nodes.get("licence_valid_until")
        if valid_until:
            days = (_dt.date.fromisoformat(valid_until) - _dt.datetime.now(_dt.timezone.utc).date()).days
            if days < 30:
                add(f"The Dashboard licence is valid until {valid_until} ({days} days).", "licences.md")
        apis = dash.get("apis") or {}
        if apis.get("active") is not None:
            for gw in bundle["gateways"]:
                count = (gw.get("loaded_apis") or {}).get("count")
                if count is not None and count < apis["active"]:
                    add(f"Gateway {gw['url']} has {count} APIs loaded but the Dashboard lists {apis['active']} active "
                        "(check segment tags and the Gateway log).", "api-not-loaded.md")
    portal = bundle["portal"]
    if portal:
        ready = portal["ready"]
        if "text/html" in ready.get("content_type", ""):
            add("Portal /ready returned an HTML page instead of JSON: the Portal licence is invalid.", "licences.md")
        elif ready.get("status") not in (200, None) or "error" in ready:
            add(f"Portal is not ready: {ready.get('status') or ready.get('error')}.", "health-and-logs.md")
    for redis in bundle["redis"]:
        if "error" in redis:
            add(f"Redis {redis['target']} is unreachable from here: {redis['error']}.", "redis.md")
        else:
            first, second = redis.get("analytics_backlog", [0, 0])
            if isinstance(second, int) and second > 0 and isinstance(first, int) and second >= first:
                add(f"Redis {redis['target']} holds {second} unprocessed analytics records and the backlog is "
                    "not shrinking: Pump is not draining it, and records expire after the Gateway's "
                    "storage_expiration_time.", "analytics.md")
    for probe in bundle["probes"]:
        if probe.get("status") == 404 and not probe.get("x_generator") and "text/plain" in probe.get("content_type", ""):
            add(f"Probe {probe['url']}: 404 'Not Found' from no matching listen path (API not loaded, or wrong path).", "api-not-loaded.md")
    text = _all_log_text(bundle)
    for needle, finding, reference in LOG_SIGNATURES:
        if needle in text:
            add(f"Log: {finding}", reference)
    for container in bundle["containers"]:
        if container.get("restarts"):
            add(f"Container {container['container']} restarted {container['restarts']} times.", "health-and-logs.md")
    unique, seen = [], set()
    for item in found:
        if item["finding"] not in seen:
            seen.add(item["finding"])
            unique.append(item)
    return unique


def markdown(bundle: dict) -> str:
    out = [f"# Tyk diagnostic bundle ({bundle['collected_at']})", ""]
    out.append("## Findings")
    out += [f"- {f['finding']} Read `{f['read']}`." for f in bundle["findings"]] or ["- None raised."]
    out += ["", "## Gateways"]
    for gw in bundle["gateways"]:
        hello = gw["hello"].get("json") or {}
        out.append(f"- {gw['url']}: /hello {gw['hello'].get('status', gw['hello'].get('error'))} "
                   f"{hello.get('status', '')} version {hello.get('version', '?')}; /ready "
                   f"{gw['ready'].get('status', gw['ready'].get('error'))}; APIs loaded "
                   f"{(gw.get('loaded_apis') or {}).get('count', 'not checked')}")
    if bundle["dashboard"]:
        d = bundle["dashboard"]
        nodes = d.get("nodes") or {}
        out += ["", "## Dashboard", f"- {d['url']}: /hello {d['hello'].get('status', d['hello'].get('error'))}; "
                f"nodes {nodes.get('active_node_count')}/{nodes.get('nodes_available')} "
                f"(remaining {nodes.get('nodes_remaining')}); licence valid until {nodes.get('licence_valid_until')}; "
                f"APIs {(d.get('apis') or {}).get('count')} (active {(d.get('apis') or {}).get('active')})"]
    if bundle["portal"]:
        p = bundle["portal"]
        out += ["", "## Portal", f"- {p['url']}: /live {p['live'].get('status', p['live'].get('error'))}; "
                f"/ready {p['ready'].get('status', p['ready'].get('error'))} {p['ready'].get('content_type', '')}"]
    if bundle["redis"]:
        out += ["", "## Redis"] + [f"- {r['target']}: {r.get('ping', r.get('error'))}; analytics backlog "
                                   f"{r.get('analytics_backlog')}" for r in bundle["redis"]]
    for source in bundle["containers"] + bundle["log_files"]:
        label = source.get("container") or source.get("path") or "logs"
        out += ["", f"## {label} {source.get('image', '')} {source.get('status', '')}".rstrip()]
        if "error" in source:
            out.append(f"- {source['error']}")
        out += [f"- {e['count']} x {e['line']}" for e in source.get("problem_lines", [])] or ["- no error or warning lines"]
    return "\n".join(out) + "\n"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gateway", action="append", default=[], help="Gateway base URL; repeat for each Gateway")
    parser.add_argument("--gateway-secret-env", default="TYK_GW_SECRET", help="variable holding the Gateway secret")
    parser.add_argument("--dashboard", help="Dashboard base URL")
    parser.add_argument("--dashboard-key-env", default="TYK_DASHBOARD_API_KEY", help="variable holding a Dashboard user API key")
    parser.add_argument("--portal", help="Developer Portal base URL")
    parser.add_argument("--pump-health", help="Pump health URL, for example http://pump:8083/health")
    parser.add_argument("--redis", action="append", default=[], help="Redis host:port; repeat for each")
    parser.add_argument("--redis-password-env", help="variable holding the Redis password")
    parser.add_argument("--backlog-gap", type=float, default=3.0, help="seconds between two analytics backlog samples (0 for one)")
    parser.add_argument("--probe", action="append", default=[], help="a URL to request through a Gateway; repeat")
    parser.add_argument("--container", action="append", default=[], help="Docker container to read; repeat")
    parser.add_argument("--log-file", action="append", default=[], help="a saved log file to scan; repeat")
    parser.add_argument("--since", default="15m", help="log window for containers (docker logs --since)")
    parser.add_argument("--max-lines", type=int, default=15, help="distinct problem lines kept per source")
    parser.add_argument("--extra-secret-env", action="append", default=[], help="another variable whose value must be redacted")
    parser.add_argument("--out", help="directory for diagnostics.json and summary.md")
    args = parser.parse_args(argv)
    if not (args.gateway or args.dashboard or args.portal or args.redis or args.container or args.log_file or args.pump_health):
        parser.error("name at least one target (--gateway, --dashboard, --portal, --redis, --container, --log-file)")
    return args


def main(argv=None, environ=None) -> int:
    environ = os.environ if environ is None else environ
    args = parse_args(argv)
    secret_vars = [args.gateway_secret_env, args.dashboard_key_env, *args.extra_secret_env]
    if args.redis_password_env:
        secret_vars.append(args.redis_password_env)
    redactor = Redactor([environ.get(name, "") for name in secret_vars])
    bundle = collect(args, environ)
    bundle["findings"] = findings(bundle)
    bundle = redactor.any(bundle)
    summary = markdown(bundle)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "diagnostics.json"), "w", encoding="utf-8") as handle:
            json.dump(bundle, handle, indent=2)
        with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as handle:
            handle.write(summary)
    sys.stdout.write(summary)
    return 1 if bundle["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
