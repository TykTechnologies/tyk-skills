#!/usr/bin/env python3
"""Inspect, configure and prove a Tyk Developer Portal through its admin API and Live Portal.

Python 3 standard library only. Secrets come from environment variables and are never printed.

Subcommands:
  inventory   providers, products, plans and catalogues, with the problems that hide products
  policies    classify Dashboard policies by partitions: product, plan, both or neither
  settings    read or change the General settings (registration, auto-approve, access flow);
              mail settings are left unchanged
  verify      a developer signs up (or logs in), requests access, gets a credential and calls the API

Environment:
  PORTAL_URL, PORTAL_API_TOKEN                      every subcommand except policies
  PORTAL_ADMIN_EMAIL, PORTAL_ADMIN_PASSWORD         settings
  DASHBOARD_URL, DASHBOARD_API_KEY                  policies
  PORTAL_DEV_EMAIL, PORTAL_DEV_PASSWORD             verify (the developer account to use or create)
"""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser

PAGE_SIZE = 100


# --------------------------------------------------------------------------- pure helpers


def list_all(fetch, path: str, page_size: int = PAGE_SIZE) -> list:
    """Read every page of a Portal list endpoint. Lists are bare arrays with no total."""
    items: list = []
    page = 1
    sep = "&" if "?" in path else "?"
    while True:
        batch = fetch(f"{path}{sep}per_page={page_size}&page={page}")
        if not isinstance(batch, list):
            raise ValueError(f"{path} did not return a list")
        items.extend(batch)
        if len(batch) < page_size:
            return items
        page += 1


def classify_policy(policy: dict) -> str:
    """What the Portal makes of a Dashboard policy on synchronisation."""
    parts = policy.get("partitions") or {}
    acl = bool(parts.get("acl"))
    limits = bool(parts.get("rate_limit") or parts.get("quota"))
    if acl and limits:
        return "both"
    if acl:
        return "product"
    if limits:
        return "plan"
    return "neither"


def inventory_problems(products: list, plans: list, catalogues: list) -> list[str]:
    """Problems that leave a developer unable to find or use something. Inputs are readbacks."""
    problems = []
    for product in products:
        name = product.get("DisplayName") or product.get("Name")
        if not product.get("Catalogues"):
            problems.append(f"product {product['ID']} {name!r} is in no catalogue: developers cannot see it")
        details = product.get("APIDetails") or []
        if not product.get("IsDocumentationOnly") and details and not any(
            d.get("OASDocument") or d.get("OASUrl") for d in details if d.get("APIType", "REST") == "REST"
        ) and any(d.get("APIType", "REST") == "REST" for d in details):
            problems.append(f"product {product['ID']} {name!r} has no API specification attached")
        if product.get("DCREnabled") and not product.get("Templates"):
            problems.append(f"product {product['ID']} {name!r} has DCR on but no client type (template)")
        if product.get("DCREnabled") and not product.get("Scopes"):
            problems.append(f"product {product['ID']} {name!r} has DCR on but no scope")
    for plan in plans:
        name = plan.get("DisplayName") or plan.get("Name")
        if not plan.get("Catalogues"):
            problems.append(f"plan {plan['ID']} {name!r} is in no catalogue: no product can be requested with it")
    for catalogue in catalogues:
        for audience in catalogue.get("OrgCatalogues") or []:
            if not audience.get("OrganisationID") and not audience.get("Organisation"):
                problems.append(
                    f"catalogue {catalogue['ID']} {catalogue.get('Name')!r} has a team audience stored without "
                    "an organisation: no developer sees the catalogue through it (set Organisations instead)"
                )
        if catalogue.get("VisibilityStatus") == "Private" and not (
            catalogue.get("Organisations") or catalogue.get("OrgCatalogues")
        ):
            problems.append(
                f"catalogue {catalogue['ID']} {catalogue.get('Name')!r} is private with no audience: "
                "every signed-in developer sees it"
            )
    return problems


