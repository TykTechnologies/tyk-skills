"""Tests for collect_diagnostics.py. Standard library only: python3 -m unittest test_collect_diagnostics.py"""
from __future__ import annotations

import io
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_diagnostics as cd  # noqa: E402

GW_SECRET = "gw-secret-value-123"
DASH_KEY = "dash-key-value-456"
LICENCE = "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3OTY0Mjg3OTl9.c2lnbmF0dXJlLXZhbHVl"


class FakeTyk(BaseHTTPRequestHandler):
    """Serves the endpoints the collector reads; behaviour set through class attributes."""

    routes: dict = {}
    disable_nagle_algorithm = True

    def log_message(self, *args):
        pass

    def do_GET(self):
        route = self.routes.get(self.path.split("?")[0])
        if route is None:
            self._send(404, "text/plain; charset=utf-8", "Not Found")
            return
        need = route.get("need")
        if need and self.headers.get(need[0]) != need[1]:
            self._send(403, "application/json", json.dumps({"error": "Attempted administrative access with invalid or missing key!"}), True)
            return
        self._send(route["status"], route.get("type", "application/json"), route["body"], route.get("generator", False))

    def _send(self, status, content_type, body, generator=False):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if generator:
            self.send_header("X-Generator", "tyk.io")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def stop(server):
    server.shutdown()
    server.server_close()


def serve(routes):
    handler = type("Handler", (FakeTyk,), {"routes": routes})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class FakeRedis(socketserver.StreamRequestHandler):
    password = "redis-pass-789"
    backlog = [0]

    def handle(self):
        authed = False
        while True:
            header = self.rfile.readline()
            if not header:
                return
            parts = []
            for _ in range(int(header[1:].strip())):
                size = int(self.rfile.readline()[1:].strip())
                parts.append(self.rfile.read(size + 2)[:-2].decode())
            command = parts[0].upper()
            if command == "AUTH":
                authed = parts[1] == self.password
                self.wfile.write(b"+OK\r\n" if authed else b"-WRONGPASS invalid password\r\n")
            elif not authed:
                self.wfile.write(b"-NOAUTH Authentication required.\r\n")
            elif command == "PING":
                self.wfile.write(b"+PONG\r\n")
            elif command == "LLEN":
                value = self.backlog.pop(0) if len(self.backlog) > 1 else self.backlog[0]
                self.wfile.write(f":{value}\r\n".encode())


def serve_redis(backlog):
    handler = type("Redis", (FakeRedis,), {"backlog": list(backlog)})
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"127.0.0.1:{server.server_address[1]}"


def healthy_gateway(redis_status="pass", ready=(200, {"status": "pass"}), apis=2):
    hello = {"status": "pass" if redis_status == "pass" else "warn", "version": "5.16.0", "description": "Tyk GW",
             "details": {"redis": {"status": redis_status, "output": "storage: Redis is either down or was not configured"
                                   if redis_status == "fail" else "", "componentType": "datastore"}}}
    return {
        "/hello": {"status": 200, "body": json.dumps(hello), "generator": True},
        "/ready": {"status": ready[0], "body": json.dumps(ready[1])},
        "/tyk/apis": {"status": 200, "body": json.dumps([{"api_id": f"api-{i}"} for i in range(apis)]),
                      "need": ("x-tyk-authorization", GW_SECRET)},
    }


class RedactorTests(unittest.TestCase):
    def test_removes_known_values_and_patterns(self):
        redactor = cd.Redactor([GW_SECRET, ""])
        text = (f"secret {GW_SECRET} licence {LICENCE} Authorization: abc123 Bearer tok.en "
                "password=hunter2 redis://user:pw@host:6379 key default68b0f3a1c2d4e5f60718293a4b5c6d7e8f "
                "node 092b68ba-214f-4e06-78ff-120d41dbee92 message=\"Tyk API Gateway 5.16.0\"")
        out = redactor.text(text)
        for leaked in (GW_SECRET, LICENCE, "abc123", "tok.en", "hunter2", ":pw@", "default68b0f3a1c2d4e5f60718293a4b5c6d7e8f"):
            self.assertNotIn(leaked, out)
        self.assertIn("092b68ba-214f-4e06-78ff-120d41dbee92", out)  # node ids stay readable
        self.assertIn("Tyk API Gateway 5.16.0", out)

    def test_json_style_secret_in_log_line(self):
        out = cd.Redactor([]).text('{"secret":"abcd","level":"error"}')
        self.assertNotIn("abcd", out)
        self.assertIn('"level":"error"', out)

    def test_nested_structures(self):
        out = cd.Redactor([DASH_KEY]).any({"a": [DASH_KEY, {"b": f"x {DASH_KEY}"}], "n": 3})
        self.assertEqual(out, {"a": [cd.REDACTED, {"b": f"x {cd.REDACTED}"}], "n": 3})


