"""Tests for Portal branding (always on with the Portal), Portal list pagination, private
.runtime/ output, and Tyk Sync 2.x delivery with its SYNC-001 and CICD-001 proofs.

Every generator run uses `--versions pinned`, so the suite needs no network. Portal and
Tyk Sync are stubbed: a local HTTP server stands in for the Portal and a script for the
tyk-sync binary, each reproducing the 2.x and 1.19.0 behaviour the scripts depend on.
"""

from __future__ import annotations

import http.server
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile


SCRIPTS = Path(__file__).resolve().parent
FORBIDDEN_WORDS = re.compile(r"\b(demo|test|mock|sample|lab|evidence|proof)\b", re.IGNORECASE)
HAS_JQ = shutil.which("jq") is not None


def load_script(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_script("init_demo_bundle", "branding_init_demo_bundle")
validator = load_script("validate_bundle", "branding_validate_bundle")
sanitizer = load_script("sanitize_bundle", "branding_sanitize_bundle")


def generate(root: Path, slug: str, name: str, modules: str, *extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root), "--slug", slug,
         "--name", name, "--modules", modules, "--versions", "pinned", *extra],
        check=check, capture_output=True, text=True,
    )


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def private_violations(root: Path) -> list[str]:
    return validator.validate_runtime_permissions(root)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>\n'


