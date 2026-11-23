import base64
import hashlib
import hmac
import http.server
import threading
import unittest
import urllib.parse
from types import SimpleNamespace

import sign_hmac_request as s


def args(**kw):
    base = dict(url="http://127.0.0.1:1/hmac/anything", key_id="k1", secret_env="SECRET",
                algorithm="hmac-sha256", method="GET", headers="", header=[], date="", path="", data=None)
    base.update(kw)
    return SimpleNamespace(**base)


class SigningString(unittest.TestCase):
    def test_request_target_and_date(self):
        got = s.signing_string("GET", "/hmac/anything", ["(request-target)", "date"],
                               {"date": "Thu, 19 Nov 2026 10:00:00 GMT"})
        self.assertEqual(got, "(request-target): get /hmac/anything\ndate: Thu, 19 Nov 2026 10:00:00 GMT")

    def test_missing_header_is_an_error(self):
        with self.assertRaises(ValueError):
            s.signing_string("GET", "/x", ["x-request-id"], {})


class Sign(unittest.TestCase):
    def test_matches_independent_hmac(self):
        string = "date: Thu, 19 Nov 2026 10:00:00 GMT"
        expected = urllib.parse.quote_plus(base64.b64encode(
            hmac.new(b"secret", string.encode(), hashlib.sha512).digest()).decode())
        self.assertEqual(s.sign(string, "secret", "hmac-sha512"), expected)

    def test_unknown_algorithm(self):
        with self.assertRaises(ValueError):
            s.sign("x", "secret", "md5")


class Build(unittest.TestCase):
    def test_adds_date_and_authorization(self):
        url, headers = s.build(args(), environ={"SECRET": "abc"})
        self.assertIn("Date", headers)
        self.assertTrue(headers["Authorization"].startswith('Signature keyId="k1",algorithm="hmac-sha256"'))
        self.assertIn('headers="(request-target) date"', headers["Authorization"])

    def test_empty_secret(self):
        with self.assertRaises(ValueError):
            s.build(args(), environ={})

    def test_aux_date_replaces_date_line(self):
        _, headers = s.build(args(header=["X-Aux-Date: Thu, 19 Nov 2026 10:00:00 GMT"], headers="date"),
                             environ={"SECRET": "abc"})
        expected = s.sign("x-aux-date: Thu, 19 Nov 2026 10:00:00 GMT", "abc", "hmac-sha256")
        self.assertIn(f'signature="{expected}"', headers["Authorization"])
        self.assertNotIn("Date", headers)


class Verifier(http.server.BaseHTTPRequestHandler):
    """Re-computes the signature the way the Gateway does and answers 200 or 400."""
    disable_nagle_algorithm = True
    secret = "abc"

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        fields = dict(part.split("=", 1) for part in auth[len("Signature "):].split(","))
        fields = {k: v.strip('"') for k, v in fields.items()}
        names = fields["headers"].split(" ")
        values = {n: self.headers.get(n, "") for n in names if n != "(request-target)"}
        string = s.signing_string(self.command, self.path.split("?")[0], names, values)
        ok = s.sign(string, self.secret, fields["algorithm"]) == fields["signature"]
        body = b'{"ok": true}' if ok else b'{"error": "Authorization field missing, malformed or invalid"}'
        self.send_response(200 if ok else 400)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Send(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Verifier)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/hmac/anything?x=1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_round_trip(self):
        import io
        from contextlib import redirect_stdout
        import os
        os.environ["SIGN_TEST_SECRET"] = "abc"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = s.main(["--url", self.url, "--key-id", "k1", "--secret-env", "SIGN_TEST_SECRET", "--send"])
        self.assertEqual(code, 0)
        self.assertTrue(buf.getvalue().startswith("HTTP 200"))

    def test_wrong_secret_is_rejected(self):
        import io
        from contextlib import redirect_stdout
        import os
        os.environ["SIGN_TEST_SECRET"] = "wrong"
        buf = io.StringIO()
        with redirect_stdout(buf):
            s.main(["--url", self.url, "--key-id", "k1", "--secret-env", "SIGN_TEST_SECRET", "--send"])
        self.assertTrue(buf.getvalue().startswith("HTTP 400"))


if __name__ == "__main__":
    unittest.main()
