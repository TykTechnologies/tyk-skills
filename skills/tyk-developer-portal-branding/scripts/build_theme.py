#!/usr/bin/env python3
"""Build a branded Tyk Developer Portal theme from the Portal's own default theme and a brand kit.

    python3 build_theme.py --source default.zip --kit <kit-dir> --output <theme>.zip
    python3 build_theme.py --check <theme>.zip        pre-flight an archive before upload

The source must be the ``default`` theme downloaded from the Portal being branded (so it matches
that Portal's version). Nothing is edited by hand: every stock brand touchpoint is replaced by
this script, each replacement is counted, and the build fails when an expected touchpoint is
not found (``--allow-missing`` turns that into a warning, for a default theme of another
version). The script then scans its own output for stock branding and refuses to write an
archive that still carries any.

Written against the default theme of Portal 1.20.1. Standard library only.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile

import brand_kit
import colour

MAX_TOTAL_BYTES = 32 * 1024 * 1024  # PORTAL_MAX_UPLOAD_SIZE default
MAX_FILE_BYTES = brand_kit.MAX_FILE_BYTES
REQUIRED_FOLDERS = ("layouts", "views", "partials", "auth", "mailers")
# Template functions Portal 1.20.1 no longer provides. A theme that calls one uploads and
# activates, then every page that uses the template answers 500 'function "env" not defined'.
REMOVED_TEMPLATE_FUNCTIONS = ("env", "expandenv", "getHostByName")
TEMPLATE_ACTION = re.compile(r"\{\{(.*?)\}\}", re.S)
REMOVED_CALL = re.compile(r"(?<![\w.$])(" + "|".join(REMOVED_TEMPLATE_FUNCTIONS) + r")(?![\w])")
BRAND_DIR = "assets/images/brand"
FONT_DIR = "assets/fonts/brand"
STYLESHEET = "assets/stylesheets/brand.css"

# Stock files that carry the vendor's logo, mascot or sample partners. They are removed from
# the built theme once nothing references them.
STOCK_BRAND_FILES = (
    "assets/images/dev-portal-logo.svg", "assets/images/new-logo.svg",
    "assets/images/tyk-portal-silhouette.png", "assets/images/about-us.png",
    "assets/images/request-access.png", "assets/images/our-apis.png",
    "assets/images/login-background.jpg", "assets/images/login.svg",
    "assets/images/partners/6point.svg", "assets/images/partners/ncs.svg",
    "assets/images/partners/continuum.svg", "assets/images/partners/orbit.svg",
    "assets/images/partners/prodig.svg", "assets/images/partners/velvet.svg",
)
# Stock illustrations drawn in the vendor's palette. Kept, with the palette swapped.
STOCK_ILLUSTRATIONS = (
    "assets/images/use-cases/consumers.svg", "assets/images/use-cases/business.svg",
    "assets/images/use-cases/research.svg", "assets/images/icons/how-to.svg",
    "assets/images/not-found.svg", "assets/images/blog.svg",
)
# What must not survive in templates and stylesheets of a built theme: (label, pattern).
STOCK_MARKERS = (
    ("vendor name", re.compile(r"\bTyk\b")),
    ("vendor domain", re.compile(r"tyk\.io|tyk\.com", re.IGNORECASE)),
    ("placeholder host", re.compile(r"portal\.com")),
    ("placeholder address", re.compile(r"customer@support\.com")),
    ("tag manager", re.compile(r"googletagmanager|GTM-[A-Z0-9]+")),
    ("stock navigation label", re.compile(r">DEVELOPER PORTAL<")),
    ("stock image", re.compile("|".join(re.escape(Path(name).name) for name in STOCK_BRAND_FILES))),
)
# The default theme loads the API reference renderers from public CDNs at whatever version is
# newest. Redoc 3.2.0 (published 2026-11-19) does not render in the default theme: the page stays
# empty with "Cannot use 'import.meta' outside a module". The build pins each renderer to a
# version that renders on Portal 1.20.0 (Redoc and Stoplight Elements also on 1.20.1).
PINNED_RENDERERS = (
    ("https://cdn.redoc.ly/redoc/latest/", "https://cdn.redoc.ly/redoc/v2.5.4/"),
    ("https://unpkg.com/@asyncapi/react-component@latest/", "https://unpkg.com/@asyncapi/react-component@3.2.3/"),
    ("https://unpkg.com/@stoplight/elements/", "https://unpkg.com/@stoplight/elements@9.0.29/"),
)
# The class names and file names below hold the vendor's name but are not branding.
MARKER_ALLOWED = re.compile(r"tyk-icons?|tykon|arrow-(?:up|down)-tyk|Tyk Javascript")


class BuildError(RuntimeError):
    pass


class Report:
    """What the build replaced, and what it expected to find but did not."""

    def __init__(self, allow_missing: bool):
        self.allow_missing = allow_missing
        self.done: dict[str, int] = {}
        self.missing: list[str] = []
        self.warnings: list[str] = []

    def record(self, label: str, count: int, required: bool = True) -> None:
        self.done[label] = self.done.get(label, 0) + count
        if count == 0 and required:
            self.missing.append(label)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def sub(pattern: str, replacement, text: str, flags: int = 0) -> tuple[str, int]:
    return re.subn(pattern, replacement if callable(replacement) else (lambda _m: replacement), text, flags=flags)


def edit(path: Path, label: str, report: Report, pattern: str, replacement, flags: int = 0, required: bool = True) -> None:
    if not path.is_file():
        report.record(label, 0, required)
        return
    text, count = sub(pattern, replacement, path.read_text(), flags)
    if count:
        path.write_text(text)
    report.record(label, count, required)


# --------------------------------------------------------------------------- extraction

def extract(source: Path, target: Path) -> Path:
    """Unpack a theme zip (or copy a theme folder) and return the folder holding theme.json.

    A theme downloaded from the Portal can list every file twice; the later entry wins."""
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                name = info.filename
                if name.endswith("/") or "__MACOSX" in name or name.startswith("/") or ".." in Path(name).parts:
                    continue
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))
    if (target / "theme.json").is_file():
        return target
    # A release archive or a repository export holds the theme one or two folders down.
    for pattern in ("*/theme.json", "*/*/theme.json"):
        nested = sorted(target.glob(pattern))
        if len(nested) == 1:
            return nested[0].parent
        if nested:
            raise BuildError(f"the source holds {len(nested)} themes; point --source at one of them")
    raise BuildError("the source has no theme.json")


# --------------------------------------------------------------------------- templates

def copy_assets(root: Path, kit: dict) -> dict:
    """Copy the kit's files into the theme. Returns {role: site path}."""
    urls: dict[str, str] = {}
    images = root / BRAND_DIR
    images.mkdir(parents=True, exist_ok=True)

    def put(role: str, name: str) -> None:
        if not name:
            return
        target = images / Path(name).name
        shutil.copyfile(kit["dir"] / name, target)
        urls[role] = f"/{BRAND_DIR}/{target.name}"

    put("logo_on_light", kit["logo"]["on_light"])
    put("logo_on_dark", kit["logo"]["on_dark"])
    put("logo_email", kit["logo"]["email"])
    put("favicon", kit["favicon"])
    for key, name in kit["images"].items():
        put(key, name)
    if kit["fonts"].get("files"):
        fonts = root / FONT_DIR
        fonts.mkdir(parents=True, exist_ok=True)
        for weight, name in kit["fonts"]["files"].items():
            target = fonts / Path(name).name
            shutil.copyfile(kit["dir"] / name, target)
            urls[f"font_{weight}"] = f"/{FONT_DIR}/{target.name}"
    return urls


