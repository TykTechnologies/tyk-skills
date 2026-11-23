#!/usr/bin/env python3
"""Minimal MCP (Streamable HTTP) client for demo verification.

Used by verify-demo.sh to prove MCP behaviour through the Tyk Gateway:
unauthenticated rejection, authenticated tools/list, filtered discovery when a
tool is blocked by policy, and per-tool rate limiting.

Usage:
    mcp-check.py list  <url> [--key KEY] [--insecure]
    mcp-check.py call  <url> <tool> [--args JSON] [--key KEY] [--insecure]
    mcp-check.py probe <url> [--key KEY] [--insecure]   # exit code = HTTP status class

Exit codes: 0 success, 1 usage/transport error, 2 MCP/HTTP error response.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"


class MCPError(Exception):
    pass


def _ctx(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _post(url: str, payload: dict, key: str | None, session: str | None, insecure: bool):
    """POST one JSON-RPC message and return (status, headers, parsed-or-None)."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if key:
        req.add_header("Authorization", key)
    if session:
        req.add_header("mcp-session-id", session)
    try:
        with urllib.request.urlopen(req, context=_ctx(insecure), timeout=20) as resp:
            return resp.status, dict(resp.headers), _parse(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), _parse(exc.read().decode())


def _parse(raw: str):
    """Parse a JSON body or the last data: frame of an SSE stream."""
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("{"):
        return json.loads(raw)
    payload = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
    return json.loads(payload) if payload else None


def connect(url: str, key: str | None, insecure: bool) -> str | None:
    """Run initialize + notifications/initialized; return the session id."""
    status, headers, parsed = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "__SLUG__-verify", "version": "1.0.0"},
            },
        },
        key,
        None,
        insecure,
    )
    if status >= 400:
        raise MCPError(f"initialize returned HTTP {status}: {json.dumps(parsed)[:300]}")
    session = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, key, session, insecure)
    return session


def tools_list(url: str, key: str | None, insecure: bool) -> list[dict]:
    session = connect(url, key, insecure)
    status, _, parsed = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, key, session, insecure)
    if status >= 400:
        raise MCPError(f"tools/list returned HTTP {status}")
    if parsed is None or "result" not in parsed:
        raise MCPError(f"tools/list returned no result: {json.dumps(parsed)[:300]}")
    return parsed["result"].get("tools", [])


def tools_call(url: str, tool: str, args: dict, key: str | None, insecure: bool):
    session = connect(url, key, insecure)
    status, _, parsed = _post(
        url,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": args}},
        key,
        session,
        insecure,
    )
    return status, parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "call", "probe"))
    parser.add_argument("url")
    parser.add_argument("tool", nargs="?")
    parser.add_argument("--args", default="{}")
    parser.add_argument("--key")
    parser.add_argument("--insecure", action="store_true")
    opts = parser.parse_args()

    try:
        if opts.action == "probe":
            status, _, parsed = _post(
                opts.url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "__SLUG__-verify", "version": "1.0.0"},
                    },
                },
                opts.key,
                None,
                opts.insecure,
            )
            print(json.dumps({"status": status, "body": parsed}))
            return 0 if status < 400 else 2

        if opts.action == "list":
            tools = tools_list(opts.url, opts.key, opts.insecure)
            print(json.dumps({"count": len(tools), "tools": [t["name"] for t in tools]}, indent=2))
            return 0

        if not opts.tool:
            parser.error("call requires a tool name")
        status, parsed = tools_call(opts.url, opts.tool, json.loads(opts.args), opts.key, opts.insecure)
        print(json.dumps({"status": status, "body": parsed}, indent=2))
        return 0 if status < 400 else 2
    except MCPError as exc:
        print(f"MCP error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # transport, TLS, JSON
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
