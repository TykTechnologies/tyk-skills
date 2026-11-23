#!/usr/bin/env python3
"""Small admin client for the Tyk AI Studio control plane API.

Standard library only. It covers the four steps that are awkward with curl:

  bootstrap   register the first admin, log in and mint an API token
  request     call any admin endpoint with that token
  push        push configuration to the edge gateways and wait until they load it
  app-secret  activate an App's credential and fetch its secret

The control plane URL comes from --url or AISTUDIO_URL. The API token is kept in
a file (--token-file or AISTUDIO_TOKEN_FILE, default ./.aistudio-token, mode
0600) and is never printed. The admin password is read from an environment
variable, never from the command line.

Examples:
  export AISTUDIO_URL=http://localhost:8080
  export AISTUDIO_ADMIN_PASSWORD='...'   # 8+ chars: upper, lower, digit, special
  python3 aistudio_admin.py bootstrap --email admin@example.com --name "Example Admin"
  python3 aistudio_admin.py request GET /api/v1/llms
  python3 aistudio_admin.py request POST /api/v1/llms --data-file llm.json
  python3 aistudio_admin.py push
  python3 aistudio_admin.py app-secret 1 --out .app-secret
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TOKEN_FILE = ".aistudio-token"


class ApiError(Exception):
    """An HTTP exchange that the caller should treat as a failure."""


def http(method: str, url: str, headers: dict[str, str] | None = None, body: bytes | None = None,
         timeout: float = 30.0) -> tuple[int, dict[str, str], bytes, list[str]]:
    """Send one request. Return status, headers, body and the Set-Cookie values."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw_headers, payload = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, raw_headers, payload = error.code, error.headers, error.read()
    except urllib.error.URLError as error:
        raise ApiError(f"cannot reach {url}: {error.reason}") from error
    cookies = raw_headers.get_all("Set-Cookie") or []
    return status, {k.lower(): v for k, v in raw_headers.items()}, payload, cookies


def cookie_pairs(set_cookie_values: list[str]) -> dict[str, str]:
    """Reduce Set-Cookie headers to name -> value (attributes dropped)."""
    pairs: dict[str, str] = {}
    for value in set_cookie_values:
        first = value.split(";", 1)[0]
        if "=" in first:
            name, content = first.split("=", 1)
            pairs[name.strip()] = content.strip()
    return pairs


def cookie_header(pairs: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in pairs.items())


def looks_like_html(headers: dict[str, str], payload: bytes) -> bool:
    return "text/html" in headers.get("content-type", "") or payload.lstrip()[:9].lower() == b"<!doctype"