def logo_tag(kit: dict, urls: dict, background: str, css_class: str) -> str:
    name, plate = brand_kit.logo_for(kit, background)
    role = "logo_on_dark" if name == kit["logo"]["on_dark"] else "logo_on_light"
    classes = css_class + (" brand-logo--plate" if plate else "")
    return f'<img src="{urls[role]}" alt="{esc(kit["logo"]["alt"])}" class="{classes}">'


def patch_navigation(root: Path, kit: dict, p: dict, urls: dict, report: Report) -> None:
    label = f'<span class="brand-nav-label">{esc(kit["nav_label"])}</span>' if kit["nav_label"] else ""
    brand = f'<a class="navbar-brand d-flex align-items-center" href="/">{logo_tag(kit, urls, p["nav_bg"], "brand-logo")}{label}</a>'
    edit(root / "partials/top_nav.tmpl", "navigation bar logo", report,
         r'<a class="navbar-brand"[^>]*>.*?</a>', brand, re.S)

    links = "".join(f'<a class="brand-footer-link" href="{esc(link["href"])}">{esc(link["label"])}</a>' for link in kit["footer_links"])
    block = (
        f'{logo_tag(kit, urls, p["footer_bg"], "brand-logo-footer")}\n'
        f'                    <p class="brand-footer-text mt-3">{esc(kit["footer_text"])}</p>'
        + (f'\n                    <p class="brand-footer-links">{links}</p>' if links else "")
    )
    edit(root / "partials/footer.tmpl", "footer logo and line", report,
         r'<img src="/assets/images/dev-portal-logo\.svg"[^>]*>\s*<h4[^>]*>[^<]*</h4>', block, re.S)
    edit(root / "partials/footer.tmpl", "footer script comment", report,
         r"<!-- Load Tyk Javascript assets -->", "<!-- Load theme JavaScript assets -->", required=False)


def patch_auth(root: Path, kit: dict, p: dict, urls: dict, report: Report) -> None:
    files = sorted([*root.glob("auth/**/*.tmpl"), *root.glob("layouts/auth/*.tmpl")])
    logo = logo_tag(kit, urls, p["auth_bg"], "brand-logo-auth")
    portal = esc(kit["portal_name"])
    counts = {"sign-in page logo": 0, "sign-in page wording": 0, "sign-in page inline background": 0}
    for path in files:
        text = original = path.read_text()
        text, n = sub(r'<img src="/assets/images/new-logo\.svg"[^>]*/?>', logo, text)
        counts["sign-in page logo"] += n
        for old, new in (
            ("Log in to your Tyk Developer Portal account", f"Log in to your {portal} account"),
            ("Login to Developer Portal", f"Log in to {portal}"),
            ("Register to developer portal", "Create account"),
            ("within this developer portal.", f"within {portal}."),
        ):
            n = text.count(old)
            text = text.replace(old, new)
            counts["sign-in page wording"] += n
        # Older fragments paint their own gradient and illustration inline; drop it so the
        # stylesheet rule for .login-page-wrapper applies everywhere.
        text, n = sub(r'(login-page-wrapper")\s+style="background:[^"]*"', lambda m: m.group(1), text)
        counts["sign-in page inline background"] += n
        if text != original:
            path.write_text(text)
    for label, count in counts.items():
        report.record(label, count)


