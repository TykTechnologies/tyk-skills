#!/usr/bin/env python3
"""Tests for governance_check.py. Run: python3 -m unittest test_governance_check.py"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import governance_check as gc  # noqa: E402

# Response bodies in the shape Dashboard 5.16.0 returned on 2026-11-19.
NON_COMPLIANT = {
    "rulesets": [
        {"id": "house-standards", "name": "House standards", "resource_type": "oas-api", "active": True,
         "action": "warn", "error_count": 2, "warn_count": 0, "info_count": 0, "hint_count": 0}
    ],
    "issues": [
        {"ruleset_id": "house-standards", "rule_id": "operation-operationId", "severity": "error",
         "path": "paths./orders/{id}.operationId", "message": "Operation has no operationId.",
         "how_to_fix": "Add a unique operationId."},
        {"ruleset_id": "house-standards", "rule_id": "tyk-authentication-enabled", "severity": "error",
         "path": "$['x-tyk-api-gateway'].server.authentication", "message": "authentication is missing.",
         "how_to_fix": "Add server.authentication."},
    ],
    "total": 2, "truncated": False, "error_count": 2, "warn_count": 0, "info_count": 0, "hint_count": 0,
    "evaluated_apis": 1, "elapsed_ms": 243,
}
UNKNOWN_CATEGORY = {"rulesets": [], "issues": [], "total": 0, "truncated": False, "error_count": 0,
                    "warn_count": 0, "info_count": 0, "hint_count": 0, "evaluated_apis": 0, "elapsed_ms": 0}


def ruleset(rid, action, errors=0, warns=0):
    return {"id": rid, "name": rid, "action": action, "error_count": errors, "warn_count": warns, "info_count": 0}


class EvaluateTest(unittest.TestCase):
    def test_errors_fail_standard(self):
        code, lines = gc.evaluate(NON_COMPLIANT, "standard", False)
        self.assertEqual(code, gc.NON_COMPLIANT)
        self.assertTrue(any("fix: Add a unique operationId." in line for line in lines))

    def test_unknown_category_cannot_evaluate(self):
        with self.assertRaisesRegex(gc.GateError, "no ruleset matched"):
            gc.evaluate(UNKNOWN_CATEGORY, "standard", False)

    def test_truncated_cannot_evaluate(self):
        body = dict(NON_COMPLIANT, truncated=True)
        with self.assertRaisesRegex(gc.GateError, "truncated"):
            gc.evaluate(body, "permissive", False)

    def test_error_field_cannot_evaluate(self):
        body = dict(NON_COMPLIANT, error="boom")
        with self.assertRaisesRegex(gc.GateError, "evaluation error"):
            gc.evaluate(body, "standard", False)

    def test_missing_rulesets_cannot_evaluate(self):
        with self.assertRaises(gc.GateError):
            gc.evaluate({"Status": "Error"}, "standard", False)

    def test_action_none_gates_by_default(self):
        body = {"rulesets": [ruleset("visibility", "none", errors=1)], "issues": [], "truncated": False}
        self.assertEqual(gc.evaluate(body, "standard", False)[0], gc.NON_COMPLIANT)

    def test_honor_action_skips_none(self):
        body = {"rulesets": [ruleset("visibility", "none", errors=1), ruleset("gate", "warn")], "issues": [], "truncated": False}
        self.assertEqual(gc.evaluate(body, "standard", True)[0], gc.PASS)

    def test_honor_action_with_only_none_cannot_evaluate(self):
        body = {"rulesets": [ruleset("visibility", "none", errors=1)], "issues": [], "truncated": False}
        with self.assertRaisesRegex(gc.GateError, "nothing gates"):
            gc.evaluate(body, "standard", True)

    def test_strictness_levels_on_warnings(self):
        body = {"rulesets": [ruleset("r", "warn", warns=3)], "issues": [], "truncated": False}
        self.assertEqual(gc.evaluate(body, "strict", False)[0], gc.WARN_ONLY)
        self.assertEqual(gc.evaluate(body, "standard", False)[0], gc.PASS)
        self.assertEqual(gc.evaluate(body, "permissive", False)[0], gc.PASS)

    def test_permissive_passes_errors(self):
        self.assertEqual(gc.evaluate(NON_COMPLIANT, "permissive", False)[0], gc.PASS)


class QueryTest(unittest.TestCase):
    def test_exactly_one_selector(self):
        with self.assertRaises(gc.GateError):
            gc.build_query(None, None, None, None)
        with self.assertRaises(gc.GateError):
            gc.build_query("a", "b", None, None)

    def test_limit_of_twenty(self):
        with self.assertRaisesRegex(gc.GateError, "at most 20"):
            gc.build_query(",".join(f"c{i}" for i in range(21)), None, None, None)

    def test_query_shape(self):
        query = gc.build_query(None, "house-standards, owasp-baseline", "orders", "Orders API")
        self.assertEqual(query, "ruleset_ids=house-standards,owasp-baseline&api_id=orders&api_name=Orders+API")

    def test_content_type(self):
        self.assertEqual(gc.content_type_for("a.json"), "application/json")
        self.assertEqual(gc.content_type_for("a.YML"), "application/yaml")
        with self.assertRaises(gc.GateError):
            gc.content_type_for("a.txt")


class ReadbackTest(unittest.TestCase):
    SENT = {"ruleset_id": "r", "name": "R", "description": "", "active": True, "resource_type": "oas-api",
            "action": "warn", "categories": ["payments"], "definition": '{"rules": {"a": {"given": "$"}}}'}

    def test_compact_json_is_equal(self):
        stored = dict(self.SENT, definition='{"rules":{"a":{"given":"$"}}}')
        self.assertEqual(gc.readback_problems(self.SENT, stored), [])

    def test_deactivated_by_put_is_reported(self):
        stored = dict(self.SENT, active=False)
        self.assertIn("active", gc.readback_problems(self.SENT, stored)[0])

    def test_yaml_sent_json_stored(self):
        sent = dict(self.SENT, definition="rules:\n  a:\n    given: $\n")
        self.assertEqual(gc.readback_problems(sent, dict(self.SENT)), [])

    def test_changed_rules_reported(self):
        stored = dict(self.SENT, definition='{"rules":{}}', categories=[])
        problems = gc.readback_problems(self.SENT, stored)
        self.assertTrue(any(p.startswith("definition") for p in problems))
        self.assertTrue(any(p.startswith("categories") for p in problems))


class FakeDashboard(BaseHTTPRequestHandler):
    status = 200
    body: dict | str = NON_COMPLIANT
    seen: list = []

    def _send(self, status, payload):
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        FakeDashboard.seen.append((self.path, self.headers.get("Authorization"), self.headers.get("Content-Type"), self.rfile.read(length)))
        self._send(FakeDashboard.status, FakeDashboard.body)

    def do_PUT(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        FakeDashboard.stored = json.loads(self.rfile.read(length))
        FakeDashboard.seen.append((self.command + " " + self.path, None, None, b""))
        self._send(200, FakeDashboard.stored)

    def do_GET(self):  # noqa: N802
        FakeDashboard.seen.append((self.path, self.headers.get("Authorization"), None, b""))
        if self.path.endswith("/stored"):
            self._send(200, FakeDashboard.stored)
        elif self.path.endswith("/missing"):
            self._send(404, {"Status": "Error", "Message": "ruleset not found"})
        else:
            self._send(200, {"ruleset_id": "house-standards", "definition": '{"rules":{}}'})

    def log_message(self, *args):
        pass


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeDashboard)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.env = {"TYK_DASHBOARD_URL": f"http://127.0.0.1:{cls.server.server_port}/", "TYK_DASHBOARD_TOKEN": "test-token"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeDashboard.status, FakeDashboard.body, FakeDashboard.seen = 200, NON_COMPLIANT, []
        self.tmp = tempfile.TemporaryDirectory()
        self.spec = Path(self.tmp.name) / "orders.yaml"
        self.spec.write_text("openapi: 3.0.3\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_check_sends_raw_spec_and_gates(self):
        code, out, _ = self.run_main(["check", str(self.spec), "--categories", "payments", "--api-id", "orders"])
        self.assertEqual(code, gc.NON_COMPLIANT)
        path, auth, ctype, body = FakeDashboard.seen[0]
        self.assertEqual(path, "/api/evaluations/sandbox/oas?categories=payments&api_id=orders")
        self.assertEqual((auth, ctype, body), ("test-token", "application/yaml", b"openapi: 3.0.3\n"))
        self.assertIn("Result: non-compliant", out)

    def test_http_error_cannot_evaluate(self):
        FakeDashboard.status, FakeDashboard.body = 403, {"Status": "Error", "Message": "Forbidden"}
        code, _, err = self.run_main(["check", str(self.spec), "--ruleset-ids", "x"])
        self.assertEqual(code, gc.CANNOT_EVALUATE)
        self.assertIn("HTTP 403", err)

    def test_non_json_cannot_evaluate(self):
        FakeDashboard.body = "<html>"
        self.assertEqual(self.run_main(["check", str(self.spec), "--ruleset-ids", "x"])[0], gc.CANNOT_EVALUATE)

    def test_house_names_are_read(self):
        env = {"TYK_DASHBOARD_URL": "", "TYK_DASHBOARD_TOKEN": "", "DASH": "http://dash.example/", "DASHBOARD_API_KEY": "k"}
        with unittest.mock.patch.dict(os.environ, env):
            self.assertEqual(gc.dashboard_env(), ("http://dash.example", "k"))
        env.update({"TYK_DASHBOARD_URL": "http://ci.example", "TYK_DASHBOARD_TOKEN": "t"})
        with unittest.mock.patch.dict(os.environ, env):
            self.assertEqual(gc.dashboard_env(), ("http://ci.example", "t"))

    def test_missing_env_cannot_evaluate(self):
        with unittest.mock.patch.dict(os.environ, {"TYK_DASHBOARD_URL": "", "TYK_DASHBOARD_TOKEN": "", "DASH": "", "DASHBOARD_API_KEY": ""}):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gc.main(["check", str(self.spec), "--ruleset-ids", "x"]), gc.CANNOT_EVALUATE)

    def test_export_writes_definition(self):
        out_dir = Path(self.tmp.name) / "rules"
        code, out, _ = self.run_main(["export", "--ruleset-ids", "house-standards", "--out", str(out_dir)])
        self.assertEqual(code, gc.PASS)
        self.assertEqual((out_dir / "house-standards.json").read_text(), '{"rules":{}}\n')

    def test_apply_replaces_and_verifies(self):
        FakeDashboard.stored = {"ruleset_id": "stored", "active": False}
        rules = Path(self.tmp.name) / "rules.json"
        rules.write_text('{"rules": {}}')
        code, out, err = self.run_main(["apply", "--ruleset-id", "stored", "--name", "Stored", "--rules", str(rules),
                                        "--categories", "payments", "--action", "warn"])
        self.assertEqual(code, gc.PASS, err)
        self.assertIn("replaced ruleset stored: active=True action=warn", out)
        self.assertTrue(FakeDashboard.stored["active"])
        self.assertTrue(any(entry[0] == "PUT /api/rulesets/stored" for entry in FakeDashboard.seen))

    def test_export_missing_ruleset(self):
        code, _, err = self.run_main(["export", "--ruleset-ids", "missing", "--out", self.tmp.name])
        self.assertEqual(code, gc.CANNOT_EVALUATE)
        self.assertIn("HTTP 404", err)



if __name__ == "__main__":
    unittest.main()
