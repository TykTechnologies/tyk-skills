#!/usr/bin/env python3
"""Small MCP client (Streamable HTTP) for checking an MCP proxy on Tyk Gateway.

Python standard library only. It speaks both protocol styles the Gateway accepts:
the session style (2025-11-25: initialize, then Mcp-Session-Id on later calls) and
the stateless style (2026-07-28: no handshake, version and method repeated in headers).

Usage:
    mcp_probe.py probe    URL [options]            one initialize (or server/discover), no follow-up
    mcp_probe.py list     URL [--kind tools|resources|prompts] [options]
    mcp_probe.py call     URL TOOL [--args JSON] [options]
    mcp_probe.py request  URL METHOD [--params JSON] [options]   any JSON-RPC method
    mcp_probe.py metadata URL [options]            follow the 401 challenge to the OAuth
                                                   protected-resource metadata document

Options:
    --header 'Name: value'   repeatable; credentials go here (for example
                             --header 'Authorization: Bearer <token>')
    --protocol VERSION       2025-11-25 (default) or 2026-07-28
    --show-headers           include the response headers in the output
    --timeout SECONDS        default 20

Output is one JSON object per run: {"status", "content_type", "body", ...}.
Exit codes: 0 the last request returned HTTP < 400 and no JSON-RPC error,
1 usage or transport failure, 2 HTTP >= 400 or a JSON-RPC error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SESSION_PROTOCOL = "2025-11-25"
STATELESS_PROTOCOL = "2026-07-28"
CLIENT_INFO = {"name": "mcp-probe", "version": "1.0.0"}
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
# Methods whose primitive name is repeated in the Mcp-Name header (stateless style).
NAMED_METHODS = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}


class ProbeError(Exception):
    """A request that the probe cannot continue from."""


def parse_headers(pairs: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition(":")
        if not sep or not name.strip():
            raise ProbeError(f"--header needs 'Name: value', got {pair!r}")
        headers[name.strip()] = value.strip()
    return headers


def parse_body(raw: str, content_type: str):
    """Return (parsed, events). A JSON body gives (object, 1); an SSE body gives the
    last JSON data frame and the number of data frames."""
    raw = raw.strip()
    if not raw:
        return None, 0
    if "text/event-stream" in content_type or raw.startswith(("event:", "data:", "id:", ":")):
        frames = []
        for block in re.split(r"\r?\n\r?\n", raw):
            data = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
            if data:
                frames.append("\n".join(data))
        for frame in reversed(frames):
            try:
                return json.loads(frame), len(frames)
            except json.JSONDecodeError:
                continue
        return raw, len(frames)
    try:
        return json.loads(raw), 1
    except json.JSONDecodeError:
        return raw, 1


def build_message(method: str, params: dict | None, msg_id: int | None, protocol: str) -> dict:
    message: dict = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        message["id"] = msg_id
    params = dict(params or {})
    if protocol == STATELESS_PROTOCOL:
        meta = dict(params.get("_meta") or {})
        meta.setdefault(META_VERSION, protocol)
        meta.setdefault(META_CAPABILITIES, {})
        params["_meta"] = meta
    if params:
        message["params"] = params
    return message


def protocol_headers(message: dict, protocol: str, session: str | None, initialised: bool) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    method = message["method"]
    if protocol == STATELESS_PROTOCOL:
        headers["MCP-Protocol-Version"] = protocol
        headers["Mcp-Method"] = method
        key = NAMED_METHODS.get(method)
        if key and key in message.get("params", {}):
            headers["Mcp-Name"] = str(message["params"][key])
    elif initialised:
        headers["MCP-Protocol-Version"] = protocol
    if session:
        headers["Mcp-Session-Id"] = session
    return headers


class Client:
    def __init__(self, url: str, headers: dict[str, str], protocol: str, timeout: float):
        self.url = url
        self.extra = headers
        self.protocol = protocol
        self.timeout = timeout
        self.session: str | None = None
        self.initialised = False
        self.next_id = 1

    def send(self, method: str, params: dict | None = None, notification: bool = False) -> dict:
        msg_id = None
        if not notification:
            msg_id = self.next_id
            self.next_id += 1
        message = build_message(method, params, msg_id, self.protocol)
        headers = protocol_headers(message, self.protocol, self.session, self.initialised)
        headers.update(self.extra)
        request = urllib.request.Request(self.url, data=json.dumps(message).encode(), method="POST", headers=headers)
        return self._open(request)

    def get(self, url: str) -> dict:
        return self._open(urllib.request.Request(url, method="GET", headers=dict(self.extra)))

    def _open(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, headers, raw = response.status, response.headers, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, headers, raw = exc.code, exc.headers, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise ProbeError(f"cannot reach {request.full_url}: {exc}") from exc
        content_type = headers.get("Content-Type", "")
        body, events = parse_body(raw, content_type)
        session = headers.get("Mcp-Session-Id")
        if session:
            self.session = session
        result = {"status": status, "content_type": content_type, "body": body}
        if "text/event-stream" in content_type:
            result["sse_events"] = events
        if headers.get("WWW-Authenticate"):
            result["www_authenticate"] = headers.get("WWW-Authenticate")
        result["_headers"] = {k: v for k, v in headers.items()}
        return result

    def connect(self) -> dict | None:
        """Open a session in the session style; nothing to do in the stateless style."""
        if self.protocol == STATELESS_PROTOCOL:
            return None
        result = self.send(
            "initialize",
            {"protocolVersion": self.protocol, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        if failed(result):
            return result
        self.initialised = True
        self.send("notifications/initialized", notification=True)
        return result


def failed(result: dict) -> bool:
    body = result.get("body")
    return result["status"] >= 400 or (isinstance(body, dict) and "error" in body)


def resource_metadata_url(challenge: str) -> str | None:
    match = re.search(r'resource_metadata="([^"]+)"', challenge or "")
    return match.group(1) if match else None


def run(opts: argparse.Namespace) -> tuple[dict, bool]:
    client = Client(opts.url, parse_headers(opts.header), opts.protocol, opts.timeout)

    if opts.action == "probe":
        if opts.protocol == STATELESS_PROTOCOL:
            result = client.send("server/discover")
        else:
            result = client.send(
                "initialize",
                {"protocolVersion": opts.protocol, "capabilities": {}, "clientInfo": CLIENT_INFO},
            )
        return result, failed(result)

    if opts.action == "metadata":
        challenge = client.send("tools/list") if opts.protocol == STATELESS_PROTOCOL else client.send(
            "initialize", {"protocolVersion": opts.protocol, "capabilities": {}, "clientInfo": CLIENT_INFO}
        )
        target = resource_metadata_url(challenge.get("www_authenticate", ""))
        if not target:
            challenge["error"] = "no resource_metadata in the WWW-Authenticate header"
            return challenge, True
        document = client.get(target)
        document["challenge_status"] = challenge["status"]
        document["www_authenticate"] = challenge.get("www_authenticate")
        document["metadata_url"] = target
        return document, failed(document)

    opened = client.connect()
    if opened is not None and failed(opened):
        opened["step"] = "initialize"
        return opened, True

    if opts.action == "list":
        result = client.send(f"{opts.kind}/list")
        body = result.get("body")
        if not failed(result) and isinstance(body, dict):
            items = body.get("result", {}).get(opts.kind, [])
            result["names"] = [item.get("name") or item.get("uri") for item in items]
            result["count"] = len(items)
            if not opts.full:
                result.pop("body")
        return result, failed(result)

    if opts.action == "call":
        result = client.send("tools/call", {"name": opts.name, "arguments": json.loads(opts.args)})
        body = result.get("body")
        is_error = isinstance(body, dict) and bool(body.get("result", {}).get("isError"))
        return result, failed(result) or is_error

    result = client.send(opts.name, json.loads(opts.params))
    return result, failed(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("probe", "list", "call", "request", "metadata"))
    parser.add_argument("url")
    parser.add_argument("name", nargs="?", help="tool name (call) or JSON-RPC method (request)")
    parser.add_argument("--args", default="{}", help="tool arguments as JSON (call)")
    parser.add_argument("--params", default="{}", help="params as JSON (request)")
    parser.add_argument("--kind", choices=("tools", "resources", "prompts"), default="tools")
    parser.add_argument("--full", action="store_true", help="list: keep the full result body")
    parser.add_argument("--header", action="append", default=[])
    parser.add_argument("--protocol", choices=(SESSION_PROTOCOL, STATELESS_PROTOCOL), default=SESSION_PROTOCOL)
    parser.add_argument("--show-headers", action="store_true")
    parser.add_argument("--timeout", type=float, default=20)
    opts = parser.parse_args(argv)
    if opts.action in ("call", "request") and not opts.name:
        parser.error(f"{opts.action} needs a name")
    try:
        result, bad = run(opts)
    except ProbeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"error: --args/--params is not valid JSON: {exc}", file=sys.stderr)
        return 1
    headers = result.pop("_headers", {})
    if opts.show_headers:
        result["headers"] = headers
    print(json.dumps(result, indent=2))
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