def own_kit(directory: Path, **overrides: object) -> Path:
    """A non-placeholder brand kit with one PNG and three SVGs, plus a stray file."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("logo.svg", "logo-dark.svg", "hero.svg"):
        (directory / name).write_text(SVG)
    (directory / "favicon.png").write_bytes(PNG)
    (directory / "notes.txt").write_text("not part of the kit\n")
    brand = {
        "name": "Northwind", "logo": "logo.svg", "logo_on_dark": "logo-dark.svg", "logo_alt": "Northwind",
        "nav_suffix": "Developers", "favicon": "favicon.png", "hero": "hero.svg", "footer_text": "© Northwind",
        "colors": {"primary": "#0a7d4f", "primary_dark": "#075c3a", "on_primary": "#ffffff", "link": "#075c3a",
                   "nav_bg": "#102a20", "hero_bg": "#102a20"},
        "font_family": "Inter, sans-serif",
        "sources": {"logo": "supplied by the organisation"},
    }
    brand.update(overrides)
    (directory / "brand.json").write_text(json.dumps(brand))
    (directory / "content.json").write_text(json.dumps({"pages": {"/": {"title": "Northwind APIs", "blocks": {"HeaderDescription": "Hello"}}}}))
    return directory


# A cut-down Portal 1.19.0 default theme carrying every stock touchpoint build-theme.py replaces.
THEME_FILES = {
    "theme.json": json.dumps({"name": "default", "version": "1.19.0", "author": "Tyk"}),
    "layouts/portal_layout.tmpl": "<html><head>\n<title>Developer Portal</title>\n</head><body>{{ template \"content\" . }}</body></html>\n",
    "layouts/product_layout_doc_stoplight.tmpl": "<html><head>\n</head><body></body></html>\n",
    "layouts/auth/login.tmpl": "<div class=\"login-page-wrapper\" style=\"background: url('/assets/images/login.svg') center center no-repeat\"></div>\n",
    "auth/password/login.tmpl": "<img src=\"/assets/images/new-logo.svg\" alt=\"Tyk Logo\" />\n<label>Log in to your Tyk Developer Portal account</label>\n",
    "partials/top_nav.tmpl": "<nav>\n    <a class=\"navbar-brand\" href=\"/\">DEVELOPER PORTAL</a>\n</nav>\n",
    "partials/footer.tmpl": "<footer>\n<img src=\"/assets/images/dev-portal-logo.svg\">\n<h4 class=\"mt-3\">Powered by © Tyk Technologies</h4>\n</footer>\n",
    "partials/catalogue_card.tmpl": "<img class=\"card-img-placeholder\" src=\"/assets/images/card-placeholder.svg\" alt=\"\">\n",
    "views/home.tmpl": (
        "<div class=\"image-container\" style=\"background: url('/assets/images/tyk-portal-silhouette.png') no-repeat left center;\"></div>\n"
        "<img src=\"/assets/images/request-access.png\" alt=\"\">\n"
        "  <!-- Partners -->\n  <div><img src=\"/assets/images/partners/acme.png\"></div>\n  <!-- end of Partners -->\n<p>after</p>\n"
    ),
    "views/flip_flop.tmpl": "<div style=\"background: url('/assets/images/about-us.png') no-repeat right center;\"></div>\n",
    "views/not_found.tmpl": "<img src=\"/assets/images/not-found.svg\" alt=\"\">\n<p>Page not found.</p>\n",
    "assets/stylesheets/main.css": ":root {\n  --tdp-primary: #335FFD;\n  --tdp-nav-bg-color: linear-gradient(91.35deg, #03031C 12.29%, #371DD7 98.13%, #335FFD 106.47%);\n}\n",
    "assets/stylesheets/pages/api-product.css": ".api-detail-background-color { background-color: #181834; }\n",
    "assets/stylesheets/pages/login.css": ".login-page-wrapper { background: url('/assets/images/login-background.jpg') no-repeat center center fixed; }\n",
    "assets/stylesheets/stoplight/elements.css": ".x { color: #335FFD; }\n",
}


def theme_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in THEME_FILES.items():
            archive.writestr(name, text)
    return path


def unzip_text(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name).decode("utf-8", errors="replace") for name in archive.namelist()}


class ModuleSelectionTests(unittest.TestCase):
    def test_portal_always_brings_branding_and_the_reverse(self):
        self.assertIn("portal-branding", generator.expand_modules("portal", "local"))
        self.assertIn("portal-branding", generator.expand_modules("portal-catalogues", "local"))
        self.assertIn("portal", generator.expand_modules("portal-branding", "local"))
        self.assertNotIn("portal-branding", generator.expand_modules("rest,api-key", "local"))

    def test_generator_and_validator_agree(self):
        for module in ("portal", "portal-branding", "tyk-sync", "cicd"):
            with self.subTest(module=module):
                self.assertEqual(generator.DEPENDENCIES[module], validator.MODULE_DEPENDENCIES[module])

    def test_a_portal_bundle_without_branding_fails_validation(self):
        config = {"schema_version": 1, "slug": "x-y-z", "display_name": "X", "modules": ["api-key", "portal", "rest"],
                  "identity_provider_mode": "local", "compose_profiles": [], "ports": {}, "proof": {},
                  "component_versions": {key: "v1.0.0" for key in ("gateway", "dashboard", "pump", "portal", "operator", "tyk_sync")}}
        self.assertTrue(any("missing module dependencies: portal-branding" in error for error in validator.validate_config(config)))


class PlaceholderBrandKitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        cls.result = generate(cls.root, "fabrikam", "Fabrikam Labs Group", "rest,api-key,portal,tyk-sync,cicd")
        cls.brand_dir = cls.root / "portal" / "brand"

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_every_portal_bundle_carries_a_placeholder_kit(self):
        brand = json.loads((self.brand_dir / "brand.json").read_text())
        self.assertIs(brand["placeholder"], True)
        self.assertEqual(brand["name"], "Fabrikam Labs Group")
        for key in ("logo", "logo_on_dark", "favicon", "hero"):
            self.assertTrue((self.brand_dir / brand[key]).is_file(), key)
        self.assertEqual(validator.brand_kit_errors(self.brand_dir), [])
        self.assertIn("portal-branding", json.loads((self.root / "demo.config.json").read_text())["modules"])
        self.assertIn("placeholder brand kit", self.result.stdout)

    def test_the_wordmark_is_text_from_the_display_name(self):
        wordmark = (self.brand_dir / "wordmark.svg").read_text()
        self.assertIn(">Fabrikam Labs Group</text>", wordmark)
        self.assertNotIn("<path", wordmark)
        self.assertIn("&amp; &lt;", generator.wordmark_svg("A & <B>", "#000"))

    def test_placeholder_kit_and_copy_stay_neutral(self):
        content = json.loads((self.brand_dir / "content.json").read_text())
        self.assertEqual(set(content["pages"]), {"/", "/about-us"})
        self.assertIn("Fabrikam Labs Group", content["pages"]["/"]["title"])
        for path in self.brand_dir.iterdir():
            text = path.read_text()
            with self.subTest(file=path.name):
                self.assertNotRegex(text, re.compile(r"\btyk\b", re.IGNORECASE))
        for value in [content["pages"]["/"]["title"], *content["pages"]["/"]["blocks"].values(),
                      *content["pages"]["/about-us"]["blocks"].values()]:
            # The display name itself says "Labs"; the generator's own words must not.
            self.assertNotRegex(value.replace("Fabrikam Labs Group", ""), FORBIDDEN_WORDS)

    def test_the_bundle_validates_and_sanitises(self):
        self.assertEqual(validator.validate_bundle(self.root, check_compose=False), [])
        self.assertEqual(sanitizer.scan_bundle(self.root), [])

    def test_readme_explains_branding_and_tyk_sync(self):
        readme = (self.root / "README.md").read_text()
        self.assertIn("## Developer Portal branding", readme)
        self.assertIn("./portal/customise-content.sh", readme)
        self.assertIn("## Tyk Sync promotion", readme)
        self.assertIn("./ci/tyk-sync-local.sh promote apis", readme)
        self.assertIn("host.docker.internal", (self.root / "ci" / "README.md").read_text())

    def test_sync_and_cicd_rows_start_blocked_not_talk_track(self):
        mapping = (self.root / "notes" / "requirements-mapping.md").read_text()
        for test_id in ("SYNC-001", "CICD-001", "PORTAL-UI-001"):
            row = next(line for line in mapping.splitlines() if test_id in line)
            self.assertIn("`blocked`", row)

    def test_a_missing_kit_fails_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            twin = Path(temporary) / "twin"
            shutil.copytree(self.root, twin)
            (twin / "portal" / "brand" / "brand.json").unlink()
            errors = validator.validate_bundle(twin, check_compose=False)
        self.assertTrue(any("portal/brand/brand.json" in error for error in errors), errors)


class BrandDirTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def test_a_supplied_kit_replaces_the_placeholder(self):
        kit = own_kit(self.work / "kit")
        root = self.work / "bundle"
        result = generate(root, "northwind", "Northwind", "portal", "--brand-dir", str(kit))
        brand_dir = root / "portal" / "brand"
        self.assertEqual(sorted(path.name for path in brand_dir.iterdir()),
                         ["brand.json", "content.json", "favicon.png", "hero.svg", "logo-dark.svg", "logo.svg"])
        self.assertEqual((brand_dir / "favicon.png").read_bytes(), PNG)
        self.assertEqual((brand_dir / "brand.json").read_bytes(), (kit / "brand.json").read_bytes())
        self.assertNotIn("placeholder brand kit", result.stdout)
        self.assertEqual(validator.validate_bundle(root, check_compose=False), [])
        # An organisation's kit is reported by the sanitiser unless this is its own bundle.
        kinds = [finding.kind for finding in sanitizer.scan_bundle(root)]
        self.assertTrue(any("organisation brand kit" in kind for kind in kinds), kinds)
        self.assertEqual(sanitizer.scan_bundle(root, allow_own_brand=True), [])

    def test_unsafe_or_incomplete_kits_are_refused_before_anything_is_written(self):
        cases = {
            "missing file": ({"logo": "absent.svg"}, None, "referenced by logo but missing"),
            "script": ({}, '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "active content"),
            "event handler": ({}, '<svg xmlns="http://www.w3.org/2000/svg" onload="x()"></svg>', "active content"),
            "javascript url": ({}, '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:x()"/></svg>', "active content"),
            "foreignObject": ({}, '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>', "active content"),
            "not svg": ({}, "plain text", "not an SVG image"),
            "gif": ({"favicon": "favicon.gif"}, None, "must name an .svg or .png"),
            "traversal": ({"hero": "../hero.svg"}, None, "must name an .svg or .png"),
            "colour": ({"colors": {"primary": "red;} body{display:none"}}, None, "#rgb or #rrggbb"),
            "font": ({"font_family": "x; } body { color: red"}, None, "font_family"),
            "no primary": ({"colors": {"link": "#000000"}}, None, "colors.primary is required"),
        }
        for label, (overrides, logo, message) in cases.items():
            with self.subTest(case=label):
                kit = own_kit(self.work / f"kit-{label.replace(' ', '-')}", **overrides)
                if logo is not None:
                    (kit / "logo.svg").write_text(logo)
                root = self.work / f"bundle-{label.replace(' ', '-')}"
                result = generate(root, "northwind", "Northwind", "portal", "--brand-dir", str(kit), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(root.exists())

    def test_brand_dir_needs_the_portal(self):
        kit = own_kit(self.work / "kit")
        result = generate(self.work / "bundle", "northwind", "Northwind", "rest", "--brand-dir", str(kit), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("select the portal module", result.stderr)


class SanitizerBrandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        generate(cls.root, "contoso", "Contoso", "portal")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def twin(self) -> Path:
        twin = Path(tempfile.mkdtemp(prefix="twin-"))
        self.addCleanup(shutil.rmtree, twin, True)
        shutil.copytree(self.root, twin / "b")
        return twin / "b"

    def test_a_stray_file_in_the_placeholder_kit_is_reported(self):
        twin = self.twin()
        (twin / "portal" / "brand" / "own-logo.png").write_bytes(PNG)
        kinds = [(str(finding.path), finding.kind) for finding in sanitizer.scan_bundle(twin)]
        self.assertIn(("portal/brand/own-logo.png", "file in the placeholder brand kit that brand.json does not name"), kinds)

    def test_dropping_the_placeholder_flag_is_reported_and_the_flag_allows_it(self):
        twin = self.twin()
        manifest = twin / "portal" / "brand" / "brand.json"
        brand = json.loads(manifest.read_text())
        del brand["placeholder"]
        manifest.write_text(json.dumps(brand))
        refused = subprocess.run([sys.executable, str(SCRIPTS / "sanitize_bundle.py"), str(twin)], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("organisation brand kit", refused.stderr)
        allowed = subprocess.run([sys.executable, str(SCRIPTS / "sanitize_bundle.py"), str(twin), "--own-brand"], capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)


class BuildThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temporary.name)
        cls.root = cls.work / "bundle"
        generate(cls.root, "contoso", "Contoso", "portal")
        cls.source = theme_zip(cls.work / "default.zip")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def build(self, brand: Path | None, name: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.root / "portal" / "build-theme.py"), "--source", str(self.source),
                   "--overlay", str(self.root / "portal" / "theme-overrides" / "assets" / "stylesheets" / "neutral-overlay.css"),
                   "--output", str(self.work / name)]
        if brand is not None:
            command += ["--brand", str(brand)]
        return subprocess.run(command, capture_output=True, text=True)

    def test_every_stock_touchpoint_is_replaced(self):
        self.assertEqual(self.build(self.root / "portal" / "brand", "branded.zip").returncode, 0)
        files = unzip_text(self.work / "branded.zip")
        everything = "\n".join(text for name, text in files.items() if "stoplight" not in name)
        for stock in ("DEVELOPER PORTAL", "dev-portal-logo.svg", "Powered by © Tyk", "new-logo.svg", 'alt="Tyk Logo"',
                      "Tyk Developer Portal", "tyk-portal-silhouette.png", "about-us.png", "request-access.png",
                      "/assets/images/login.svg", "card-placeholder.svg", "not-found.svg", "<!-- Partners -->", "/partners/", "#181834"):
            with self.subTest(stock=stock):
                self.assertNotIn(stock, everything)
        self.assertIn('src="/assets/images/brand/wordmark-on-dark.svg"', files["partials/top_nav.tmpl"])
        self.assertIn("Developer Portal</span>", files["partials/top_nav.tmpl"])
        self.assertIn("© Contoso", files["partials/footer.tmpl"])
        self.assertIn("Log in to your Contoso Developer Portal account", files["auth/password/login.tmpl"])
        self.assertIn("no-repeat center center / contain", files["views/home.tmpl"])
        self.assertIn("<p>after</p>", files["views/home.tmpl"])
        for layout in ("layouts/portal_layout.tmpl", "layouts/product_layout_doc_stoplight.tmpl"):
            self.assertIn('<link rel="icon" href="/assets/images/brand/favicon.svg">', files[layout])
        self.assertIn("assets/images/brand/hero.svg", files)
        overlay = files["assets/stylesheets/neutral-overlay.css"]
        for variable in ("--tdp-primary: #3a5a78;", "--tdp-nav-bg-color: #1f2933;", "--tdp-dark: #1f2933;",
                         "--tdp-secondary: #5b6b7b;", "--tdp-link-color: #2c4660;"):
            self.assertIn(variable, overlay)
        self.assertIn(".btn.portal-primary", overlay)
        self.assertIn(".btn-primary-outline", overlay)
        self.assertIn(".login-page-wrapper { background: var(--tdp-dark) !important; }", overlay)
        # Hard-coded palette colours are remapped, except in the bundled Stoplight CSS.
        self.assertIn("#1f2933", files["assets/stylesheets/pages/api-product.css"])
        self.assertIn("#335FFD", files["assets/stylesheets/stoplight/elements.css"])
        self.assertEqual(json.loads(files["theme.json"])["author"], "Contoso Platform Team")

    def test_without_a_brand_the_stock_branding_survives(self):
        self.assertEqual(self.build(None, "unbranded.zip").returncode, 0)
        files = unzip_text(self.work / "unbranded.zip")
        self.assertIn("DEVELOPER PORTAL", files["partials/top_nav.tmpl"])
        self.assertIn("<!-- Partners -->", files["views/home.tmpl"])
        self.assertNotIn("--tdp-primary", files["assets/stylesheets/neutral-overlay.css"])

    def test_unsafe_brand_values_are_refused_at_build_time(self):
        kit = own_kit(self.work / "bad-kit", colors={"primary": "#fff; } body { display: none"})
        result = self.build(kit, "bad.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("colors.primary", result.stderr)


FAKE_THEME_CURL = r'''#!/usr/bin/env bash
# A Portal theme API: list, download (writes the fixture to -o as curl would), upload, activate.
method=GET url="" out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -X) method="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
case "${method} ${url}" in
  "GET "*"/themes?per_page=100") echo '[{"ID":"default","Name":"default","Status":"Current"}]' ;;
  "GET "*"/themes/default/download") cat "${FAKE_THEME_ZIP}" >"${out}" ;;
  "POST "*"/themes/upload") echo '{}' ;;
  "PUT "*"/themes/neutral-demo/activate") echo '{}' ;;
  "GET "*"/themes/neutral-demo") echo '{"Status":"Current"}' ;;
  *) echo "unexpected ${method} ${url}" >&2; exit 22 ;;
esac
'''


@unittest.skipUnless(HAS_JQ, "jq is required")
class ThemeRuntimePermissionTests(unittest.TestCase):
    """apply-neutral-theme.sh leaves .runtime/ private, which validate_bundle.py requires."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "bundle"
        generate(self.root, "contoso", "Contoso", "portal")
        write_executable(self.work / "bin" / "curl", FAKE_THEME_CURL)
        self.env = dict(os.environ, PATH=f"{self.work / 'bin'}{os.pathsep}{os.environ['PATH']}",
                        PORTAL_API_TOKEN="stub", FAKE_THEME_ZIP=str(theme_zip(self.work / "default.zip")))

    def run_script(self, script: Path) -> subprocess.CompletedProcess[str]:
        # Under the common default umask, so a script that does not set its own leaves 0644 files.
        return subprocess.run(["bash", "-c", 'umask 022; exec "$0"', str(script)], capture_output=True, text=True, env=self.env)

    def test_theme_archives_and_build_trees_are_private(self):
        result = self.run_script(self.root / "portal" / "apply-neutral-theme.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        theme_dir = self.root / ".runtime" / "portal-theme"
        self.assertTrue((theme_dir / "neutral-demo.zip").is_file())
        self.assertEqual(private_violations(self.root), [])
        self.assertEqual(stat.S_IMODE(theme_dir.stat().st_mode), 0o700)
        self.assertEqual(list(theme_dir.glob("build.*")), [])

    def test_without_umask_and_chmod_the_validator_catches_it(self):
        script = self.root / "portal" / "apply-neutral-theme.sh"
        text = script.read_text().replace("umask 077\n", "").replace('chmod -R go-rwx "${THEME_RUNTIME_DIR}"\n', "")
        self.assertNotIn("umask 077", text)
        script.write_text(text)
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("neutral-demo.zip" in error for error in private_violations(self.root)))


