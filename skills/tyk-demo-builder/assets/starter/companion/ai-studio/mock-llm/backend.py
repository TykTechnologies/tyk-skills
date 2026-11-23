#!/usr/bin/env python3
"""Minimal OpenAI-compatible stub LLM backend.

Exists so the AI Studio companion can prove *which* backend served a request
(routing, the unified ingress, failover) without real provider credentials or
spend. Each instance identifies itself via the BACKEND_NAME environment
variable, echoed back in the completion text, in the `model` field and in the
`x-backend-name` response header.

Serves POST /v1/chat/completions (the path AI Studio's /llm/ hop forwards to
when an LLM is configured with vendor=openai and a custom API endpoint), both
as plain JSON and, when the request carries `stream: true`, as an OpenAI
server-sent-event stream.
"""
import json
import os
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND_NAME = os.environ.get("BACKEND_NAME", "unknown-backend")
PORT = int(os.environ.get("PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (BACKEND_NAME, fmt % args), flush=True)

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-backend-name", BACKEND_NAME)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"status": "ok", "backend": BACKEND_NAME})
        else:
            self._send(404, {"error": "not found"})

    def _read_body(self):
        # AI Studio forwards request bodies with Transfer-Encoding: chunked
        # (no Content-Length). Reading only Content-Length leaves the chunk data
        # on the socket, where the next read parses it as a new request line and
        # answers 400 "Bad request syntax ('8a')" on the gateway's idle channel.
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            body = b""
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line.split(b";", 1)[0] or b"0", 16)
                if size == 0:
                    # Drain optional trailers up to the terminating blank line.
                    while self.rfile.readline().strip():
                        pass
                    return body
                body += self.rfile.read(size)
                self.rfile.readline()  # CRLF after each chunk
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _send_sse(self, events):
        # Server-sent events, one "data:" line per chunk, then the [DONE] marker.
        body = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
        body += b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-backend-name", BACKEND_NAME)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self._read_body() or b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            req = {}

        if not self.path.endswith("/chat/completions"):
            self._send(404, {"error": {"message": "unsupported path " + self.path}})
            return

        requested_model = req.get("model", "unspecified")
        stream = bool(req.get("stream"))
        print(
            "[%s] chat/completions model=%s stream=%s"
            % (BACKEND_NAME, requested_model, stream),
            flush=True,
        )
        completion_id = "chatcmpl-%s-%d" % (BACKEND_NAME, int(time.time() * 1000))
        created = int(time.time())
        content = "SERVED-BY=%s MODEL=%s" % (BACKEND_NAME, requested_model)
        usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

        if stream:
            # AI Studio's OpenAI-compatible bridge (/ai/{slug}, the unified
            # /v1 ingress and the Model Router) drives the upstream through a
            # streaming client even for a non-streaming caller, and reassembles
            # the answer from the SSE chunks. A JSON-only stub therefore comes
            # back to the caller as an empty completion with zero usage, with a
            # 200 status that hides the failure.
            base = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": requested_model,
            }
            events = [
                dict(base, choices=[{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]),
                dict(base, choices=[{"index": 0, "delta": {"content": content}, "finish_reason": None}]),
                dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]),
                dict(base, choices=[], usage=usage),
            ]
            self._send_sse(events)
            return

        self._send(
            200,
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": requested_model,
                "backend": BACKEND_NAME,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            },
        )


def _stop(signum, _frame):
    # The stub runs as PID 1 in its container, where the kernel gives SIGTERM no
    # default action: without a handler every `docker compose stop` (and the
    # failover proof's stop of llm-alpha) waits out the 10 s grace period and
    # ends in SIGKILL. Leave serve_forever() at once instead.
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("stub LLM backend '%s' listening on :%d" % (BACKEND_NAME, PORT), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
