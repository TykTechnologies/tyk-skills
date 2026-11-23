"""Tests for check_dashboard_queries.py (standard library only)."""
from __future__ import annotations

import http.server
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from contextlib import redirect_stdout, redirect_stderr

import check_dashboard_queries as cdq

HERE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.join(HERE, "..", "assets", "lab", "grafana", "dashboards", "tyk-gateway-otel.json")


class FakePrometheus(http.server.BaseHTTPRequestHandler):
    """Answers /api/v1/query with one series unless the query mentions 'missing_metric'."""

    queries: list[str] = []

    def do_GET(self):  # noqa: N802 (http.server naming)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("query", [""])[0]
        FakePrometheus.queries.append(query)
        result = [] if "missing_metric" in query else [{"metric": {}, "value": [0, "1"]}]
        body = json.dumps({"status": "success", "data": {"resultType": "vector", "result": result}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


class SubstituteTests(unittest.TestCase):
    def test_known_variables_and_braces(self):
        out = cdq.substitute('rate(x{job=~"$job"}[$__rate_interval]) ${job}', {"job": "gw", "__rate_interval": "1m"})
        self.assertEqual(out, 'rate(x{job=~"gw"}[1m]) gw')

    def test_unknown_variables_become_match_all(self):
        self.assertEqual(cdq.substitute('x{a=~"$api", b=~"${other}"}', {}), 'x{a=~".*", b=~".*"}')

    def test_longest_name_first(self):
        self.assertEqual(cdq.substitute("$api_id $api", {"api": "A", "api_id": "B"}), "B A")

    def test_dollar_without_name_is_kept(self):
        self.assertEqual(cdq.substitute("cost $ 5", {}), "cost $ 5")


class IterQueriesTests(unittest.TestCase):
    def test_rows_and_multiple_targets(self):
        dash = {"panels": [
            {"id": 1, "title": "a", "targets": [{"expr": "up"}, {"expr": "down"}]},
            {"id": 2, "title": "row", "panels": [{"id": 3, "title": "b", "targets": [{"expr": "x"}]}]},
            {"id": 4, "title": "text", "targets": [{"refId": "A"}]},
        ]}
        self.assertEqual([q[2] for q in cdq.iter_queries(dash)], ["up", "down", "x"])

    def test_shipped_dashboard_has_queries(self):
        with open(DASHBOARD, encoding="utf-8") as fh:
            dash = json.load(fh)
        exprs = [q[2] for q in cdq.iter_queries(dash)]
        self.assertGreaterEqual(len(exprs), 10)
        self.assertTrue(any("tyk_api_requests_total" in e for e in exprs))
        self.assertTrue(all("exported_job" not in e for e in exprs))


class MainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), FakePrometheus)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def run_main(self, dashboard: dict, *extra: str) -> tuple[int, str]:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(dashboard, fh)
            path = fh.name
        try:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cdq.main([path, "--prometheus", self.url, *extra])
            return code, out.getvalue()
        finally:
            os.unlink(path)

    def test_all_ok(self):
        code, out = self.run_main({"panels": [{"id": 1, "title": "t", "targets": [{"expr": 'up{job=~"$job"}'}]}]},
                                  "--var", "job=tyk-gateway")
        self.assertEqual(code, 0)
        self.assertIn("ok", out)
        self.assertIn('up{job=~"tyk-gateway"}', FakePrometheus.queries[-1])

    def test_empty_panel_fails(self):
        code, out = self.run_main({"panels": [{"id": 7, "title": "gone", "targets": [{"expr": "missing_metric"}]}]})
        self.assertEqual(code, 1)
        self.assertIn("EMPTY panel 7", out)

    def test_wrapped_export_is_accepted(self):
        code, _ = self.run_main({"dashboard": {"panels": [{"id": 1, "title": "t", "targets": [{"expr": "up"}]}]}})
        self.assertEqual(code, 0)

    def test_unreachable_prometheus(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"panels": [{"id": 1, "targets": [{"expr": "up"}]}]}, fh)
            path = fh.name
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = cdq.main([path, "--prometheus", "http://127.0.0.1:9"])
        finally:
            os.unlink(path)
        self.assertEqual(code, 2)

    def test_missing_file(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cdq.main(["/nonexistent.json", "--prometheus", self.url]), 2)


if __name__ == "__main__":
    unittest.main()