def patch_images(root: Path, kit: dict, p: dict, urls: dict, report: Report) -> None:
    def background(role: str, fallback: str = "") -> str:
        url = urls.get(role) or urls.get(fallback)
        return f"background: url('{url}') no-repeat center center / contain;" if url else ""

    def swap_background(path: Path, stock: str, role: str, fallback: str, label: str) -> None:
        css = background(role, fallback)
        edit(path, label, report, rf"background: url\('/assets/images/{re.escape(stock)}'\)[^\";]*;",
             css if css else "background: none;")

    # The one stock stylesheet rule that names a stock image: the photo behind the sign-in pages.
    edit(root / "assets/stylesheets/pages/login.css", "sign-in page background photo", report,
         r"background: url\('/assets/images/login-background\.jpg'\)[^;]*;", f"background: {p['auth_bg']};")

    home, flip = root / "views/home.tmpl", root / "views/flip_flop.tmpl"
    swap_background(home, "tyk-portal-silhouette.png", "hero", "", "home hero image")
    swap_background(home, "about-us.png", "about", "", "home about image")
    swap_background(flip, "tyk-portal-silhouette.png", "hero", "", "page hero image")
    swap_background(flip, "about-us.png", "about", "", "page about image")
    swap_background(root / "views/blog_listing.tmpl", "blog.svg", "blog", "", "blog header image")

    getting_started = urls.get("getting_started")
    edit(home, "home getting-started image", report, r'<img src="/assets/images/request-access\.png"[^>]*>',
         f'<img src="{getting_started}" alt="" class="img-fluid">' if getting_started else "")

    # The stock strip shows six sample company logos, never anyone's real partners.
    edit(home, "home sample partner strip", report, r"[ \t]*<!-- Partners -->.*?<!-- end of Partners -->\n?", "", re.S)

    error_image = urls.get("error")
    if error_image:
        for name in ("not_found", "forbidden", "internal_error"):
            edit(root / f"views/{name}.tmpl", "error page image", report,
                 r"/assets/images/not-found\.svg", error_image, required=False)

    placeholder = urls.get("card_placeholder")
    if not placeholder:
        (root / BRAND_DIR / "card-placeholder.svg").write_text(card_placeholder_svg(p))
        placeholder = f"/{BRAND_DIR}/card-placeholder.svg"
    for name in ("partials/catalogue_card.tmpl", "views/portal_product_detail.tmpl", "partials/product_doc_hero.tmpl"):
        edit(root / name, "product card placeholder image", report,
             r"/assets/images/card-placeholder\.svg", placeholder)

    swaps = {"#00D9BA": p["primary"], "#FF6C7D": colour.mix(colour.WHITE, p["primary"], 0.55),
             "#D6B218": colour.mix(colour.WHITE, p["primary"], 0.3), "#BCECE3": p["tint"],
             "#505071": p["text_muted"], "#7A79A1": p["text_muted"], "#A2A2CC": p["border"], "#C3C3E5": p["border"]}
    recoloured = 0
    for name in STOCK_ILLUSTRATIONS:
        path = root / name
        if not path.is_file():
            continue
        text = original = path.read_text()
        for stock, new in swaps.items():
            text = re.sub(re.escape(stock), new, text, flags=re.I)
        if text != original:
            path.write_text(text)
            recoloured += 1
    report.record("stock illustrations recoloured", recoloured, required=False)


