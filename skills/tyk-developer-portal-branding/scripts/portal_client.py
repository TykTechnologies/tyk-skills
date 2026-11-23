#!/usr/bin/env python3
"""Small client for the Tyk Developer Portal, standard library only.

Two ways in, because the Portal has two surfaces:

* ``PortalAPI``: the admin API under ``/portal-api``. The header is ``Authorization: <token>``
  with the raw token (no ``Bearer`` prefix). Lists are bare JSON arrays of 20 items unless
  ``per_page`` is sent, so ``list_all`` pages until a short page.
* ``PortalSession``: a browser-like session (cookie jar plus the CSRF token the Portal writes
  into every page) for the things that have no API: menus, the General settings form (mail),
  and for fetching signed-in pages when verifying.

Environment: PORTAL_URL, PORTAL_API_TOKEN, PORTAL_ADMIN_EMAIL, PORTAL_ADMIN_PASSWORD.
Nothing here prints a token or a password.
"""

from __future__ import annotations

import html.parser
import http.cookiejar
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PAGE_SIZE = 100
CSRF_META = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


class PortalError(RuntimeError):
    """An HTTP call the caller did not expect to fail. Carries status and body."""

    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} -> {status}: {body.strip()[:400]}")
        self.status = status
        self.body = body


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise SystemExit(f"Set {name} in the environment.")
    return value


def multipart(field: str, filename: str, data: bytes, content_type: str | None = None) -> tuple[bytes, str]:
    """One-file multipart/form-data body and its Content-Type header value."""
    boundary = uuid.uuid4().hex
    content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def multipart_fields(pairs: list[tuple[str, str]]) -> tuple[bytes, str]:
    """A multipart/form-data body of plain fields, the encoding the admin forms use."""
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n' for name, value in pairs]
    return ("".join(parts) + f"--{boundary}--\r\n").encode(), f"multipart/form-data; boundary={boundary}"


class _FormReader(html.parser.HTMLParser):
    """Collects what a browser would submit for the form whose action is `action`."""

    def __init__(self, action: str):
        super().__init__(convert_charrefs=True)
        self.action = action
        self.inside = False
        self.pairs: list[tuple[str, str]] = []
        self._select: str | None = None
        self._select_value: str | None = None
        self._option: dict | None = None
        self._textarea: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        # A browser keeps the first of a repeated attribute; the admin forms repeat `value` on
        # checkboxes (value="true" then value="false"), so the first one must win here too.
        a: dict = {}
        for key, value in attrs:
            a.setdefault(key, value)
        if tag == "form":
            self.inside = a.get("action") == self.action
            return
        if not self.inside:
            return
        name = a.get("name")
        if tag == "input" and name:
            kind = (a.get("type") or "text").lower()
            if kind in ("checkbox", "radio") and "checked" not in a:
                return
            if kind in ("file", "submit", "button", "image"):
                return
            self.pairs.append((name, a.get("value") or ""))
        elif tag == "select" and name:
            self._select, self._select_value = name, None
        elif tag == "option" and self._select is not None:
            self._option = a
            if "selected" in a:
                self._select_value = a.get("value") or ""
        elif tag == "textarea" and name:
            self._textarea, self._text = name, []

    def handle_data(self, data):
        if self._textarea is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "select" and self._select is not None:
            if self._select_value is not None:
                self.pairs.append((self._select, self._select_value))
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            text = "".join(self._text)
            self.pairs.append((self._textarea, text[1:] if text.startswith("\n") else text))
            self._textarea = None


def read_form(page: str, action: str) -> list[tuple[str, str]]:
    """The (name, value) pairs a browser would send for the form with this action."""
    reader = _FormReader(action)
    reader.feed(page)
    return reader.pairs


