"""HTTPS upstream for the upstream-mtls module.

Requires a client certificate issued by the bundle's upstream CA and refuses the TLS
handshake without one. Each response reports the common name of the client certificate it
saw, so a proof can show which identity the gateway presented. The handshake runs in the
request thread, so a slow or refused client never blocks the listener.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl


CERTS = Path("/certs")
CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
CONTEXT.load_cert_chain(CERTS / "server.crt", CERTS / "server.key")
CONTEXT.load_verify_locations(CERTS / "ca.crt")
CONTEXT.verify_mode = ssl.CERT_REQUIRED


class Handler(BaseHTTPRequestHandler):
    hits = 0

    def respond(self):
        Handler.hits += 1
        peer = self.connection.getpeercert() or {}
        names = [value for rdn in peer.get("subject", ()) for key, value in rdn if key == "commonName"]
        data = json.dumps({
            "path": self.path,
            "client_cn": names[0] if names else "",
            "tls_version": self.connection.version(),
            "hits": Handler.hits,
        }, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = respond
    do_POST = respond

    def log_message(self, *args):
        pass


class TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def finish_request(self, request, client_address):
        try:
            connection = CONTEXT.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            # No client certificate, or one from another CA: refused during the handshake.
            return
        self.RequestHandlerClass(connection, client_address, self)


TLSServer(("0.0.0.0", 8443), Handler).serve_forever()
