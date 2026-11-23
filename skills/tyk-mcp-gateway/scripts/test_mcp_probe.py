#!/usr/bin/env python3
"""Tests for mcp_probe.py. Run: python3 -m unittest test_mcp_probe (from this folder)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mcp_probe  # noqa: E402

TOOLS = [{"name": "get_order", "inputSchema": {"type": "object"}}, {"name": "list_orders", "inputSchema": {"type": "object"}}]


class FakeMCP(BaseHTTPRequestHandler):
    """A stand-in for a gateway in front of an MCP server. Requires 'Authorization: good'."""

    seen: list[dict] = []

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status, body, content_type="application/json", extra=None):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/.well-known/oauth-protected-resource"):
            self._send(200, {"resource": "http://gateway.example.com/orders-mcp/", "authorization_servers": ["https://idp.example.com"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        FakeMCP.seen.append({"message": message, "headers": {k.lower(): v for k, v in self.headers.items()}})
        if self.headers.get("Authorization") != "good":
            host = self.headers.get("Host")
            challenge = f'Bearer realm="tyk", resource_metadata="http://{host}/orders-mcp/.well-known/oauth-protected-resource"'
            error = {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32001, "message": "Authorization field missing"}}
            return self._send(401, error, extra={"WWW-Authenticate": challenge})
        method = message["method"]
        if "id" not in message:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
            return self._send(200, {"jsonrpc": "2.0", "id": message["id"], "result": result}, extra={"Mcp-Session-Id": "session-1"})
        if method == "tools/list":
            frame = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": TOOLS}})
            return self._send(200, f"event: message\ndata: {frame}\n\n", content_type="text/event-stream")
        if method == "tools/call" and message["params"]["name"] == "get_order":
            result = {"content": [{"type": "text", "text": json.dumps(message["params"]["arguments"])}]}
            return self._send(200, {"jsonrpc": "2.0", "id": message["id"], "result": result})
        error = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32002, "message": "tool is not available"}}
        return self._send(403, error)


class ProbeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMCP)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/orders-mcp/mcp"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeMCP.seen.clear()

    def run_probe(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = mcp_probe.main(list(argv))
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)


class PureFunctions(unittest.TestCase):
    def test_parse_headers(self):
        self.assertEqual(mcp_probe.parse_headers(["Authorization: Bearer a:b", "X-Tenant:partner-a"]),
                         {"Authorization": "Bearer a:b", "X-Tenant": "partner-a"})
        with self.assertRaises(mcp_probe.ProbeError):
            mcp_probe.parse_headers(["no-separator"])

    def test_parse_body_json_and_sse(self):
        self.assertEqual(mcp_probe.parse_body('{"a": 1}', "application/json"), ({"a": 1}, 1))
        stream = 'data: {"method": "notifications/progress"}\n\nevent: message\ndata: {"id": 5, "result": {}}\n\n'
        self.assertEqual(mcp_probe.parse_body(stream, "text/event-stream"), ({"id": 5, "result": {}}, 2))
        self.assertEqual(mcp_probe.parse_body("", "application/json"), (None, 0))
        self.assertEqual(mcp_probe.parse_body("Forbidden", "text/plain"), ("Forbidden", 1))

    def test_stateless_message_and_headers(self):
        message = mcp_probe.build_message("tools/call", {"name": "get_order", "arguments": {}}, 1, mcp_probe.STATELESS_PROTOCOL)
        meta = message["params"]["_meta"]
        self.assertEqual(meta[mcp_probe.META_VERSION], "2026-07-28")
        self.assertEqual(meta[mcp_probe.META_CAPABILITIES], {})
        headers = mcp_probe.protocol_headers(message, mcp_probe.STATELESS_PROTOCOL, None, False)
        self.assertEqual(headers["MCP-Protocol-Version"], "2026-07-28")
        self.assertEqual(headers["Mcp-Method"], "tools/call")
        self.assertEqual(headers["Mcp-Name"], "get_order")

    def test_session_headers(self):
        message = mcp_probe.build_message("initialize", {"protocolVersion": "2025-11-25"}, 1, mcp_probe.SESSION_PROTOCOL)
        self.assertNotIn("_meta", message["params"])
        self.assertNotIn("MCP-Protocol-Version", mcp_probe.protocol_headers(message, mcp_probe.SESSION_PROTOCOL, None, False))
        later = mcp_probe.protocol_headers({"method": "tools/list"}, mcp_probe.SESSION_PROTOCOL, "abc", True)
        self.assertEqual((later["MCP-Protocol-Version"], later["Mcp-Session-Id"]), ("2025-11-25", "abc"))

    def test_resource_metadata_url(self):
        challenge = 'Bearer realm="tyk", resource_metadata="http://gw.example.com/x/.well-known/oauth-protected-resource"'
        self.assertEqual(mcp_probe.resource_metadata_url(challenge), "http://gw.example.com/x/.well-known/oauth-protected-resource")
        self.assertIsNone(mcp_probe.resource_metadata_url('Bearer realm="tyk"'))


class AgainstFakeServer(ProbeTestCase):
    def test_probe_without_credentials_reports_challenge(self):
        code, result = self.run_probe("probe", self.url)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], 401)
        self.assertEqual(result["body"]["error"]["code"], -32001)
        self.assertIn("resource_metadata=", result["www_authenticate"])

    def test_list_runs_handshake_and_parses_sse(self):
        code, result = self.run_probe("list", self.url, "--header", "Authorization: good")
        self.assertEqual(code, 0)
        self.assertEqual(result["names"], ["get_order", "list_orders"])
        self.assertEqual(result["sse_events"], 1)
        methods = [entry["message"]["method"] for entry in FakeMCP.seen]
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list"])
        self.assertEqual(FakeMCP.seen[-1]["headers"]["mcp-session-id"], "session-1")

    def test_stateless_list_sends_no_handshake(self):
        code, result = self.run_probe("list", self.url, "--header", "Authorization: good", "--protocol", "2026-07-28")
        self.assertEqual(code, 0)
        self.assertEqual(len(FakeMCP.seen), 1)
        self.assertEqual(FakeMCP.seen[0]["headers"]["mcp-method"], "tools/list")
        self.assertNotIn("mcp-session-id", FakeMCP.seen[0]["headers"])

    def test_call_passes_arguments_and_flags_errors(self):
        code, result = self.run_probe("call", self.url, "get_order", "--args", '{"order_id": "42"}', "--header", "Authorization: good")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(result["body"]["result"]["content"][0]["text"]), {"order_id": "42"})
        code, result = self.run_probe("call", self.url, "delete_order", "--header", "Authorization: good")
        self.assertEqual((code, result["status"]), (2, 403))

    def test_metadata_follows_the_challenge(self):
        code, result = self.run_probe("metadata", self.url)
        self.assertEqual(code, 0)
        self.assertEqual(result["challenge_status"], 401)
        self.assertEqual(result["body"]["authorization_servers"], ["https://idp.example.com"])

    def test_bad_arguments_and_unreachable_host(self):
        code, _ = self.run_probe("call", self.url, "get_order", "--args", "{not json", "--header", "Authorization: good")
        self.assertEqual(code, 1)
        code, _ = self.run_probe("probe", "http://127.0.0.1:9/mcp", "--timeout", "2")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