class LogSummaryTests(unittest.TestCase):
    def test_counts_problem_lines_in_all_three_formats(self):
        lines = [
            'time="2026-11-19T13:30:10Z" level=error message="storage: Redis is either down or was not configured"',
            'time="2026-11-19T13:30:11Z" level=error message="storage: Redis is either down or was not configured"',
            'time="2026-11-19T13:30:12Z" level=info message="Tyk API Gateway 5.16.0" prefix=main',
            '{"level":"warning","message":"Not Found","time":"2026-11-19T13:47:39Z"}',
            "2026-11-19T13:41:21.141Z\terror\tlicenser/helpers.go:79\tLicense is invalid: ",
        ]
        summary = cd.summarise_log_lines(lines, 10)
        self.assertEqual(summary["lines_read"], 5)
        top = summary["problem_lines"][0]
        self.assertEqual(top["count"], 2)
        self.assertTrue(top["line"].startswith("level=error"))
        self.assertEqual(len(summary["problem_lines"]), 3)
        self.assertFalse(any("level=info" in e["line"] for e in summary["problem_lines"]))


class FindingTests(unittest.TestCase):
    def bundle(self, **overrides):
        base = {"gateways": [], "dashboard": None, "portal": None, "redis": [], "containers": [],
                "probes": [], "log_files": []}
        base.update(overrides)
        return base

    def gw(self, ready_status, message, hello_details=None):
        return {"url": "http://gw", "hello": {"status": 200, "json": {"status": "pass", "details": hello_details or {}}},
                "ready": {"status": ready_status, "json": {"status": "error", "message": message}}}

    def texts(self, bundle):
        return " | ".join(f["finding"] for f in cd.findings(bundle))

    def test_ready_messages(self):
        self.assertIn("Redis is unreachable", self.texts(self.bundle(gateways=[self.gw(503, "Redis connection not available")])))
        self.assertIn("never loaded its APIs", self.texts(self.bundle(gateways=[self.gw(503, "A successful API reload did not happen")])))
        self.assertIn("shutting down", self.texts(self.bundle(gateways=[self.gw(503, "Gateway is shutting down")])))

    def test_dashboard_licence_and_nodes(self):
        text = self.texts(self.bundle(dashboard={"nodes": {"status": 402}}))
        self.assertIn("402", text)
        text = self.texts(self.bundle(dashboard={"nodes": {"status": 200, "nodes_remaining": 0, "licence_valid_until": "2099-01-01"}}))
        self.assertIn("no node slot left", text)
        self.assertNotIn("valid until", text)

    def test_portal_html_ready_means_licence(self):
        portal = {"live": {"status": 200}, "ready": {"status": 200, "content_type": "text/html"}}
        self.assertIn("Portal licence", self.texts(self.bundle(portal=portal)))

    def test_backlog_growth(self):
        self.assertIn("not draining", self.texts(self.bundle(redis=[{"target": "r:6379", "analytics_backlog": [5, 7]}])))
        self.assertEqual(cd.findings(self.bundle(redis=[{"target": "r:6379", "analytics_backlog": [5, 0]}])), [])

    def test_log_signatures(self):
        containers = [{"container": "gw", "problem_lines": [
            {"count": 4, "line": 'level=error message="Response failed with code 403; retrying in 5s" prefix=dashboard'}]}]
        self.assertIn("node secret", self.texts(self.bundle(containers=containers)))

    def test_missing_apis_against_dashboard(self):
        gw = {"url": "http://gw", "hello": {"status": 200, "json": {}}, "ready": {"status": 200, "json": {}},
              "loaded_apis": {"status": 200, "count": 1}}
        text = self.texts(self.bundle(gateways=[gw], dashboard={"nodes": {}, "apis": {"active": 3}}))
        self.assertIn("1 APIs loaded but the Dashboard lists 3", text)

    def test_unmatched_404_probe(self):
        probe = {"url": "http://gw/x", "status": 404, "content_type": "text/plain; charset=utf-8", "x_generator": ""}
        self.assertIn("no matching listen path", self.texts(self.bundle(probes=[probe])))
        probe = dict(probe, x_generator="tyk.io", content_type="application/json")
        self.assertEqual(cd.findings(self.bundle(probes=[probe])), [])