def read_token(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError as error:
        raise ApiError(f"no API token at {path}: run bootstrap first ({error.strerror})") from error
    if not token:
        raise ApiError(f"the token file {path} is empty: run bootstrap again")
    return token


def write_secret_file(path: str, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.chmod(path, 0o600)


def api(base: str, token: str, method: str, path: str, data: object | None = None) -> tuple[int, object]:
    """Call an admin endpoint with the API token. Return status and decoded JSON (or text)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    status, response_headers, payload, _ = http(method, base.rstrip("/") + path, headers, body)
    if looks_like_html(response_headers, payload):
        # An unknown route answers 200 with the admin UI page.
        raise ApiError(f"{method} {path} returned the admin UI page (status {status}): the route does not exist")
    text = payload.decode("utf-8", "replace")
    try:
        return status, json.loads(text) if text.strip() else None
    except ValueError:
        return status, text


def bootstrap(base: str, email: str, name: str, password: str, token_file: str) -> str:
    """Register the first admin if needed, log in and mint an API token."""
    base = base.rstrip("/")
    status, headers, _, set_cookies = http("GET", base + "/csrf-token")
    csrf = headers.get("x-csrf-token", "")
    if status != 200 or not csrf:
        raise ApiError(f"GET /csrf-token gave status {status} and no X-CSRF-Token header")
    cookies = cookie_pairs(set_cookies)

    def post(path: str, payload: object | None) -> tuple[int, bytes, list[str]]:
        request_headers = {"X-CSRF-Token": csrf, "Cookie": cookie_header(cookies), "Content-Type": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else b""
        code, _, content, new_cookies = http("POST", base + path, request_headers, body)
        return code, content, new_cookies

    attributes = {"email": email, "name": name, "password": password, "with_portal": True, "with_chat": True}
    status, content, _ = post("/auth/register", {"data": {"type": "users", "attributes": attributes}})
    text = content.decode("utf-8", "replace")
    if status == 201:
        print(f"registered {email}")
    elif status == 400 and "already in use" in text:
        print(f"{email} is already registered")
    else:
        raise ApiError(f"POST /auth/register gave {status}: {text.strip()}")

    login = {"data": {"type": "users", "attributes": {"email": email, "password": password}}}
    status, content, new_cookies = post("/auth/login", login)
    if status != 200:
        raise ApiError(f"POST /auth/login gave {status}: {content.decode('utf-8', 'replace').strip()}")
    cookies.update(cookie_pairs(new_cookies))
    if not json.loads(content).get("is_admin"):
        raise ApiError(f"{email} is not an admin: only the first registered user becomes one")

    status, content, _ = post("/common/me/api-key/roll", None)
    if status != 200:
        raise ApiError(f"POST /common/me/api-key/roll gave {status}: {content.decode('utf-8', 'replace').strip()}")
    token = json.loads(content)["data"]["api_key"]
    write_secret_file(token_file, token)
    print(f"API token written to {token_file} (any earlier token of this user stopped working)")
    return token


def edge_states(base: str, token: str) -> list[dict]:
    """Return the attributes of every registered edge gateway."""
    status, body = api(base, token, "GET", "/api/v1/edges?page_size=100")
    if status != 200 or not isinstance(body, dict):
        raise ApiError(f"GET /api/v1/edges gave {status}: {body}")
    return [row.get("attributes", {}) for row in body.get("data", [])]


def push(base: str, token: str, wait: float, poll: float = 1.0) -> list[dict]:
    """Push configuration to the edges and wait until every connected edge has loaded it.

    Succeeds when nothing is pending, at least one edge is connected and every connected
    edge reports in_sync. A push made while an edge is reconnecting is delivered when it
    returns, so this keeps polling until the deadline. Disconnected edges are listed.
    """
    status, body = api(base, token, "POST", "/api/v1/edges/reload-all")
    if status not in (200, 202):
        raise ApiError(f"POST /api/v1/edges/reload-all gave {status}: {body}")
    message = ""
    if isinstance(body, dict):
        message = body.get("data", {}).get("attributes", {}).get("message", "")
    print(f"push accepted ({status}) {message}".rstrip())
    deadline = time.monotonic() + wait
    while True:
        status, sync = api(base, token, "GET", "/api/v1/sync/status")
        if status != 200 or not isinstance(sync, dict):
            raise ApiError(f"GET /api/v1/sync/status gave {status}: {sync}")
        edges = edge_states(base, token)
        connected = [edge for edge in edges if edge.get("status") == "connected"]
        behind = [edge.get("edge_id") for edge in connected if edge.get("sync_status") != "in_sync"]
        if connected and not behind and not sync.get("has_pending"):
            for edge in edges:
                print(f"edge {edge.get('edge_id')}: {edge.get('status')}, {edge.get('sync_status')}")
            return edges
        if time.monotonic() >= deadline:
            if not connected:
                raise ApiError(f"no edge gateway is connected after {wait:.0f}s (registered: {len(edges)})")
            raise ApiError(f"edges still not in sync after {wait:.0f}s: {behind or sync.get('pending_namespaces')}")
        time.sleep(poll)


def app_secret(base: str, token: str, app_id: str) -> str:
    """Activate the App's credential and return its secret."""
    status, body = api(base, token, "POST", f"/api/v1/apps/{app_id}/activate-credential")
    if status not in (200, 204):
        raise ApiError(f"POST /api/v1/apps/{app_id}/activate-credential gave {status}: {body}")
    status, body = api(base, token, "GET", f"/api/v1/apps/{app_id}")
    if status != 200 or not isinstance(body, dict):
        raise ApiError(f"GET /api/v1/apps/{app_id} gave {status}: {body}")
    credential_id = body["data"]["attributes"]["credential_id"]
    status, body = api(base, token, "GET", f"/api/v1/credentials/{credential_id}")
    if status != 200 or not isinstance(body, dict):
        raise ApiError(f"GET /api/v1/credentials/{credential_id} gave {status}: {body}")
    return body["data"]["attributes"]["secret"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Admin client for the Tyk AI Studio control plane API.")
    parser.add_argument("--url", default=os.environ.get("AISTUDIO_URL", ""), help="control plane URL (or AISTUDIO_URL)")
    parser.add_argument("--token-file", default=os.environ.get("AISTUDIO_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    commands = parser.add_subparsers(dest="command", required=True)

    boot = commands.add_parser("bootstrap", help="register the first admin, log in, mint an API token")
    boot.add_argument("--email", required=True)
    boot.add_argument("--name", default="Administrator")
    boot.add_argument("--password-env", default="AISTUDIO_ADMIN_PASSWORD",
                      help="environment variable that holds the admin password")

    request = commands.add_parser("request", help="call an admin endpoint")
    request.add_argument("method")
    request.add_argument("path", help="for example /api/v1/llms")
    group = request.add_mutually_exclusive_group()
    group.add_argument("--data", help="JSON request body")
    group.add_argument("--data-file", help="file holding the JSON request body")

    pusher = commands.add_parser("push", help="push configuration to the edges and wait")
    pusher.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the edges (default 60)")

    secret = commands.add_parser("app-secret", help="activate an App credential and fetch its secret")
    secret.add_argument("app_id")
    secret.add_argument("--out", help="write the secret to this file (mode 0600) instead of printing it")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.url:
        print("set --url or AISTUDIO_URL to the control plane, for example http://localhost:8080", file=sys.stderr)
        return 2
    try:
        if args.command == "bootstrap":
            password = os.environ.get(args.password_env, "")
            if not password:
                print(f"set {args.password_env} to the admin password", file=sys.stderr)
                return 2
            bootstrap(args.url, args.email, args.name, password, args.token_file)
            return 0
        token = read_token(args.token_file)
        if args.command == "request":
            data = None
            if args.data is not None:
                data = json.loads(args.data)
            elif args.data_file:
                with open(args.data_file, encoding="utf-8") as handle:
                    data = json.load(handle)
            status, body = api(args.url, token, args.method.upper(), args.path, data)
            print(f"HTTP {status}")
            if body is not None:
                print(body if isinstance(body, str) else json.dumps(body, indent=2))
            return 0 if status < 400 else 1
        if args.command == "push":
            push(args.url, token, args.wait)
            return 0
        if args.command == "app-secret":
            secret = app_secret(args.url, token, args.app_id)
            if args.out:
                write_secret_file(args.out, secret)
                print(f"App secret written to {args.out}")
            else:
                print(secret)
            return 0
    except ApiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"error: invalid JSON: {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
