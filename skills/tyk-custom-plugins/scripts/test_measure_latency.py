import http.server
import io
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_latency  # noqa: E402


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        status = 200 if self.path.startswith("/ok") else 500
        body = b"x"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class MeasureLatencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_parse_target(self):
        self.assertEqual(measure_latency.parse_target("a=http://localhost:8080/x/y?z=1"),
                         ("a", "localhost", 8080, "/x/y?z=1", False))
        self.assertEqual(measure_latency.parse_target("b=https://gw.example.com/p")[2], 443)
        with self.assertRaises(ValueError):
            measure_latency.parse_target("http://no-name")
        with self.assertRaises(ValueError):
            measure_latency.parse_target("a=ftp://x/y")

    def test_percentile(self):
        self.assertEqual(measure_latency.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9), 10)
        self.assertEqual(measure_latency.percentile([5], 0.9), 5)

    def test_run_counts_requests_and_failures(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = measure_latency.main([
                "--requests", "5", "--warmup", "1",
                f"good=http://127.0.0.1:{self.port}/ok", f"bad=http://127.0.0.1:{self.port}/fail"])
        text = out.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("good", text)
        self.assertIn("n=5 non200=0", text)
        self.assertIn("bad", text)
        self.assertIn("no successful requests (6 not 200)", text)

    def test_all_good_exits_zero(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = measure_latency.main(["--requests", "3", "--warmup", "0", "--header", "X-Test: 1",
                                         f"a=http://127.0.0.1:{self.port}/ok", f"b=http://127.0.0.1:{self.port}/ok2"])
        self.assertEqual(code, 0)
        self.assertIn("diff=+0.00ms", out.getvalue())


if __name__ == "__main__":
    unittest.main()
