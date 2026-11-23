#!/usr/bin/env python3
"""Screenshot every page type of a Developer Portal at three widths and audit what is rendered.

    python3 screenshots.py --output <dir> [--widths 1440,768,390] [--only home,catalogue]

For each page (signed out, then signed in as a developer for the private pages) it saves
``<page>-<width>.png`` and writes ``audit.json`` with, per page: the HTTP status and final
address, text whose colour does not reach WCAG AA contrast against the background actually
painted behind it, stock wording and stock images still visible, images that failed to load,
sideways overflow at each width, the fonts the browser really used, and every third-party host
the page contacted. Exit status is 1 when any page has a finding.

Environment: PORTAL_URL; PORTAL_API_TOKEN (to find product and documentation pages);
PORTAL_DEV_EMAIL and PORTAL_DEV_PASSWORD (a developer account, for the signed-in pages; without
them those pages are listed as not checked). Needs a local Chrome, Chromium or Edge.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import cdp
import pages as portal_pages
from portal_client import PortalAPI, PortalSession
import stock

AUDIT_JS = r"""
(() => {
  const parse = (c) => {
    const m = (c || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,\/ ]+/).filter(Boolean).map(parseFloat);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const over = (top, under) => ({
    r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  // The colour painted behind an element. When a picture or gradient sits behind it, the
  // translucent layers above the picture are flattened over black and over white and both
  // results are returned, so the caller can take the worse case.
  const behind = (el) => {
    const layers = []; let image = false;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      const c = parse(s.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
      if (s.backgroundImage && s.backgroundImage !== 'none') { image = true; break; }
    }
    const flat = (start) => { let base = start; for (const c of [...layers].reverse()) base = over(c, base); return base; };
    if (!image) return { colours: [flat({ r: 255, g: 255, b: 255, a: 1 })], image: false, known: true };
    return { colours: [flat({ r: 0, g: 0, b: 0, a: 1 }), flat({ r: 255, g: 255, b: 255, a: 1 })], image: true, known: layers.length > 0 };
  };
  const VIEWER = 'elements-api, redoc, .redoc-wrap, #asyncapi, .aui-root, #graphiql-container, .graphiql-container, iframe';
  const label = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 4; n = n.parentElement) {
      let part = n.tagName.toLowerCase();
      if (n.id) part += '#' + n.id;
      const cls = (n.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
      if (cls.length) part += '.' + cls.join('.');
      parts.unshift(part);
    }
    return parts.join(' > ');
  };
  const low = []; const seen = new Set(); let onImage = 0; let checked = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const text = walker.currentNode.nodeValue.trim();
    const el = walker.currentNode.parentElement;
    if (!text || !el || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'OPTION', 'TEMPLATE'].includes(el.tagName)) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;
    const box = el.getBoundingClientRect();
    if (!el.getClientRects().length || box.width < 1 || box.height < 1) continue;
    const bg = behind(el);
    if (!bg.known) { onImage += 1; continue; }
    const raw = parse(s.color); if (!raw) continue;
    let ratio = Infinity, worst = bg.colours[0], shown = raw;
    for (const colour of bg.colours) {
      const fg = raw.a < 1 ? over(raw, colour) : raw;
      const a = lum(fg), b = lum(colour);
      const r = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (r < ratio) { ratio = r; worst = colour; shown = fg; }
    }
    const size = parseFloat(s.fontSize), weight = parseInt(s.fontWeight, 10) || 400;
    const minimum = (size >= 24 || (size >= 18.66 && weight >= 700)) ? 3 : 4.5;
    checked += 1;
    if (ratio < minimum) {
      const key = label(el) + hex(shown) + hex(worst);
      if (!seen.has(key)) {
        seen.add(key);
        low.push({ text: text.slice(0, 60), element: label(el), colour: hex(shown), background: hex(worst),
                   ratio: Math.round(ratio * 100) / 100, minimum, size, over_image: bg.image,
                   viewer: !!el.closest(VIEWER) });
      }
    }
  }
  const family = (selector) => { const el = document.querySelector(selector); return el ? getComputedStyle(el).fontFamily : null; };
  return {
    title: document.title,
    text: document.body.innerText,
    low_contrast: low, text_on_images: onImage, text_checked: checked,
    broken_images: [...document.images].filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.currentSrc || i.src),
    image_sources: [...document.images].map((i) => i.currentSrc || i.src),
    overflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    fonts_loaded: [...new Set([...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family.replace(/["']/g, '')))],
    font_families: { body: family('body'), heading: family('h1, h2'), button: family('.btn') },
    favicon: (document.querySelector('link[rel~="icon"]') || {}).href || null,
    logo: (document.querySelector('.navbar-brand img, .login-page-wrapper .logo img') || {}).src || null,
  };
})()
"""


def audit_page(browser: cdp.Browser, base: str, page: dict, widths: list[int], output: Path, extra_phrases: tuple[str, ...]) -> dict:
    result: dict = {"name": page["name"], "path": page["path"], "private": page["private"],
                    "expect": page.get("expect", 200), "widths": {}}
    for index, width in enumerate(widths):
        browser.viewport(width, 900 if width >= 600 else 844)
        loaded = browser.navigate(base + page["path"], settle=2.5 if page.get("docs") else 1.2)
        data = browser.evaluate(AUDIT_JS)
        shot = output / f"{page['name']}-{width}.png"
        signed_out = page["private"] and "/auth/password/login" in loaded["url"]
        if not signed_out:
            browser.screenshot(shot)
        result["widths"][str(width)] = {"overflow_px": data["overflow"], "screenshot": None if signed_out else shot.name,
                                        "low_contrast": data["low_contrast"]}
        if index == 0:
            text = data.pop("text")
            sources = " ".join(data.pop("image_sources"))
            result.update({
                "status": loaded["status"], "final_url": loaded["url"], "title": data["title"],
                "redirected_to_sign_in": signed_out,
                "shows_not_found": page["name"] != "not-found" and ("Page not found." in text),
                "shows_internal_error": "Internal error" in text and "Try again." in text,
                "stock_phrases": stock.phrases_in(text + " " + data["title"], extra_phrases),
                "stock_images": [name for name in stock.STOCK_IMAGES if f"/assets/images/{name}" in sources],
                "broken_images": data["broken_images"], "failed_requests": loaded["failed_requests"],
                "third_party_hosts": loaded["third_party_hosts"],
                "fonts_loaded": data["fonts_loaded"], "font_families": data["font_families"],
                "favicon": data["favicon"], "logo": data["logo"],
                "text_checked": data["text_checked"], "text_on_images": data["text_on_images"],
                "viewer_contrast": [item for item in data["low_contrast"] if item.get("viewer")],
            })
    return result


def findings(page: dict) -> list[str]:
    out = []
    if page.get("redirected_to_sign_in"):
        return ["redirected to sign-in: not checked"]
    if page.get("status") is not None and page.get("status") != page.get("expect", 200):
        out.append(f"HTTP {page.get('status')}, expected {page.get('expect', 200)}")
    if page.get("shows_not_found"):
        out.append("shows the not-found page")
    if page.get("shows_internal_error"):
        out.append("shows the internal error page")
    for key, label in (("stock_phrases", "stock wording"), ("stock_images", "stock image"),
                       ("broken_images", "image did not load"), ("failed_requests", "asset request failed")):
        for item in page.get(key) or []:
            out.append(f"{label}: {item}")
    if not page.get("favicon"):
        out.append("no favicon link")
    for width, data in page.get("widths", {}).items():
        if data["overflow_px"] > 2:
            out.append(f"content overflows sideways by {data['overflow_px']}px at {width}px")
        for item in data["low_contrast"]:
            if item.get("viewer"):
                continue  # the API reference viewer's own colours; listed under viewer_contrast
            out.append(f"low contrast {item['ratio']}:1 (needs {item['minimum']}:1) at {width}px: "
                       f"{item['colour']} on {item['background']}{' (over an image, worst case)' if item.get('over_image') else ''} "
                       f"in {item['element']} \"{item['text']}\"")
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--widths", default="1440,768,390")
    parser.add_argument("--only", help="comma-separated page names (or prefixes) to visit")
    parser.add_argument("--no-blog", action="store_true", help="the Portal runs with the blog switched off")
    parser.add_argument("--stock-phrase", action="append", default=[], help="extra wording that must not appear (repeatable)")
    args = parser.parse_args(argv)
    widths = [int(value) for value in args.widths.split(",")]
    base = os.environ.get("PORTAL_URL", "").rstrip("/")
    if not base:
        raise SystemExit("Set PORTAL_URL in the environment.")
    api = PortalAPI() if os.environ.get("PORTAL_API_TOKEN") else None
    todo = portal_pages.discover(api, blog=not args.no_blog)
    if args.only:
        wanted = tuple(args.only.split(","))
        todo = [page for page in todo if page["name"].startswith(wanted)]
    args.output.mkdir(parents=True, exist_ok=True)

    developer = os.environ.get("PORTAL_DEV_EMAIL") and os.environ.get("PORTAL_DEV_PASSWORD")
    results, skipped = [], []
    with cdp.Browser(widths[0], 900) as browser:
        for page in [page for page in todo if not page["private"]]:
            results.append(audit_page(browser, base, page, widths, args.output, tuple(args.stock_phrase)))
        private = [page for page in todo if page["private"] and not page.get("member")]
        member_pages = [page for page in todo if page.get("member")]
        if private and developer:
            session = PortalSession(base)
            session.login(os.environ["PORTAL_DEV_EMAIL"], os.environ["PORTAL_DEV_PASSWORD"])
            browser.set_cookies(session.cookies(), base)
            # The signed-in navigation bar differs from the signed-out one, so the home page is
            # looked at once more.
            results.append(audit_page(browser, base, {"name": "home-signed-in", "path": "/", "private": True},
                                      widths, args.output, tuple(args.stock_phrase)))
            for page in private:
                results.append(audit_page(browser, base, page, widths, args.output, tuple(args.stock_phrase)))
        else:
            skipped = [page["name"] for page in private]
        if member_pages and os.environ.get("PORTAL_MEMBER_EMAIL") and os.environ.get("PORTAL_MEMBER_PASSWORD"):
            browser.call("Network.clearBrowserCookies")
            session = PortalSession(base)
            session.login(os.environ["PORTAL_MEMBER_EMAIL"], os.environ["PORTAL_MEMBER_PASSWORD"])
            browser.set_cookies(session.cookies(), base)
            for page in member_pages:
                results.append(audit_page(browser, base, page, widths, args.output, tuple(args.stock_phrase)))

    summary = {"portal": base, "widths": widths, "pages": results, "not_checked": skipped,
               "third_party_hosts": sorted({host for page in results for host in page.get("third_party_hosts", [])})}
    problems = 0
    for page in results:
        page["findings"] = findings(page)
        problems += len(page["findings"])
    (args.output / "audit.json").write_text(json.dumps(summary, indent=2) + "\n")
    for page in results:
        mark = "ok " if not page["findings"] else "FIX"
        print(f"{mark} {page['name']:<32} {page.get('status')} {page['path']}")
        for finding in page["findings"]:
            print(f"      - {finding}")
    if skipped:
        print("not checked (set PORTAL_DEV_EMAIL and PORTAL_DEV_PASSWORD): " + ", ".join(skipped))
    print(f"third-party hosts contacted: {', '.join(summary['third_party_hosts']) or 'none'}")
    print(f"{len(results)} pages, {problems} findings; screenshots and audit.json in {args.output}")
    return 1 if problems or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
