from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import base64
import hashlib
import json
import os
import re
import subprocess
import time
import uuid


ROOT = Path(os.environ.get("IDENTITY_DATA_DIR", "/data"))
PRIVATE_KEY = ROOT / "jwt-private.pem"
PUBLIC_KEY = ROOT / "jwt-public.pem"
CLIENTS = {}
ISSUER = os.environ.get("ISSUER", "http://identity:8080")
# Optional override for every URL this service advertises. Leave it unset: by default the
# URLs are built from the Host the caller used, because callers reach this service by
# different names. Host tools use localhost:<IDENTITY_PORT>, while the Portal and the
# gateway use identity:8080 from inside the Compose network. Portal DCR calls the
# registration_endpoint it reads from the discovery document, so a fixed localhost URL
# would point the Portal container at itself.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
SAFE_HOST = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?")


def b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def ensure_keys():
    ROOT.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY.exists():
        return
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(PRIVATE_KEY)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["openssl", "pkey", "-in", str(PRIVATE_KEY), "-pubout", "-out", str(PUBLIC_KEY)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def jwk():
    modulus = subprocess.check_output(["openssl", "rsa", "-pubin", "-in", str(PUBLIC_KEY), "-modulus", "-noout"], text=True).strip().split("=", 1)[1]
    number = int(modulus, 16)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "local-demo", "n": b64url(raw), "e": "AQAB"}


def mint(claims):
    header = {"alg": "RS256", "typ": "JWT", "kid": "local-demo"}
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": "tyk-demo", "sub": "demo-client", "scope": "demo.read", "iat": now, "exp": now + 3600}
    payload.update(claims)
    signing = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}.{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = subprocess.check_output(["openssl", "dgst", "-sha256", "-sign", str(PRIVATE_KEY)], input=signing.encode())
    return f"{signing}.{b64url(signature)}"


def advertised_base_url(host_header):
    """Return the base URL to advertise to a caller that sent this Host header."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = (host_header or "").strip()
    if host and SAFE_HOST.fullmatch(host):
        return f"http://{host}"
    return ISSUER


class Handler(BaseHTTPRequestHandler):
    def base_url(self):
        return advertised_base_url(self.headers.get("Host", ""))

    def json_body(self):
        length = int(self.headers.get("content-length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/.well-known/jwks.json":
            self.send_json({"keys": [jwk()]})
        elif self.path == "/.well-known/openid-configuration":
            base = self.base_url()
            self.send_json({"issuer": ISSUER, "jwks_uri": f"{base}/.well-known/jwks.json", "registration_endpoint": f"{base}/register", "token_endpoint": f"{base}/token"})
        elif self.path.startswith("/register/"):
            client_id = self.path.rsplit("/", 1)[-1]
            self.send_json(CLIENTS.get(client_id, {"error": "not_found"}), 200 if client_id in CLIENTS else 404)
        else:
            self.send_json({"status": "ok"})

    def do_POST(self):
        if self.path == "/token":
            self.send_json({"access_token": mint(self.json_body()), "token_type": "Bearer", "expires_in": 3600})
        elif self.path == "/register":
            body = self.json_body()
            client_id = uuid.uuid4().hex
            client = {**body, "client_id": client_id, "client_secret": uuid.uuid4().hex, "registration_client_uri": f"{self.base_url()}/register/{client_id}", "registration_access_token": uuid.uuid4().hex}
            CLIENTS[client_id] = client
            self.send_json(client, 201)
        else:
            self.send_json({"error": "not_found"}, 404)

    def do_PUT(self):
        if not self.path.startswith("/register/"):
            self.send_json({"error": "not_found"}, 404)
            return
        client_id = self.path.rsplit("/", 1)[-1]
        if client_id not in CLIENTS:
            self.send_json({"error": "not_found"}, 404)
            return
        preserved = {key: CLIENTS[client_id][key] for key in ("client_id", "client_secret", "registration_client_uri", "registration_access_token")}
        CLIENTS[client_id] = {**self.json_body(), **preserved}
        self.send_json(CLIENTS[client_id])

    def do_DELETE(self):
        client_id = self.path.rsplit("/", 1)[-1]
        if CLIENTS.pop(client_id, None) is None:
            self.send_json({"error": "not_found"}, 404)
        else:
            self.send_response(204)
            self.end_headers()


if __name__ == "__main__":
    ensure_keys()
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
