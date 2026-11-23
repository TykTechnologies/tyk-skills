#!/usr/bin/env python3
"""Stand-in LLM backend that speaks the chat completions API.

For evaluating an AI gateway without provider keys or spend. Standard library
only. It answers:

  POST /v1/chat/completions   JSON or server-sent events (when "stream": true)
  POST /v1/embeddings         a fixed-size vector per input
  GET  /v1/models             the models in STUB_MODELS
  GET  /_last                 the last request it received (method, path, headers, body)
  POST /_mode                 {"mode": "ok" | "error" | "ratelimit" | "hang"} to simulate an outage

Every answer names the backend (STUB_NAME) in the content, in the `model`
field's sibling `system_fingerprint` and in an `x-stub-name` response header,
so a caller can tell which backend served a request.

A prompt containing the word `say-iban` makes the stub put an IBAN in its
answer, to exercise response-side filters. A prompt containing `call-tool`,
sent with a `tools` array, makes it answer with a tool call.
"""
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ.get("STUB_NAME", "stub")
PORT = int(os.environ.get("STUB_PORT", "8080"))
MODELS = [m for m in os.environ.get("STUB_MODELS", "stub-small,stub-large").split(",") if m]
STATE = {"mode": os.environ.get("STUB_MODE", "ok"), "last": None, "count": 0}
LOCK = threading.Lock()
IBAN = "DE89370400440532013000"


def count_tokens(text):
    return max(1, len(text.split()))


def last_user_text(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return str(content or "")
    return ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (NAME, fmt % args))

    def read_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            data = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline().strip():
                        pass
                    return data
                data += self.rfile.read(size)
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))

    def send_json(self, status, payload, extra=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-stub-name", NAME)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/_last":
            return self.send_json(200, {"count": STATE["count"], "last": STATE["last"], "mode": STATE["mode"]})
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            data = [{"id": m, "object": "model", "owned_by": NAME} for m in MODELS]
            return self.send_json(200, {"object": "list", "data": data})
        if self.path in ("/", "/health"):
            return self.send_json(200, {"status": "ok", "name": NAME, "mode": STATE["mode"]})
        self.send_json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self):
        raw = self.read_body()
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if self.path == "/_mode":
            STATE["mode"] = body.get("mode", "ok")
            return self.send_json(200, {"mode": STATE["mode"]})
        with LOCK:
            STATE["count"] += 1
            STATE["last"] = {
                "method": "POST",
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
        mode = STATE["mode"]
        if mode == "error":
            return self.send_json(500, {"error": {"message": "stub %s is failing" % NAME, "type": "server_error"}})
        if mode == "ratelimit":
            return self.send_json(429, {"error": {"message": "stub %s rate limit" % NAME, "type": "rate_limit_error"}},
                                  {"Retry-After": "1"})
        if mode == "hang":
            time.sleep(600)
            return
        if self.path.endswith("/embeddings"):
            return self.embeddings(body)
        if self.path.endswith("/chat/completions"):
            return self.chat(body)
        self.send_json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def embeddings(self, body):
        inputs = body.get("input", "")
        inputs = inputs if isinstance(inputs, list) else [inputs]
        data = [{"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3, 0.4]} for i in range(len(inputs))]
        tokens = sum(count_tokens(str(i)) for i in inputs)
        self.send_json(200, {"object": "list", "data": data, "model": body.get("model", MODELS[0]),
                             "usage": {"prompt_tokens": tokens, "total_tokens": tokens}})

    def chat(self, body):
        model = body.get("model") or MODELS[0]
        prompt = last_user_text(body.get("messages"))
        text = "SERVED-BY=%s model=%s echo: %s" % (NAME, model, prompt)
        if "say-iban" in prompt:
            text += " The account is %s." % IBAN
        tool_calls = None
        if "call-tool" in prompt and body.get("tools"):
            fn = body["tools"][0].get("function", {}).get("name", "tool")
            tool_calls = [{"id": "call_stub_1", "type": "function",
                           "function": {"name": fn, "arguments": json.dumps({"query": prompt})}}]
        prompt_tokens = sum(count_tokens(str(m.get("content", ""))) for m in body.get("messages", []))
        completion_tokens = count_tokens(text)
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                 "total_tokens": prompt_tokens + completion_tokens}
        base = {"id": "chatcmpl-stub-%d" % STATE["count"], "created": int(time.time()), "model": model,
                "system_fingerprint": "stub-" + NAME}
        if not body.get("stream"):
            message = {"role": "assistant", "content": None if tool_calls else text}
            if tool_calls:
                message["tool_calls"] = tool_calls
            payload = dict(base, object="chat.completion", usage=usage, choices=[
                {"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}])
            return self.send_json(200, payload)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("x-stub-name", NAME)
        self.end_headers()

        def emit(obj):
            line = ("data: %s\n\n" % (obj if isinstance(obj, str) else json.dumps(obj))).encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
            self.wfile.flush()

        chunk = dict(base, object="chat.completion.chunk")
        emit(dict(chunk, choices=[{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]))
        if tool_calls:
            delta = {"tool_calls": [dict(tool_calls[0], index=0)]}
            emit(dict(chunk, choices=[{"index": 0, "delta": delta, "finish_reason": None}]))
        else:
            words = text.split(" ")
            for i, word in enumerate(words):
                piece = word if i == len(words) - 1 else word + " "
                emit(dict(chunk, choices=[{"index": 0, "delta": {"content": piece}, "finish_reason": None}]))
        emit(dict(chunk, choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool_calls else "stop"}]))
        emit(dict(chunk, choices=[], usage=usage))
        emit("[DONE]")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    sys.stderr.write("[%s] listening on %d, models %s\n" % (NAME, PORT, ",".join(MODELS)))
    server.serve_forever()


if __name__ == "__main__":
    main()