class StubPortal(http.server.BaseHTTPRequestHandler):
    """Portal 1.19.0 CMS behaviour: lists return 20 items unless per_page is passed, and the
    content-block order changes after an update."""

    state: dict = {}

    def log_message(self, *args):
        pass

    def reply(self, value, code=200):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def page_of(self, items, query):
        per_page = int(query.get("per_page", ["20"])[0])
        return items[:per_page]

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        parts = parsed.path.strip("/").split("/")
        state = self.state
        if self.headers.get("Authorization") != "stub":
            return self.reply({"error": "unauthorised"}, 401)
        if parts == ["portal-api", "pages"]:
            return self.reply(self.page_of(list(state["pages"].values()), query))
        if len(parts) == 3 and parts[1] == "pages":
            return self.reply(state["pages"][int(parts[2])])
        if len(parts) == 4 and parts[3] == "content-blocks":
            return self.reply(self.page_of(state["blocks"][int(parts[2])], query))
        self.reply({"error": "not found"}, 404)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state = self.state
        state["puts"] += 1
        if len(parts) == 3 and parts[1] == "pages":
            state["pages"][int(parts[2])]["Title"] = body["Title"]
            return self.reply(state["pages"][int(parts[2])])
        if len(parts) == 5 and parts[3] == "content-blocks":
            blocks = state["blocks"][int(parts[2])]
            for block in blocks:
                if block["ID"] == int(parts[4]):
                    block.update({key: body[key] for key in ("Content", "MarkdownContent") if key in body})
            blocks.reverse()
            return self.reply({})
        self.reply({"error": "not found"}, 404)


