#!/usr/bin/env python3
"""Check a branded Developer Portal over HTTP: no browser needed.

    python3 verify.py --portal-name "Example Corp Developers" --theme example-corp

For every page type (the list comes from ``pages.py``: home, CMS pages, catalogue, each product
and API reference page, blog, sign-in, sign-up, forgot password, not found, and the signed-in
developer pages) it fetches the page and reports:

* the HTTP status, and a private page that sent the visitor back to sign-in;
* stock wording and stock images of the default theme still on the page (``stock.py``);
* a missing favicon link, a missing brand stylesheet, a browser title without the portal name;
* tag-manager or analytics snippets in the HTML.

It also checks that the active theme is the one named, and that ``/robots.txt`` is not empty.
Run it on the stock Portal first: it must report findings there (that is the baseline), and
none after branding. Exit status 1 when anything is found.

Environment: PORTAL_URL, PORTAL_API_TOKEN; PORTAL_DEV_EMAIL and PORTAL_DEV_PASSWORD for the
signed-in pages (without them those pages are listed as not checked); optionally
PORTAL_MEMBER_EMAIL and PORTAL_MEMBER_PASSWORD (a consumer-team-member account) to see the 403
page. Standard library only.
"""

from __future__ import annotations

import argparse
import html.parser
import json
import os
import re
import sys

import pages as portal_pages
from portal_client import PortalAPI, PortalSession
import stock

BRAND_STYLESHEET = "/assets/stylesheets/brand.css"
TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
FAVICON = re.compile(r"<link[^>]+rel=\"(?:shortcut )?icon\"", re.I)


class _Text(html.parser.HTMLParser):
    """The text a visitor can read: text nodes outside script, style and template."""

    SKIP = {"script", "style", "template", "noscript", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth and data.strip():
            self.parts.append(" ".join(data.split()))


def visible_text(page: str) -> str:
    reader = _Text()
    reader.feed(page)
    return " ".join(reader.parts)


def page_findings(page: dict, status: int, body: str, final_path: str, portal_name: str,
                  extra: tuple[str, ...] = ()) -> list[str]:
    """What is wrong with one fetched page. `final_path` is where the request ended up."""
    out: list[str] = []
    if page["private"] and final_path.startswith("/auth/password/login"):
        return ["sent to sign-in: the signed-in page was not checked"]
    expected = page.get("expect", 200)  # the Portal answers 200 even for its not-found page
    if status != expected:
        out.append(f"HTTP {status}, expected {expected}")
    title_match = TITLE.search(body)
    title = " ".join(title_match.group(1).split()) if title_match else ""
    for phrase in stock.phrases_in(visible_text(body) + " " + title, extra):
        out.append(f"stock wording: {phrase}")
    for name in stock.images_in(body):
        out.append(f"stock image: {name}")
    for label in stock.tracking_in(body):
        out.append(f"tracking snippet: {label}")
    if not FAVICON.search(body):
        out.append("no favicon link")
    if BRAND_STYLESHEET not in body:
        out.append("brand stylesheet not linked")
    if portal_name and portal_name not in title:
        out.append(f"browser title {title!r} does not name the portal")
    if page["name"] != "not-found" and "Page not found." in body:
        out.append("shows the not-found page")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--portal-name", default="", help="the name the browser title must carry")
    parser.add_argument("--theme", help="the theme that must be active")
    parser.add_argument("--no-blog", action="store_true", help="the Portal runs with the blog switched off")
    parser.add_argument("--stock-phrase", action="append", default=[], help="extra wording that must not appear (repeatable)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    api = PortalAPI()
    base = api.base
    result: dict = {"portal": base, "checks": [], "pages": [], "not_checked": [], "optional_not_checked": []}

    themes = api.get("/themes") or []
    active = next((theme["Name"] for theme in themes if theme.get("Status") == "Current"), None)
    theme_problems = []
    if active == "default":
        theme_problems.append("the default theme is active")
    if args.theme and active != args.theme:
        theme_problems.append(f"active theme is {active!r}, expected {args.theme!r}")
    result["checks"].append({"name": "active theme", "value": active, "findings": theme_problems})

    visitor = PortalSession(base)
    status, robots, _ = visitor.get("/robots.txt")
    robots_problems = [] if status == 200 and robots.strip() else [f"/robots.txt is empty (HTTP {status})"]
    result["checks"].append({"name": "robots.txt", "value": robots.strip()[:200], "findings": robots_problems})

    todo = portal_pages.discover(api, blog=not args.no_blog)
    developer = None
    if os.environ.get("PORTAL_DEV_EMAIL") and os.environ.get("PORTAL_DEV_PASSWORD"):
        developer = PortalSession(base)
        developer.login(os.environ["PORTAL_DEV_EMAIL"], os.environ["PORTAL_DEV_PASSWORD"])
        todo.insert(len([p for p in todo if not p["private"]]), {"name": "home-signed-in", "path": "/", "private": True})
    member = None
    if os.environ.get("PORTAL_MEMBER_EMAIL") and os.environ.get("PORTAL_MEMBER_PASSWORD"):
        member = PortalSession(base)
        member.login(os.environ["PORTAL_MEMBER_EMAIL"], os.environ["PORTAL_MEMBER_PASSWORD"])
    for page in todo:
        if page.get("member") and not member:
            result["optional_not_checked"].append(page["name"])
            continue
        if page["private"] and not page.get("member") and not developer:
            result["not_checked"].append(page["name"])
            continue
        session = member if page.get("member") else developer if page["private"] else visitor
        status, body, headers = session.get(page["path"], follow=False)
        final = page["path"]
        if status in (301, 302, 303, 307, 308):
            final = headers.get("Location", "")
            final = re.sub(r"^https?://[^/]+", "", final)
            status, body, _ = session.get(final)
        found = page_findings(page, status, body, final, args.portal_name, tuple(args.stock_phrase))
        result["pages"].append({"name": page["name"], "path": page["path"], "status": status, "findings": found})

    problems = sum(len(item["findings"]) for item in result["checks"] + result["pages"])
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result["checks"]:
            print(f"{'ok ' if not item['findings'] else 'FIX'} {item['name']}: {item['value']}")
            for finding in item["findings"]:
                print(f"      - {finding}")
        for item in result["pages"]:
            print(f"{'ok ' if not item['findings'] else 'FIX'} {item['name']:<34} {item['status']} {item['path']}")
            for finding in item["findings"]:
                print(f"      - {finding}")
        if result["not_checked"]:
            print("not checked (set PORTAL_DEV_EMAIL and PORTAL_DEV_PASSWORD): " + ", ".join(result["not_checked"]))
        if result["optional_not_checked"]:
            print("not checked (optional; set PORTAL_MEMBER_EMAIL and PORTAL_MEMBER_PASSWORD): "
                  + ", ".join(result["optional_not_checked"]))
        print(f"{len(result['pages'])} pages, {problems} findings")
    return 1 if problems or result["not_checked"] else 0


if __name__ == "__main__":
    sys.exit(main())
