#!/usr/bin/env python3
"""Receive Tyk Gateway webhooks and show exactly what arrived.

Python standard library only. Every request is printed and kept in memory; GET /_received
returns them as JSON (method, path, headers, body, time), so a test can assert on them.

    python3 webhook_receiver.py --port 9000                 # answer 200 to every webhook
    python3 webhook_receiver.py --port 9000 --status 500    # answer 500, to see what Tyk does
    curl -s localhost:9000/_received                        # what arrived so far
    curl -s -X DELETE localhost:9000/_received              # forget them

Run it where the Gateway can reach it (in a container on the Gateway's network, or on the host
with host.docker.internal). For a lab only: it has no authentication.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECEIVED: list[dict] = []
LOCK = threading.Lock()


def make_handler(status: int, quiet: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True  # http.server otherwise adds about 40 ms per response

        def _reply(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _record(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            entry = {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "method": self.command,
                "path": self.path,
                "headers": {k: v for k, v in self.headers.items()},
                "body": raw.decode(errors="replace"),
            }
            with LOCK:
                RECEIVED.append(entry)
            if not quiet:
                print(json.dumps(entry), flush=True)
            self._reply(status, json.dumps({"received": True}).encode())

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/_received"):
                with LOCK:
                    body = json.dumps(RECEIVED, indent=2).encode()
                self._reply(200, body)
                return
            self._record()

        def do_DELETE(self):  # noqa: N802
            if self.path.startswith("/_received"):
                with LOCK:
                    RECEIVED.clear()
                self._reply(200, b"[]")
                return
            self._record()

        do_POST = do_PUT = do_PATCH = _record  # noqa: N815

        def log_message(self, fmt, *args):  # keep stdout for the JSON lines
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--status", type=int, default=200, help="status code to answer webhooks with")
    p.add_argument("--quiet", action="store_true", help="do not print each request")
    args = p.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.status, args.quiet))
    print(f"listening on {args.host}:{args.port}, answering {args.status}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
