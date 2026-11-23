#!/usr/bin/env python3
"""Build the branded Portal theme from the running Portal's default theme, using the Python
standard library: the neutral overlay stylesheet plus the brand kit in portal/brand/."""

import argparse
import html
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile


parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--overlay", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--brand", type=Path, help="directory holding brand.json and the assets it names")
args = parser.parse_args()

# brand.json values reach CSS and templates, so they are held to shapes that cannot close a
# rule or open a tag (validate_bundle.py checks the same before a bundle is used).
ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.(?:svg|png)")
COLOR = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})")
FONT = re.compile(r"[A-Za-z0-9 ,'\"-]{1,200}")

# Portal theme colour variables and the brand.json colour that fills each. The default theme
# paints the navigation, buttons, links, hero and footer from these variables (the nav from the
# --tdp-nav-bg-color gradient in main.css), so element selectors in an overlay have no visible
# effect; overriding the variables in :root does.
VARIABLES = {
    "--tdp-primary": "primary", "--tdp-primary-dark": "primary_dark", "--tdp-primary-light": "primary_light",
    "--tdp-brand-dark": "primary_dark", "--tdp-brand-darker": "primary_darker",
    "--tdp-primary-btn-color": "primary", "--tdp-primary-btn-hover-color": "primary_dark",
    "--tdp-link-color": "link", "--tdp-nav-bg-color": "nav_bg", "--tdp-nav-link-color": "nav_link",
    "--tdp-text-color": "text", "--tdp-dark": "hero_bg", "--tdp-secondary": "secondary",
    # Despite its name, the blue of the links on the sign-in pages.
    "--tdp-light-purple": "link",
}
# Tyk palette colours hard-coded in the theme CSS rather than read from a variable, for
# example the product hero navy in pages/api-product.css.
HARD_CODED = {
    "#335FFD": "primary", "#2A4DD0": "primary_dark", "#223EAC": "primary_darker",
    "#1A3088": "primary_dark", "#371DD7": "primary_dark",
    "#03031C": "hero_bg", "#181834": "hero_bg",
}
# Stock illustrations that feature the Tyk mascot; the brand hero image replaces them.
MASCOTS = ("tyk-portal-silhouette.png", "about-us.png", "request-access.png", "login.svg", "blog.svg", "card-placeholder.svg",
           "not-found.svg")


def load_brand(brand_dir: Path) -> dict:
    brand = json.loads((brand_dir / "brand.json").read_text())
    if not isinstance(brand, dict):
        raise SystemExit("portal/brand/brand.json must be an object")
    for key in ("logo", "logo_on_dark", "favicon", "hero"):
        name = brand.get(key)
        if name and (not ASSET_NAME.fullmatch(name) or not (brand_dir / name).is_file()):
            raise SystemExit(f"portal/brand/brand.json: {key} must name an .svg or .png file in portal/brand/")
    for key, value in (brand.get("colors") or {}).items():
        if not isinstance(value, str) or not COLOR.fullmatch(value):
            raise SystemExit(f"portal/brand/brand.json: colors.{key} must be a #rgb or #rrggbb colour")
    font = brand.get("font_family")
    if font and (not isinstance(font, str) or not FONT.fullmatch(font)):
        raise SystemExit("portal/brand/brand.json: font_family holds characters a CSS font list cannot")
    return brand


