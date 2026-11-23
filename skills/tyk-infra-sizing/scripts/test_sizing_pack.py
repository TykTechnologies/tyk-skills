#!/usr/bin/env python3
"""Behaviour, content-hygiene and end-to-end tests for the sizing skill."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sizing_docx_content as C  # noqa: E402
import sizing_engine as E  # noqa: E402
import sizing_markdown as M  # noqa: E402

EXAMPLE = json.loads((HERE.parent / "examples" / "multi-region-hybrid.json").read_text())

# Words about how the engine is built or tested, which must never reach the report.
TOOLING_MARKERS = ("internal tool", "private repositor", "golden", "regression case", "fixture", "sizing_data", "guidance snapshot")


def brief(**overrides) -> dict:
    base = {"cloud": "aws", "traffic": {"peak_rps": 3000}}
    base.update(overrides)
    return base


class BriefValidationTest(unittest.TestCase):
    def assertRejected(self, b: dict, fragment: str) -> None:
        with self.assertRaises(E.BriefError) as ctx:
            E.size_estate(b)
        self.assertIn(fragment, str(ctx.exception))

    def test_traffic_is_required(self):
        self.assertRejected({"cloud": "aws", "traffic": {}}, "peak_rps")

    def test_cloud_is_required(self):
        self.assertRejected({"traffic": {"peak_rps": 10}}, "cloud is required")

    def test_unknown_workload_key(self):
        self.assertRejected(brief(workload={"redis_gb": 4}), "unknown keys")

    def test_enum_and_range(self):
        self.assertRejected(brief(workload={"auth_profile": "oauth"}), "auth_profile")
        self.assertRejected(brief(workload={"request_log_retention_days": 400}), "outside")

    def test_shares_must_sum_to_one(self):
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": [{"traffic_share": 0.7}, {"traffic_share": 0.6}]}]), "sum to 1")
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": [{"traffic_share": 0.2}, {"traffic_share": 0.2}]}]), "sum to 1")
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": [{"traffic_share": 0.9}, {"traffic_share": 0.3}, {}]}]), "exceed")

    def test_types_are_checked(self):
        self.assertRejected({"cloud": "aws", "traffic": {"peak_rps": "3000"}}, "peak_rps")
        self.assertRejected({"cloud": "aws", "traffic": {"monthly_requests": "1e9"}}, "monthly_requests")
        self.assertRejected(brief(environments=["prod"]), "must be an object")
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": ["dp"]}]), "must be an object")
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": [{"traffic_share": "0.5"}, {}]}]), "traffic_share")
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": [{"traffic_share": 1.5}, {"traffic_share": -0.5}]}]), "traffic_share")
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": [{"min_gateways": "x"}]}]), "min_gateways")
        self.assertRejected({"cloud": 5, "traffic": {"peak_rps": 10}}, "cloud")
        self.assertRejected(brief(target=4), "target")
        self.assertRejected(brief(workload={"detailed_analytics": "false"}), "true or false")
        self.assertRejected(brief(scenarios=[{"environment": ["Production"]}]), "unknown environment")
        self.assertRejected(brief(environments=[{"name": "P", "control_plane": {"region": "apac"}}]), "region")
        self.assertRejected(brief(target="fargate", environments=[{"name": "P", "data_planes": [{"gateway_vcpu": 64}]}]), "Fargate")
        self.assertRejected(brief(traffic={"peak_rps": 100, "avg_rps": 500}), "cannot exceed")

    def test_plane_names_must_be_strings(self):
        self.assertRejected(brief(environments=[{"name": "P", "data_planes": [{"name": ["a"]}]}]), "strings")

    def test_production_cannot_borrow_non_production_control_plane(self):
        self.assertRejected(brief(environments=[{"name": "Hub", "posture": "non-production"},
                                                {"name": "Prod", "posture": "production", "control_plane": {"mode": "shared", "shared_with": "Hub"}}]),
                            "cannot borrow")

    def test_fargate_is_aws_only(self):
        self.assertRejected(brief(cloud="gcp", target="fargate"), "fargate")

    def test_shared_control_plane_needs_a_dedicated_host(self):
        self.assertRejected(brief(environments=[{"name": "P"}, {"name": "D", "control_plane": {"mode": "shared", "shared_with": "Nope"}}]), "shared_with")


class EstateBehaviourTest(unittest.TestCase):
    def setUp(self):
        self.result = E.size_estate(copy.deepcopy(EXAMPLE))
        self.prod = self.result["environments"][0]

    def test_traffic_derivation_from_monthly_volume(self):
        t = self.result["traffic"]
        self.assertAlmostEqual(t["avg_rps"], 2_000_000_000 / (30.4 * 86400))
        self.assertAlmostEqual(t["peak_rps"], t["avg_rps"] * 4.5)

    def test_uneven_shares_follow_the_brief(self):
        shares = [p["share"] for p in self.prod["planes"]]
        self.assertEqual(shares, [0.4, 0.3, 0.15, 0.15])
        self.assertAlmostEqual(self.prod["planes"][0]["peak_rps"], self.result["traffic"]["peak_rps"] * 0.4)

    def test_on_prem_plane_is_sized_but_never_priced(self):
        onprem = self.prod["planes"][3]
        self.assertFalse(onprem["priced"])
        self.assertEqual(onprem["costs"], {"k8s": 0, "gateways": 0, "redis": 0, "pump": 0})
        self.assertGreaterEqual(onprem["gateways"]["count"], 2)
        self.assertIn("On-prem DC", self.prod["unpriced_items"])
        self.assertEqual(onprem["gateways"]["rate_source"], "assumed")

    def test_total_is_sum_of_rows(self):
        for env in self.result["environments"]:
            self.assertEqual(env["total_monthly"], sum(r["monthly"] for r in env["cost_rows"]))
        self.assertEqual(self.result["estate_total_monthly"], sum(e["total_monthly"] for e in self.result["environments"]))

    def test_multi_plane_has_mdcb_and_single_plane_does_not(self):
        self.assertTrue(self.prod["control_plane"]["mdcb"])
        single = E.size_estate(brief())["environments"][0]
        self.assertFalse(single["control_plane"]["mdcb"])
        self.assertEqual(single["control_plane"]["cp_redis_gb"], 0)
        self.assertTrue(single["planes"][0]["redis"]["persistent"])
        self.assertFalse(self.prod["planes"][0]["redis"]["persistent"])

    def test_shared_control_plane_costs_nothing(self):
        dev = self.result["environments"][2]
        self.assertEqual(dev["control_plane"]["mode"], "shared")
        self.assertEqual(sum(dev["control_plane"]["costs"].values()), 0)
        self.assertEqual(dev["control_plane"]["components"], [])

    def test_non_production_uses_burstable_tiers(self):
        staging = self.result["environments"][1]
        self.assertTrue(all(p["redis"]["tier_name"].startswith("cache.t4g") for p in staging["planes"]))
        self.assertTrue(all(p["gateways"]["vcpu"] == 2 for p in staging["planes"]))

    def test_analytics_disabled_scenario_drops_pump_and_storage(self):
        scenario = self.result["scenarios"][0]
        variant = scenario["result"]
        self.assertTrue(all(p["pump"] is None for p in variant["planes"]))
        self.assertTrue(all(p["redis"]["parts"]["analytics_buffer"] == 0 for p in variant["planes"]))
        self.assertLess(variant["control_plane"]["db"]["storage_gb"], self.prod["control_plane"]["db"]["storage_gb"])

    def test_throughput_factor_adds_gateways(self):
        base = E.size_estate(brief(traffic={"peak_rps": 20000}))["environments"][0]["planes"][0]["gateways"]["count"]
        derated = E.size_estate(brief(traffic={"peak_rps": 20000}, workload={"throughput_factor": 0.5}))["environments"][0]["planes"][0]["gateways"]["count"]
        self.assertGreater(derated, base)

    def test_n1_holds_with_ha_and_fails_without(self):
        # N+1 makes the N-1 check hold by construction; single-node production with several
        # gateways cannot survive losing one, and must say so.
        ha = E.size_estate(brief(traffic={"peak_rps": 12000}))["environments"][0]
        self.assertTrue(ha["planes"][0]["gateways"]["n1_ok"])
        single = E.size_estate(brief(traffic={"peak_rps": 12000}, environments=[{"name": "P", "posture": "production", "resilience": "single-node"}]))["environments"][0]
        g = single["planes"][0]["gateways"]
        self.assertGreater(g["count"], 1)
        self.assertFalse(g["n1_ok"])
        self.assertTrue(any(n["tone"] == "risk" and n["topic"] == "gateways" for n in single["notes"]))

    def test_single_gateway_production_fails_n1(self):
        result = E.size_estate(brief(environments=[{"name": "P", "posture": "production", "resilience": "single-node"}]))
        env = result["environments"][0]
        self.assertEqual(env["planes"][0]["gateways"]["count"], 1)
        self.assertFalse(env["planes"][0]["gateways"]["n1_ok"])
        self.assertTrue(any(n["tone"] == "risk" and "single gateway" in n["text"] for n in env["notes"]))
        summary = json.dumps(C.executive_summary(result))
        self.assertIn("cannot carry its peak", summary)

    def test_shared_control_plane_is_an_mdcb_relationship(self):
        result = E.size_estate(brief(environments=[{"name": "Prod"}, {"name": "Dev", "posture": "non-production", "traffic": {"peak_rps": 50},
                                                                          "control_plane": {"mode": "shared", "shared_with": "Prod"}}]))
        prod, dev = result["environments"]
        # Prod stays one install with its Dashboard and adds MDCB for Dev's plane.
        self.assertTrue(prod["control_plane"]["mdcb"])
        self.assertIn("Tyk MDCB", prod["control_plane"]["components"])
        self.assertTrue(prod["planes"][0]["redis"]["persistent"])
        self.assertEqual(prod["control_plane"]["cp_redis_gb"], 0)
        self.assertEqual(prod["control_plane"]["db"]["raw_share"], 1)
        self.assertTrue(dev["multi_dc"])
        self.assertFalse(dev["planes"][0]["redis"]["persistent"])
        self.assertGreater(dev["planes"][0]["redis"]["parts"]["emergency_config"], 0)
        self.assertEqual(dev["control_plane"]["db"]["raw_share"], 0)  # hybrid-aggregate by default
        arch = json.dumps(C.architecture_section(result, None))
        self.assertIn("also runs MDCB", arch)
        self.assertNotIn("needs no MDCB", arch)
        state = json.dumps(C.state_section(result))
        self.assertIn("Redis (shared with the Dashboard)", state)
        self.assertNotIn("Redis (control plane)", state)
        alone = E.size_estate(brief())["environments"][0]
        self.assertGreater(prod["control_plane"]["db"]["storage_gb"], alone["control_plane"]["db"]["storage_gb"])

    def test_environment_override_counts_as_supplied(self):
        result = E.size_estate(brief(environments=[{"name": "P", "workload": {"active_api_keys": 5000000}}]))
        env = result["environments"][0]
        self.assertNotIn("active_api_keys", env["defaulted"])
        self.assertNotIn("active_api_keys", {q["input"] for q in result["questions"]})
        self.assertFalse(any("were not supplied" in n["text"] for n in env["notes"]))

    def test_traffic_sources(self):
        peak_only = E.size_estate(brief())["traffic"]
        self.assertEqual((peak_only["peak_source"], peak_only["avg_source"]), ("supplied", "assumed"))
        volume = E.size_estate(brief(traffic={"monthly_requests": 1e9}))["traffic"]
        self.assertEqual((volume["peak_source"], volume["avg_source"]), ("derived", "volume"))
        rows = C.scope_section(E.size_estate(brief()))["blocks"]
        traffic_table = next(b for b in rows if b["type"] == "table")
        labels = {r[0]: r[2] for r in traffic_table["rows"]}
        self.assertEqual(labels["Peak design target"], "Supplied")
        self.assertEqual(labels["Average load"], "Assumed")

    def test_cross_cloud_transfer_is_priced_as_egress(self):
        b = brief(workload={"multi_dc_analytics": "control-plane"}, traffic={"peak_rps": 30000},
                  environments=[{"name": "P", "data_planes": [{"cloud": "aws", "hosts_control_plane": True}, {"cloud": "gcp"}]}])
        env = E.size_estate(b)["environments"][0]
        gcp = env["planes"][1]
        self.assertTrue(gcp["inter_plane_cross_cloud"])
        self.assertAlmostEqual(env["transfer"]["inter_dc_cost"], gcp["inter_plane_gb"] * 0.12)

    def test_redis_beyond_a_single_node_is_sharded(self):
        env = E.size_estate(brief(traffic={"peak_rps": 100000}, workload={"active_api_keys": 5000000, "analytics_buffer_seconds": 3600}))["environments"][0]
        redis = env["planes"][0]["redis"]
        self.assertGreater(redis["gb"], 270)
        self.assertGreater(redis["shards"], 1)
        self.assertIn("cluster mode", redis["tier"])
        self.assertTrue(any("beyond the largest single node" in n["text"] for n in env["notes"]))

    def test_long_outage_tolerance_names_the_gateway_expiry(self):
        result = E.size_estate(brief(traffic={"peak_rps": 20000}, workload={"analytics_buffer_seconds": 600}))
        env = result["environments"][0]
        self.assertTrue(any("storage_expiration_time to at least 600" in n["text"] for n in env["notes"]))
        report = M.render_markdown(C.build_content(result))
        self.assertIn("analytics_config.storage_expiration_time", report)

    def test_pump_is_priced_off_kubernetes(self):
        k8s = E.size_estate(brief())["environments"][0]
        ecs = E.size_estate(brief(target="ecs"))["environments"][0]
        self.assertEqual(k8s["planes"][0]["costs"]["pump"], 0)
        self.assertGreater(ecs["planes"][0]["costs"]["pump"], 0)
        self.assertTrue(any(r["group"] == "pump" for r in ecs["cost_rows"]))

    def test_defaults_become_questions(self):
        result = E.size_estate(brief())
        asked = {q["input"] for q in result["questions"]}
        self.assertIn("peak_multiplier", asked)
        self.assertIn("active_api_keys", asked)
        self.assertNotIn("multi_dc_analytics", asked)  # single plane: not relevant

    def test_non_production_default_load_is_flagged(self):
        result = E.size_estate(brief(environments=[{"name": "P"}, {"name": "Dev"}]))
        self.assertIn("non_production_traffic", {q["input"] for q in result["questions"]})
        self.assertAlmostEqual(result["environments"][1]["traffic"]["peak_rps"], 150)


class ReportContentTest(unittest.TestCase):
    def test_report_text_has_no_tooling_markers(self):
        briefs = [
            copy.deepcopy(EXAMPLE),
            brief(),
            {"cloud": "on-prem", "target": "vms", "traffic": {"peak_rps": 2000}},
            brief(target="fargate", workload={"throughput_factor": 0.6, "analytics": "disabled"}),
            brief(cloud="oci", workload={"storage_engine": "mongodb", "detailed_analytics": True}, environments=[{"name": "P", "data_planes": 3}]),
        ]
        for b in briefs:
            content = C.build_content(E.size_estate(b))
            for text in (json.dumps(content).lower(), M.render_markdown(content).lower()):
                for marker in TOOLING_MARKERS:
                    self.assertNotIn(marker, text, f"report content mentions {marker!r}")

    def test_structure_and_references(self):
        content = C.build_content(E.size_estate(copy.deepcopy(EXAMPLE)), "diagram.png")
        titles = [s["title"] for s in content["sections"]]
        for required in ("Scope and inputs", "Recommended architecture", "Redis sizing", "Analytics store sizing",
                         "Estimated monthly infrastructure cost", "Assumptions and open questions", "Validation plan", "References"):
            self.assertIn(required, titles)
        refs = content["sections"][-1]["blocks"][0]["items"]
        self.assertTrue(all(r["url"].startswith("https://tyk.io/") for r in refs))
        images = [b for s in content["sections"] for b in s.get("blocks", []) if b["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(content["client"], "Example Organisation")

    def test_markdown_report_renders_every_block(self):
        content = C.build_content(E.size_estate(copy.deepcopy(EXAMPLE)), "/elsewhere/production.png")
        report = M.render_markdown(content, Path("/elsewhere"))
        self.assertTrue(report.startswith("# Infrastructure Sizing\n"))
        self.assertIn("Example Organisation", report.splitlines()[2])
        for section in content["sections"]:
            self.assertIn(f"\n## {section['title']}\n", report)
        self.assertIn("## Executive summary", report)
        self.assertIn("| Metric | Value | Basis |", report)
        self.assertIn("![Production Tyk deployment topology](production.png)", report)
        self.assertIn("- [Tyk performance benchmarks](https://tyk.io/performance-benchmarks/)", report)
        self.assertIn("1. ", report.split("### Open questions")[1])
        self.assertIn("1. [Scope and inputs](#scope-and-inputs)", report)  # linked contents
        self.assertIn("> **", report)  # callouts
        self.assertNotIn("{'type'", report)  # no block fell through unrendered

    def test_on_prem_only_estate_has_no_cost_section_but_has_bom(self):
        content = C.build_content(E.size_estate({"cloud": "on-prem", "target": "vms", "traffic": {"peak_rps": 2000}}))
        titles = [s["title"] for s in content["sections"]]
        self.assertNotIn("Estimated monthly infrastructure cost", titles)
        self.assertIn("Appendix: virtual machine bill of materials", titles)


class CommandLineErrorTest(unittest.TestCase):
    def test_malformed_or_missing_brief_is_one_line_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text('{"cloud": "aws", "traffic": {"peak_rps": 10},}')
            out = Path(tmp) / "out"
            for script, path in (("build_sizing_pack.py", bad), ("size_estate.py", bad), ("size_estate.py", Path(tmp) / "missing.json")):
                args = [sys.executable, str(HERE / script), str(path)] + (["--out", str(out)] if script == "build_sizing_pack.py" else [])
                run = subprocess.run(args, capture_output=True, text=True)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertTrue(run.stderr.startswith("brief error:"), run.stderr)
                self.assertNotIn("Traceback", run.stderr)
            nameless = Path(tmp) / "nameless.json"
            nameless.write_text(json.dumps({"cloud": "aws", "traffic": {"peak_rps": "x"}}))
            run = subprocess.run([sys.executable, str(HERE / "build_sizing_pack.py"), str(nameless)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn("peak_rps", run.stderr)
            invalid = Path(tmp) / "invalid.json"
            invalid.write_text(json.dumps({"cloud": "gcp", "target": "ecs", "traffic": {"peak_rps": 10}}))
            run = subprocess.run([sys.executable, str(HERE / "build_sizing_pack.py"), str(invalid), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertFalse(out.exists(), "the output folder was created before the brief was validated")

    def test_single_production_gateway_is_a_failure_in_the_summary(self):
        import size_estate as S
        summary = S.markdown(E.size_estate(brief(environments=[{"name": "P", "resilience": "single-node"}])))
        self.assertIn("**fails**: single instance", summary)


class WorkspaceTest(unittest.TestCase):
    def test_default_sizing_folder_follows_the_workspace_root(self):
        import build_sizing_pack as B
        previous = os.environ.get("TYK_WORKSPACE")
        os.environ["TYK_WORKSPACE"] = "/tmp/workspace-root"
        try:
            self.assertEqual(B.workspace_sizing_dir("Example Organisation"), Path("/tmp/workspace-root/example-organisation/sizing"))
        finally:
            if previous is None:
                os.environ.pop("TYK_WORKSPACE")
            else:
                os.environ["TYK_WORKSPACE"] = previous
        with self.assertRaises(SystemExit):
            B.workspace_sizing_dir("!!!")

    def test_output_inside_the_plugin_is_refused(self):
        import build_sizing_pack as B
        with self.assertRaises(SystemExit):
            B.refuse_inside_repository(B.REPO_ROOT / "project-sizing")
        B.refuse_inside_repository(Path(tempfile.gettempdir()) / "project-sizing")  # outside: allowed


def _has_docx() -> bool:
    try:
        import docx  # noqa: F401
        return True
    except ImportError:
        return False


def _has_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _pack_args(tmp: str) -> list[str]:
    brief_path = Path(tmp) / "brief.json"
    brief_path.write_text(json.dumps(EXAMPLE))
    args = [sys.executable, str(HERE / "build_sizing_pack.py"), str(brief_path), "--out", str(Path(tmp) / "out"), "--name", "pack"]
    if not _has_pillow():
        args.append("--no-diagram")
    return args


class PackWithoutThemeTest(unittest.TestCase):
    def test_markdown_report_is_built_and_docx_is_skipped(self):
        if (HERE.parent.parent / "tyk-docx-theme" / "SKILL.md").is_file():
            self.skipTest("a tyk-docx-theme skill sits beside this one in the repository layout")
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as empty:
            env = dict(os.environ, CLAUDE_CONFIG_DIR=empty, CODEX_HOME=empty)
            args = _pack_args(tmp)
            run = subprocess.run(args, capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            skipped = [line for line in run.stderr.splitlines() if line.startswith("DOCX skipped")]
            self.assertEqual(skipped, ["DOCX skipped: the optional tyk-docx-theme skill is not installed."])
            produced = json.loads(run.stdout)
            self.assertIsNone(produced["docx"])
            out = Path(tmp) / "out"
            self.assertEqual(list(out.glob("*.docx")), [])
            for name in ("sizing-result.json", "sizing-summary.md", "sizing-content.json", "sizing-report.md"):
                self.assertTrue((out / name).is_file(), name)
            report = (out / "sizing-report.md").read_text()
            for heading in ("## Executive summary", "## Recommended architecture", "## Assumptions and open questions", "### Open questions", "## Validation plan"):
                self.assertIn(heading, report)
            if "--no-diagram" not in args:
                self.assertTrue(any(p.get("png") for p in produced["diagrams"]))
                self.assertIn("![Production Tyk deployment topology](production.png)", report)
                self.assertTrue((out / "production.png").is_file())


@unittest.skipUnless(_has_docx(), "python-docx not installed")
class PackWithThemeTest(unittest.TestCase):
    def test_pack_builds_and_validates_docx(self):
        import build_sizing_pack as B
        if B.resolve_sibling("tyk-docx-theme") is None:
            self.skipTest("the optional tyk-docx-theme skill is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            args = _pack_args(tmp)
            run = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            produced = json.loads(run.stdout)
            self.assertEqual(produced["docx_validation"], "passed")
            self.assertTrue(Path(produced["docx"]).is_file())
            self.assertTrue(Path(produced["report"]).is_file())
            if "--no-diagram" not in args:
                import zipfile
                with zipfile.ZipFile(produced["docx"]) as archive:
                    xml = archive.read("word/document.xml").decode()
                    media = [n for n in archive.namelist() if n.startswith("word/media/")]
                self.assertIn('w:orient="landscape"', xml)
                self.assertTrue(any(p.get("png") for p in produced["diagrams"]))
                self.assertGreaterEqual(len(media), 3)  # banner, closing mark, topology


class DiagramFileAllocationTest(unittest.TestCase):
    def test_colliding_names_get_distinct_deterministic_stems(self):
        import build_sizing_pack as B
        names = ["EU West", "EU-West", "eu west", "Production", "eu-west-2"]
        stems = B.allocate_stems(names)
        self.assertEqual(stems, {"EU West": "eu-west", "EU-West": "eu-west-2", "eu west": "eu-west-3",
                                 "Production": "production", "eu-west-2": "eu-west-2-2"})
        self.assertEqual(len(set(stems.values())), len(names))
        self.assertEqual(B.allocate_stems(names), stems)

    @unittest.skipUnless(_has_pillow(), "Pillow not installed")
    def test_each_environment_keeps_its_own_diagram_and_the_report_embeds_the_right_one(self):
        # The production environment is the second to claim the slug "eu-west", so a stem
        # re-derived from its name would point at the other environment's file.
        b = brief(environments=[
            {"name": "EU West", "posture": "non-production", "traffic": {"peak_rps": 111}},
            {"name": "EU-West", "posture": "production", "traffic": {"peak_rps": 2222}},
            {"name": "eu west", "posture": "non-production", "traffic": {"peak_rps": 333}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            brief_path = Path(tmp) / "brief.json"
            brief_path.write_text(json.dumps(b))
            out = Path(tmp) / "out"
            run = subprocess.run([sys.executable, str(HERE / "build_sizing_pack.py"), str(brief_path), "--out", str(out), "--name", "pack"],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            produced = json.loads(run.stdout)
            entries = {e["environment"]: e for e in produced["diagrams"]}
            self.assertEqual(set(entries), {"EU West", "EU-West", "eu west"})
            files = [e["excalidraw"] for e in entries.values()] + [e["png"] for e in entries.values()]
            self.assertEqual(len(set(files)), len(files), "two environments share an output file")
            expected_rps = {"EU West": "111", "EU-West": "2,222", "eu west": "333"}
            for name, entry in entries.items():
                texts = " ".join(el.get("text", "") for el in json.loads(Path(entry["excalidraw"]).read_text())["elements"])
                self.assertIn(f"{name} topology", texts)
                for other in set(entries) - {name}:
                    self.assertNotIn(f"{other} topology", texts)
                self.assertIn(f"Peak {expected_rps[name]} RPS", texts)
            report = Path(produced["report"]).read_text()
            self.assertIn(f"]({Path(entries['EU-West']['png']).name})", report)
            for other in ("EU West", "eu west"):
                self.assertNotIn(Path(entries[other]["png"]).name + ")", report)
            content = json.loads(Path(produced["content"]).read_text())
            images = [blk["path"] for sec in content["sections"] for blk in sec.get("blocks", []) if blk["type"] == "image"]
            self.assertEqual(images, [entries["EU-West"]["png"]])


class ClaimWordingTest(unittest.TestCase):
    BANNED = ("every Dashboard report", "one encrypted", "one compressed, encrypted", "gRPC",
              "Every request makes a Redis round trip", "every request pays a Redis round trip",
              "open benchmark harness", "30% to 60%", "verified price", "reloads changes when the link returns")

    def test_generated_text_does_not_overclaim(self):
        import sizing_diagram
        multi = copy.deepcopy(EXAMPLE)
        multi["traffic"] = {"peak_rps": 80000}
        multi["workload"]["multi_dc_analytics"] = "control-plane"
        texts = []
        for b in (copy.deepcopy(EXAMPLE), multi):
            result = E.size_estate(b)
            content = C.build_content(result)
            texts += [json.dumps(content), M.render_markdown(content), json.dumps(result)]
        for text in texts:
            for banned in self.BANNED:
                self.assertNotIn(banned, text)
        self.assertIn("GraphQL Activity by Graph", texts[0])
        self.assertIn("TLS", texts[0])
        self.assertIn("API consumer traffic still enters", texts[1])
        self.assertIn("What this sizing does not model", texts[0])
        caption = next(line for line in Path(sizing_diagram.__file__).read_text().splitlines() if "over outbound RPC" in line)
        self.assertNotIn("one encrypted", caption)
        self.assertNotIn("one encrypted", Path(sizing_diagram.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
