"""Unit tests for aistudio_admin.py against an in-process fake control plane."""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aistudio_admin  # noqa: E402


class FakeStudio(BaseHTTPRequestHandler):
    """Mimics the control plane behaviour the client depends on."""

    state: dict = {}

    def log_message(self, *args):
        pass

    def reply(self, status, payload=None, headers=None, content_type="application/json"):
        body = b"" if payload is None else (payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        return json.loads(raw) if raw else None

    def csrf_ok(self):
        return (self.headers.get("X-CSRF-Token") == "csrf-1"
                and "_gorilla_csrf=cookie-1" in self.headers.get("Cookie", ""))

    def bearer_ok(self):
        return self.headers.get("Authorization") == "Bearer " + self.state.get("api_key", "none")

    def do_GET(self):
        state = self.state
        if self.path == "/csrf-token":
            return self.reply(200, None, [("X-CSRF-Token", "csrf-1"),
                                          ("Set-Cookie", "_gorilla_csrf=cookie-1; Path=/; HttpOnly")])
        if not self.bearer_ok():
            return self.reply(401, {"error": "Unauthorized"})
        if self.path == "/api/v1/sync/status":
            state["status_calls"] = state.get("status_calls", 0) + 1
            pending = state["status_calls"] <= state.get("pending_polls", 0)
            return self.reply(200, {"data": [{"namespace": "default", "synced_count": 0 if pending else 1,
                                              "total_edges": 1}],
                                    "has_pending": pending, "pending_namespaces": ["default"] if pending else []})
        if self.path.startswith("/api/v1/edges"):
            pending = state.get("status_calls", 0) <= state.get("pending_polls", 0)
            rows = [{"type": "edges", "attributes": dict(edge, sync_status="pending" if pending and
                     edge["status"] == "connected" else edge["sync_status"])}
                    for edge in state.get("edges", [{"edge_id": "edge-01", "status": "connected",
                                                     "sync_status": "in_sync"}])]
            return self.reply(200, {"data": rows})
        if self.path == "/api/v1/apps/7":
            return self.reply(200, {"data": {"type": "app", "id": "7", "attributes": {"credential_id": 9}}})
        if self.path == "/api/v1/credentials/9":
            return self.reply(200, {"data": {"attributes": {"secret": "app-secret-value", "active": True}}})
        if self.path == "/api/v1/llms":
            return self.reply(200, {"data": []})
        return self.reply(200, b"<!doctype html><html></html>", content_type="text/html; charset=utf-8")

    def do_POST(self):
        state = self.state
        payload = self.body()
        if self.path == "/auth/register":
            if not self.csrf_ok():
                return self.reply(403, b"Forbidden - CSRF token not found in request", content_type="text/plain")
            if state.get("registered"):
                detail = "email already in use, please log in, verify emil, or reset password"
                return self.reply(400, {"errors": [{"title": "Bad Request", "detail": detail}]})
            state["registered"] = payload["data"]["attributes"]
            return self.reply(201, {"message": "User registered successfully"})
        if self.path == "/auth/login":
            if not self.csrf_ok():
                return self.reply(403, b"Forbidden - CSRF token not found in request", content_type="text/plain")
            wanted = state.get("registered", {})
            given = payload["data"]["attributes"]
            if given.get("password") != wanted.get("password"):
                return self.reply(401, {"error": "Invalid credentials"})
            return self.reply(200, {"message": "Login successful", "is_admin": state.get("is_admin", True)},
                              [("Set-Cookie", "session=session-1; Path=/; HttpOnly; Secure")])
        if self.path == "/common/me/api-key/roll":
            if not self.csrf_ok() or "session=session-1" not in self.headers.get("Cookie", ""):
                return self.reply(401, {"error": "Unauthorized"})
            state["api_key"] = "token-%d" % (state.get("rolls", 0) + 1)
            state["rolls"] = state.get("rolls", 0) + 1
            return self.reply(200, {"data": {"api_key": state["api_key"]}})
        if not self.bearer_ok():
            return self.reply(401, {"error": "Unauthorized"})
        if self.path == "/api/v1/edges/reload-all":
            state["pushes"] = state.get("pushes", 0) + 1
            return self.reply(202, {"data": {"attributes": {"message": "Push recorded for 1 edge(s)."}}})
        if self.path == "/api/v1/apps/7/activate-credential":
            state["activated"] = True
            return self.reply(204)
        if self.path == "/api/v1/llms":
            state["created_llm"] = payload
            return self.reply(201, {"data": {"type": "llms", "id": "3"}})
        return self.reply(404, {"error": "not found"})


class AdminClientTest(unittest.TestCase):
    def setUp(self):
        FakeStudio.state = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeStudio)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.dir = tempfile.TemporaryDirectory()
        self.token_file = os.path.join(self.dir.name, "token")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def test_bootstrap_registers_logs_in_and_stores_token(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        self.assertEqual(token, "token-1")
        self.assertEqual(Path(self.token_file).read_text(), "token-1")
        self.assertEqual(stat.S_IMODE(os.stat(self.token_file).st_mode), 0o600)
        self.assertEqual(FakeStudio.state["registered"]["email"], "admin@example.com")

    def test_bootstrap_is_repeatable_when_the_admin_exists(self):
        aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        self.assertEqual(token, "token-2")
        self.assertEqual(Path(self.token_file).read_text(), "token-2")

    def test_bootstrap_refuses_a_non_admin(self):
        FakeStudio.state["is_admin"] = False
        with self.assertRaises(aistudio_admin.ApiError) as raised:
            aistudio_admin.bootstrap(self.base, "second@example.com", "Second", "Good-Pass1!", self.token_file)
        self.assertIn("not an admin", str(raised.exception))
        self.assertFalse(os.path.exists(self.token_file))

    def test_bootstrap_reports_a_wrong_password(self):
        aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        with self.assertRaises(aistudio_admin.ApiError) as raised:
            aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Wrong-Pass1!", self.token_file)
        self.assertIn("/auth/login gave 401", str(raised.exception))

    def test_request_sends_the_bearer_token_and_body(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        body = {"data": {"type": "llms", "attributes": {"name": "Stub Alpha"}}}
        status, answer = aistudio_admin.api(self.base, token, "POST", "/api/v1/llms", body)
        self.assertEqual(status, 201)
        self.assertEqual(answer["data"]["id"], "3")
        self.assertEqual(FakeStudio.state["created_llm"], body)

    def test_request_flags_the_admin_ui_page_for_an_unknown_route(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        with self.assertRaises(aistudio_admin.ApiError) as raised:
            aistudio_admin.api(self.base, token, "GET", "/api/v1/does-not-exist")
        self.assertIn("route does not exist", str(raised.exception))

    def test_wrong_token_is_a_401_not_an_exception(self):
        status, answer = aistudio_admin.api(self.base, "wrong", "GET", "/api/v1/llms")
        self.assertEqual(status, 401)
        self.assertEqual(answer, {"error": "Unauthorized"})

    def test_push_waits_until_nothing_is_pending(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        FakeStudio.state["pending_polls"] = 2
        edges = aistudio_admin.push(self.base, token, wait=5, poll=0.01)
        self.assertEqual([edge["sync_status"] for edge in edges], ["in_sync"])
        self.assertEqual(FakeStudio.state["pushes"], 1)
        self.assertEqual(FakeStudio.state["status_calls"], 3)

    def test_push_fails_when_no_edge_is_connected(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        FakeStudio.state["edges"] = [{"edge_id": "edge-01", "status": "disconnected", "sync_status": "in_sync"}]
        with self.assertRaises(aistudio_admin.ApiError) as raised:
            aistudio_admin.push(self.base, token, wait=0.05, poll=0.01)
        self.assertIn("no edge gateway is connected", str(raised.exception))

    def test_push_ignores_a_disconnected_edge_when_another_is_in_sync(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        FakeStudio.state["edges"] = [{"edge_id": "edge-01", "status": "connected", "sync_status": "in_sync"},
                                     {"edge_id": "edge-02", "status": "disconnected", "sync_status": "in_sync"}]
        edges = aistudio_admin.push(self.base, token, wait=5, poll=0.01)
        self.assertEqual(len(edges), 2)

    def test_push_times_out_while_edges_stay_pending(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        FakeStudio.state["pending_polls"] = 10 ** 6
        with self.assertRaises(aistudio_admin.ApiError) as raised:
            aistudio_admin.push(self.base, token, wait=0.05, poll=0.01)
        self.assertIn("still not in sync", str(raised.exception))

    def test_app_secret_activates_then_reads_the_credential(self):
        token = aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        self.assertEqual(aistudio_admin.app_secret(self.base, token, "7"), "app-secret-value")
        self.assertTrue(FakeStudio.state["activated"])

    def test_main_request_exit_codes(self):
        aistudio_admin.bootstrap(self.base, "admin@example.com", "Example Admin", "Good-Pass1!", self.token_file)
        common = ["--url", self.base, "--token-file", self.token_file]
        self.assertEqual(aistudio_admin.main(common + ["request", "GET", "/api/v1/llms"]), 0)
        self.assertEqual(aistudio_admin.main(common + ["request", "POST", "/api/v1/unknown", "--data", "{}"]), 1)
        self.assertEqual(aistudio_admin.main(common + ["request", "GET", "/api/v1/does-not-exist"]), 1)

    def test_main_needs_a_token_file_and_a_url(self):
        self.assertEqual(aistudio_admin.main(["--url", self.base, "--token-file", self.token_file, "push"]), 1)
        self.assertEqual(aistudio_admin.main(["--url", "", "push"]), 2)

    def test_cookie_helpers(self):
        pairs = aistudio_admin.cookie_pairs(["a=1; Path=/; HttpOnly", "session=x=y; Secure"])
        self.assertEqual(pairs, {"a": "1", "session": "x=y"})
        self.assertEqual(aistudio_admin.cookie_header(pairs), "a=1; session=x=y")


if __name__ == "__main__":
    unittest.main()