class FormParser(HTMLParser):
    """Collects the fields of every form on a page, as a browser would submit them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.csrf_meta = ""
        self.labels_values: list[tuple[str, str]] = []
        self._select: str | None = None
        self._textarea: str | None = None
        self._label_pending: str | None = None
        self._in_label = False
        self._label_text = ""

    def _form(self) -> dict:
        if not self.forms:
            self.forms.append({"attrs": {}, "fields": []})
        return self.forms[-1]

    def handle_starttag(self, tag, attrs):
        attrs_d: dict = {}
        for key, value in attrs:  # keep the first occurrence, as browsers do
            attrs_d.setdefault(key, value)
        if tag == "meta" and attrs_d.get("name") == "csrf-token":
            self.csrf_meta = attrs_d.get("content") or ""
        elif tag == "form":
            self.forms.append({"attrs": attrs_d, "fields": []})
        elif tag == "input" and attrs_d.get("name"):
            kind = (attrs_d.get("type") or "text").lower()
            if kind in ("checkbox", "radio"):
                if "checked" in attrs_d:
                    self._form()["fields"].append((attrs_d["name"], attrs_d.get("value") or "on", kind))
            elif kind not in ("submit", "button", "file", "image"):
                self._form()["fields"].append((attrs_d["name"], attrs_d.get("value") or "", kind))
        elif tag == "input" and "content-field" in (attrs_d.get("class") or "") and self._label_pending:
            self.labels_values.append((self._label_pending, attrs_d.get("value") or ""))
            self._label_pending = None
        elif tag == "select" and attrs_d.get("name"):
            self._select = attrs_d["name"]
        elif tag == "option" and self._select and "selected" in attrs_d:
            self._form()["fields"].append((self._select, attrs_d.get("value") or "", "select"))
        elif tag == "textarea" and attrs_d.get("name"):
            self._textarea = attrs_d["name"]
            self._form()["fields"].append((self._textarea, "", "textarea"))
        elif tag == "h6" and "heading-small" in (attrs_d.get("class") or ""):
            self._in_label = True
            self._label_text = ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None
        elif tag == "h6" and self._in_label:
            self._in_label = False
            self._label_pending = self._label_text.strip().rstrip(":")

    def handle_data(self, data):
        if self._in_label:
            self._label_text += data
        if self._textarea:
            name, value, kind = self._form()["fields"][-1]
            self._form()["fields"][-1] = (name, value + data, kind)


def parse_page(html: str) -> FormParser:
    parser = FormParser()
    parser.feed(html)
    return parser


def credential_from_app_page(html: str) -> dict:
    """The credential a developer sees on their app page: Token, or Client ID and Secret."""
    found: dict = {}
    for label, value in parse_page(html).labels_values:
        if label in ("Token", "Client ID", "Secret") and value and label not in found:
            found[label] = value
    return found


def multipart(fields: list[tuple[str, str]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = []
    for name, value in fields:
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n")
    out.append(f"--{boundary}--\r\n")
    return "".join(out).encode(), f"multipart/form-data; boundary={boundary}"


def redact(text: str) -> str:
    return re.sub(r"(?i)(secret|token|password|credential|key)([\"'=: ]+)([^\"'&\s,}]{6,})", r"\1\2<redacted>", text)


# --------------------------------------------------------------------------- HTTP clients


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib signature
        return None


class PortalAPI:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, body=None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}/portal-api{path}", data=data, method=method)
        req.add_header("Authorization", self.token)  # raw token, no "Bearer"
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as err:
            raw = err.read()
            status = err.code
        try:
            return status, json.loads(raw or b"null")
        except json.JSONDecodeError:
            return status, raw.decode(errors="replace")

    def get(self, path: str):
        status, body = self.request("GET", path)
        if status != 200:
            raise SystemExit(f"GET /portal-api{path} -> {status}: {redact(str(body))[:300]}")
        return body

    def all(self, path: str) -> list:
        return list_all(self.get, path)


class Browser:
    """A cookie-keeping client that submits Live Portal and admin forms like a browser."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)

    def _send(self, method: str, path: str, data: bytes | None = None, ctype: str | None = None):
        url = path if path.startswith("http") else f"{self.base}{path}"
        req = urllib.request.Request(url, data=data, method=method)
        # The Portal's CSRF check wants the token field plus a same-origin signal; browsers send
        # Sec-Fetch-Site, and over plain HTTP an Origin header alone is refused.
        req.add_header("Sec-Fetch-Site", "same-origin")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            resp = self.opener.open(req, timeout=30)
        except urllib.error.HTTPError as err:
            resp = err
        body = resp.read().decode(errors="replace")
        return resp.status if hasattr(resp, "status") else resp.code, resp.headers.get("Location", ""), body

    def get(self, path: str):
        return self._send("GET", path)

    def post_form(self, path: str, fields: list[tuple[str, str]], csrf: str, as_multipart: bool = False):
        fields = [f for f in fields if f[0] != "csrf_token"] + [("csrf_token", csrf)]
        if as_multipart:
            data, ctype = multipart(fields)
        else:
            data, ctype = urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded"
        return self._send("POST", path, data, ctype)

    def login(self, email: str, password: str) -> bool:
        _, _, page = self.get("/auth/password/login")
        csrf = parse_page(page).csrf_meta
        status, location, _ = self.post_form("/auth/password/login", [("login", email), ("password", password)], csrf)
        return status in (302, 303) and "/auth/password/login" not in location


