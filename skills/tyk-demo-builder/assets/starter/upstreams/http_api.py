from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
from pathlib import Path
import time


LOG_FILE = Path("/var/log/demo/upstream.jsonl")
OAS_DIR = Path("/app/apis")


class Handler(BaseHTTPRequestHandler):
    hits = 0

    def body(self):
        length = int(self.headers.get("content-length", "0"))
        if not length:
            return None
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def send_json(self, payload, status=200):
        data = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def response(self):
        if self.path == "/stats":
            self.send_json({"hits": Handler.hits})
            return
        # Serve the bundle's OAS definitions so the Portal can attach a specification to a
        # product via OASUrl. The developer's browser fetches this, not the Portal container,
        # which is why the response carries a permissive CORS header below. Read-only, so it
        # answers GET alone, and is not counted as a request hit, so it cannot disturb
        # upstream-counter assertions.
        if self.command == "GET" and self.path.startswith("/openapi/"):
            name = self.path.rsplit("/", 1)[-1]
            if name.endswith(".json") and "/" not in name and ".." not in name:
                candidate = OAS_DIR / name
                if candidate.is_file():
                    body = candidate.read_bytes()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    # The Portal's specification renderer fetches this from the developer's
                    # browser, so the request is cross-origin relative to the Portal itself.
                    # Without this header the renderer reports only "Document could not be
                    # loaded" and the browser console shows an opaque "Failed to fetch".
                    self.send_header("access-control-allow-origin", "*")
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self.send_json({"error": "specification not found"}, 404)
            return
        Handler.hits += 1
        traceparent = self.headers.get("traceparent", "")
        trace_id = traceparent.split("-")[1] if traceparent.count("-") >= 3 else ""
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": int(time.time()), "service": "demo-upstream", "trace_id": trace_id, "path": self.path}) + "\n")
        if self.path.startswith("/status/"):
            try:
                status = int(self.path.split("/", 2)[2])
            except (ValueError, IndexError):
                status = 500
            self.send_json({"unsafe_internal_detail": "must not reach the client", "status": status}, status)
            return
        if self.path.endswith("/failure") or self.path == "/v1/breaker":
            self.send_json({"code": "UPSTREAM_UNAVAILABLE"}, 503)
            return
        parts = self.path.strip("/").split("/")
        self.send_json({
            "region": parts[0] if parts and parts[0] else "global",
            "path": self.path,
            "body": self.body(),
            "hits": Handler.hits,
            "timestamp": int(time.time()),
            "headers": {
                "x-correlation-id": self.headers.get("X-Correlation-Id", ""),
                "traceparent": traceparent,
                "x-demo-owner": self.headers.get("X-Demo-Owner", ""),
                "x-demo-post-hook": self.headers.get("X-Demo-Post-Hook", ""),
                "x-grpc-enriched": self.headers.get("X-GRPC-Enriched", ""),
                # Which API version served the request (versioning module).
                "x-service-version": self.headers.get("X-Service-Version", ""),
                # A credential the gateway injected (vault module) is reported as a hash, so
                # the value itself never reaches a response body or an evidence file.
                "x-upstream-credential-sha256": hashlib.sha256(self.headers["X-Upstream-Credential"].encode()).hexdigest()
                if self.headers.get("X-Upstream-Credential") else "",
            },
        })

    do_GET = response
    do_POST = response
    do_PUT = response


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