class PortalAPI:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int = 60):
        self.base = (base_url or env("PORTAL_URL")).rstrip("/")
        self.token = token or env("PORTAL_API_TOKEN")
        self.timeout = timeout

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None,
                ok: tuple[int, ...] = (200, 201)) -> tuple[int, bytes]:
        url = f"{self.base}/portal-api{path}"
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Authorization", self.token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
        if ok and status not in ok:
            raise PortalError(method, url, status, payload.decode("utf-8", "replace"))
        return status, payload

    def json(self, method: str, path: str, payload=None, ok: tuple[int, ...] = (200, 201)):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"} if body is not None else {}
        _, raw = self.request(method, path, body, headers, ok)
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str):
        return self.json("GET", path)

    def put(self, path: str, payload):
        return self.json("PUT", path, payload)

    def post(self, path: str, payload):
        return self.json("POST", path, payload)

    def delete(self, path: str):
        return self.json("DELETE", path)

    def list_all(self, path: str) -> list:
        """Every item of a list endpoint. The API returns 20 per page and no total."""
        items: list = []
        separator = "&" if "?" in path else "?"
        page = 1
        while True:
            batch = self.get(f"{path}{separator}per_page={PAGE_SIZE}&page={page}") or []
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                return items
            page += 1

    def upload(self, path: str, filename: str, data: bytes, field: str = "file",
               content_type: str | None = None, ok: tuple[int, ...] = (200, 201)) -> tuple[int, str]:
        body, header = multipart(field, filename, data, content_type)
        status, raw = self.request("POST", path, body, {"Content-Type": header}, ok)
        return status, raw.decode("utf-8", "replace")

    def wait_ready(self, attempts: int = 60, delay: float = 2.0) -> None:
        """After bootstrap or a restart the API answers 404 or drops the connection for a while."""
        for _ in range(attempts):
            try:
                status, _ = self.request("GET", "/themes", ok=())
                if status == 200:
                    return
            except (urllib.error.URLError, ConnectionError, OSError):
                pass
            time.sleep(delay)
        raise SystemExit(f"{self.base}/portal-api/themes did not answer 200; is the token valid?")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PortalSession:
    """Cookie session against the live Portal and its admin interface."""

    def __init__(self, base_url: str | None = None, timeout: int = 60):
        self.base = (base_url or env("PORTAL_URL")).rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.timeout = timeout
        self.csrf = ""
        self._follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self._stay = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect)

    def _open(self, request, follow: bool) -> tuple[int, str, dict]:
        opener = self._follow if follow else self._stay
        try:
            with opener.open(request, timeout=self.timeout) as response:
                status, raw, headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            status, raw, headers = error.code, error.read(), dict(error.headers)
        text = raw.decode("utf-8", "replace")
        token = CSRF_META.search(text)
        if token:
            self.csrf = token.group(1)
        return status, text, headers

    def get(self, path: str, follow: bool = True) -> tuple[int, str, dict]:
        return self._open(urllib.request.Request(self.base + path), follow)

    def post_form(self, path: str, fields: list[tuple[str, str]] | dict, follow: bool = False) -> tuple[int, str, dict]:
        """POST a urlencoded form. The CSRF token of the last page fetched is added."""
        pairs = list(fields.items()) if isinstance(fields, dict) else list(fields)
        if self.csrf and not any(key == "csrf_token" for key, _ in pairs):
            pairs.append(("csrf_token", self.csrf))
        request = urllib.request.Request(self.base + path, data=urllib.parse.urlencode(pairs).encode(), method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        # What a browser sends for a form on the same site. Without it the Portal's CSRF check
        # answers 400 "Bad Request" to a scripted post, even with a valid token.
        request.add_header("Sec-Fetch-Site", "same-origin")
        return self._open(request, follow)

    def post_multipart(self, path: str, pairs: list[tuple[str, str]], follow: bool = False) -> tuple[int, str, dict]:
        """POST plain fields as multipart/form-data, as the admin interface's forms do."""
        body, header = multipart_fields(pairs)
        request = urllib.request.Request(self.base + path, data=body, method="POST")
        request.add_header("Content-Type", header)
        request.add_header("Sec-Fetch-Site", "same-origin")
        return self._open(request, follow)

    def login(self, email: str | None = None, password: str | None = None) -> None:
        """Sign in through the password form. Raises if the Portal does not accept it."""
        email = email or env("PORTAL_ADMIN_EMAIL")
        password = password or env("PORTAL_ADMIN_PASSWORD")
        self.get("/auth/password/login")
        status, _, headers = self.post_form("/auth/password/login", {"login": email, "password": password})
        location = headers.get("Location", "")
        if status not in (302, 303) or "/auth/password/login" in location:
            raise SystemExit(f"Portal sign-in for {email} was not accepted (HTTP {status}).")

    def cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.jar)

    def cookies(self) -> list[dict]:
        return [{"name": cookie.name, "value": cookie.value, "path": cookie.path or "/"} for cookie in self.jar]