# --------------------------------------------------------------------------- subcommands


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"set {name} in the environment")
    return value


def cmd_inventory(args) -> int:
    api = PortalAPI(env("PORTAL_URL"), env("PORTAL_API_TOKEN"))
    providers = api.all("/providers")
    for p in providers:
        print(f"provider {p['ID']} {p['Name']!r}: status {p.get('Status')}, last synced {p.get('LastSynced')}")
    products = [api.get(f"/products/{p['ID']}") for p in api.all("/products")]
    plans = [api.get(f"/plans/{p['ID']}") for p in api.all("/plans")]
    catalogues = [api.get(f"/catalogues/{c['ID']}") for c in api.all("/catalogues")]
    for p in products:
        print(
            f"product {p['ID']} {p.get('DisplayName') or p['Name']!r}: auth {p.get('AuthType')}, "
            f"APIs {[d.get('APIID') for d in p.get('APIDetails') or []]}, catalogues {p.get('Catalogues')}, "
            f"DCR {p.get('DCREnabled')}"
        )
    for p in plans:
        print(
            f"plan {p['ID']} {p.get('DisplayName') or p['Name']!r}: rate {p.get('RateLimit')}/{p.get('Per')}s, "
            f"quota {p.get('Quota')}, auto-approve {p.get('AutoApproveAccessRequests')}, catalogues {p.get('Catalogues')}"
        )
    for c in catalogues:
        print(
            f"catalogue {c['ID']} {c['Name']!r}: {c.get('VisibilityStatus')}, organisations {c.get('Organisations')}, "
            f"team audiences {len(c.get('OrgCatalogues') or [])}, products {c.get('Products')}, plans {c.get('Plans')}"
        )
    problems = [f"provider {p['ID']} status is {p.get('Status')}" for p in providers if p.get("Status") != "Up"]
    problems += inventory_problems(products, plans, catalogues)
    for problem in problems:
        print(f"PROBLEM {problem}")
    return 1 if problems and args.strict else 0


def cmd_policies(args) -> int:
    base = env("DASHBOARD_URL").rstrip("/")
    req = urllib.request.Request(f"{base}/api/portal/policies?p=-1")
    req.add_header("authorization", env("DASHBOARD_API_KEY"))
    with urllib.request.urlopen(req, timeout=30) as resp:
        policies = json.loads(resp.read()).get("Data") or []
    tags = set(args.tags.split(",")) if args.tags else set()
    bad = 0
    for pol in policies:
        kind = classify_policy(pol)
        synced = not tags or bool(tags & set(pol.get("tags") or []))
        note = "" if synced else " (not synchronised: no provider tag)"
        if kind in ("neither", "both"):
            bad += 1
            note += " <- the Portal makes no product or plan of this" if kind == "neither" else " <- ambiguous"
        print(f"{pol['_id']} {pol.get('name')!r}: {kind}{note}")
    return 1 if bad and args.strict else 0


SETTINGS = {
    "registration": "QorResource.RegistrationAllow",
    "auto-approve-users": "QorResource.RegistrationAutoApprove",
    "login": "QorResource.LoginEnabled",
    "access-flow": "QorResource.APIProductAccessFlow",
    "organisation-requests": "QorResource.BecomeOrganizationEnabled",
    "auto-approve-organisations": "QorResource.OrganizationAutoApprove",
}
# Mail fields (sender, admin address, SMTP, subjects) are left to the
# tyk-developer-portal-branding skill; this command sends them back unchanged.


def current_settings(fields: list[tuple[str, str, str]]) -> dict:
    """Effective value per setting name: a checked checkbox wins over its hidden false twin."""
    values: dict = {}
    for name, value, kind in fields:
        if kind == "checkbox":
            values[name] = "true"
        elif kind == "hidden" and name in values:
            continue
        else:
            values.setdefault(name, value)
    return values


ROBOTS_FIELD = "QorResource.RobotsTxtContent"
ROBOTS_DEFAULT = "User-agent: *\nDisallow: /\n"