def card_placeholder_svg(p: dict) -> str:
    """A plain band in the brand tint for products that have no image of their own."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="388" height="180" viewBox="0 0 388 180" role="img" aria-hidden="true">'
        f'<rect width="388" height="180" fill="{p["tint"]}"/>'
        f'<rect x="150" y="62" width="88" height="56" rx="8" fill="none" stroke="{p["primary"]}" stroke-width="4"/>'
        f'<path d="M166 82h56M166 98h36" stroke="{p["primary"]}" stroke-width="4" stroke-linecap="round"/></svg>\n'
    )


def patch_layouts(root: Path, kit: dict, urls: dict, report: Report) -> None:
    favicon = urls.get("favicon")
    counts = {"stylesheet link": 0, "favicon link": 0, "page title": 0, "tag manager snippet removed": 0, "html lang": 0}
    for path in sorted(root.glob("layouts/*.tmpl")):
        text = original = path.read_text()
        if "</head>" not in text:
            continue
        preview = "{{.previewId}}" if "main.css{{.previewId}}" in text else ""
        link = f'<link href="/{STYLESHEET}{preview}" rel="stylesheet">'
        if STYLESHEET not in text:
            # After the theme's own main.css, so the brand rules win at equal specificity.
            text, n = sub(r'(<link href="/assets/stylesheets/main\.css[^"]*" rel="stylesheet">)', lambda m: f"{m.group(1)}\n    {link}", text)
            if not n:
                text = text.replace("</head>", f"    {link}\n</head>", 1)
            counts["stylesheet link"] += 1
        if favicon and 'rel="icon"' not in text:
            text = text.replace("</head>", f'    <link rel="icon" href="{favicon}">\n</head>', 1)
            counts["favicon link"] += 1
        portal = esc(kit["portal_name"])
        # Browser tab titles: the page's own title, then the portal's name. The default theme
        # falls back to "Developer Portal", leaves blog titles empty and prints the product's
        # internal name on API documentation pages.
        for pattern, title in (
            (r"<title>\{\{ if \$pageTitle\}\}.*?</title>",
             "<title>{{ if $pageTitle }}{{ $pageTitle }} - {{ else }}{{ if .page }}{{ .page.Title }} - {{ end }}{{ end }}" + portal + "</title>"),
            (r"<title>\{\{ if \.page\}\}.*?</title>", "<title>{{ if .page }}{{ .page.Title }} - {{ end }}" + portal + "</title>"),
            (r"<title>\{\{ \.page\.Title \}\}</title>", "<title>{{ if .page }}{{ if .page.Title }}{{ .page.Title }} - {{ end }}{{ end }}" + portal + "</title>"),
            (r"<title>\{\{\.product\.Name\}\}: \{\{\.api\.Name\}\} Documentation</title>",
             "<title>{{ if .product.DisplayName }}{{ .product.DisplayName }}{{ else }}{{ .product.Name }}{{ end }} - API reference - " + portal + "</title>"),
            (r"<title>\{\{\.Name\}\} Documentation</title>", "<title>{{ .Name }} - API reference - " + portal + "</title>"),
            (r"<title>\{\{\.Name\}\} - GraphQL Playground</title>", "<title>{{ .Name }} - GraphQL - " + portal + "</title>"),
        ):
            text, n = sub(pattern, title, text, re.S)
            counts["page title"] += n
        text, n = sub(r"[ \t]*<!-- Google Tag Manager -->.*?<!-- End Google Tag Manager -->\n?", "", text, re.S)
        counts["tag manager snippet removed"] += n
        text, n = sub(r'<html lang="en">', f'<html lang="{kit["lang"]}">', text)
        counts["html lang"] += n
        if text != original:
            path.write_text(text)
    report.record("stylesheet link", counts["stylesheet link"])
    report.record("favicon link", counts["favicon link"], required=bool(favicon))
    report.record("page title", counts["page title"])
    report.record("tag manager snippet removed", counts["tag manager snippet removed"], required=False)
    report.record("html lang", counts["html lang"], required=False)


def pin_renderers(root: Path, report: Report) -> None:
    pinned = 0
    for path in sorted([*root.glob("layouts/*.tmpl"), *root.glob("views/*.tmpl"), *root.glob("partials/*.tmpl")]):
        text = original = path.read_text()
        for unpinned, fixed in PINNED_RENDERERS:
            pinned += text.count(unpinned)
            text = text.replace(unpinned, fixed)
        if text != original:
            path.write_text(text)
    report.record("API reference renderers pinned", pinned)


# --------------------------------------------------------------------------- stylesheets

# The default theme names one font family per weight and uses those names in about sixty
# rules. Pointing the names at the brand's files changes the typeface everywhere without
# touching a rule. The icon font (tykon) is left alone.
STOCK_FACES = {
    "Inter-Regular": "400", "Inter-Medium": "500", "Inter-SemiBold": "600", "Inter-Semibold": "600", "Inter-Bold": "700",
    "OpenSans-Regular": "400", "OpenSans-regular": "400", "Open Sans": "400", "OpenSans-Semibold": "600", "OpenSans-Bold": "700",
    "Smoolthan-Regular": "400", "Smoolthan-Semibold": "600", "Smoolthan-Bold": "700",
}
# Descriptors the stock @font-face rules carry; an alias must repeat them to replace the rule.
STOCK_DESCRIPTORS = {
    "Inter-Regular": "400", "Inter-Bold": "700", "Inter-Medium": "normal", "Inter-SemiBold": "normal",
    "OpenSans-Regular": "400", "OpenSans-Semibold": "600", "OpenSans-Bold": "800",
    "Smoolthan-Regular": "400", "Smoolthan-Semibold": "600", "Smoolthan-Bold": "800",
}
LOCAL_SUFFIX = {"400": ("", " Regular"), "500": (" Medium", ""), "600": (" Semibold", " SemiBold", " Bold"), "700": (" Bold",)}


def font_sources(kit: dict, urls: dict, weight: str) -> str:
    fonts = kit["fonts"]
    sources = []
    if fonts.get("files"):
        available = sorted(fonts["files"], key=lambda w: (abs(int(w) - int(weight)), -int(w)))
        chosen = available[0]
        name = fonts["files"][chosen]
        sources.append(f'url("{urls[f"font_{chosen}"]}") format("{brand_kit.FONT_FORMATS[Path(name).suffix.lower()]}")')
    for family in fonts.get("local", []):
        for suffix in LOCAL_SUFFIX[weight]:
            sources.append(f'local("{family}{suffix}")')
    return ", ".join(sources)


def write_fonts(root: Path, kit: dict, urls: dict, report: Report) -> None:
    if not kit["fonts"]:
        report.record("typeface", 0, required=False)
        return
    path = root / "assets/stylesheets/fonts.css"
    text = path.read_text() if path.is_file() else ""
    icon = re.search(r"@font-face\s*\{[^}]*font-family:\s*['\"]?tykon['\"]?[^}]*\}", text, re.S)
    if not icon:
        report.record("icon font kept", 0)
    rules = [f"/* Generated by build_theme.py: the default theme's font names, served by {kit['fonts']['family']}. */"]
    for family, descriptor in STOCK_DESCRIPTORS.items():
        rules.append(
            "@font-face {\n"
            f"  font-family: '{family}';\n"
            f"  src: {font_sources(kit, urls, STOCK_FACES[family])};\n"
            f"  font-weight: {descriptor};\n  font-style: normal;\n  font-display: swap;\n}}"
        )
    if icon:
        rules.append(icon.group(0))
    path.write_text("\n\n".join(rules) + "\n")
    report.record("typeface", len(STOCK_DESCRIPTORS))


def brand_css(kit: dict, p: dict, urls: dict) -> str:
    """The one stylesheet that carries the brand. Loaded after the theme's main.css."""
    light_nav = not colour.is_dark(p["nav_bg"])
    auth_image = urls.get("auth_background")
    auth_background = (f"{p['auth_bg']} url('{auth_image}') no-repeat center center / cover" if auth_image else p["auth_bg"])
    height = kit["logo"]["height"]
    lines = [
        f"/* Brand stylesheet for {kit['name']}. Generated by build_theme.py from brand.json; do not edit by hand. */",
        "",
        "/* 1. Variables the default theme paints from. */",
        ":root {",
        f"  --tdp-primary: {p['primary']};",
        f"  --tdp-primary-dark: {p['primary_pressed']};",
        f"  --tdp-brand-dark: {p['primary_hover']};",
        f"  --tdp-brand-darker: {p['primary_pressed']};",
        f"  --tdp-link-color: {p['link']};",
        f"  --link-default: {p['link']};",
        f"  --tdp-light-purple: {p['link']};",
        f"  --tdp-nav-bg-color: {p['nav_bg']};",
        "  /* Also the text on the product header, the active tab and the modal bar, so it stays",
        "     the on-dark colour. Navigation and footer text are set by their own rules below. */",
        f"  --tdp-nav-link-color: {p['on_dark']};",
        "  /* Also breadcrumb and table text, so it stays a text colour. The dark bands are set below. */",
        f"  --tdp-dark: {p['text']};",
        f"  --tdp-link-dark: {p['dark']};",
        f"  --tdp-text-color-extra-dark: {p['text']};",
        f"  --tdp-text-color: {p['text_muted']};",
        f"  --tdp-extra-dark: {p['text']};",
        f"  --tdp-secondary: {p['secondary']};",
        f"  --tdp-secondary-dark: {p['text_muted']};",
        f"  --tdp-tab-active: {p['tint']};",
        f"  --surface-brand-hover: {p['tint']};",
        f"  --tdp-light-gray: {colour.mix(colour.WHITE, p['text_muted'], 0.1)};",
        f"  --tdp-light-border: 1px solid {p['border']};",
        "  /* The default greys (#808080, #868686) and the translucent sign-in panel do not reach",
        "     4.5:1 contrast, whatever the brand. */",
        f"  --tdb-text-color-gray: {p['text_muted']};",
        f"  --tdb-text-color-gray-lighter: {p['text_muted']};",
        "  --tdp-login-form: #FFFFFF;",
        "}",
        "",
        "/* 2. Navigation bar. */",
        f".navbar.navbar-expand-lg.navbar-dark.bg-dark {{ background: {p['nav_bg']} !important;"
        + (f" border-bottom: 1px solid {p['border']};" if light_nav else "") + " }",
        f".navbar .nav-link, .navbar .navbar-brand, .navbar-dark .navbar-nav .nav-link, .navbar .brand-nav-label {{ color: {p['nav_text']}; }}",
        f".navbar-dark .navbar-nav .nav-link:focus, .navbar-dark .navbar-nav .nav-link:hover {{ color: {p['nav_hover']}; }}",
        f".navbar-menu-items > .nav-item::after {{ background-color: {p['nav_hover']}; }}",
        f".navbar .admin-dash {{ color: {p['nav_text']}; }}",
        ".navbar .navbar-brand { flex: 0 0 auto; white-space: nowrap; }",
        f".brand-logo {{ height: {height}px; width: auto; max-width: 220px; }}",
        ".brand-nav-label { margin-left: 12px; font-size: 15px; font-weight: 600; letter-spacing: 0.02em; }",
        ".brand-logo--plate { background: #FFFFFF; border-radius: 6px; padding: 4px 8px; box-sizing: content-box; }",
    ]
    if light_nav:
        lines += [
            "/* Light bar: the stock toggler and cart icons are white. */",
            f".navbar-dark .navbar-toggler {{ border-color: {p['border']}; }}",
            ".navbar-dark .navbar-toggler-icon, .navbar .cart__icon, .navbar a[href='/portal/private/cart/provision'] img { filter: invert(1); }",
        ]
    lines += [
        "",
        "/* 3. Footer. The default theme paints it from the navigation variables. */",
        f"footer {{ background: {p['footer_bg']}; color: {p['footer_text']}; }}",
        f"footer h4, footer h6, footer p, footer .footer-menu__link {{ color: {p['footer_text']}; }}",
        f"footer a, footer li a {{ color: {p['footer_link']}; }}",
        "footer a:hover { text-decoration: underline; }",
        f".brand-logo-footer {{ height: {height + 4}px; width: auto; max-width: 240px; }}",
        ".brand-footer-text { font-size: 14px; margin-bottom: 8px; }",
        ".brand-footer-links a { margin-right: 20px; font-size: 14px; }",
        "",
        "/* 4. Dark bands and the product and API documentation header (a fixed colour in the default theme). */",
        f".dark-to-light-bg, .light-to-dark-bg {{ background: {p['dark']}; color: {p['on_dark']}; }}",
        f".api-detail-background-color {{ background-color: {p['dark']}; }}",
        f".card.dark-background {{ background: {p['dark']}; color: {p['on_dark']}; }}",
        "",
        "/* 5. Buttons, tabs and links. The default theme writes these labels in white. */",
        f".btn-primary, .btn.btn-primary, .portal-primary, .btn.portal-primary {{ color: {p['on_primary']}; }}",
        f".btn-primary:focus, .btn-primary:hover, .btn-primary:not(:disabled):not(.disabled):active, .portal-primary:focus, .portal-primary:hover, "
        f".portal-primary:not(:disabled):not(.disabled):active {{ color: {p['on_primary_hover']}; }}",
        f".btn-primary-outline, .btn-primary-link, .btn:hover.btn-primary-link:hover {{ color: {p['link']}; }}",
        f".btn-primary-outline:hover {{ color: {p['link_hover']}; }}",
        f".tabs-wrapper .step.active {{ color: {p['on_primary']}; }}",
        f".tabs-wrapper .tab {{ color: {p['on_tint']}; }}",
        f".tabs-wrapper .tab:hover {{ color: {colour.on(p['primary_pressed'])}; }}",
        f"a:hover {{ color: {p['link_hover']}; }}",
        "/* Links and the active step inside a dark band take the band's text colour. */",
        f".dark-to-light-bg a:not(.btn), .light-to-dark-bg a:not(.btn), .dark-to-light-bg .step.active, .dark-to-light-bg .step:hover {{ color: {p['on_dark']}; }}",
        ".dark-to-light-bg .step.active { text-decoration: underline; text-underline-offset: 4px; }",
        "/* The white card that sits inside a dark band keeps ordinary link colours. */",
        f".inner-container a:not(.btn) {{ color: {p['link']}; }}",
        "code { color: #A61E63; }",
        f".catalogue-controls__view-toggle button.active svg, .active#grid-svg, .active#list-svg {{ fill: {p['on_primary']}; }}",
        f".active#view-as-list, .active#view-as-cards, .catalogue-controls__view-toggle button.active, "
        f".catalogue-controls__view-toggle button.active .view-toggle__label {{ color: {p['on_primary']}; }}",
        f".sidebar-nav a, .sidebar-nav a:hover {{ color: {p['text_muted']}; }}",
        "input::placeholder, textarea::placeholder { color: #6B7280; }",
        "",
        "/* 6. Sign-in, sign-up and password pages. */",
        f"html body div.login-page-wrapper {{ background: {auth_background}; }}",
        f".brand-logo-auth {{ height: {max(height, 36)}px; width: auto; max-width: 280px; }}",
        ".login-page-wrapper .logo { width: auto; justify-content: center; margin-bottom: 24px; }",
        "/* The default theme draws these inputs without a border, on a grey panel. */",
        ".login-form-input { border: 1px solid #848B98; background: #FFFFFF; }",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- emails

def mail_frame(kit: dict, p: dict, urls: dict) -> tuple[str, str]:
    """Opening and closing HTML of a branded email. Email clients need inline styles, absolute
    image URLs and a raster logo, so the logo is shown only when the kit has portal_url and
    logo.email; otherwise the brand name is written as text."""
    if kit["portal_url"] and urls.get("logo_email"):
        mark = f'<img src="{kit["portal_url"]}{urls["logo_email"]}" alt="{esc(kit["logo"]["alt"])}" height="32" style="height:32px;border:0;display:block;">'
    else:
        mark = f'<span style="font-size:20px;font-weight:700;color:{p["on_dark"]};">{esc(kit["name"])}</span>'
    support = (f' Questions? Write to <a href="mailto:{kit["support_email"]}" style="color:{p["link"]};">{kit["support_email"]}</a>.'
               if kit["support_email"] else "")
    head = (
        f'<div style="margin:0;padding:24px 0;background:#F3F4F6;font-family:Arial,Helvetica,sans-serif;color:{p["text"]};">'
        '<table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" style="width:600px;max-width:100%;background:#FFFFFF;border-radius:8px;overflow:hidden;">'
        f'<tr><td style="padding:20px 28px;background:{p["dark"]};">{mark}</td></tr>'
        '<tr><td style="padding:28px;font-size:15px;line-height:1.5;">\n'
    )
    tail = (
        '\n</td></tr>'
        f'<tr><td style="padding:16px 28px;border-top:1px solid {p["border"]};font-size:12px;color:{p["text_muted"]};">'
        f'{esc(kit["footer_text"])}.{support}</td></tr></table></div>\n'
    )
    return head, tail


def patch_mailers(root: Path, kit: dict, p: dict, urls: dict, report: Report) -> None:
    mailers = root / "mailers"
    name, portal = kit["name"], kit["portal_name"]
    admin_url = f'{kit["portal_url"]}/admin' if kit["portal_url"] else "the admin application"
    support = kit["support_email"]
    fixes = 0
    for path in sorted(mailers.rglob("*.tmpl")):
        if path.parent.name == "layouts":
            continue
        text = original = path.read_text()
        text = text.replace("https://portal.com/admin", admin_url)
        if support:
            text = text.replace("customer@support.com", support)
        else:
            text = re.sub(r"\s*If you did not perform this action, please contact the customer support team at customer@support\.com\.", "", text)
        for old, new in (
            ("Welcome to the Developer portal", f"Welcome to {portal}"),
            ("registering with the Developer Portal", f"registering with {portal}"),
            ("manage the developer portal content by logging in portal at", "manage the portal content by signing in at"),
            ("log in to the developer portal", f"sign in to {portal}"),
            ("log in to thr developer portal", f"sign in to {portal}"),
            ("request ahs been submitted", "request has been submitted"),
            ("The Teams", f"The {name} team"), ("The Team", f"The {name} team"), ("The team", f"The {name} team"),
            ("<title>Credential Expiring Soon</title>", f"<title>{esc(portal)}</title>"),
        ):
            text = text.replace(old, new)
        if path.name.endswith(".text.tmpl"):
            # Four stock plain-text mails carry HTML tags (<p>, <strong>, <br/>), which a mail
            # client shows literally in the text part.
            text = re.sub(r"<br\s*/?>", "\n", text)
            text = re.sub(r"</?(?:p|strong)>", "", text)
        if text != original:
            path.write_text(text)
            fixes += 1
    report.record("email wording", fixes)

    head, tail = mail_frame(kit, p, urls)
    layout = mailers / "layouts/emails.html.tmpl"
    if layout.is_file():
        layout.write_text(f"{head}{{{{ yield }}}}{tail}")
    report.record("email layout", int(layout.is_file()))
    # These two are sent without the layout, so the frame goes into the files themselves.
    framed = 0
    for path in (mailers / "auth/reset_password.html.tmpl", mailers / "auth/targeted_invite.html.tmpl"):
        if path.is_file() and "role=\"presentation\"" not in path.read_text():
            path.write_text(f"{head}{path.read_text()}{tail}")
            framed += 1
    report.record("emails sent without the layout", framed)


# --------------------------------------------------------------------------- finishing

def write_manifest(root: Path, kit: dict, version: str | None) -> dict:
    path = root / "theme.json"
    manifest = json.loads(path.read_text())
    manifest["name"] = kit["theme_name"]
    manifest["version"] = version or "1.0.0"
    manifest["author"] = kit["name"]
    path.write_text(json.dumps(manifest, indent=4) + "\n")
    return manifest


def remove_stock_files(root: Path, report: Report) -> None:
    removed = 0
    for name in STOCK_BRAND_FILES:
        path = root / name
        if path.is_file():
            path.unlink()
            removed += 1
    partners = root / "assets/images/partners"
    if partners.is_dir() and not any(partners.iterdir()):
        partners.rmdir()
    report.record("stock brand files removed", removed, required=False)


def scan_for_stock(root: Path) -> list[str]:
    """Stock branding left in the templates and stylesheets of a theme folder."""
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".tmpl", ".css", ".json", ".js", ".html"}:
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(("assets/vendor/", "assets/stoplight/")):
            continue
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            cleaned = MARKER_ALLOWED.sub("", line)
            for label, pattern in STOCK_MARKERS:
                if pattern.search(cleaned):
                    findings.append(f"{relative}:{number}: {label}: {line.strip()[:120]}")
    for name in STOCK_BRAND_FILES:
        if (root / name).is_file():
            findings.append(f"{name}: stock brand file still in the theme")
    return findings