def serve(handler) -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@unittest.skipUnless(HAS_JQ, "jq is required")
class CustomiseContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        generate(cls.root, "contoso", "Contoso", "portal")
        cls.content = json.loads((cls.root / "portal" / "brand" / "content.json").read_text())
        cls.server, cls.url = serve(StubPortal)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.temporary.cleanup()

    def setUp(self):
        home = [{"ID": 100 + index, "Name": f"Filler{index}", "Content": "x", "MarkdownEnabled": False} for index in range(22)]
        # Named blocks sit beyond the first 20, where a default-page lookup cannot see them.
        home += [{"ID": 200 + index, "Name": name, "Content": "About Tyk", "MarkdownEnabled": name == "HeaderDescription", "MarkdownContent": "Tyk"}
                 for index, name in enumerate(self.content["pages"]["/"]["blocks"])]
        about = [{"ID": 300 + index, "Name": name, "Content": "Tyk", "MarkdownEnabled": False}
                 for index, name in enumerate(self.content["pages"]["/about-us"]["blocks"])]
        StubPortal.state = {
            "pages": {1: {"ID": 1, "Path": "/", "Title": "Build the future with Tyk APIs"},
                      2: {"ID": 2, "Path": "/about-us", "Title": "About us"}},
            "blocks": {1: home, 2: about}, "puts": 0,
        }
        self.env = dict(os.environ, PORTAL_URL=self.url, PORTAL_API_TOKEN="stub")

    def run_script(self, script: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(script)], capture_output=True, text=True, env=self.env)

    def test_every_named_block_is_rewritten_and_a_rerun_changes_nothing(self):
        result = self.run_script(self.root / "portal" / "customise-content.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = StubPortal.state
        for path, page_id in (("/", 1), ("/about-us", 2)):
            want = self.content["pages"][path]
            self.assertEqual(state["pages"][page_id]["Title"], want["title"])
            blocks = {block["Name"]: block for block in state["blocks"][page_id]}
            for name, value in want["blocks"].items():
                self.assertEqual(blocks[name]["Content"], value)
        self.assertEqual(blocks_of(state, 1)["HeaderDescription"]["MarkdownContent"], self.content["pages"]["/"]["blocks"]["HeaderDescription"])
        puts = state["puts"]
        again = self.run_script(self.root / "portal" / "customise-content.sh")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("0 change(s)", again.stdout)
        self.assertEqual(state["puts"], puts)

    def test_a_default_page_lookup_misses_blocks(self):
        script = self.root / "portal" / "customise-content-unpaged.sh"
        script.write_text((self.root / "portal" / "customise-content.sh").read_text().replace("?per_page=100", ""))
        script.chmod(0o755)
        result = self.run_script(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not on the Portal page /", result.stderr)


def blocks_of(state: dict, page_id: int) -> dict:
    return {block["Name"]: block for block in state["blocks"][page_id]}


# Every list call a lookup depends on must ask for more than the default page of 20.
LIST_ENDPOINT = re.compile(
    r"/(?:providers|products|plans|catalogues|users|pages|organisations|apps|oauth-providers|themes|"
    r"content-blocks|teams|audiences|spec-details|client-types|access-requests)(?:\?\S*)?$"
)
PORTAL_GET = re.compile(r"""(?:portal_api GET|portal_read|papi)\s+["']?([^"'\s)]+)""")


def unpaginated_list_calls(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() for match in PORTAL_GET.finditer(line)
            if LIST_ENDPOINT.search(match.group(1)) and "per_page=" not in match.group(1)]


class PortalPaginationAuditTests(unittest.TestCase):
    def test_every_portal_list_lookup_asks_for_a_full_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            generate(root, "contoso", "Contoso", "rest,api-key,jwt,portal,dcr,portal-catalogues")
            scripts = sorted((root / "portal").glob("*.sh")) + [root / "scripts" / "verify-demo.sh"]
            self.assertGreater(len(scripts), 5)
            for script in scripts:
                with self.subTest(script=script.name):
                    self.assertEqual(unpaginated_list_calls(script.read_text()), [])

    def test_the_audit_catches_a_default_page_lookup(self):
        self.assertEqual(len(unpaginated_list_calls('blocks="$(portal_api GET "/pages/${id}/content-blocks")"')), 1)
        self.assertEqual(len(unpaginated_list_calls("pages=\"$(portal_api GET /pages)\"")), 1)
        self.assertEqual(unpaginated_list_calls('portal_api GET "/products/${id}"'), [])


class StubBrandedPortal(http.server.BaseHTTPRequestHandler):
    pages: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = self.pages.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(HAS_JQ, "jq is required")
class PortalUiProofTests(unittest.TestCase):
    """The PORTAL-UI-001 block of the generated verify-demo.sh against a stub Portal."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temporary.name)
        generated = cls.work / "generated"
        generate(generated, "contoso", "Contoso", "portal")
        verify = (generated / "scripts" / "verify-demo.sh").read_text()
        cls.block = verify[verify.index("if module_enabled portal-branding; then"):verify.index("if module_enabled portal-catalogues; then")]
        cls.placeholder = json.loads((generated / "portal" / "brand" / "brand.json").read_text())
        cls.server, cls.url = serve(StubBrandedPortal)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.temporary.cleanup()

    def bundle(self, brand: dict, shots: int = 0, registered: bool = True, **entry) -> Path:
        """Write the brand kit and `shots` captures, registered in the manifest the way
        scripts/record-screenshot.sh does unless `registered` is False; `entry` overrides fields."""
        root = Path(tempfile.mkdtemp(prefix="ui-", dir=self.work))
        (root / "portal" / "brand").mkdir(parents=True)
        brand_path = root / "portal" / "brand" / "brand.json"
        brand_path.write_text(json.dumps(brand))
        screenshots = root / "evidence" / "screenshots"
        screenshots.mkdir(parents=True)
        manifest = {}
        for name in ("portal-theme", "portal-catalogue", "portal-openapi-viewer", "portal-login")[:shots]:
            (screenshots / f"{name}.png").write_bytes(PNG)
            manifest[name] = dict({
                "file": f"{name}.png", "sha256": hashlib.sha256(PNG).hexdigest(), "url": f"{self.url}/",
                "marker": "x", "org_id": "", "brand_digest": hashlib.sha256(brand_path.read_bytes()).hexdigest(),
            }, **entry)
        if registered:
            (screenshots / "manifest.json").write_text(json.dumps(manifest))
        return root

    def serve_branded(self, brand: dict, extra: str = "", primary: str | None = None, theme: str = "Current") -> None:
        logo = brand.get("logo_on_dark") or brand["logo"]
        page = (f'<html><head><link rel="icon" href="/assets/images/brand/{brand["favicon"]}"></head>'
                f'<body><img src="/assets/images/brand/{logo}">{extra}</body></html>')
        StubBrandedPortal.pages = {
            "/portal-api/themes?per_page=100": json.dumps([{"ID": "neutral-demo", "Name": "neutral-demo", "Status": theme}]),
            "/": page, "/about-us": page, "/portal/catalogue-products": page, "/auth/password/login": page,
            "/assets/stylesheets/neutral-overlay.css": f":root {{\n  --tdp-primary: {primary or brand['colors']['primary']};\n}}\n",
        }

    def run_block(self, root: Path) -> tuple[str, str]:
        harness = root / "harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"ROOT_DIR={json.dumps(str(root))}\nPORTAL_URL={self.url}\nPORTAL_API_TOKEN=stub\n"
            'record() { printf "%s|%s|%s\\n" "$1" "$2" "$3"; }\n'
            'module_enabled() { [[ " portal portal-branding " == *" $1 "* ]]; }\n' + self.block
        )
        result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, check=True)
        test_id, status, detail = result.stdout.strip().split("|", 2)
        self.assertEqual(test_id, "PORTAL-UI-001")
        return status, detail

    def own_brand(self) -> dict:
        brand = dict(self.placeholder)
        del brand["placeholder"]
        brand["colors"] = dict(brand["colors"], primary="#0a7d4f")
        return brand

    def test_the_placeholder_brand_is_a_valid_technical_proof(self):
        """DB-10: the neutral kit proves the Portal carries no stock Tyk branding; an
        organisation's kit is a presentation choice, not a prerequisite."""
        self.serve_branded(self.placeholder)
        status, detail = self.run_block(self.bundle(self.placeholder, shots=0))
        self.assertEqual(status, "blocked")
        self.assertIn("scripts/record-screenshot.sh", detail)
        status, detail = self.run_block(self.bundle(self.placeholder, shots=4))
        self.assertEqual(status, "passed", detail)
        self.assertIn("neutral placeholder brand", detail)

    def test_an_own_brand_needs_the_four_screenshots(self):
        brand = self.own_brand()
        self.serve_branded(brand)
        status, detail = self.run_block(self.bundle(brand, shots=2))
        self.assertEqual(status, "blocked")
        self.assertIn("2/4 browser screenshots", detail)
        status, detail = self.run_block(self.bundle(brand, shots=4))
        self.assertEqual(status, "passed", detail)
        self.assertIn("#0a7d4f", detail)

    def test_screenshots_must_be_registered_for_this_stack_and_brand(self):
        """DB-06: a non-empty PNG is not evidence unless it is bound to this run."""
        brand = self.own_brand()
        self.serve_branded(brand)
        cases = {
            "unregistered": (dict(registered=False), "not registered"),
            "replaced file": (dict(sha256="0" * 64), "file changed since it was registered"),
            "other portal": (dict(url="http://other.invalid/"), "captured from another Portal"),
            "other stack": (dict(org_id="org-2"), "registered for another stack"),
            "older brand": (dict(brand_digest="0" * 64), "taken with a different brand kit"),
        }
        for label, (overrides, expect) in cases.items():
            with self.subTest(case=label):
                status, detail = self.run_block(self.bundle(brand, shots=4, **overrides))
                self.assertEqual(status, "blocked", detail)
                self.assertIn(expect, detail)

    def test_stock_branding_or_a_missing_brand_element_fails(self):
        brand = self.own_brand()
        cases = {
            "stock logo": dict(extra='<img src="/assets/images/new-logo.svg">', expect="stock 'new-logo.svg'"),
            "stock copy": dict(extra="<h2>About Tyk</h2>", expect="stock 'About Tyk'"),
            "stock nav": dict(extra='<a class="navbar-brand" href="/">DEVELOPER PORTAL</a>', expect="DEVELOPER PORTAL"),
            "stock title": dict(extra="<h1>Build the future with Tyk APIs</h1>", expect="with Tyk APIs"),
            "wrong primary": dict(primary="#335FFD", expect="overlay lacks --tdp-primary #0a7d4f"),
            "inactive theme": dict(theme="Inactive", expect="theme is not active"),
        }
        for label, case in cases.items():
            with self.subTest(case=label):
                expect = case.pop("expect")
                self.serve_branded(brand, **case)
                status, detail = self.run_block(self.bundle(brand, shots=4))
                self.assertEqual(status, "failed")
                self.assertIn(expect, detail)
        with self.subTest(case="no favicon"):
            self.serve_branded(dict(brand, favicon="other.svg"))
            status, detail = self.run_block(self.bundle(brand, shots=4))
            self.assertEqual(status, "failed")
            self.assertIn("no brand favicon", detail)


FAKE_TYK_SYNC = r'''#!/usr/bin/env python3
"""tyk-sync v2.2.2 as ci/tyk-sync.sh uses it, over a directory standing in for the Dashboard."""
import json, os, pathlib, sys
state = pathlib.Path(os.environ["FAKE_SYNC_STATE"])
org = os.environ["FAKE_SYNC_ORG"]
with open(os.environ["FAKE_SYNC_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "secret": os.environ.get("TYKGIT_DB_SECRET")}) + "\n")
args = sys.argv[1:]
command = args[0]
if "--org" in args or "-o" in args:
    print("Error: unknown flag: --org"); sys.exit(1)
if "--secret" in args or "-s" in args:
    print("secret on the command line"); sys.exit(3)
def option(name):
    return args[args.index(name) + 1]
if command == "dump":
    target = pathlib.Path(option("--target")); target.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(state.glob("*.json")):
        out = target / f"oas-{path.stem}.json"
        out.write_text(json.dumps({"oas": json.loads(path.read_text()), "hook_references": [], "is_site": False,
                                   "sort_by": 0, "user_owners": [], "user_group_owners": []}))
        files.append({"file": out.name, "oas": {}})
    policy = target / "policy-0001.json"
    policy.write_text(json.dumps({"id": "0001", "name": "Policy", "access_rights": {}}))
    (target / ".tyk.json").write_text(json.dumps({"type": "apidef", "files": files, "policies": [{"file": policy.name}]}))
    for path in target.iterdir():
        path.chmod(0o644)  # a container writes with its own umask
    sys.exit(0)
if command in ("publish", "update"):
    source = pathlib.Path(option("--path"))
    for entry in json.loads((source / ".tyk.json").read_text())["files"]:
        wrapper = json.loads((source / entry["file"]).read_text())
        if "oas" not in wrapper or "oas" not in entry:
            print("not a Tyk OAS wrapper"); sys.exit(4)
        document = wrapper["oas"]
        api_id = document["x-tyk-api-gateway"]["info"]["id"]
        exists = (state / f"{api_id}.json").exists()
        if command == "publish" and exists:
            print("object seems to exist (same ID, API ID or Listen Path), use update()"); sys.exit(1)
        if command == "update" and not exists:
            print("object does not exist, use create()"); sys.exit(1)
        document["x-tyk-api-gateway"]["info"]["orgId"] = org
        (state / f"{api_id}.json").write_text(json.dumps(document))
    sys.exit(0)
sys.exit(2)
'''


@unittest.skipUnless(HAS_JQ, "jq is required")
class TykSyncDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "bundle"
        generate(self.root, "contoso", "Contoso", "rest,api-key,tyk-sync,cicd")
        self.state = self.work / "dashboard"
        self.state.mkdir()
        core = json.loads((self.root / "apis" / "contoso-core.oas.json").read_text())
        core["x-tyk-api-gateway"]["info"]["orgId"] = "org-1"
        (self.state / "contoso-core.json").write_text(json.dumps(core))
        self.log = self.work / "sync.log"
        write_executable(self.work / "bin" / "tyk-sync", FAKE_TYK_SYNC)
        self.env = dict(os.environ, TYK_SYNC_BIN=str(self.work / "bin" / "tyk-sync"), TYK_SYNC_DASHBOARD_URL="http://dashboard.invalid",
                        TYK_SYNC_SECRET="s3cret-key", TYK_SYNC_ORG_ID="org-1", FAKE_SYNC_STATE=str(self.state),
                        FAKE_SYNC_ORG="org-1", FAKE_SYNC_LOG=str(self.log))

    def sync(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", 'umask 022; exec "$0" "$@"', str(self.root / "ci" / "tyk-sync.sh"), *args],
                              capture_output=True, text=True, env=dict(self.env, **env))

    def summary(self) -> dict:
        return json.loads((self.root / ".runtime" / "tyk-sync" / "latest-promotion.json").read_text())

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_promote_updates_existing_and_publishes_new_apis_then_repeats(self):
        first = self.sync("promote", str(self.root / "apis"), TYK_SYNC_PROMOTION_ID="first")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        summary = self.summary()
        self.assertEqual((summary["proof"], summary["operation"], summary["org_id"]), ("SYNC-001", "promote", "org-1"))
        self.assertEqual(summary["api_ids"], ["contoso-api-key", "contoso-core"])
        self.assertEqual((summary["created"], summary["updated"]), (1, 1))
        self.assertTrue((self.state / "contoso-api-key.json").is_file())
        evidence = self.root / ".runtime" / "tyk-sync" / "evidence" / "first"
        self.assertEqual(sorted(path.name for path in evidence.glob("*.json")), ["contoso-api-key.json", "contoso-core.json"])
        index = json.loads((self.root / ".runtime" / "tyk-sync" / "snapshots" / "first" / ".tyk.json").read_text())
        self.assertEqual(index["type"], "apidef")
        self.assertTrue(all(entry["oas"] == {} for entry in index["files"]))
        # The secret reaches tyk-sync only through TYKGIT_DB_SECRET, and no command gets --org.
        calls = self.calls()
        self.assertTrue(all(call["secret"] == "s3cret-key" for call in calls))
        self.assertNotIn("s3cret-key", json.dumps([call["argv"] for call in calls]))
        self.assertEqual([call["argv"][0] for call in calls], ["dump", "update", "publish", "dump"])
        self.assertEqual(private_violations(self.root), [])
        again = self.sync("promote", str(self.root / "apis"), TYK_SYNC_PROMOTION_ID="second")
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        self.assertEqual((self.summary()["created"], self.summary()["updated"]), (0, 2))
        immutable = self.sync("promote", str(self.root / "apis"), TYK_SYNC_PROMOTION_ID="second")
        self.assertNotEqual(immutable.returncode, 0)
        self.assertIn("immutable", immutable.stderr)

    def test_rollback_reapplies_the_captured_definitions(self):
        self.assertEqual(self.sync("promote", str(self.root / "apis"), TYK_SYNC_PROMOTION_ID="first").returncode, 0)
        rollback_dir = self.root / ".runtime" / "tyk-sync" / "rollback" / "first"
        self.assertTrue((rollback_dir / ".tyk.json").is_file())
        result = self.sync("rollback", str(rollback_dir), TYK_SYNC_PROMOTION_ID="back")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.summary()["operation"], self.summary()["promotion_id"]), ("rollback", "back"))
        self.assertEqual(private_violations(self.root), [])

    def test_a_read_back_from_another_organisation_fails(self):
        result = self.sync("promote", str(self.root / "apis"), FAKE_SYNC_ORG="org-2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("organisation org-1", result.stderr)
        self.assertFalse((self.root / ".runtime" / "tyk-sync" / "latest-promotion.json").exists())

    def test_an_unowned_definition_is_refused(self):
        foreign = self.work / "foreign"
        shutil.copytree(self.root / "apis", foreign)
        document = json.loads((foreign / "contoso-core.oas.json").read_text())
        document["x-tyk-api-gateway"]["info"]["id"] = "someone-else"
        (foreign / "other.oas.json").write_text(json.dumps(document))
        result = self.sync("promote", str(foreign))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing unowned Tyk Sync definition: someone-else", result.stderr)

    def test_dump_output_is_made_private(self):
        script = self.root / "ci" / "tyk-sync.sh"
        script.write_text(script.read_text().replace('chmod -R go-rwx "$1"', ":"))
        self.assertEqual(self.sync("promote", str(self.root / "apis")).returncode, 0)
        self.assertTrue(any("tyk-sync/raw" in error for error in private_violations(self.root)))

    def test_definition_ids_are_read_from_the_dump_wrapper(self):
        wrapped = self.work / "wrapped.json"
        wrapped.write_text(json.dumps({"oas": {"x-tyk-api-gateway": {"info": {"id": "api-1"}}}, "hook_references": []}))
        policy = self.work / "policy.json"
        policy.write_text(json.dumps({"id": "api-1", "access_rights": {}}))
        source = (self.root / "ci" / "tyk-sync.sh").read_text()
        function = source[source.index("definition_id() {"):source.index("\n}\n", source.index("definition_id() {")) + 3]
        harness = self.work / "ids.sh"
        harness.write_text(function + f'definition_id "{wrapped}"; echo "|"; definition_id "{policy}"\n')
        output = subprocess.run(["bash", str(harness)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(output.split(), ["api-1", "|"])
        # The 1.x expression this replaced reads nothing from a 2.x dump.
        old = subprocess.run(["jq", "-r", '.api_id // .id // .["x-tyk-api-gateway"].info.id // empty', str(wrapped)],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(old.strip(), "")


FAKE_DOCKER = r'''#!/usr/bin/env bash
printf '%s\n' "$@" >"${FAKE_DOCKER_LOG}"
'''


class TykSyncDockerWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "bundle"
        generate(self.root, "contoso", "Contoso", "rest,tyk-sync")
        write_executable(self.work / "bin" / "docker", FAKE_DOCKER)
        self.log = self.work / "docker.log"
        self.env = dict(os.environ, PATH=f"{self.work / 'bin'}{os.pathsep}{os.environ['PATH']}", FAKE_DOCKER_LOG=str(self.log),
                        TYKGIT_DB_SECRET="s3cret-key")
        self.env.pop("TYK_SYNC_VERSION", None)

    def test_the_wrapper_runs_the_pinned_image_at_the_bundle_path(self):
        result = subprocess.run([str(self.root / "ci" / "tyk-sync-docker.sh"), "dump", "--target", "x"], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.log.read_text().splitlines()
        # The wrapper resolves its own directory with cd and pwd, the logical path.
        root = str(self.root)
        self.assertEqual(argv[argv.index("--entrypoint") + 1], "/opt/tyk-sync/tyk-sync")
        self.assertEqual(argv[argv.index("--add-host") + 1], "host.docker.internal:host-gateway")
        self.assertEqual(argv[argv.index("-v") + 1], f"{root}:{root}")
        self.assertEqual(argv[argv.index("-w") + 1], root)
        self.assertEqual(argv[argv.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertEqual(argv[argv.index("-e") + 1], "TYKGIT_DB_SECRET")
        self.assertIn(f"tykio/tyk-sync:{generator.KNOWN_GOOD_VERSIONS['tyk_sync']}", argv)
        self.assertEqual(argv[-3:], ["dump", "--target", "x"])
        self.assertNotIn("s3cret-key", argv)

    def test_without_a_version_the_wrapper_refuses(self):
        for name in (".env", ".env.example"):
            path = self.root / name
            path.write_text("\n".join(line for line in path.read_text().splitlines() if not line.startswith("TYK_SYNC_VERSION=")) + "\n")
        result = subprocess.run([str(self.root / "ci" / "tyk-sync-docker.sh"), "dump"], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TYK_SYNC_VERSION", result.stderr)
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(HAS_JQ, "jq is required")
    def test_the_local_entry_point_targets_this_stack(self):
        runtime = self.root / ".runtime"
        runtime.mkdir(mode=0o700)
        (runtime / "runtime.env").write_text("export DASHBOARD_API_KEY=s3cret-key\nexport ORG_ID=org-1\n")
        (runtime / "runtime.env").chmod(0o600)
        state = self.work / "dashboard"
        state.mkdir()
        write_executable(self.work / "bin" / "tyk-sync", FAKE_TYK_SYNC)
        log = self.work / "sync.log"
        env = dict(self.env, TYK_SYNC_BIN=str(self.work / "bin" / "tyk-sync"), FAKE_SYNC_STATE=str(state),
                   FAKE_SYNC_ORG="org-1", FAKE_SYNC_LOG=str(log))
        env.pop("TYKGIT_DB_SECRET")
        result = subprocess.run([str(self.root / "ci" / "tyk-sync-local.sh"), "promote", "apis"], capture_output=True, text=True,
                                env=env, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        port = json.loads((self.root / "demo.config.json").read_text())["ports"]["dashboard"]
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(calls[0]["argv"][:3], ["dump", "--dashboard", f"http://host.docker.internal:{port}"])
        self.assertEqual(calls[0]["secret"], "s3cret-key")


FAKE_DASHBOARD_CURL = r'''#!/usr/bin/env bash
# Answers 200 for a URL listed in FAKE_LIVE_URLS and 404 otherwise, as -w '%{http_code}' would.
url=""
for arg in "$@"; do [[ "${arg}" == http* ]] && url="${arg}"; done
for live in ${FAKE_LIVE_URLS:-}; do [[ "${url}" == "${live}" ]] && { printf 200; exit 0; }; done
printf 404
'''


@unittest.skipUnless(HAS_JQ, "jq is required")
class SyncAndCicdProofTests(unittest.TestCase):
    """The SYNC-001 and CICD-001 blocks of the generated verify-demo.sh."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temporary.name)
        cls.generated = cls.work / "generated"
        generate(cls.generated, "contoso", "Contoso", "rest,api-key,tyk-sync,cicd")
        verify = (cls.generated / "scripts" / "verify-demo.sh").read_text()
        cls.block = verify[verify.index("# Tyk Sync delivery runs out of band"):verify.index("if module_enabled mcp; then")]
        write_executable(cls.work / "bin" / "curl", FAKE_DASHBOARD_CURL)
        # The digests ci/tyk-sync.sh records for what it delivered: sha256 of jq -S -c.
        cls.digests = {}
        for file in sorted((cls.generated / "apis").glob("*.oas.json")):
            canonical = subprocess.run(["jq", "-S", "-c", "(.oas // .)", str(file)], capture_output=True, check=True).stdout
            api_id = json.loads(file.read_text())["x-tyk-api-gateway"]["info"]["id"]
            cls.digests[api_id] = hashlib.sha256(canonical).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def bundle(self, promotion: dict | None = None, evidence: dict[str, dict] | None = None) -> Path:
        root = Path(tempfile.mkdtemp(prefix="sync-", dir=self.work))
        shutil.copytree(self.generated / "apis", root / "apis")
        shutil.copytree(self.generated / "ci", root / "ci")
        sync = root / ".runtime" / "tyk-sync"
        sync.mkdir(parents=True)
        if promotion is not None:
            if promotion.get("api_digests") == "current":
                promotion = dict(promotion, api_digests=self.digests)
            (sync / "latest-promotion.json").write_text(json.dumps(promotion))
        if evidence is not None:
            directory = sync / "evidence" / promotion["promotion_id"]
            directory.mkdir(parents=True)
            for api_id, info in evidence.items():
                (directory / f"{api_id}.json").write_text(json.dumps({"oas": {"x-tyk-api-gateway": {"info": info}}}))
        return root

    def run_block(self, root: Path, live: tuple[str, ...] = ("contoso-core", "contoso-api-key")) -> dict[str, tuple[str, str]]:
        harness = root / "harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"ROOT_DIR={json.dumps(str(root))}\nDASHBOARD_URL=http://dashboard.invalid\nDASHBOARD_API_KEY=stub\nORG_ID=org-1\n"
            'record() { printf "%s|%s|%s\\n" "$1" "$2" "$3"; }\n'
            'module_enabled() { [[ " tyk-sync cicd " == *" $1 "* ]]; }\n' + self.block
        )
        result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, check=True,
                                env=dict(os.environ, PATH=f"{self.work / 'bin'}{os.pathsep}{os.environ['PATH']}",
                                         FAKE_LIVE_URLS=" ".join(f"http://dashboard.invalid/api/apis/oas/{api}" for api in live)))
        rows = {}
        for line in result.stdout.strip().splitlines():
            test_id, status, detail = line.split("|", 2)
            rows[test_id] = (status, detail)
        return rows

    PROMOTION = {"proof": "SYNC-001", "operation": "promote", "promotion_id": "p1", "org_id": "org-1",
                 "created": 1, "updated": 1, "api_digests": "current", "recorded_at": "2026-10-14T00:00:00Z"}

    def evidence(self, org: str = "org-1", ids: tuple[str, ...] = ("contoso-core", "contoso-api-key")) -> dict:
        return {api_id: {"id": api_id, "orgId": org} for api_id in ids}

    def test_no_promotion_blocks_both(self):
        rows = self.run_block(self.bundle())
        self.assertEqual(rows["SYNC-001"][0], "blocked")
        self.assertIn("./ci/tyk-sync-local.sh promote apis", rows["SYNC-001"][1])
        self.assertEqual(rows["CICD-001"][0], "blocked")
        self.assertIn("SYNC-001 blocked", rows["CICD-001"][1])

    def test_a_complete_read_back_passes_both(self):
        rows = self.run_block(self.bundle(self.PROMOTION, self.evidence()))
        self.assertEqual(rows["SYNC-001"][0], "passed", rows)
        self.assertIn("all 2 owned APIs", rows["SYNC-001"][1])
        self.assertEqual(rows["CICD-001"][0], "passed", rows)
        self.assertIn("CI template checks and local promotion", rows["CICD-001"][1])
        self.assertIn("No CI runner ran", rows["CICD-001"][1])

    def test_an_api_edited_since_the_promotion_blocks_both(self):
        """DB-05: the same API id with different content is not the delivered revision."""
        root = self.bundle(self.PROMOTION, self.evidence())
        target = root / "apis" / "contoso-core.oas.json"
        document = json.loads(target.read_text())
        document["x-tyk-api-gateway"]["upstream"]["url"] = "http://changed.invalid"
        target.write_text(json.dumps(document))
        rows = self.run_block(root)
        self.assertEqual(rows["SYNC-001"][0], "blocked", rows)
        self.assertIn("does not match apis/ for contoso-core", rows["SYNC-001"][1])
        self.assertEqual(rows["CICD-001"][0], "blocked")
        # A record written before digests existed is not trusted either.
        legacy = {key: value for key, value in self.PROMOTION.items() if key != "api_digests"}
        rows = self.run_block(self.bundle(legacy, self.evidence()))
        self.assertEqual(rows["SYNC-001"][0], "blocked", rows)

    def test_stale_or_foreign_promotions_are_blocked_and_a_vanished_api_fails(self):
        cases = {
            "other org record": (dict(self.PROMOTION, org_id="org-2"), self.evidence(), ("contoso-core", "contoso-api-key"), "blocked", "another organisation"),
            "other org read-back": (self.PROMOTION, self.evidence(org="org-2"), ("contoso-core", "contoso-api-key"), "blocked", "predates"),
            "api added since": (self.PROMOTION, self.evidence(ids=("contoso-core",)), ("contoso-core", "contoso-api-key"), "blocked", "contoso-api-key"),
            "api gone": (self.PROMOTION, self.evidence(), ("contoso-core",), "failed", "no longer in this Dashboard: contoso-api-key"),
        }
        for label, (promotion, evidence, live, status, message) in cases.items():
            with self.subTest(case=label):
                rows = self.run_block(self.bundle(promotion, evidence), live)
                self.assertEqual(rows["SYNC-001"][0], status, rows)
                self.assertIn(message, rows["SYNC-001"][1])
                self.assertEqual(rows["CICD-001"][0], "blocked")

    def test_a_template_without_its_manual_gate_fails_cicd(self):
        root = self.bundle(self.PROMOTION, self.evidence())
        pipeline = root / "ci" / "gitlab" / "tyk-sync.yml"
        pipeline.write_text(pipeline.read_text().replace("when: manual", "when: always"))
        rows = self.run_block(root)
        self.assertEqual(rows["SYNC-001"][0], "passed")
        self.assertEqual(rows["CICD-001"][0], "failed")
        self.assertIn("when: manual", rows["CICD-001"][1])


if __name__ == "__main__":
    unittest.main()
