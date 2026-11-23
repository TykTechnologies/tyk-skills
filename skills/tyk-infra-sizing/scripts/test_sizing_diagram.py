#!/usr/bin/env python3
"""Tests for sizing_diagram: sizing results drawn as Tyk .excalidraw diagrams.

The drawing library lives in the sibling tyk-excalidraw-diagrams skill; every test is
skipped when it cannot be located, so this skill still tests cleanly on its own.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sizing_diagram as SD  # noqa: E402
import sizing_engine as E  # noqa: E402

LIB = SD.load_library()
EXAMPLE = HERE.parent / "examples" / "multi-region-hybrid.json"


def brief(planes, **env):
    return {
        "organisation": "Test Organisation",
        "cloud": "aws",
        "traffic": {"peak_rps": 9000},
        "environments": [{"name": "Production", "posture": "production", "data_planes": planes, **env}],
    }


@unittest.skipIf(LIB is None, "tyk-excalidraw-diagrams skill not found")
class SizingDiagramTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def draw(self, result: dict, environment: str | None = None) -> tuple[dict, list[str]]:
        out = Path(self.tmp.name) / "out.excalidraw"
        written = SD.build_diagram(result, str(out), environment)
        self.assertEqual(Path(written), out)
        doc = json.loads(out.read_text())
        self.assertEqual(doc["type"], "excalidraw")
        texts = [e["text"] for e in doc["elements"] if e["type"] == "text"]
        return doc, texts

    def assert_valid(self, doc: dict) -> None:
        ids = {e["id"] for e in doc["elements"]}
        self.assertEqual(doc["files"], {})
        for element in doc["elements"]:
            self.assertNotEqual(element["type"], "image")
            if element["type"] == "arrow":
                self.assertIsNone(element["roundness"])
            for bound in element.get("boundElements") or []:
                self.assertIn(bound["id"], ids)

    def assert_plane_facts(self, env: dict, texts: list[str]) -> None:
        joined = "\n".join(texts)
        for plane in env["planes"]:
            self.assertIn(plane["label"], texts)
            self.assertIn(f"{plane['gateways']['count']} x Tyk Gateway", joined)

    def components(self, doc: dict) -> set[str]:
        return {(e.get("customData") or {}).get("tykComponent") for e in doc["elements"]} - {None}

    def has_mdcb(self, texts: list[str]) -> bool:
        return "Tyk MDCB" in texts

    def test_one_plane_has_no_mdcb(self) -> None:
        result = E.size_estate(brief(1))
        env = result["environments"][0]
        doc, texts = self.draw(result)
        self.assert_valid(doc)
        self.assert_plane_facts(env, texts)
        self.assertFalse(self.has_mdcb(texts))
        self.assertNotIn("Control-plane Redis", texts)
        self.assertIn("Tyk Dashboard", texts)
        self.assertIn("Test Organisation - Production topology", texts)

    def test_example_four_planes(self) -> None:
        result = E.size_estate(json.loads(EXAMPLE.read_text()))
        env = result["environments"][0]
        self.assertEqual(len(env["planes"]), 4)
        doc, texts = self.draw(result)
        self.assert_valid(doc)
        self.assert_plane_facts(env, texts)
        self.assertTrue(self.has_mdcb(texts))
        self.assertIn("Control-plane Redis", texts)
        self.assertIn(f"{env['control_plane']['cp_redis_gb']} GB", texts)
        # The on-prem plane uses the teal/gold glyph variants.
        self.assertIn("gateway-teal", self.components(doc))
        self.assertIn("redis-gold", self.components(doc))

    def test_eight_planes(self) -> None:
        result = E.size_estate(brief(8))
        env = result["environments"][0]
        doc, texts = self.draw(result)
        self.assert_valid(doc)
        self.assert_plane_facts(env, texts)
        self.assertEqual(texts.count("Tyk MDCB"), 1)
        green = [e for e in doc["elements"] if e["type"] == "arrow" and e["strokeColor"] == LIB.GREEN
                 and e["strokeStyle"] == "dashed"]
        # One outbound control route per data plane, plus the legend sample.
        self.assertEqual(len(green), 8 + 1)

    def test_shared_control_plane(self) -> None:
        result = E.size_estate(json.loads(EXAMPLE.read_text()))
        env = next(e for e in result["environments"] if e["name"] == "Development")
        doc, texts = self.draw(result, "Development")
        self.assert_valid(doc)
        self.assert_plane_facts(env, texts)
        self.assertFalse(self.has_mdcb(texts))
        self.assertNotIn("Tyk Dashboard", texts)
        self.assertIn("Shared control plane", texts)
        self.assertTrue(any("Uses the Staging control plane" in t for t in texts))

    def test_analytics_disabled_draws_no_pump(self) -> None:
        result = E.size_estate({**brief(2), "workload": {"analytics": "disabled"}})
        doc, texts = self.draw(result)
        self.assert_valid(doc)
        self.assertNotIn("Tyk Pump", texts)
        self.assertNotIn("tyk-pump", self.components(doc))

    def test_n1_shortfall_note_in_production(self) -> None:
        result = E.size_estate(brief(1, resilience="single-node"))
        self.assertFalse(result["environments"][0]["planes"][0]["gateways"]["n1_ok"])
        _, texts = self.draw(result)
        self.assertIn("N-1 capacity shortfall", texts)

    def test_default_environment_and_unknown_name(self) -> None:
        result = E.size_estate(json.loads(EXAMPLE.read_text()))
        self.assertEqual(SD.select_environment(result, None)["name"], "Production")
        with self.assertRaises(ValueError):
            SD.select_environment(result, "Nope")


if __name__ == "__main__":
    unittest.main()