def settings_submission(fields: list[tuple[str, str, str]], wanted: dict) -> list[tuple[str, str]]:
    """The form body a browser would send with the wanted values changed.

    A checked box sends "true" followed by its hidden "false"; an unchecked one sends only the
    hidden "false". Fields that are not changed are sent exactly as the page holds them.
    """
    boolean = {name for name, _, kind in fields if kind == "hidden"} & {
        name for name, value, _ in fields if value in ("true", "false")
    }
    submit = [(n, v) for n, v, _ in fields if n != "csrf_token" and n not in wanted]
    # A fresh Portal serves "User-agent: *" / "Disallow: /" while this box is empty, but saving
    # the form with it empty publishes a blank robots.txt, and a blank one is then refused.
    submit = [(n, ROBOTS_DEFAULT if n == ROBOTS_FIELD and not v.strip() else v) for n, v in submit]
    for name, value in wanted.items():
        if name in boolean or value in ("true", "false"):
            submit += [(name, "true"), (name, "false")] if value == "true" else [(name, "false")]
        else:
            submit.append((name, value))
    return submit


def read_settings(browser: Browser) -> tuple[dict, list, str]:
    status, _, page = browser.get("/admin/general")
    if status != 200:
        raise SystemExit(f"GET /admin/general -> {status}; is the admin login right?")
    parsed = parse_page(page)
    form = next((f for f in parsed.forms if f["attrs"].get("action") == "/admin/general"), None)
    if not form:
        raise SystemExit("the General settings form was not found on /admin/general")
    return current_settings(form["fields"]), form["fields"], parsed.csrf_meta


def cmd_settings(args) -> int:
    browser = Browser(env("PORTAL_URL"))
    if not browser.login(env("PORTAL_ADMIN_EMAIL"), env("PORTAL_ADMIN_PASSWORD")):
        raise SystemExit("admin login failed")
    values, fields, csrf = read_settings(browser)
    wanted = {}
    for pair in args.set or []:
        key, _, value = pair.partition("=")
        if key not in SETTINGS:
            raise SystemExit(f"unknown setting {key!r}; known: {', '.join(SETTINGS)}")
        wanted[SETTINGS[key]] = value
    if wanted:
        submit = settings_submission(fields, wanted)
        status, _, _ = browser.post_form("/admin/general", submit, csrf, as_multipart=True)
        if status not in (200, 302, 303):
            raise SystemExit(f"saving General settings -> HTTP {status}")
        values, _, _ = read_settings(browser)
        for name, value in wanted.items():
            if values.get(name, "false") != value:
                raise SystemExit(f"{name} reads back {values.get(name)!r}, wanted {value!r}: not applied")
    for key, name in SETTINGS.items():
        print(f"{key} = {values.get(name, 'false')}")
    return 0