def write_zip(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != ".DS_Store":
                info = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())


def check_archive(path: Path) -> list[str]:
    """Everything that would make the Portal refuse this archive, or accept it and break later.

    A refused upload is kept: the Portal has already stored the files it unpacked, and at the
    next start a stored theme folder it cannot load interrupts theme loading (see
    references/pitfalls.md)."""
    problems: list[str] = []
    if not path.is_file():
        return [f"{path} does not exist"]
    size = path.stat().st_size
    if size > MAX_TOTAL_BYTES:
        problems.append(f"archive is {size} bytes; the Portal's default upload limit is {MAX_TOTAL_BYTES} bytes (32 MB)")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return [f"{path.name} is not a zip archive"]
    with archive:
        names = [info.filename for info in archive.infolist() if not info.filename.endswith("/")]
        if "theme.json" not in names:
            wrapped = [name for name in names if name.endswith("/theme.json")]
            problems.append("theme.json is not at the root of the archive"
                            + (f" (found {wrapped[0]}: zip the theme's contents, not its folder)" if wrapped else ""))
            return problems
        for name in names:
            if name != "theme.json" and name.endswith("theme.json"):
                problems.append(f"a second theme.json at {name}: the Portal refuses nested themes")
            if ".." in Path(name).parts or name.startswith("/"):
                problems.append(f"entry escapes the theme folder: {name}")
            if "__MACOSX" in name or Path(name).name == ".DS_Store":
                problems.append(f"operating-system residue in the archive: {name}")
        for info in archive.infolist():
            if info.file_size > MAX_FILE_BYTES:
                problems.append(f"{info.filename} is {info.file_size} bytes; keep each file at or under {MAX_FILE_BYTES} bytes (5 MB)")
        try:
            manifest = json.loads(archive.read("theme.json"))
        except ValueError as error:
            problems.append(f"theme.json is not valid JSON: {error}")
            return problems
        name = manifest.get("name") if isinstance(manifest, dict) else None
        if not isinstance(name, str) or not 1 <= len(name) <= 50:
            problems.append("theme.json name must be 1 to 50 characters")
        elif not brand_kit.THEME_NAME.fullmatch(name):
            problems.append(f"theme.json name {name!r}: use a-z, 0-9 and - only, so the name is safe in API paths")
        if not str(manifest.get("version") or "").strip():
            problems.append("theme.json version is empty")
        templates = manifest.get("templates") if isinstance(manifest, dict) else None
        if not isinstance(templates, list) or not templates:
            problems.append("theme.json has no templates: no page could use this theme")
        folders = {name.split("/", 1)[0] for name in names if "/" in name}
        for folder in REQUIRED_FOLDERS:
            if folder not in folders:
                problems.append(f"folder {folder}/ is missing: the Portal cannot load a theme without it")
        for member in names:
            if member.endswith(".tmpl"):
                text = archive.read(member).decode("utf-8", "replace")
                for action in TEMPLATE_ACTION.findall(text):
                    found = REMOVED_CALL.search(action)
                    if found:
                        problems.append(f"{member} calls {found.group(1)}, which Portal 1.20.1 removed: "
                                        "the page answers 500 'function not defined'")
                        break
        for entry in templates or []:
            if isinstance(entry, dict):
                if f"views/{entry.get('template')}.tmpl" not in names:
                    problems.append(f"theme.json template {entry.get('template')!r} has no views/{entry.get('template')}.tmpl")
                if f"layouts/{entry.get('layout')}.tmpl" not in names:
                    problems.append(f"theme.json template {entry.get('template')!r} names layout {entry.get('layout')!r}, which is not in layouts/")
    return problems


