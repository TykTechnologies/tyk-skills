#!/usr/bin/env python3
"""Write a Developer Portal's copy, names, images, menus and mail settings from one file.

    python3 apply_content.py --content content.json [--dry-run] [--only pages,products]
    python3 apply_content.py --export exported.json        what the Portal holds now

``content.json`` (see ``assets/content.example.json``) may hold any of these sections:

    pages       title and content blocks of CMS pages, by path; "create": true adds a page
    products    display name, description, HTML content, featured flag, tags, images, by Name
    plans       display name and description, by Name
    catalogues  name, by current Name
    menus       the items of the Primary and Footer menus (no API: the admin form is used)
    email       sender, admin address, SMTP host and the subject of each notification
                (no API: the General settings form is used)

Every write is read back and compared; the script exits non-zero when a value did not persist.
Running it twice changes nothing the second time. Image paths are relative to the content file.

Environment: PORTAL_URL, PORTAL_API_TOKEN; for menus and email also PORTAL_ADMIN_EMAIL and
PORTAL_ADMIN_PASSWORD; optional PORTAL_SMTP_PASSWORD. Standard library only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import re
import sys

from portal_client import PortalAPI, PortalError, PortalSession, read_form

SECTIONS = ("pages", "products", "plans", "catalogues", "menus", "email")
TAG_NAME = re.compile(r"[A-Za-z0-9_./-]+")  # what Portal 1.20.0 accepts; spaces, commas and non-ASCII get 422
PRODUCT_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif"}
DEFAULT_ROBOTS = "User-agent: *\nDisallow: /\n"
MENU_FIELD = "QorResource.menus,menu_items"
SUBJECT_FIELDS = {
    "reset_password": "EmailResetSubject", "access_approved": "EmailApproveSubject", "access_rejected": "EmailRejectSubject",
    "signup_request": "EmailSignupSubject", "invite_admin": "EmailInviteSubject", "account_activated": "EmailActivateSubject",
    "account_deactivated": "EmailDeactivateSubject", "invite_user": "EmailInviteConsumerSubject",
    "organisation_request": "EmailOrganisationRequestSubject", "organisation_approved": "EmailOrganisationApproveSubject",
    "organisation_rejected": "EmailOrganisationRejectSubject", "welcome_admin": "EmailWelcomeAdminSubject",
    "welcome_developer": "EmailWelcomeDevSubject",
}
EMAIL_FIELDS = {"from": "EmailDefaultFrom", "admin": "EmailAdmin", "smtp_host": "EmailSMTPHost",
                "smtp_port": "EmailSMTPPort", "smtp_user": "EmailSMTPUser"}


class Run:
    def __init__(self, api: PortalAPI, base: Path, dry_run: bool):
        self.api, self.base, self.dry_run = api, base, dry_run
        self.changes: list[str] = []
        self.unchanged = 0
        self.errors: list[str] = []
        self._session: PortalSession | None = None

    def session(self) -> PortalSession:
        if self._session is None:
            self._session = PortalSession(self.api.base)
            self._session.login()
        return self._session

    def change(self, what: str) -> bool:
        """Record a change. Returns False in a dry run, so the caller skips the write."""
        self.changes.append(what)
        return not self.dry_run

    def error(self, message: str) -> None:
        self.errors.append(message)


# ------------------------------------------------------------------------------- pages

def apply_pages(run: Run, wanted: dict) -> None:
    pages = {page["Path"]: page for page in run.api.list_all("/pages")}
    for path, spec in wanted.items():
        page = pages.get(path)
        if page is None:
            if not spec.get("create"):
                run.error(f"page {path}: not on the Portal (add \"create\": true and a template to create it)")
                continue
            body = {"Title": spec.get("title") or path.strip("/"), "Path": path, "Template": spec.get("template", "flip_flop"),
                    "Status": "Published", "AllowFormSubmission": False}
            if not run.change(f"page {path}: created from template {body['Template']}"):
                continue
            try:
                page = run.api.post("/pages", body)
            except PortalError as error:
                run.error(f"page {path}: {error.body.strip()[:200]}")
                continue
        page_id = page["ID"]
        title = spec.get("title")
        if title and page.get("Title") != title:
            if run.change(f"page {path}: title"):
                run.api.put(f"/pages/{page_id}", {**page, "Title": title})
                if run.api.get(f"/pages/{page_id}").get("Title") != title:
                    run.error(f"page {path}: title did not persist")
        elif title:
            run.unchanged += 1
        blocks = {block["Name"]: block for block in run.api.list_all(f"/pages/{page_id}/content-blocks")}
        for name, value in (spec.get("blocks") or {}).items():
            block = blocks.get(name)
            if block is None:
                if not run.change(f"page {path}: block {name} added"):
                    continue
                try:
                    block = run.api.post(f"/pages/{page_id}/content-blocks", {"Name": name, "Content": value})
                except PortalError as error:
                    run.error(f"page {path}: block {name}: {error.body.strip()[:200]}")
                continue
            current_ok = block.get("Content") == value and (not block.get("MarkdownEnabled") or block.get("MarkdownContent") == value)
            if current_ok:
                run.unchanged += 1
                continue
            if not run.change(f"page {path}: block {name}"):
                continue
            # A Markdown-enabled block renders MarkdownContent, so both fields carry the copy.
            update = {**block, "Content": value}
            if block.get("MarkdownEnabled"):
                update["MarkdownContent"] = value
            run.api.put(f"/pages/{page_id}/content-blocks/{block['ID']}", update)
            if run.api.get(f"/pages/{page_id}/content-blocks/{block['ID']}").get("Content") != value:
                run.error(f"page {path}: block {name} did not persist")
        missing = sorted(set(blocks) - set(spec.get("blocks") or {}))
        if missing and spec.get("blocks") is not None:
            run.changes.append(f"NOTE page {path}: blocks on the Portal that the file does not set: {', '.join(missing)}")


# ---------------------------------------------------------------------------- products

def by_name(items: list[dict]) -> dict[str, dict]:
    return {item["Name"]: item for item in items}


def upload_image(run: Run, product_id: int, kind: str, relative: str, label: str) -> None:
    path = (run.base / relative).resolve()
    if not path.is_file():
        run.error(f"{label}: {kind} file {relative} not found next to the content file")
        return
    if path.suffix.lower() not in PRODUCT_IMAGE_TYPES:
        run.error(f"{label}: {kind} must be PNG, JPG or GIF (the Portal refuses SVG here); got {path.name}")
        return
    if not run.change(f"{label}: {kind} image {path.name}"):
        return
    status, body = run.api.upload(f"/products/{product_id}/{kind}", path.name, path.read_bytes(), ok=())
    if status != 200:
        run.error(f"{label}: {kind} upload refused (HTTP {status}): {body.strip()[:200]}")


def apply_products(run: Run, wanted: dict) -> None:
    products = by_name(run.api.list_all("/products"))
    known_tags = {tag["Name"] for tag in run.api.list_all("/tags")}
    for name, spec in wanted.items():
        listed = products.get(name)
        if not listed:
            run.error(f"product {name!r}: not on the Portal (products are matched by Name; have: {', '.join(sorted(products)) or 'none'})")
            continue
        product = run.api.get(f"/products/{listed['ID']}")
        label = f"product {name!r}"
        fields = {"DisplayName": spec.get("display_name"), "Description": spec.get("description"),
                  "Content": spec.get("content"), "Feature": spec.get("featured")}
        update = {key: value for key, value in fields.items() if value is not None and product.get(key) != value}
        if update:
            if run.change(f"{label}: {', '.join(sorted(update))}"):
                run.api.put(f"/products/{product['ID']}", update)
                after = run.api.get(f"/products/{product['ID']}")
                for key, value in update.items():
                    if after.get(key) != value:
                        run.error(f"{label}: {key} did not persist")
        else:
            run.unchanged += 1
        if spec.get("tags") is not None:
            attached = {tag["Name"] for tag in run.api.list_all(f"/products/{product['ID']}/tags")}
            for tag in spec["tags"]:
                if not TAG_NAME.fullmatch(tag):
                    run.error(f"{label}: tag {tag!r}: tag names take ASCII letters, digits and - _ . / only (no spaces)")
                    continue
                if tag in attached:
                    run.unchanged += 1
                    continue
                if not run.change(f"{label}: tag {tag}"):
                    continue
                try:
                    if tag not in known_tags:
                        run.api.post("/tags", {"Name": tag})
                        known_tags.add(tag)
                    run.api.post(f"/products/{product['ID']}/tags", {"Name": tag})
                except PortalError as error:
                    run.error(f"{label}: tag {tag}: {error.body.strip()[:200]}")
        # The Portal renames each upload, so an image is sent only when the product has none
        # or when "replace_images" asks for it.
        for kind, current in (("logo", product.get("Logo")), ("preview", product.get("Preview"))):
            if spec.get(kind) and (not current or spec.get("replace_images")):
                upload_image(run, product["ID"], kind, spec[kind], label)
            elif spec.get(kind):
                run.unchanged += 1


def apply_named(run: Run, kind: str, endpoint: str, wanted: dict, mapping: dict) -> None:
    items = by_name(run.api.list_all(endpoint))
    for name, spec in wanted.items():
        item = items.get(name) or next((i for i in items.values() if spec.get("name") and i["Name"] == spec["name"]), None)
        if not item:
            run.error(f"{kind} {name!r}: not on the Portal (have: {', '.join(sorted(items)) or 'none'})")
            continue
        current = run.api.get(f"{endpoint}/{item['ID']}")
        update = {field: spec[key] for key, field in mapping.items() if spec.get(key) is not None and current.get(field) != spec[key]}
        if not update:
            run.unchanged += 1
            continue
        if run.change(f"{kind} {name!r}: {', '.join(sorted(update))}"):
            run.api.put(f"{endpoint}/{item['ID']}", update)
            after = run.api.get(f"{endpoint}/{item['ID']}")
            for field, value in update.items():
                if after.get(field) != value:
                    run.error(f"{kind} {name!r}: {field} did not persist")


# ------------------------------------------------------------------------------- menus

def menu_ids(session: PortalSession) -> dict[str, int]:
    """{menu Path: id}, read from the admin interface (menus have no API)."""
    _, listing, _ = session.get("/admin/menus?per_page=100")
    found: dict[str, int] = {}
    for menu_id in sorted({int(value) for value in re.findall(r"/admin/menus/(\d+)", listing)}):
        _, page, _ = session.get(f"/admin/menus/{menu_id}/edit")
        fields = dict(read_form(page, f"/admin/menus/{menu_id}"))
        if fields.get("QorResource.Path"):
            found[fields["QorResource.Path"]] = menu_id
    return found


def menu_items(pairs: list[tuple[str, str]]) -> list[dict]:
    rows: dict[int, dict] = {}
    for key, value in pairs:
        match = re.fullmatch(re.escape(MENU_FIELD) + r"\[(\d+)\]\.(ID|Title|Path)", key)
        if match:
            rows.setdefault(int(match.group(1)), {})[match.group(2)] = value
    return [{"index": index, **row} for index, row in sorted(rows.items())]


def apply_menus(run: Run, wanted: dict) -> None:
    session = run.session()
    ids = menu_ids(session)
    for menu_path, items in wanted.items():
        for item in items:
            if set(item) - {"title", "path"}:
                run.error(f"menu {menu_path}: only title and path are written by this script; set sub-menus in the admin interface")
        desired = [(item["title"], item["path"]) for item in items]
        if menu_path not in ids:
            if not run.change(f"menu {menu_path}: created with {len(desired)} items"):
                continue
            pairs = [("QorResource.Name", menu_path), ("QorResource.Path", menu_path)]
            for index, (title, path) in enumerate(desired):
                pairs += [(f"{MENU_FIELD}[{index}].ID", "0"), (f"{MENU_FIELD}[{index}].Title", title), (f"{MENU_FIELD}[{index}].Path", path)]
            session.post_multipart("/admin/menus", pairs)
            ids = menu_ids(session)
            if menu_path not in ids:
                run.error(f"menu {menu_path}: was not created")
                continue
        menu_id = ids[menu_path]
        _, page, _ = session.get(f"/admin/menus/{menu_id}/edit")
        pairs = read_form(page, f"/admin/menus/{menu_id}")
        existing = [row for row in menu_items(pairs) if row.get("ID") not in (None, "", "0")]
        if [(row.get("Title"), row.get("Path")) for row in existing] == desired:
            run.unchanged += 1
            continue
        if not run.change(f"menu {menu_path}: {len(desired)} items"):
            continue
        blank = next((row["index"] for row in menu_items(pairs) if row.get("ID") == "0"), len(existing))
        out = []
        for key, value in pairs:
            match = re.fullmatch(re.escape(MENU_FIELD) + r"\[(\d+)\]\.(Title|Path)", key)
            if match:
                index, field = int(match.group(1)), match.group(2)
                position = next((n for n, row in enumerate(existing) if row["index"] == index), None)
                if position is not None and position < len(desired):
                    value = desired[position][0 if field == "Title" else 1]
                elif position is None and index == blank and len(desired) > len(existing):
                    value = desired[len(existing)][0 if field == "Title" else 1]
            out.append((key, value))
        for position, row in enumerate(existing):
            if position >= len(desired):
                out.append((f"{MENU_FIELD}[{row['index']}]._destroy", "1"))
        # The form carries one blank row; further new items are added as extra rows.
        for extra, (title, path) in enumerate(desired[len(existing) + 1:], start=1):
            index = blank + extra
            out += [(f"{MENU_FIELD}[{index}].ID", "0"), (f"{MENU_FIELD}[{index}].Title", title), (f"{MENU_FIELD}[{index}].Path", path)]
        session.post_multipart(f"/admin/menus/{menu_id}", out)
        _, page, _ = session.get(f"/admin/menus/{menu_id}/edit")
        after = [(row.get("Title"), row.get("Path")) for row in menu_items(read_form(page, f"/admin/menus/{menu_id}")) if row.get("ID") not in (None, "", "0")]
        if after != desired:
            run.error(f"menu {menu_path}: read back {after}, wanted {desired}")


# ------------------------------------------------------------------------------- email

def apply_email(run: Run, wanted: dict) -> None:
    session = run.session()
    _, page, _ = session.get("/admin/general")
    pairs = read_form(page, "/admin/general")
    if not pairs:
        run.error("email: the General settings form was not readable (is the account a Portal admin?)")
        return
    target: dict[str, str] = {}
    for key, field in EMAIL_FIELDS.items():
        if wanted.get(key) is not None:
            target[f"QorResource.{field}"] = str(wanted[key])
    for key, value in (wanted.get("subjects") or {}).items():
        if key not in SUBJECT_FIELDS:
            run.error(f"email: unknown subject {key!r} (known: {', '.join(sorted(SUBJECT_FIELDS))})")
            continue
        target[f"QorResource.{SUBJECT_FIELDS[key]}"] = value
    password = os.environ.get("PORTAL_SMTP_PASSWORD")
    current = {}
    for key, value in pairs:
        current.setdefault(key, value)
    changed = sorted(key for key, value in target.items() if current.get(key) != value)
    robots_blank = not current.get("QorResource.RobotsTxtContent", "").strip()
    if not changed and not password:
        run.unchanged += 1
        return
    if not run.change("email: " + ", ".join(key.split(".", 1)[1] for key in changed) + (" and the SMTP password" if password else "")):
        return
    out = []
    for key, value in pairs:
        if key in target:
            value = target[key]
        elif key == "QorResource.EmailSMTPPass" and password:
            value = password
        elif key == "QorResource.RobotsTxtContent":
            # Saving this form with the box empty turns /robots.txt into an empty file, so the
            # default (block all crawlers) is written out when nothing else was set.
            value = DEFAULT_ROBOTS if robots_blank else value.strip() + "\n"
        out.append((key, value))
    session.post_multipart("/admin/general", out)
    _, page, _ = session.get("/admin/general")
    after_pairs = read_form(page, "/admin/general")
    after: dict[str, str] = {}
    for key, value in after_pairs:
        after.setdefault(key, value)
    failed = [key for key, value in target.items() if after.get(key) != value]
    for key in failed:
        run.error(f"email: {key.split('.', 1)[1]} did not persist")
    smtp_connection = {f"QorResource.{EMAIL_FIELDS[k]}" for k in ("smtp_host", "smtp_port", "smtp_user")}
    if failed and not password and smtp_connection & set(changed):
        # With an SMTP password stored, the form refuses a new host, port or user unless the
        # password is entered again ("Re-enter the SMTP password ...").
        run.error("email: a change of SMTP host, port or user needs the SMTP password again: "
                  "set PORTAL_SMTP_PASSWORD and run again")
    for key, value in current.items():
        if key in target or key in ("QorResource.RobotsTxtContent", "QorResource.EmailSMTPPass"):
            continue
        if after.get(key) != value:
            run.error(f"General settings: {key.split('.', 1)[1]} changed from {value!r} to {after.get(key)!r}; restore it in the admin interface")
    if robots_blank:
        run.changes.append("NOTE robots.txt: the default rules (block all crawlers) were written into General settings, "
                           "because saving that form with an empty box serves an empty robots.txt")


# ------------------------------------------------------------------------------ export

def export(api: PortalAPI) -> dict:
    out: dict = {"pages": {}, "products": {}, "plans": {}, "catalogues": {}}
    for page in api.list_all("/pages"):
        blocks = api.list_all(f"/pages/{page['ID']}/content-blocks")
        out["pages"][page["Path"]] = {"title": page.get("Title"), "template": page.get("Template"), "status": page.get("Status"),
                                      "blocks": {block["Name"]: block.get("Content") for block in sorted(blocks, key=lambda b: b["ID"])}}
    for listed in api.list_all("/products"):
        product = api.get(f"/products/{listed['ID']}")
        out["products"][product["Name"]] = {
            "display_name": product.get("DisplayName"), "description": product.get("Description"), "content": product.get("Content"),
            "featured": product.get("Feature"), "tags": product.get("Tags") or [], "path": product.get("Path"),
            "has_logo": bool(product.get("Logo")), "has_preview": bool(product.get("Preview"))}
    for listed in api.list_all("/plans"):
        plan = api.get(f"/plans/{listed['ID']}")
        out["plans"][plan["Name"]] = {"display_name": plan.get("DisplayName"), "description": plan.get("Description")}
    for catalogue in api.list_all("/catalogues"):
        out["catalogues"][catalogue["Name"]] = {"visibility": catalogue.get("VisibilityStatus")}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--content", type=Path)
    parser.add_argument("--export", type=Path, help="write what the Portal holds now to this file and exit")
    parser.add_argument("--only", help="comma-separated sections to apply (" + ", ".join(SECTIONS) + ")")
    parser.add_argument("--dry-run", action="store_true", help="list what would change and write nothing")
    args = parser.parse_args(argv)
    api = PortalAPI()
    if args.export:
        args.export.write_text(json.dumps(export(api), indent=2, ensure_ascii=False) + "\n")
        print(f"exported to {args.export}")
        return 0
    if not args.content:
        parser.error("--content or --export is required")
    content = json.loads(args.content.read_text())
    unknown = set(content) - set(SECTIONS) - {"$comment"}
    if unknown:
        raise SystemExit(f"content file has unknown sections: {', '.join(sorted(unknown))}")
    only = set(args.only.split(",")) if args.only else set(SECTIONS)
    run = Run(api, args.content.parent, args.dry_run)
    try:
        if "pages" in only and content.get("pages"):
            apply_pages(run, content["pages"])
        if "products" in only and content.get("products"):
            apply_products(run, content["products"])
        if "plans" in only and content.get("plans"):
            apply_named(run, "plan", "/plans", content["plans"], {"display_name": "DisplayName", "description": "Description"})
        if "catalogues" in only and content.get("catalogues"):
            apply_named(run, "catalogue", "/catalogues", content["catalogues"], {"name": "Name"})
        if "menus" in only and content.get("menus"):
            apply_menus(run, content["menus"])
        if "email" in only and content.get("email"):
            apply_email(run, content["email"])
    except PortalError as error:
        run.error(str(error))
    for change in run.changes:
        print(("would change: " if args.dry_run and not change.startswith("NOTE") else "") + change)
    for error in run.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"{len([c for c in run.changes if not c.startswith('NOTE')])} change(s), {run.unchanged} already as wanted, {len(run.errors)} error(s)")
    return 1 if run.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
