import http.server
import io
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

import burst_test


class LimitedHandler(http.server.BaseHTTPRequestHandler):
    """Answers 200 for the first `limit` requests, then 429 with a Tyk-style body."""

    disable_nagle_algorithm = True
    limit = 3
    seen = 0
    lock = threading.Lock()

    def do_GET(self):
        cls = type(self)
        with cls.lock:
            cls.seen += 1
            allowed = cls.seen <= cls.limit
        body = b'{"ok": true}' if allowed else b'{\n    "error": "Rate Limit Exceeded"\n}'
        self.send_response(200 if allowed else 429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-RateLimit-Limit", str(cls.limit))
        self.send_header("X-Seen-Authorization", self.headers.get("Authorization", ""))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class BurstAgainstServer(unittest.TestCase):
    def setUp(self):
        LimitedHandler.seen = 0
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LimitedHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/orders"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_counts_and_first_rejection(self):
        results, elapsed = burst_test.run_burst([self.url], 5, headers={"Authorization": "k"})
        summary = burst_test.summarise(results, elapsed, window=60)
        self.assertEqual(summary["sequence"], [200, 200, 200, 429, 429])
        self.assertEqual(summary["ok"], 3)
        self.assertEqual(summary["first_rejection"]["position"], 4)
        self.assertEqual(summary["first_rejection"]["body"], '{ "error": "Rate Limit Exceeded" }')
        self.assertEqual(summary["first_response_headers"], {"X-RateLimit-Limit": "3"})
        self.assertEqual(summary["warnings"], [])
        self.assertEqual(burst_test.check(summary, expect_ok=3, expect_reject=429), [])

    def test_main_passes_and_fails(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = burst_test.main(["--url", self.url, "--count", "5", "--expect-ok", "3", "--expect-reject", "429"])
        self.assertEqual(code, 0)
        self.assertIn("PASS", out.getvalue())
        LimitedHandler.seen = 0
        out = io.StringIO()
        with redirect_stdout(out):
            code = burst_test.main(["--url", self.url, "--count", "5", "--expect-ok", "5"])
        self.assertEqual(code, 1)
        self.assertIn("FAIL: expected 5 successful responses, got 3", out.getvalue())


class BurstLogic(unittest.TestCase):
    @staticmethod
    def fake_sender(statuses):
        queue = list(statuses)

        def sender(url, method, headers, timeout):
            return {"url": url, "status": queue.pop(0), "headers": {}, "body": ""}

        return sender

    def test_round_robin_over_gateways(self):
        sender = self.fake_sender([200, 200, 200, 429])
        results, _ = burst_test.run_burst(["http://a", "http://b"], 4, sender=sender)
        self.assertEqual([result["url"] for result in results], ["http://a", "http://b", "http://a", "http://b"])
        summary = burst_test.summarise(results, 0.5)
        self.assertEqual(summary["per_url"], {"http://a": {"200": 2}, "http://b": {"200": 1, "429": 1}})

    def test_interval_sleeps_between_requests_only(self):
        naps = []
        burst_test.run_burst(["http://a"], 3, interval=2, sender=self.fake_sender([200] * 3), sleep=naps.append)
        self.assertEqual(naps, [2, 2])

    def test_window_warning(self):
        results, _ = burst_test.run_burst(["http://a"], 2, sender=self.fake_sender([200, 200]))
        summary = burst_test.summarise(results, elapsed=61.0, window=60)
        self.assertEqual(len(summary["warnings"]), 1)
        self.assertIn("more than one window", summary["warnings"][0])
        self.assertEqual(burst_test.summarise(results, elapsed=5.0, window=60)["warnings"], [])

    def test_expectations(self):
        results, _ = burst_test.run_burst(["http://a"], 4, sender=self.fake_sender([200, 200, 403, 429]))
        summary = burst_test.summarise(results, 1.0)
        self.assertEqual(burst_test.check(summary, expect_ok=2), [])
        self.assertEqual(burst_test.check(summary, expect_ok=3, tolerance=1), [])
        self.assertTrue(burst_test.check(summary, expect_ok=4))
        self.assertIn("also got [403]", burst_test.check(summary, expect_reject=429)[0])
        all_ok, _ = burst_test.run_burst(["http://a"], 2, sender=self.fake_sender([200, 200]))
        self.assertIn("got none", burst_test.check(burst_test.summarise(all_ok, 1.0), expect_reject=429)[0])

    def test_header_parsing(self):
        self.assertEqual(burst_test.parse_header("Authorization: abc"), ("Authorization", "abc"))
        with self.assertRaises(ValueError):
            burst_test.parse_header("no-colon")
        self.assertEqual(burst_test.parse_header_env("Authorization=KEY", {"KEY": "abc"}), ("Authorization", "abc"))
        with self.assertRaises(ValueError):
            burst_test.parse_header_env("Authorization=KEY", {})
        with self.assertRaises(ValueError):
            burst_test.parse_header_env("Authorization", {})

    def test_bad_usage_exits_2(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(burst_test.main(["--url", "http://a", "--count", "0"]), 2)
            self.assertEqual(burst_test.main(["--url", "http://a", "--count", "1", "--header", "broken"]), 2)


if __name__ == "__main__":
    unittest.main()
