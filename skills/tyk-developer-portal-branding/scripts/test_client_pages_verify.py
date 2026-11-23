"""Tests for portal_client.py, pages.py, stock.py, verify.py and apply_content.py.

Standard library only, no network: the admin API is replaced by a small in-memory fake.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))

import apply_content  # noqa: E402
import apply_theme  # noqa: E402
import screenshots  # noqa: E402
import pages  # noqa: E402
import portal_client  # noqa: E402
import stock  # noqa: E402
import verify  # noqa: E402

GENERAL_FORM = """
<form action="/admin/general" method="POST">
  <input type="hidden" name="_method" value="PUT">
  <input type="text" name="QorResource.EmailDefaultFrom" value="">
  <input type="checkbox" name="QorResource.RegistrationAllow" value="true" value="false" checked>
  <input type="checkbox" name="QorResource.Unticked" value="true">
  <select name="QorResource.PortalState"><option value="public">Public</option><option value="private" selected>Private</option></select>
  <textarea name="QorResource.RobotsTxtContent">
User-agent: *
Disallow: /</textarea>
  <input type="file" name="QorResource.Logo">
  <input type="submit" name="commit" value="Save">
</form>
<form action="/admin/other"><input name="ignored" value="x"></form>
"""


class FakeAPI:
    """Answers GET paths from a dict; pages lists by per_page/page like the Portal does."""

    def __init__(self, data: dict):
        self.data = data
        self.base = "http://portal.test"
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str):
        self.calls.append(("GET", path))
        route, _, query = path.partition("?")
        value = self.data[route]
        if isinstance(value, list):
            params = dict(part.split("=") for part in query.split("&") if part)
            size, page = int(params.get("per_page", 20)), int(params.get("page", 1))
            return value[(page - 1) * size: page * size]
        return value

    list_all = portal_client.PortalAPI.list_all

    def put(self, path, payload):
        self.calls.append(("PUT", path))


class FormAndPagingTests(unittest.TestCase):
    def test_read_form_matches_what_a_browser_sends(self):
        pairs = portal_client.read_form(GENERAL_FORM, "/admin/general")
        self.assertEqual(pairs, [
            ("_method", "PUT"),
            ("QorResource.EmailDefaultFrom", ""),
            ("QorResource.RegistrationAllow", "true"),
            ("QorResource.PortalState", "private"),
            ("QorResource.RobotsTxtContent", "User-agent: *\nDisallow: /"),
        ])

    def test_multipart_fields(self):
        body, header = portal_client.multipart_fields([("a", "1"), ("b", "two words")])
        boundary = header.split("boundary=")[1]
        self.assertIn(f"--{boundary}\r\nContent-Disposition: form-data; name=\"b\"\r\n\r\ntwo words\r\n".encode(), body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))

    def test_list_all_reads_every_page(self):
        api = FakeAPI({"/pages": [{"ID": n} for n in range(1, 251)]})
        self.assertEqual(len(api.list_all("/pages")), 250)
        self.assertEqual([call[1] for call in api.calls],
                         ["/pages?per_page=100&page=1", "/pages?per_page=100&page=2", "/pages?per_page=100&page=3"])


class PagesTests(unittest.TestCase):
    def fake(self) -> FakeAPI:
        return FakeAPI({
            "/pages": [{"ID": 1, "Path": "/", "Status": "Published"},
                       {"ID": 2, "Path": "/about-us", "Status": "Published"},
                       {"ID": 3, "Path": "/draft", "Status": "Draft"},
                       {"ID": 4, "Path": "/portal/catalogue-products", "Status": "Published"}],
            "/products": [{"ID": 1}, {"ID": 2}],
            "/products/1": {"Path": "orders", "APIDetails": [{"APIID": "abc", "APIType": "REST"}], "SpecDetails": None},
            "/products/2": {"Path": "events", "APIDetails": None,
                            "SpecDetails": [{"SpecAlias": "events"}, {"SpecAlias": "graph", "GraphQLSDL": "/system/x.graphql"}]},
        })

    def test_discover_covers_every_layout(self):
        found = {page["name"]: page for page in pages.discover(self.fake())}
        for name in ("home", "catalogue", "sign-in", "sign-up", "forgot-password", "reset-password", "not-found",
                     "blog", "page-about-us", "product-orders", "docs-rest-abc", "docs-spec-events",
                     "docs-graphql-graph", "dashboard", "apps", "profile", "forbidden"):
            self.assertIn(name, found)
        self.assertNotIn("page-draft", found)
        self.assertEqual(found["forbidden"]["expect"], 403)
        self.assertTrue(found["dashboard"]["private"])
        self.assertEqual(found["docs-spec-events"]["path"], "/portal/catalogue-products/events/events/docs")

    def test_discover_without_api_or_blog(self):
        names = [page["name"] for page in pages.discover(None, blog=False)]
        self.assertNotIn("blog", names)
        self.assertIn("home", names)


STOCK_PAGE = """<html><head><title> Developer Portal </title><link href="/assets/stylesheets/main.css" rel="stylesheet">
<script>var x = "Tyk";</script></head><body>
<a class="navbar-brand" href="/">DEVELOPER PORTAL</a><img src="/assets/images/dev-portal-logo.svg">
<h4>Powered by © Tyk Technologies</h4></body></html>"""
BRANDED_PAGE = """<html><head><title>Catalogue - Example Corp Developers</title>
<link href="/assets/stylesheets/brand.css" rel="stylesheet"><link rel="icon" href="/assets/images/brand/favicon.svg">
<script>var tyk = "Tyk";</script></head><body><a class="navbar-brand" href="/"><img src="/assets/images/brand/logo.svg"></a>
<p>© 2026 Example Corp</p></body></html>"""


class VerifyTests(unittest.TestCase):
    page = {"name": "catalogue", "path": "/portal/catalogue-products", "private": False}

    def test_visible_text_skips_scripts(self):
        self.assertNotIn("Tyk", verify.visible_text(BRANDED_PAGE))
        self.assertIn("Example Corp", verify.visible_text(BRANDED_PAGE))

    def test_stock_page_is_reported(self):
        found = verify.page_findings(self.page, 200, STOCK_PAGE, self.page["path"], "Example Corp Developers")
        joined = "\n".join(found)
        for expected in ("stock wording: DEVELOPER PORTAL", "stock wording: Tyk", "stock image: dev-portal-logo.svg",
                         "no favicon link", "brand stylesheet not linked", "does not name the portal"):
            self.assertIn(expected, joined)

    def test_branded_page_is_clean(self):
        self.assertEqual(verify.page_findings(self.page, 200, BRANDED_PAGE, self.page["path"], "Example Corp Developers"), [])

    def test_status_and_sign_in_redirect(self):
        forbidden = {"name": "forbidden", "path": "/portal/private/users/invite", "private": True, "expect": 403}
        self.assertEqual(verify.page_findings(forbidden, 403, BRANDED_PAGE, forbidden["path"], ""), [])
        self.assertIn("HTTP 200, expected 403", verify.page_findings(forbidden, 200, BRANDED_PAGE, forbidden["path"], ""))
        private = {"name": "profile", "path": "/portal/private/profile", "private": True}
        self.assertEqual(verify.page_findings(private, 200, BRANDED_PAGE, "/auth/password/login", ""),
                         ["sent to sign-in: the signed-in page was not checked"])

    def test_stock_helpers(self):
        self.assertEqual(stock.phrases_in("Welcome to Tyk Developer Portal"), ["Welcome to Tyk Developer Portal"])
        self.assertEqual(stock.phrases_in("Example Corp"), [])
        self.assertEqual(stock.images_in('<img src="/assets/images/new-logo.svg">'), ["new-logo.svg"])
        self.assertEqual(stock.tracking_in("<script>GTM-ABC1234</script>"), ["Google Tag Manager"])


class ContentTests(unittest.TestCase):
    def test_menu_items_are_read_in_order(self):
        field = apply_content.MENU_FIELD
        pairs = [(f"{field}[1].ID", "7"), (f"{field}[1].Title", "About"), (f"{field}[1].Path", "/about-us"),
                 (f"{field}[0].ID", "6"), (f"{field}[0].Title", "APIs"), (f"{field}[0].Path", "/portal/catalogue-products"),
                 ("QorResource.Name", "Primary")]
        rows = apply_content.menu_items(pairs)
        self.assertEqual([(row["Title"], row["Path"]) for row in rows], [("APIs", "/portal/catalogue-products"), ("About", "/about-us")])

    def test_dry_run_changes_nothing(self):
        api = FakeAPI({
            "/pages": [{"ID": 1, "Path": "/", "Title": "Build the future with Tyk APIs"}],
            "/pages/1/content-blocks": [{"ID": 10, "Name": "HeaderDescription", "Content": "stock"}],
        })
        run = apply_content.Run(api, Path("."), dry_run=True)
        apply_content.apply_pages(run, {"/": {"title": "Build with Example Corp APIs", "blocks": {"HeaderDescription": "new"}}})
        self.assertEqual([call for call in api.calls if call[0] != "GET"], [])
        self.assertEqual(run.changes, ["page /: title", "page /: block HeaderDescription"])
        self.assertEqual(run.errors, [])

    def test_already_applied_counts_as_unchanged(self):
        api = FakeAPI({
            "/pages": [{"ID": 1, "Path": "/", "Title": "Build with Example Corp APIs"}],
            "/pages/1/content-blocks": [{"ID": 10, "Name": "HeaderDescription", "Content": "new"}],
        })
        run = apply_content.Run(api, Path("."), dry_run=False)
        apply_content.apply_pages(run, {"/": {"title": "Build with Example Corp APIs", "blocks": {"HeaderDescription": "new"}}})
        self.assertEqual(run.changes, [])
        self.assertEqual(run.unchanged, 2)

    def test_example_content_file_is_valid(self):
        import json
        content = json.loads((Path(__file__).resolve().parent.parent / "assets" / "content.example.json").read_text())
        self.assertEqual(set(content) - set(apply_content.SECTIONS) - {"$comment"}, set())
        self.assertEqual(set(content["email"]["subjects"]), set(apply_content.SUBJECT_FIELDS))


class ApplyThemeTests(unittest.TestCase):
    def test_upload_refuses_a_bad_archive_before_any_request(self):
        import tempfile
        api = FakeAPI({})
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "bad.zip"
            archive.write_text("not a zip")
            with self.assertRaises(SystemExit) as raised:
                apply_theme.upload(api, archive)
        self.assertIn("not a zip archive", str(raised.exception))
        self.assertEqual(api.calls, [])

    def test_find_uses_the_list(self):
        api = FakeAPI({"/themes": [{"Name": "default", "Status": "Not in use"}, {"Name": "example-corp", "Status": "Current"}]})
        self.assertEqual(apply_theme.find(api, "example-corp")["Status"], "Current")
        self.assertIsNone(apply_theme.find(api, "nope"))


class ScreenshotFindingsTests(unittest.TestCase):
    def test_findings(self):
        page = {"status": 200, "expect": 200, "stock_phrases": ["Tyk"], "stock_images": [], "broken_images": [],
                "failed_requests": [], "favicon": None,
                "widths": {"390": {"overflow_px": 12, "low_contrast": [
                    {"ratio": 2.1, "minimum": 4.5, "colour": "#FFFFFF", "background": "#F59E0B", "element": "span",
                     "text": "Grid", "viewer": False},
                    {"ratio": 2.2, "minimum": 4.5, "colour": "#05B870", "background": "#EBEEF5", "element": "a",
                     "text": "toc", "viewer": True}]}}}
        found = screenshots.findings(page)
        self.assertIn("stock wording: Tyk", found)
        self.assertIn("no favicon link", found)
        self.assertIn("content overflows sideways by 12px at 390px", found)
        self.assertEqual(len([item for item in found if item.startswith("low contrast")]), 1)
        self.assertEqual(screenshots.findings({"redirected_to_sign_in": True}), ["redirected to sign-in: not checked"])


if __name__ == "__main__":
    unittest.main()
