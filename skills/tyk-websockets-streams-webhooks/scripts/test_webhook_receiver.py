import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import webhook_receiver as r


class ReceiverTest(unittest.TestCase):
    def start(self, status=200):
        r.RECEIVED.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), r.make_handler(status, quiet=True))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def call(self, url, method="GET", body=None, headers=None):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_records_post_and_lists_it(self):
        base = self.start()
        status, _ = self.call(base + "/tyk/ratelimit", "POST", b'{"event": "RatelimitExceeded"}',
                              {"Content-Type": "application/json", "User-Agent": "Tyk-Hookshot"})
        self.assertEqual(status, 200)
        status, body = self.call(base + "/_received")
        self.assertEqual(status, 200)
        got = json.loads(body)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["method"], "POST")
        self.assertEqual(got[0]["path"], "/tyk/ratelimit")
        self.assertEqual(json.loads(got[0]["body"])["event"], "RatelimitExceeded")
        self.assertEqual(got[0]["headers"]["User-Agent"], "Tyk-Hookshot")

    def test_configured_status(self):
        base = self.start(status=500)
        status, _ = self.call(base + "/x", "POST", b"{}")
        self.assertEqual(status, 500)
        self.assertEqual(len(r.RECEIVED), 1)

    def test_delete_clears(self):
        base = self.start()
        self.call(base + "/x", "PUT", b"a")
        status, body = self.call(base + "/_received", "DELETE")
        self.assertEqual((status, json.loads(body)), (200, []))
        self.assertEqual(r.RECEIVED, [])

    def test_get_on_other_path_is_recorded(self):
        base = self.start()
        self.call(base + "/hook?x=1")
        self.assertEqual(r.RECEIVED[0]["method"], "GET")
        self.assertEqual(r.RECEIVED[0]["path"], "/hook?x=1")


if __name__ == "__main__":
    unittest.main()
