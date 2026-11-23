#!/usr/bin/env python3
"""What a freshly installed Developer Portal 1.20.1 shows before it is branded.

Used by the checks to prove that none of it is left. The phrases are the seeded copy of the
Home and About pages and the fixed wording of the default theme; the image names are the
default theme's logo, mascot and sample partner files.
"""

from __future__ import annotations

import re

# Whole phrases, matched case-sensitively in the text a visitor reads.
STOCK_PHRASES = (
    "DEVELOPER PORTAL",
    "Build the future with Tyk APIs",
    "Welcome to Tyk Developer Portal",
    "EXPLORE API PRODUCTS",
    "Our Use Cases",
    "Learn more about our use cases",
    "Build for consumers",
    "Build for business",
    "step-by-step guide accessing our API Products",
    "Discover our API Products",
    "Our API products let you use approved customer data",
    "Featured Blog Posts",
    "Our Partners",
    "partnered with some of the world",
    "About Tyk",
    "Tyk Pronunciation",
    "We believe in engineering",
    "Log in to your Tyk Developer Portal account",
    "customer@support.com",
    "portal.com/admin",
)
VENDOR_WORD = re.compile(r"\bTyk\b")

STOCK_IMAGES = (
    "dev-portal-logo.svg", "new-logo.svg", "tyk-portal-silhouette.png", "about-us.png",
    "request-access.png", "our-apis.png", "login-background.jpg", "login.svg",
    "partners/6point.svg", "partners/ncs.svg", "partners/continuum.svg", "partners/orbit.svg",
    "partners/prodig.svg", "partners/velvet.svg",
)

# Tracking code the default theme carries (inactive unless the Portal switches it on).
TRACKING = (
    ("Google Tag Manager", re.compile(r"googletagmanager\.com|GTM-[A-Z0-9]{4,}")),
    ("Google Analytics", re.compile(r"google-analytics\.com|gtag\(")),
    ("FullStory", re.compile(r"fullstory\.com|window\['_fs_")),
)

# Hosts the default theme loads scripts, styles and fonts from. Not branding, but a visitor's
# browser contacts each one, so the report names them.
DEFAULT_THIRD_PARTY_HOSTS = (
    "unpkg.com", "cdn.redoc.ly", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "code.jquery.com",
    "stackpath.bootstrapcdn.com",
)


def phrases_in(text: str, extra: tuple[str, ...] = ()) -> list[str]:
    found = [phrase for phrase in (*STOCK_PHRASES, *extra) if phrase in text]
    if VENDOR_WORD.search(text) and not any("Tyk" in phrase for phrase in found):
        found.append("Tyk")
    return found


def images_in(html: str) -> list[str]:
    return [name for name in STOCK_IMAGES if f"/assets/images/{name}" in html]


def tracking_in(html: str) -> list[str]:
    return [label for label, pattern in TRACKING if pattern.search(html)]