def apply_brand(theme_root: Path, brand_dir: Path) -> None:
    """Replace every stock Tyk brand touchpoint of the default theme with the brand kit's.

    Touchpoints in the Portal 1.19.0 default theme: the nav brand text, the footer logo and
    "Powered by" line, the logo and heading on the auth pages, the mascot illustrations, the
    partner-logo strip on the home page, the favicon (absent by default) and the colours.
    """
    brand = load_brand(brand_dir)
    images = theme_root / "assets" / "images" / "brand"
    images.mkdir(parents=True, exist_ok=True)
    for key in ("logo", "logo_on_dark", "favicon", "hero"):
        if brand.get(key):
            shutil.copyfile(brand_dir / brand[key], images / brand[key])

    def url(key: str, fallback: str = "") -> str:
        name = brand.get(key) or brand.get(fallback)
        return f"/assets/images/brand/{name}" if name else ""

    name = html.escape(brand.get("name", ""))
    alt = html.escape(brand.get("logo_alt") or brand.get("name", ""))
    # The nav, the footer and the auth pages all sit on dark backgrounds.
    on_dark = url("logo_on_dark", "logo")

    nav = theme_root / "partials" / "top_nav.tmpl"
    if nav.is_file() and on_dark:
        suffix = html.escape(brand.get("nav_suffix", ""))
        label = f'<span class="brand-suffix ml-2">{suffix}</span>' if suffix else ""
        nav.write_text(re.sub(
            r'<a class="navbar-brand"[^>]*>.*?</a>',
            lambda _match: f'<a class="navbar-brand d-flex align-items-center" href="/"><img src="{on_dark}" alt="{alt}" class="brand-logo">{label}</a>',
            nav.read_text(), count=1, flags=re.S))

    footer = theme_root / "partials" / "footer.tmpl"
    if footer.is_file():
        text = footer.read_text()
        if on_dark:
            text = text.replace('src="/assets/images/dev-portal-logo.svg"', f'src="{on_dark}" alt="{alt}" class="brand-logo-footer"')
        footer_text = html.escape(brand.get("footer_text") or (f"© {brand['name']}" if brand.get("name") else ""))
        if footer_text:
            text = text.replace("Powered by © Tyk Technologies", footer_text)
        footer.write_text(text)

    auth_templates = [*theme_root.glob("auth/**/*.tmpl"), *theme_root.glob("layouts/auth/*.tmpl")]
    for template in auth_templates:
        text = original = template.read_text()
        if on_dark:
            text = text.replace("/assets/images/new-logo.svg", on_dark).replace('alt="Tyk Logo"', f'alt="{alt}"')
        if name:
            text = text.replace("Tyk Developer Portal", f"{name} Developer Portal")
        if text != original:
            template.write_text(text)

    hero = url("hero")
    if hero:
        for template in [*theme_root.glob("views/*.tmpl"), *theme_root.glob("partials/*.tmpl"), *auth_templates]:
            text = original = template.read_text()
            for mascot in MASCOTS:
                text = text.replace(f"/assets/images/{mascot}", hero)
            # The stock illustrations are anchored to one side at their natural size.
            text = re.sub(rf"url\('{re.escape(hero)}'\) no-repeat (?:left|right) center;",
                          f"url('{hero}') no-repeat center center / contain;", text)
            if text != original:
                template.write_text(text)

    # The stock partner-logo strip shows sample companies, never anyone's real partners.
    home = theme_root / "views" / "home.tmpl"
    if home.is_file():
        home.write_text(re.sub(r"\s*<!-- Partners -->.*?<!-- end of Partners -->", "", home.read_text(), flags=re.S))

    favicon = url("favicon")
    if favicon:
        link = f'<link rel="icon" href="{favicon}">'
        for layout in theme_root.glob("layouts/**/*.tmpl"):
            text = layout.read_text()
            if "</head>" in text and link not in text:
                layout.write_text(text.replace("</head>", f"    {link}\n</head>", 1))

    colors = brand.get("colors") or {}
    for css in (theme_root / "assets" / "stylesheets").rglob("*.css"):
        if "stoplight" in css.parts or css.name == "neutral-overlay.css":
            continue
        text = original = css.read_text()
        for stock, key in HARD_CODED.items():
            if colors.get(key):
                text = re.sub(re.escape(stock), colors[key], text, flags=re.I)
        if text != original:
            css.write_text(text)

    rules = [":root {", *(f"  {variable}: {colors[key]};" for variable, key in VARIABLES.items() if colors.get(key)), "}"]
    if colors.get("on_primary"):
        # Primary buttons, including the auth pages' .btn.portal-primary, need a readable label.
        rules.append(f".btn-primary, .button-primary, .btn-primary:hover, .btn.portal-primary {{ color: {colors['on_primary']} !important; }}")
    if colors.get("link"):
        # Outline buttons and placeholders take the primary colour on white by default, which is
        # unreadable for a light brand colour.
        rules.append(f".btn-primary-outline {{ color: {colors['link']} !important; border-color: {colors.get('primary', colors['link'])} !important; }}")
        rules.append("input::placeholder, textarea::placeholder { color: #6c757d !important; }")
    if brand.get("font_family"):
        rules.append(f"body, h1, h2, h3, h4, h5, h6, .btn {{ font-family: {brand['font_family']}; }}")
    # The stock login backdrop (pages/login.css) features the Tyk mascot.
    rules.append(".login-page-wrapper { background: var(--tdp-dark) !important; }")
    rules += [".brand-logo { height: 28px; width: auto; }",
              ".brand-logo-footer { height: 32px; width: auto; }",
              ".brand-suffix { font-weight: 500; letter-spacing: 0.02em; }"]
    styles = theme_root / "assets" / "stylesheets" / "neutral-overlay.css"
    styles.write_text(styles.read_text() + "\n/* brand: generated from portal/brand/brand.json */\n" + "\n".join(rules) + "\n")


with tempfile.TemporaryDirectory() as temporary:
    extracted = Path(temporary) / "theme"
    if args.source.is_dir():
        shutil.copytree(args.source, extracted)
    else:
        with zipfile.ZipFile(args.source) as archive:
            archive.extractall(extracted)
    manifests = list(extracted.rglob("theme.json"))
    if not manifests:
        raise SystemExit("source theme has no theme.json")
    manifest_path = manifests[0]
    theme_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    manifest["name"] = "neutral-demo"
    manifest["version"] = f"{manifest.get('version', '0.0.0')}-neutral"
    manifest["author"] = "__DISPLAY_NAME__ Platform Team"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    styles = theme_root / "assets" / "stylesheets"
    styles.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.overlay, styles / "neutral-overlay.css")
    marker = '<link href="/assets/stylesheets/neutral-overlay.css{{.previewId}}" rel="stylesheet">'
    for layout in theme_root.glob("layouts/portal_layout*.tmpl"):
        text = layout.read_text()
        if "neutral-overlay.css" not in text:
            layout.write_text(text.replace("</head>", f"    {marker}\n</head>", 1))

    if args.brand and (args.brand / "brand.json").is_file():
        apply_brand(theme_root, args.brand)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(theme_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(theme_root))
