import base64
import hashlib
import io
import os
import socket
import struct
import threading
import unittest
from contextlib import redirect_stdout

import ws_probe as w


def server_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """An unmasked server frame, as a WebSocket server sends it."""
    head = bytes([0x80 | opcode])
    if len(payload) < 126:
        head += bytes([len(payload)])
    else:
        head += bytes([126]) + struct.pack("!H", len(payload))
    return head + payload


class FrameTest(unittest.TestCase):
    def test_accept_value_matches_rfc_example(self):
        self.assertEqual(w.accept_value("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_round_trip(self):
        for size in (0, 5, 125, 126, 300, 70000):
            payload = os.urandom(size)
            frame = w.encode_frame(payload, mask_key=b"\x01\x02\x03\x04")
            opcode, decoded, used = w.decode_frame(frame)
            self.assertEqual((opcode, decoded, used), (0x1, payload, len(frame)), size)

    def test_incomplete_frame_returns_none(self):
        frame = server_frame(b"hello")
        self.assertIsNone(w.decode_frame(frame[:3]))
        self.assertIsNone(w.decode_frame(b""))

    def test_parse_headers(self):
        self.assertEqual(w.parse_headers(["Authorization: abc", "X-A:b:c"]),
                         [("Authorization", "abc"), ("X-A", "b:c")])
        with self.assertRaises(SystemExit):
            w.parse_headers(["no-colon"])


class FakeServer:
    """A one-connection server: answers the upgrade (or refuses it) and echoes text frames."""

    def __init__(self, refuse: bool = False):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.refuse = refuse
        self.request = b""
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.sock.accept()
        try:
            self.handle(conn)
        finally:
            conn.close()
            self.sock.close()

    def handle(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(4096)
        self.request = data
        if self.refuse:
            body = b'{"error": "Authorization field missing"}'
            conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: " + str(len(body)).encode()
                         + b"\r\n\r\n" + body)
            conn.close()
            return
        key = [l.split(b":", 1)[1].strip() for l in data.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1(key + w.GUID.encode()).digest())
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        buf = b""
        conn.settimeout(3)
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    frame = w.decode_frame(buf)
                    if frame is None:
                        break
                    opcode, payload, used = frame
                    buf = buf[used:]
                    if opcode == 0x8:
                        conn.close()
                        return
                    if payload == b"split":
                        # A fragmented text message: first frame without FIN, then a continuation.
                        conn.sendall(bytes([0x01, 3]) + b"spl" + server_frame(b"it", opcode=0x0))
                        continue
                    conn.sendall(server_frame(payload))
        except OSError:
            pass


class ProbeTest(unittest.TestCase):
    def run_probe(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = w.main(list(args))
        return code, buf.getvalue()

    def test_echo(self):
        srv = FakeServer()
        code, out = self.run_probe(f"ws://127.0.0.1:{srv.port}/chat/?access_token=k", "-H", "X-Test: 1",
                                   "--send", "hello", "--listen", "0.5")
        self.assertEqual(code, 0)
        self.assertIn("handshake: 101", out)
        self.assertIn("< hello", out)
        self.assertIn(b"GET /chat/?access_token=k HTTP/1.1", srv.request)
        self.assertIn(b"X-Test: 1", srv.request)

    def test_continuation_frames_are_printed(self):
        srv = FakeServer()
        code, out = self.run_probe(f"ws://127.0.0.1:{srv.port}/x", "--send", "split", "--listen", "0.5")
        self.assertEqual(code, 0)
        self.assertIn("< spl", out)
        self.assertIn("< (continued) it", out)

    def test_refused_upgrade_prints_body(self):
        srv = FakeServer(refuse=True)
        code, out = self.run_probe(f"ws://127.0.0.1:{srv.port}/x", "--send", "hi")
        self.assertEqual(code, 1)
        self.assertIn("handshake refused: HTTP/1.1 401 Unauthorized", out)
        self.assertIn("Authorization field missing", out)

    def test_keepalive_counts_echoes(self):
        srv = FakeServer()
        code, out = self.run_probe(f"ws://127.0.0.1:{srv.port}/x", "--hold", "1.2", "--keepalive", "0.3")
        self.assertEqual(code, 0)
        self.assertIn("keepalive frames echoed:", out)
        self.assertIn("still open after", out)
        echoed = int(out.split("keepalive frames echoed:")[1].split()[0])
        self.assertGreaterEqual(echoed, 2)

    def test_rejects_http_scheme(self):
        with self.assertRaises(SystemExit):
            self.run_probe("http://127.0.0.1:1/x")


if __name__ == "__main__":
    unittest.main()
