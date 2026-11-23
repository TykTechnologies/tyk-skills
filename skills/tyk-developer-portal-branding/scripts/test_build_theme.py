"""Tests for build_theme.py (standard library only, no network).

The fixture is a small theme that carries the same stock snippets as the default theme of
Portal 1.20.0 (navigation text, footer logo and line, sign-in logo, photo and wording, home
images and partner strip, page titles, tag-manager block, mail wording), so every replacement
the build makes is exercised without the 12 MB original.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parent))

import build_theme  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"

STOCK = {
    "theme.json": json.dumps({"name": "default", "version": "0.0.1", "author": "Vendor",
                              "templates": [{"name": "Home", "template": "home", "layout": "portal_layout"}]}),
    "layouts/portal_layout.tmpl": (
        '<html lang="en">\n<head>\n    <title>{{ if $pageTitle}} {{ $pageTitle}} {{else}} {{ if .page}} {{.page.Title}} '
        '{{else}} Developer Portal {{end}} {{end}}</title>\n'
        '    <link href="/assets/stylesheets/main.css{{.previewId}}" rel="stylesheet">\n'
        "    {{ if IsFullstoryEnabled }}\n    <!-- Google Tag Manager -->\n    <script>GTM-ABCDEFG googletagmanager</script>\n"
        "    <!-- End Google Tag Manager -->\n    {{ end }}\n</head>\n<body>{{ yield }}</body>\n</html>\n"),
    "layouts/product_layout_doc_stoplight.tmpl": (
        '<html lang="en">\n<head>\n    <title>{{.product.Name}}: {{.api.Name}} Documentation</title>\n'
        '    <script src="https://unpkg.com/@stoplight/elements/web-components.min.js"></script>\n'
        '    <link href="/assets/stylesheets/main.css" rel="stylesheet">\n</head>\n<body></body>\n</html>\n'),
    "views/home.tmpl": (
        '<div class="col-sm-6 image-container" style="background: url(\'/assets/images/tyk-portal-silhouette.png\') no-repeat left center;"></div>\n'
        '<div class="col-sm-6 text-right"><img src="/assets/images/request-access.png" alt=""></div>\n'
        '<div class="col-sm-6 image-container" style="background: url(\'/assets/images/about-us.png\') no-repeat right center;"></div>\n'
        "  <!-- Partners -->\n  <img src=\"/assets/images/partners/ncs.svg\">\n  <!-- end of Partners -->\n"),
    "views/flip_flop.tmpl": (
        '<div style="background: url(\'/assets/images/tyk-portal-silhouette.png\') no-repeat left center;"></div>\n'
        '<div style="background: url(\'/assets/images/about-us.png\') no-repeat right center;"></div>\n'),
    "views/blog_listing.tmpl": '<div style="background: url(\'/assets/images/blog.svg\') no-repeat;"></div>\n',
    "views/not_found.tmpl": '<img src="/assets/images/not-found.svg" alt="">\n',
    "partials/top_nav.tmpl": '<nav class="navbar">\n    <a class="navbar-brand" href="/">DEVELOPER PORTAL</a>\n</nav>\n',
    "partials/footer.tmpl": ('<footer>\n<img src="/assets/images/dev-portal-logo.svg">\n'
                             '<h4 class="mt-3">Powered by © Tyk Technologies</h4>\n<!-- Load Tyk Javascript assets -->\n</footer>\n'),
    "partials/catalogue_card.tmpl": '<img src="/assets/images/card-placeholder.svg">\n',
    "auth/password/login.tmpl": ('<div class="login-page-wrapper" style="background: linear-gradient(#36364C, #505071)">\n'
                                 '<img src="/assets/images/new-logo.svg" alt="Tyk Logo" />\n'
                                 "<p>Log in to your Tyk Developer Portal account</p>\n</div>\n"),
    "assets/stylesheets/main.css": ":root { --tdp-primary: #335FFD; }\n",
    "assets/stylesheets/fonts.css": ("@font-face { font-family: 'Inter-Regular'; src: url('/assets/fonts/Inter-Regular.woff2'); }\n"
                                     "@font-face { font-family: 'tykon'; src: url('/assets/fonts/tykon.woff'); }\n"),
    "assets/stylesheets/pages/login.css": ".login-page-wrapper {\n  background: url('/assets/images/login-background.jpg') no-repeat center center fixed;\n}\n",
    "assets/images/dev-portal-logo.svg": "<svg/>",
    "assets/images/new-logo.svg": "<svg/>",
    "assets/images/partners/ncs.svg": "<svg/>",
    "assets/images/login-background.jpg": "jpg",
    "assets/images/use-cases/consumers.svg": '<svg><path fill="#00D9BA"/></svg>',
    "mailers/layouts/emails.html.tmpl": "<html>{{ yield }}</html>",
    "mailers/auth/reset_password.html.tmpl": "<p>Reset</p>",
    "mailers/auth/reset_password.text.tmpl": "<p>Reset at {{reset_password_url}}</p>\nHi, <strong>Sam</strong><br/>\n",
    "mailers/welcome.html.tmpl": ("<p>Welcome to the Developer portal. Log in at https://portal.com/admin.</p>\n"
                                  "<p>If you did not perform this action, please contact the customer support team at customer@support.com.</p>\n"
                                  "<p>The Team</p>\n"),
}


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def zip_tree(root: Path, target: Path, prefix: str = "") -> Path:
    with zipfile.ZipFile(target, "w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, prefix + path.relative_to(root).as_posix())
    return target


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = write_tree(self.root / "default", STOCK)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, kit: str = "brand-kit-neutral") -> tuple[dict, Path]:
        output = self.root / "out.zip"
        result = build_theme.build(self.source, ASSETS / kit, output, allow_missing=True)
        return result, output

    def read(self, output: Path) -> dict[str, str]:
        with zipfile.ZipFile(output) as archive:
            return {name: archive.read(name).decode(errors="replace") for name in archive.namelist()}

    def test_build_replaces_every_stock_touchpoint(self):
        result, output = self.build()
        files = self.read(output)
        self.assertEqual(result["theme"], "example-corp")
        self.assertEqual(json.loads(files["theme.json"])["name"], "example-corp")
        self.assertIn("brand-logo", files["partials/top_nav.tmpl"])
        self.assertNotIn("DEVELOPER PORTAL", files["partials/top_nav.tmpl"])
        self.assertIn("© 2026 Example Corp", files["partials/footer.tmpl"])
        self.assertIn("Log in to your Example Corp Developers account", files["auth/password/login.tmpl"])
        self.assertNotIn("style=\"background", files["auth/password/login.tmpl"])
        self.assertIn("/assets/stylesheets/brand.css{{.previewId}}", files["layouts/portal_layout.tmpl"])
        self.assertIn("/assets/stylesheets/brand.css\"", files["layouts/product_layout_doc_stoplight.tmpl"])
        self.assertIn("@stoplight/elements@9.0.29/", files["layouts/product_layout_doc_stoplight.tmpl"])
        self.assertEqual(result["replaced"]["API reference renderers pinned"], 1)
        self.assertIn('rel="icon"', files["layouts/portal_layout.tmpl"])
        self.assertIn("Example Corp Developers</title>", files["layouts/portal_layout.tmpl"])
        self.assertNotIn("googletagmanager", files["layouts/portal_layout.tmpl"])
        self.assertNotIn("Partners", files["views/home.tmpl"])
        self.assertIn("/assets/images/brand/hero.svg", files["views/home.tmpl"])
        self.assertIn("--tdp-primary: #0F766E;", files["assets/stylesheets/brand.css"])
        self.assertNotIn("login-background.jpg", files["assets/stylesheets/pages/login.css"])
        self.assertNotIn("#00D9BA", files["assets/images/use-cases/consumers.svg"])
        for name in ("assets/images/dev-portal-logo.svg", "assets/images/new-logo.svg",
                     "assets/images/partners/ncs.svg", "assets/images/login-background.jpg"):
            self.assertNotIn(name, files)
        self.assertEqual(build_theme.check_archive(output), [])

    def test_mail_wording_and_frame(self):
        _, output = self.build()
        files = self.read(output)
        welcome = files["mailers/welcome.html.tmpl"]
        self.assertIn("Welcome to Example Corp Developers", welcome)
        self.assertIn("https://developers.example.com/admin", welcome)
        self.assertIn("developers@example.com", welcome)
        self.assertIn("The Example Corp team", welcome)
        self.assertIn("{{ yield }}", files["mailers/layouts/emails.html.tmpl"])
        self.assertIn('role="presentation"', files["mailers/auth/reset_password.html.tmpl"])
        self.assertEqual(files["mailers/auth/reset_password.text.tmpl"], "Reset at {{reset_password_url}}\nHi, Sam\n\n")

    def test_light_navigation_kit(self):
        result, output = self.build("brand-kit-neutral-light")
        css = self.read(output)["assets/stylesheets/brand.css"]
        self.assertEqual(result["theme"], "example-labs")
        self.assertIn("background: #FFFFFF !important", css)
        self.assertIn("filter: invert(1)", css)
        self.assertIn("brand-logo--plate", self.read(output)["partials/footer.tmpl"])

    def test_missing_touchpoint_fails_without_allow_missing(self):
        (self.source / "partials/top_nav.tmpl").write_text("<nav></nav>\n")
        with self.assertRaises(build_theme.BuildError) as raised:
            build_theme.build(self.source, ASSETS / "brand-kit-neutral", self.root / "out.zip")
        self.assertIn("navigation bar logo", str(raised.exception))

    def test_leftover_stock_branding_blocks_the_build(self):
        (self.source / "views/extra.tmpl").write_text("<p>Powered by Tyk</p>\n")
        with self.assertRaises(build_theme.BuildError) as raised:
            self.build()
        self.assertIn("views/extra.tmpl", str(raised.exception))

    def test_source_zip_with_duplicate_entries_and_wrapped_folder(self):
        archive = zip_tree(self.source, self.root / "wrapped.zip", prefix="default/")
        result = build_theme.build(archive, ASSETS / "brand-kit-neutral", self.root / "out.zip", allow_missing=True)
        self.assertEqual(result["theme"], "example-corp")

    def test_source_two_folders_down(self):
        archive = zip_tree(self.source, self.root / "release.zip", prefix="portal-default-theme-1.20.0/src/")
        result = build_theme.build(archive, ASSETS / "brand-kit-neutral", self.root / "out.zip", allow_missing=True)
        self.assertEqual(result["theme"], "example-corp")

    def test_build_is_repeatable(self):
        _, first = self.build()
        data = first.read_bytes()
        _, second = self.build()
        self.assertEqual(data, second.read_bytes())


class CheckArchiveTests(unittest.TestCase):
    """Each archive the Portal 1.20.0 or 1.20.1 refused or loaded broken, caught before upload."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def archive(self, files: dict[str, str | bytes], name: str = "t.zip") -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, content in files.items():
                archive.writestr(entry, content)
        return path

    def theme(self, **manifest) -> dict[str, str]:
        base = {"name": "example-corp", "version": "1.0.0",
                "templates": [{"name": "Home", "template": "home", "layout": "portal_layout"}]}
        base.update(manifest)
        return {"theme.json": json.dumps(base), "layouts/portal_layout.tmpl": "x", "views/home.tmpl": "x",
                "partials/p.tmpl": "x", "auth/a.tmpl": "x", "mailers/m.tmpl": "x"}

    def test_valid_archive(self):
        self.assertEqual(build_theme.check_archive(self.archive(self.theme())), [])

    def test_problems(self):
        cases = {
            "not a zip archive": None,
            "not at the root": {f"mytheme/{k}": v for k, v in self.theme().items()},
            "a second theme.json": {**self.theme(), "extra/theme.json": "{}"},
            "escapes the theme folder": {**self.theme(), "../evil.txt": "x"},
            "name must be 1 to 50": self.theme(name="x" * 51),
            "use a-z, 0-9 and -": self.theme(name="Example Corp Theme"),
            "version is empty": self.theme(version=""),
            "folder mailers/ is missing": {k: v for k, v in self.theme().items() if not k.startswith("mailers/")},
            "has no views/missing.tmpl": self.theme(templates=[{"name": "M", "template": "missing", "layout": "portal_layout"}]),
            "operating-system residue": {**self.theme(), "__MACOSX/._theme.json": "x"},
        }
        for message, files in cases.items():
            with self.subTest(message):
                if files is None:
                    path = self.root / "notzip.zip"
                    path.write_text("plain text")
                else:
                    path = self.archive(files, f"{abs(hash(message))}.zip")
                problems = build_theme.check_archive(path)
                self.assertTrue(any(message in problem for problem in problems), problems)

    def test_file_over_five_megabytes(self):
        files = {**self.theme(), "assets/big.bin": b"0" * (build_theme.MAX_FILE_BYTES + 1)}
        problems = build_theme.check_archive(self.archive(files))
        self.assertTrue(any("big.bin" in problem for problem in problems), problems)

    def test_removed_template_functions(self):
        for call in ('{{ env "HOME" }}', '{{ expandenv "$HOME" }}', '{{- getHostByName "example.com" -}}',
                     '{{ if true }}{{ env "X" | upper }}{{ end }}'):
            with self.subTest(call):
                files = {**self.theme(), "views/home.tmpl": f"<p>{call}</p>"}
                problems = build_theme.check_archive(self.archive(files, f"{abs(hash(call))}.zip"))
                self.assertTrue(any("views/home.tmpl calls" in problem for problem in problems), problems)

    def test_names_that_only_look_like_removed_functions(self):
        for text in ("{{ .Env }}", "{{ $env := 1 }}{{ $env }}", "{{ environment }}", "env outside an action",
                     '{{ .Site.env }}'):
            with self.subTest(text):
                files = {**self.theme(), "views/home.tmpl": text}
                self.assertEqual(build_theme.check_archive(self.archive(files, f"{abs(hash(text))}.zip")), [])

    def test_cli_check(self):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            self.assertEqual(build_theme.main(["--check", str(self.archive(self.theme()))]), 0)
        self.assertIn("safe to upload", out.getvalue())


if __name__ == "__main__":
    unittest.main()
