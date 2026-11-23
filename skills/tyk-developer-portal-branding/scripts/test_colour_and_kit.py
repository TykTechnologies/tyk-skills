"""Tests for colour.py and brand_kit.py (standard library only, no network)."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))

import brand_kit  # noqa: E402
import colour  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


def write_kit(folder: Path, brand: dict, files: dict[str, str | bytes] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in (files or {"logo.svg": SVG}).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    (folder / "brand.json").write_text(json.dumps(brand))
    return folder


def minimal(**extra) -> dict:
    return {"name": "Example Corp", "logo": {"on_light": "logo.svg"}, "colors": {"primary": "#0F766E"}, **extra}


class ColourTests(unittest.TestCase):
    def test_contrast_extremes(self):
        self.assertAlmostEqual(colour.contrast("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(colour.contrast("#777777", "#777777"), 1.0, places=5)

    def test_short_hex_and_normalise(self):
        self.assertEqual(colour.parse("#fff"), (255, 255, 255))
        self.assertEqual(colour.normalise("#0f766e"), "#0F766E")
        with self.assertRaises(ValueError):
            colour.parse("red")
        with self.assertRaises(ValueError):
            colour.parse("#12345")

    def test_on_picks_the_readable_text_colour(self):
        self.assertEqual(colour.on("#0B1F1D"), colour.WHITE)
        self.assertEqual(colour.on("#F59E0B"), colour.INK)

    def test_readable_reaches_the_minimum(self):
        amber = "#F59E0B"  # 2.15:1 on white
        self.assertLess(colour.contrast(amber, "#FFFFFF"), colour.AA_TEXT)
        fixed = colour.readable(amber)
        self.assertGreaterEqual(colour.contrast(fixed, "#FFFFFF"), colour.AA_TEXT)
        self.assertEqual(colour.readable("#0F766E"), "#0F766E")


class KitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_shipped_neutral_kits_load_and_pass_contrast(self):
        for name in ("brand-kit-neutral", "brand-kit-neutral-light"):
            kit = brand_kit.load(ASSETS / name)
            self.assertTrue(kit["placeholder"])
            report = brand_kit.contrast_report(brand_kit.palette(kit))
            failing = [row for row in report if not row["ok"]]
            self.assertEqual(failing, [], f"{name}: {failing}")

    def test_defaults_are_filled(self):
        kit = brand_kit.load(write_kit(self.root / "k", minimal()))
        self.assertEqual(kit["portal_name"], "Example Corp Developer Portal")
        self.assertEqual(kit["theme_name"], "example-corp")
        self.assertEqual(kit["logo"]["height"], 28)
        self.assertEqual(kit["footer_text"], "© Example Corp")

    def test_light_primary_gets_dark_label_and_darker_link(self):
        kit = brand_kit.load(write_kit(self.root / "k", minimal(colors={"primary": "#F59E0B", "nav_bg": "#FFFFFF"})))
        p = brand_kit.palette(kit)
        self.assertEqual(p["on_primary"], colour.INK)
        self.assertGreaterEqual(colour.contrast(p["link"], "#FFFFFF"), colour.AA_TEXT)
        self.assertEqual(p["nav_text"], colour.INK)

    def test_rejections(self):
        cases = {
            "markup in text": (minimal(name="<b>x</b>"), None, "plain text"),
            "bad colour": (minimal(colors={"primary": "teal"}), None, "colour"),
            "unknown colour role": (minimal(colors={"primary": "#000", "brand": "#111"}), None, "not a known colour role"),
            "theme named default": (minimal(theme_name="default"), None, "must not be 'default'"),
            "theme name with space": (minimal(theme_name="Example Corp"), None, "theme_name"),
            "missing logo file": (minimal(logo={"on_light": "nope.svg"}), None, "not in the kit folder"),
            "path escape": (minimal(logo={"on_light": "../logo.svg"}), None, "relative file name"),
            "svg with script": (minimal(), {"logo.svg": "<svg><script>alert(1)</script></svg>"}, "carries script"),
            "svg with handler": (minimal(), {"logo.svg": '<svg onload="x()"></svg>'}, "event handler"),
            "logo height": (minimal(logo={"on_light": "logo.svg", "height": 80}), None, "logo.height"),
            "unknown image slot": (minimal(images={"banner": "logo.svg"}), None, "not a known image slot"),
            "fonts without regular": (minimal(fonts={"family": "Brand", "files": {"700": "logo.svg"}}), None, "fonts.files"),
            "bad portal url": (minimal(portal_url="javascript:alert(1)"), None, "portal_url"),
            "bad footer link": (minimal(footer_links=[{"label": "x", "href": "javascript:x"}]), None, "footer_links"),
        }
        for label, (brand, files, message) in cases.items():
            with self.subTest(label):
                folder = write_kit(self.root / label.replace(" ", "-"), brand, files)
                with self.assertRaises(brand_kit.KitError) as raised:
                    brand_kit.load(folder)
                self.assertIn(message, str(raised.exception))

    def test_file_over_the_per_file_limit_is_refused(self):
        big = b"\x89PNG" + b"0" * brand_kit.MAX_FILE_BYTES
        folder = write_kit(self.root / "big", minimal(images={"hero": "hero.png"}), {"logo.svg": SVG, "hero.png": big})
        with self.assertRaises(brand_kit.KitError) as raised:
            brand_kit.load(folder)
        self.assertIn("at most", str(raised.exception))

    def test_logo_plate_when_only_the_other_variant_exists(self):
        kit = brand_kit.load(write_kit(self.root / "k", minimal()))
        self.assertEqual(brand_kit.logo_for(kit, "#FFFFFF"), ("logo.svg", False))
        self.assertEqual(brand_kit.logo_for(kit, "#0B1F1D"), ("logo.svg", True))

    def test_cli_exit_codes(self):
        folder = write_kit(self.root / "k", minimal())
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            self.assertEqual(brand_kit.main(["brand_kit.py", str(folder)]), 0)
            self.assertEqual(brand_kit.main(["brand_kit.py", str(self.root / "missing")]), 1)
        self.assertEqual(json.loads(out.getvalue())["theme_name"], "example-corp")


if __name__ == "__main__":
    unittest.main()