def build(source: Path, kit_dir: Path, output: Path, version: str | None = None, allow_missing: bool = False) -> dict:
    kit = brand_kit.load(kit_dir)
    p = brand_kit.palette(kit)
    report = Report(allow_missing)
    with tempfile.TemporaryDirectory() as temporary:
        root = extract(source, Path(temporary) / "theme")
        for folder in REQUIRED_FOLDERS:
            if not (root / folder).is_dir():
                raise BuildError(f"the source theme has no {folder}/ folder; is it a Portal theme?")
        urls = copy_assets(root, kit)
        patch_navigation(root, kit, p, urls, report)
        patch_auth(root, kit, p, urls, report)
        patch_images(root, kit, p, urls, report)
        patch_layouts(root, kit, urls, report)
        pin_renderers(root, report)
        write_fonts(root, kit, urls, report)
        (root / STYLESHEET).write_text(brand_css(kit, p, urls))
        patch_mailers(root, kit, p, urls, report)
        manifest = write_manifest(root, kit, version)
        remove_stock_files(root, report)
        leftovers = scan_for_stock(root)
        if report.missing and not allow_missing:
            raise BuildError("expected touchpoints not found in the source theme (is it the default theme of Portal 1.20.1?): "
                             + "; ".join(sorted(set(report.missing))) + ". Rerun with --allow-missing to build anyway.")
        if leftovers:
            raise BuildError("stock branding is still in the built theme:\n  " + "\n  ".join(leftovers[:40]))
        write_zip(root, output)
    problems = check_archive(output)
    if problems:
        output.unlink(missing_ok=True)
        raise BuildError("the built archive would not load:\n  " + "\n  ".join(problems))
    contrast = brand_kit.contrast_report(p)
    for row in contrast:
        if not row["ok"]:
            report.warn(f"low contrast {row['ratio']}:1 (minimum {row['minimum']}:1) for {row['where']}: "
                        f"{row['text']} {row['text_colour']} on {row['background']} {row['background_colour']}")
    if kit["placeholder"]:
        report.warn("this is a placeholder kit: the Portal will not carry the organisation's own brand")
    if not kit["logo"]["on_dark"] or not kit["logo"]["on_light"]:
        report.warn("only one logo variant supplied: it is shown on a white plate wherever the surface does not suit it")
    for slot, effect in (("hero", "the home and page header band has no picture"),
                         ("about", "the home and About picture column has no picture"),
                         ("error", "the not-found and 403 pages keep the default theme's illustration, recoloured")):
        if slot not in kit["images"]:
            report.warn(f"images.{slot} not supplied: {effect}")
    if kit["fonts"] and not kit["fonts"].get("files"):
        report.warn("fonts.files is empty: the typeface shows only for visitors who have it installed")
    return {
        "theme": manifest["name"], "version": manifest["version"], "output": str(output),
        "bytes": output.stat().st_size, "replaced": report.done, "missing": sorted(set(report.missing)),
        "warnings": report.warnings, "palette": p, "contrast": contrast,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, help="the default theme downloaded from the Portal (zip or folder)")
    parser.add_argument("--kit", type=Path, help="brand kit folder holding brand.json")
    parser.add_argument("--output", type=Path, help="where to write the theme archive")
    parser.add_argument("--version", help="theme version to write into theme.json (default 1.0.0)")
    parser.add_argument("--allow-missing", action="store_true", help="build even when an expected touchpoint is absent")
    parser.add_argument("--check", type=Path, metavar="ZIP", help="only pre-flight an archive and exit")
    args = parser.parse_args(argv)
    if args.check:
        problems = check_archive(args.check)
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        print(f"{args.check}: {'not safe to upload' if problems else 'safe to upload'}")
        return 1 if problems else 0
    if not (args.source and args.kit and args.output):
        parser.error("--source, --kit and --output are required")
    try:
        result = build(args.source, args.kit, args.output, args.version, args.allow_missing)
    except (BuildError, brand_kit.KitError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
