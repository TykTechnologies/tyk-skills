#!/usr/bin/env python3
"""The pages of a Developer Portal that a branding check has to visit.

Every layout of the default theme is covered: the main layout (home, CMS pages, catalogue,
product, checkout, sign-in pages, error pages), the private layout (dashboard, apps, profile,
users, analytics), the blog layout, and each API documentation layout a product on the Portal
actually uses. Product and documentation addresses are read from the admin API, so the list
fits the Portal in front of you.
"""

from __future__ import annotations

from portal_client import PortalAPI, PortalError

NOT_FOUND_PATH = "/brand-check-page-that-does-not-exist"

PUBLIC_FIXED = (
    ("home", "/"),
    ("catalogue", "/portal/catalogue-products"),
    ("sign-in", "/auth/password/login"),
    ("sign-up", "/auth/password/register"),
    ("forgot-password", "/auth/password/new"),
    # The set-new-password form renders for any token value; a real token comes by email.
    ("reset-password", "/auth/password/edit?token=brand-check"),
    ("not-found", NOT_FOUND_PATH),
)
PRIVATE_FIXED = (
    ("dashboard", "/portal/private/dashboard"),
    ("apps", "/portal/private/apps"),
    ("profile", "/portal/private/profile"),
    ("analytics", "/portal/private/analytics"),
    ("users", "/portal/private/users"),
    ("organisation", "/portal/private/organisation"),
    ("cart", "/portal/private/cart/provision"),
)
# Pages a team member (role consumer-team-member) may not open: the Portal answers 403 with the
# theme's forbidden view.
MEMBER_FORBIDDEN = (("forbidden", "/portal/private/users/invite"),)
RESERVED_PREFIXES = ("/portal/", "/auth/", "/admin", "/portal-api", "/blog", "/assets/", "/system/", "/api/")


def slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "page"


def discover(api: PortalAPI | None, blog: bool = True, max_products: int = 6) -> list[dict]:
    """[{name, path, private}] in visiting order. Without an API client only the fixed pages."""
    pages = [{"name": name, "path": path, "private": False} for name, path in PUBLIC_FIXED]
    if blog:
        pages.append({"name": "blog", "path": "/blog", "private": False})
    if api:
        for page in api.list_all("/pages"):
            path = page.get("Path") or ""
            if page.get("Status") == "Published" and path not in ("/", "") and not path.startswith(RESERVED_PREFIXES):
                pages.append({"name": f"page-{slug(path)}", "path": path, "private": False})
        seen_kinds: set[str] = set()
        for product in api.list_all("/products")[:max_products]:
            try:
                detail = api.get(f"/products/{product['ID']}")
            except PortalError:
                continue
            base = f"/portal/catalogue-products/{detail.get('Path')}"
            pages.append({"name": f"product-{slug(detail.get('Path') or str(product['ID']))}", "path": base, "private": False})
            for api_detail in detail.get("APIDetails") or []:
                kind = (api_detail.get("APIType") or "REST").lower()
                if api_detail.get("APIID") and kind not in seen_kinds:
                    seen_kinds.add(kind)
                    pages.append({"name": f"docs-{slug(kind)}-{slug(api_detail['APIID'])[:24]}",
                                  "path": f"{base}/{api_detail['APIID']}/docs", "private": False, "docs": True})
            # Documentation-only products: one page per specification (OpenAPI, AsyncAPI or
            # GraphQL). The Portal picks the renderer from the document itself.
            for spec in detail.get("SpecDetails") or []:
                alias = spec.get("SpecAlias")
                if alias:
                    kind = "graphql" if spec.get("GraphQLSDL") or spec.get("GraphQLServerEndpoint") else "spec"
                    pages.append({"name": f"docs-{kind}-{slug(alias)[:24]}", "path": f"{base}/{alias}/docs",
                                  "private": False, "docs": True})
    pages += [{"name": name, "path": path, "private": True} for name, path in PRIVATE_FIXED]
    pages += [{"name": name, "path": path, "private": True, "member": True, "expect": 403} for name, path in MEMBER_FORBIDDEN]
    return pages
