#!/usr/bin/env python3
"""Load, validate and complete a Developer Portal brand kit.

A kit is a folder holding ``brand.json`` and the files it names (see
``references/brand-kit.md`` and ``assets/brand-kit-neutral/``). Kit values end up in CSS
and in HTML templates, so every value is held to a shape that cannot close a rule or open a
tag, and every file is checked before it is copied into a theme.

    python3 brand_kit.py <kit-dir>            validate, print the completed palette and the
                                              contrast of every text/background pair
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import colour

MAX_FILE_BYTES = 5 * 1024 * 1024  # the Portal's documented per-file limit for theme files
IMAGE_TYPES = {".svg", ".png", ".jpg", ".jpeg", ".webp"}
FAVICON_TYPES = {".svg", ".png", ".ico"}
FONT_TYPES = {".woff2", ".woff", ".ttf", ".otf"}
FONT_FORMATS = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}
FONT_WEIGHTS = ("400", "500", "600", "700")
ASSET_PATH = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9._-]{0,99}/){0,3}[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
TEXT = re.compile(r"[^<>{}\x00-\x1f]{1,200}")
FONT_NAME = re.compile(r"[A-Za-z0-9 _-]{1,60}")
THEME_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,49}")
URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LANG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")
HREF = re.compile(r"(?:https?://[^\s\"'<>{}]{1,300}|/[A-Za-z0-9._~/#?=&-]{0,300}|mailto:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)")
SVG_ACTIVE = re.compile(r"<script|\son[a-z]+\s*=|javascript:|<foreignObject", re.IGNORECASE)

IMAGE_KEYS = ("hero", "about", "getting_started", "auth_background", "blog", "error", "card_placeholder")
COLOUR_KEYS = (
    "primary", "primary_hover", "primary_pressed", "on_primary", "link", "link_hover",
    "nav_bg", "nav_text", "dark", "on_dark", "footer_bg", "footer_text", "footer_link",
    "auth_bg", "text", "text_muted", "tint", "secondary", "border",
)


class KitError(ValueError):
    pass


def _fail(message: str) -> None:
    raise KitError(f"brand.json: {message}")


def _text(kit: dict, key: str, required: bool = False) -> str:
    value = kit.get(key)
    if value in (None, ""):
        if required:
            _fail(f"{key} is required")
        return ""
    if not isinstance(value, str) or not TEXT.fullmatch(value):
        _fail(f"{key} must be plain text without < > {{ }} (at most 200 characters)")
    return value


def _asset(kit_dir: Path, name, label: str, types: set[str]) -> str:
    if not isinstance(name, str) or not ASSET_PATH.fullmatch(name) or ".." in name:
        _fail(f"{label} must be a relative file name inside the kit folder")
    path = kit_dir / name
    if not path.is_file():
        _fail(f"{label} names {name}, which is not in the kit folder")
    if path.suffix.lower() not in types:
        _fail(f"{label} must be one of {', '.join(sorted(types))}; got {name}")
    size = path.stat().st_size
    if size == 0:
        _fail(f"{label} ({name}) is empty")
    if size > MAX_FILE_BYTES:
        _fail(f"{label} ({name}) is {size} bytes; a theme file may be at most {MAX_FILE_BYTES} bytes (5 MB)")
    if path.suffix.lower() == ".svg" and SVG_ACTIVE.search(path.read_text(errors="replace")):
        _fail(f"{label} ({name}) carries script, an event handler, a javascript: URL or foreignObject")
    return name


def load(kit_dir: Path) -> dict:
    """Read and validate ``brand.json``. Returns the kit with defaults filled in."""
    kit_dir = Path(kit_dir)
    source = kit_dir / "brand.json"
    if not source.is_file():
        raise KitError(f"{source} not found")
    try:
        kit = json.loads(source.read_text())
    except ValueError as error:
        raise KitError(f"brand.json is not valid JSON: {error}") from error
    if not isinstance(kit, dict):
        _fail("must be an object")

    out: dict = {"dir": kit_dir, "placeholder": bool(kit.get("placeholder"))}
    out["name"] = _text(kit, "name", required=True)
    out["portal_name"] = _text(kit, "portal_name") or f"{out['name']} Developer Portal"
    out["nav_label"] = _text(kit, "nav_label")
    out["footer_text"] = _text(kit, "footer_text") or f"© {out['name']}"
    theme_name = kit.get("theme_name") or re.sub(r"[^a-z0-9]+", "-", out["name"].lower()).strip("-")[:50]
    if not isinstance(theme_name, str) or not THEME_NAME.fullmatch(theme_name) or theme_name == "default":
        _fail("theme_name must be 1 to 50 characters of a-z, 0-9 and -, and must not be 'default'")
    out["theme_name"] = theme_name
    lang = kit.get("lang", "en")
    if not isinstance(lang, str) or not LANG.fullmatch(lang):
        _fail("lang must be a language tag such as en or de-CH")
    out["lang"] = lang

    portal_url = kit.get("portal_url", "")
    if portal_url and (not isinstance(portal_url, str) or not URL.fullmatch(portal_url)):
        _fail("portal_url must be an http(s) URL without query string, for example https://developers.example.com")
    out["portal_url"] = portal_url.rstrip("/")
    support = kit.get("support_email", "")
    if support and (not isinstance(support, str) or not EMAIL.fullmatch(support)):
        _fail("support_email must be an email address")
    out["support_email"] = support

    logo = kit.get("logo")
    if not isinstance(logo, dict) or not (logo.get("on_light") or logo.get("on_dark")):
        _fail("logo must be an object with on_light and/or on_dark (the logo file for light and for dark surfaces)")
    out["logo"] = {
        "on_light": _asset(kit_dir, logo["on_light"], "logo.on_light", IMAGE_TYPES) if logo.get("on_light") else "",
        "on_dark": _asset(kit_dir, logo["on_dark"], "logo.on_dark", IMAGE_TYPES) if logo.get("on_dark") else "",
        "email": _asset(kit_dir, logo["email"], "logo.email", {".png", ".jpg", ".jpeg"}) if logo.get("email") else "",
        "alt": _text(logo, "alt") or out["name"],
    }
    height = logo.get("height", 28)
    if not isinstance(height, int) or isinstance(height, bool) or not 16 <= height <= 44:
        _fail("logo.height must be a whole number of pixels from 16 to 44 (the navigation bar is 55px tall)")
    out["logo"]["height"] = height

    out["favicon"] = _asset(kit_dir, kit["favicon"], "favicon", FAVICON_TYPES) if kit.get("favicon") else ""

    images = kit.get("images") or {}
    if not isinstance(images, dict):
        _fail("images must be an object")
    for key in images:
        if key not in IMAGE_KEYS:
            _fail(f"images.{key} is not a known image slot ({', '.join(IMAGE_KEYS)})")
    out["images"] = {key: _asset(kit_dir, images[key], f"images.{key}", IMAGE_TYPES) for key in IMAGE_KEYS if images.get(key)}

    colours = kit.get("colors") or {}
    if not isinstance(colours, dict) or not colours.get("primary"):
        _fail("colors.primary is required")
    for key, value in colours.items():
        if key not in COLOUR_KEYS:
            _fail(f"colors.{key} is not a known colour role ({', '.join(COLOUR_KEYS)})")
        try:
            colour.parse(value)
        except ValueError:
            _fail(f"colors.{key} must be a #rgb or #rrggbb colour")
    out["colors"] = {key: colour.normalise(value) for key, value in colours.items()}

    fonts = kit.get("fonts") or {}
    if not isinstance(fonts, dict):
        _fail("fonts must be an object")
    out["fonts"] = {}
    if fonts:
        family = fonts.get("family")
        if not isinstance(family, str) or not FONT_NAME.fullmatch(family):
            _fail("fonts.family must be a font name of letters, digits, spaces, - and _")
        files = fonts.get("files") or {}
        local = fonts.get("local") or []
        if not isinstance(files, dict) or not isinstance(local, list):
            _fail("fonts.files must be an object and fonts.local a list")
        for weight in files:
            if weight not in FONT_WEIGHTS:
                _fail(f"fonts.files.{weight}: weights are {', '.join(FONT_WEIGHTS)}")
        for name in local:
            if not isinstance(name, str) or not FONT_NAME.fullmatch(name):
                _fail("fonts.local entries must be installed font names")
        if files and "400" not in files:
            _fail("fonts.files needs at least the 400 (regular) weight")
        if not files and not local:
            _fail("fonts needs files (self-hosted font files) or local (names of fonts installed on visitors' machines)")
        out["fonts"] = {
            "family": family,
            "files": {weight: _asset(kit_dir, files[weight], f"fonts.files.{weight}", FONT_TYPES) for weight in FONT_WEIGHTS if weight in files},
            "local": list(local),
        }

    links = kit.get("footer_links") or []
    if not isinstance(links, list):
        _fail("footer_links must be a list of {label, href}")
    out["footer_links"] = []
    for link in links:
        if not isinstance(link, dict) or not TEXT.fullmatch(str(link.get("label", ""))) or not HREF.fullmatch(str(link.get("href", ""))):
            _fail("footer_links entries need a plain-text label and an href that is a path, an http(s) URL or a mailto:")
        out["footer_links"].append({"label": link["label"], "href": link["href"]})

    sources = kit.get("sources") or {}
    if not isinstance(sources, dict):
        _fail("sources must be an object: where each asset and colour came from")
    out["sources"] = sources
    return out


def palette(kit: dict) -> dict:
    """Every colour role, with the ones the kit left out derived from the ones it gave."""
    given = kit["colors"]
    p: dict = {}
    p["primary"] = given["primary"]
    p["primary_hover"] = given.get("primary_hover") or colour.darken(p["primary"], 0.14)
    p["primary_pressed"] = given.get("primary_pressed") or colour.darken(p["primary"], 0.28)
    p["on_primary"] = given.get("on_primary") or colour.on(p["primary"])
    p["link"] = given.get("link") or colour.readable(p["primary"])
    p["link_hover"] = given.get("link_hover") or colour.darken(p["link"], 0.25)
    p["dark"] = given.get("dark") or colour.mix(colour.darken(p["primary"], 0.82), "#101418", 0.5)
    p["on_dark"] = given.get("on_dark") or colour.on(p["dark"])
    p["nav_bg"] = given.get("nav_bg") or p["dark"]
    p["nav_text"] = given.get("nav_text") or colour.on(p["nav_bg"])
    p["footer_bg"] = given.get("footer_bg") or p["dark"]
    p["footer_text"] = given.get("footer_text") or colour.on(p["footer_bg"])
    p["footer_link"] = given.get("footer_link") or p["footer_text"]
    p["auth_bg"] = given.get("auth_bg") or p["dark"]
    p["text"] = given.get("text") or "#1B1D22"
    p["text_muted"] = given.get("text_muted") or "#4A5160"
    p["tint"] = given.get("tint") or colour.mix(colour.WHITE, p["primary"], 0.09)
    p["secondary"] = given.get("secondary") or "#5B6472"
    p["border"] = given.get("border") or "#D9DDE3"
    # Hover colour of a navigation link: the primary when it reads on the bar, else the text.
    p["nav_hover"] = p["primary"] if colour.contrast(p["primary"], p["nav_bg"]) >= colour.AA_LARGE else p["nav_text"]
    p["on_primary_hover"] = colour.on(p["primary_hover"])
    p["on_tint"] = colour.readable(p["text"], p["tint"])
    return p


# (text role, background role, minimum ratio, where it shows)
CONTRAST_PAIRS = (
    ("on_primary", "primary", colour.AA_TEXT, "primary button label, active tab"),
    ("on_primary_hover", "primary_hover", colour.AA_TEXT, "primary button label on hover"),
    ("link", "#FFFFFF", colour.AA_TEXT, "links on white"),
    ("nav_text", "nav_bg", colour.AA_TEXT, "navigation bar links"),
    ("nav_hover", "nav_bg", colour.AA_LARGE, "navigation bar link on hover"),
    ("on_dark", "dark", colour.AA_TEXT, "hero bands, product and docs header"),
    ("footer_text", "footer_bg", colour.AA_TEXT, "footer text"),
    ("footer_link", "footer_bg", colour.AA_TEXT, "footer links"),
    ("text", "#FFFFFF", colour.AA_TEXT, "body text"),
    ("text_muted", "#FFFFFF", colour.AA_TEXT, "secondary text"),
    ("text", "tint", colour.AA_TEXT, "tabs and side-menu highlight"),
    ("secondary", "#FFFFFF", colour.AA_TEXT, "secondary buttons and pagination"),
)


def contrast_report(p: dict) -> list[dict]:
    rows = []
    for text_role, background_role, minimum, where in CONTRAST_PAIRS:
        background = background_role if background_role.startswith("#") else p[background_role]
        ratio = colour.contrast(p[text_role], background)
        rows.append({
            "text": text_role, "background": background_role, "text_colour": p[text_role],
            "background_colour": background, "ratio": round(ratio, 2), "minimum": minimum,
            "ok": ratio >= minimum, "where": where,
        })
    return rows


def logo_for(kit: dict, background: str) -> tuple[str, bool]:
    """The logo file for a surface of this colour, and whether it needs a white plate because
    only the variant made for the opposite kind of surface was supplied."""
    wanted, other = ("on_dark", "on_light") if colour.is_dark(background) else ("on_light", "on_dark")
    if kit["logo"][wanted]:
        return kit["logo"][wanted], False
    return kit["logo"][other], True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        kit = load(Path(argv[1]))
    except KitError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    p = palette(kit)
    report = contrast_report(p)
    print(json.dumps({"name": kit["name"], "theme_name": kit["theme_name"], "placeholder": kit["placeholder"],
                      "palette": p, "contrast": report}, indent=2))
    return 0 if all(row["ok"] for row in report) else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