class RedisTests(unittest.TestCase):
    def test_auth_ping_and_backlog(self):
        server, target = serve_redis([3, 4])
        try:
            result = cd.check_redis(target, FakeRedis.password, sample_gap=0.01)
        finally:
            stop(server)
        self.assertEqual(result["ping"], "PONG")
        self.assertEqual(result["analytics_backlog"], [3, 4])

    def test_wrong_password_is_reported(self):
        server, target = serve_redis([0])
        try:
            result = cd.check_redis(target, "wrong", sample_gap=0)
        finally:
            stop(server)
        self.assertIn("WRONGPASS", result["error"])

    def test_unreachable(self):
        result = cd.check_redis("127.0.0.1:1", None, sample_gap=0)
        self.assertIn("error", result)


class EndToEndTests(unittest.TestCase):
    def test_healthy_stack_writes_redacted_bundle(self):
        gw, gw_url = serve(healthy_gateway())
        nodes = {"data": {"nodes": [{"hostname": "gw-1"}], "active_node_count": 1, "nodes_available": 10,
                          "nodes_remaining": 9, "valid_until": 4102444800}}
        dash, dash_url = serve({
            "/hello": {"status": 200, "type": "text/plain", "body": '{"status": "ok"}'},
            "/api/system/nodes": {"status": 200, "body": json.dumps(nodes), "need": ("authorization", DASH_KEY)},
            "/api/apis": {"status": 200, "body": json.dumps({"apis": [{"api_definition": {"active": True}}] * 2}),
                          "need": ("authorization", DASH_KEY)},
        })
        portal, portal_url = serve({"/live": {"status": 200, "type": "text/plain", "body": '{"code":"OK"}'},
                                    "/ready": {"status": 200, "type": "text/plain", "body": '{"code":"OK"}'}})
        redis, redis_target = serve_redis([0])
        environ = {"TYK_GW_SECRET": GW_SECRET, "TYK_DASHBOARD_API_KEY": DASH_KEY, "REDIS_PW": FakeRedis.password}
        try:
            with tempfile.TemporaryDirectory() as out, redirect_stdout(io.StringIO()) as stdout:
                code = cd.main(["--gateway", gw_url, "--dashboard", dash_url, "--portal", portal_url,
                                "--redis", redis_target, "--redis-password-env", "REDIS_PW", "--backlog-gap", "0",
                                "--out", out], environ)
                with open(os.path.join(out, "diagnostics.json")) as handle:
                    raw_json = handle.read()
                with open(os.path.join(out, "summary.md")) as handle:
                    written = raw_json + handle.read()
        finally:
            for server in (gw, dash, portal, redis):
                stop(server)
        self.assertEqual(code, 0, stdout.getvalue())
        bundle = json.loads(raw_json)
        self.assertEqual(bundle["gateways"][0]["loaded_apis"]["count"], 2)
        self.assertEqual(bundle["dashboard"]["nodes"]["licence_valid_until"], "2100-01-01")
        self.assertEqual(bundle["redis"][0]["ping"], "PONG")
        for secret in (GW_SECRET, DASH_KEY, FakeRedis.password):
            self.assertNotIn(secret, written)
            self.assertNotIn(secret, stdout.getvalue())

    def test_failing_gateway_raises_findings_and_exit_1(self):
        gw, gw_url = serve(healthy_gateway(redis_status="fail", ready=(503, {"status": "error", "message": "Redis connection not available"}), apis=0))
        try:
            with redirect_stdout(io.StringIO()) as stdout:
                code = cd.main(["--gateway", gw_url, "--probe", gw_url + "/missing"], {"TYK_GW_SECRET": GW_SECRET})
        finally:
            gw.shutdown()
        self.assertEqual(code, 1)
        text = stdout.getvalue()
        self.assertIn("Redis is unreachable", text)
        self.assertIn("loaded no APIs", text)
        self.assertIn("no matching listen path", text)

    def test_wrong_secret_is_named(self):
        gw, gw_url = serve(healthy_gateway())
        try:
            with redirect_stdout(io.StringIO()) as stdout:
                cd.main(["--gateway", gw_url], {"TYK_GW_SECRET": "not-the-secret"})
        finally:
            gw.shutdown()
        self.assertIn("refused the secret", stdout.getvalue())

    def test_log_file_is_scanned(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as handle:
            handle.write('time="x" level=error message="Listen path collision, changed to /up/-up2" api_id=up2\n')
            path = handle.name
        try:
            with redirect_stdout(io.StringIO()) as stdout:
                code = cd.main(["--log-file", path], {})
        finally:
            os.unlink(path)
        self.assertEqual(code, 1)
        self.assertIn("share a listen path", stdout.getvalue())

    def test_no_target_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as raised, redirect_stdout(io.StringIO()):
            sys.stderr, saved = io.StringIO(), sys.stderr
            try:
                cd.main([], {})
            finally:
                sys.stderr = saved
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