def cmd_verify(args) -> int:
    portal = env("PORTAL_URL")
    api = PortalAPI(portal, env("PORTAL_API_TOKEN"))
    email, password = env("PORTAL_DEV_EMAIL"), env("PORTAL_DEV_PASSWORD")
    product = api.get(f"/products/{args.product}")
    browser = Browser(portal)

    if args.register:
        _, _, page = browser.get("/auth/password/register")
        csrf = parse_page(page).csrf_meta
        status, _, body = browser.post_form(
            "/auth/password/register",
            [("email", email), ("first", "Verify"), ("last", "Developer"), ("password1", password),
             ("password2", password), ("invite_id", "")],
            csrf,
        )
        print(f"register: HTTP {status}")
    if not browser.login(email, password):
        print("FAIL developer login refused (inactive account waiting for approval, or wrong password)")
        return 1
    print("login: ok")

    status, _, page = browser.get(f"/portal/catalogue-products/{product['Path']}")
    csrf = parse_page(page).csrf_meta
    status, _, _ = browser.post_form(
        f"/portal/catalogue-products/{product['Path']}",
        [("product_id", str(product["ID"])), ("catalogue_id", str(args.catalogue)), ("plan_id", str(args.plan))],
        csrf,
    )
    print(f"add to cart: HTTP {status}")
    status, _, page = browser.get("/portal/private/cart/provision")
    parsed = parse_page(page)
    form = next((f for f in parsed.forms if any(n == "app-action" for n, _, _ in f["fields"])), None)
    if not form:
        print("FAIL the cart has no request form (is the product and plan in a catalogue this developer sees?)")
        return 1
    app_name = args.app or f"verify-{int(time.time())}"
    submit = []
    for name, value, kind in form["fields"]:
        if name == "app-action":
            value = "create"
        elif name == "client_name":
            value = app_name
        submit.append((name, value))
    if not any(n == "app-action" for n, _ in submit):
        submit.append(("app-action", "create"))
    status, location, _ = browser.post_form("/portal/private/cart/provision", submit, parsed.csrf_meta, as_multipart=True)
    match = re.search(r"/portal/private/apps/(\d+)", location or "")
    if status not in (302, 303) or not match:
        print(f"FAIL submitting the request: HTTP {status} {location}")
        return 1
    app_id = int(match.group(1))
    print(f"access request submitted for app {app_id} ({app_name})")

    requests_ = [r for r in api.all("/access_requests") if r.get("Client") == app_name]
    if not requests_:
        print("FAIL no access request found for the new app")
        return 1
    request = requests_[0]
    print(f"access request {request['ID']}: {request['Status']}")
    if request["Status"] == "pending":
        if not args.approve:
            print(f"pending: approve request {request['ID']} in the admin UI or with "
                  f"PUT /portal-api/access_requests/{request['ID']}/approve, then open app {app_id} "
                  "as the developer; or run again with --approve (that run makes a new app and request)")
            return 2
        status, body = api.request("PUT", f"/access_requests/{request['ID']}/approve")
        if status != 200:
            print(f"FAIL approve -> HTTP {status}: {redact(json.dumps(body))[:400]}")
            return 1
        print("approved through the admin API")

    _, _, page = browser.get(f"/portal/private/apps/{app_id}")
    cred = credential_from_app_page(page)
    headers = {}
    if "Token" in cred:
        headers[args.header] = cred["Token"]
        print("credential: auth token shown on the developer's app page")
    elif "Client ID" in cred and "Secret" in cred:
        if not args.token_url:
            print("FAIL OAuth client issued; pass --token-url and --scope to mint a token")
            return 1
        basic = base64.b64encode(f"{cred['Client ID']}:{cred['Secret']}".encode()).decode()
        form_data = {"grant_type": "client_credentials"}
        if args.scope:
            form_data["scope"] = args.scope
        req = urllib.request.Request(args.token_url, data=urllib.parse.urlencode(form_data).encode(), method="POST")
        req.add_header("Authorization", f"Basic {basic}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = json.loads(resp.read())
        headers["Authorization"] = f"Bearer {token['access_token']}"
        print(f"credential: OAuth client from the app page; token issued with scope {token.get('scope')!r}")
    else:
        print("FAIL no credential on the developer's app page")
        return 1

    # A credential issued a moment ago can be refused until the Gateways have reloaded the new
    # policy or identity-provider mapping, so retry a refusal for a short while.
    deadline = time.time() + args.wait
    while True:
        req = urllib.request.Request(args.call)
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
        except urllib.error.HTTPError as err:
            status = err.code
            body = err.read().decode(errors="replace")[:200]
            if status in (401, 403) and status != args.expect and time.time() < deadline:
                print(f"API call: HTTP {status} {body.strip()} - retrying")
                time.sleep(3)
                continue
            print(f"gateway body: {body}")
        break
    print(f"API call {args.call}: HTTP {status}")
    if status != args.expect:
        print(f"FAIL expected HTTP {args.expect}")
        return 1
    print("PASS")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inventory", help="providers, products, plans, catalogues and the problems found")
    p.add_argument("--strict", action="store_true", help="exit 1 when a problem is found")
    p.set_defaults(func=cmd_inventory)
    p = sub.add_parser("policies", help="classify Dashboard policies by their partitions")
    p.add_argument("--tags", help="the provider's policy tags, comma separated")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_policies)
    p = sub.add_parser("settings", help="read or change General settings through the admin form")
    p.add_argument("--set", action="append", metavar="NAME=VALUE", help=f"one of {', '.join(SETTINGS)}")
    p.set_defaults(func=cmd_settings)
    p = sub.add_parser("verify", help="a developer requests access and calls the API with the credential")
    p.add_argument("--product", type=int, required=True)
    p.add_argument("--plan", type=int, required=True)
    p.add_argument("--catalogue", type=int, required=True)
    p.add_argument("--call", required=True, help="Gateway URL to call with the credential")
    p.add_argument("--register", action="store_true", help="sign the developer up through the public form first")
    p.add_argument("--approve", action="store_true", help="approve a pending request through the admin API")
    p.add_argument("--app", help="app name to create (default verify-<time>)")
    p.add_argument("--header", default="Authorization", help="header that carries an auth token")
    p.add_argument("--token-url", help="IdP token endpoint, for an OAuth client issued by DCR")
    p.add_argument("--scope", help="scopes to request, for example 'orders_api free_plan'")
    p.add_argument("--expect", type=int, default=200)
    p.add_argument("--wait", type=int, default=20, help="seconds to retry a 401/403 right after issue")
    p.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
