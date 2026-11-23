#!/usr/bin/env python3
"""Tests for the Tyk infrastructure sizing engine.

RegressionCaseTest replays fixtures/regression_cases.json (sizing input -> every number the
engine produced after the last reviewed change). It is a compatibility check: it catches an
unintended change, but it cannot show a number is right. Recapture the cases only after
checking every changed number against its source, and say why in the commit.

The other classes check the method independently: formulas written out here rather than
imported, boundaries, and the review findings (SZ-01 to SZ-14) the engine was changed for.
"""

from __future__ import annotations

import ast
import json
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sizing_data as D  # noqa: E402
import sizing_engine as E  # noqa: E402

CASES = json.loads((HERE / "fixtures" / "regression_cases.json").read_text())

WORKLOAD_KEYS = set(E.WORKLOAD_DEFAULTS)
GIB = 1024 * 1024  # KiB per GiB


def brief_from_case_input(inp: dict) -> dict:
    """Translate one regression-case input into an equivalent single-environment brief."""
    traffic = {"peak_rps": inp["peak_rps"]}
    if "avg_rps" in inp:
        traffic["avg_rps"] = inp["avg_rps"]
    workload = {k: v for k, v in inp.items() if k in WORKLOAD_KEYS}
    target = {"eks": "kubernetes"}.get(inp.get("deployment_target", "eks"), inp.get("deployment_target", "eks"))
    return {
        "cloud": inp["cloud"],
        "region": inp.get("region", "us"),
        "target": target,
        "traffic": traffic,
        "workload": workload,
        "environments": [{
            "name": "Env",
            "posture": inp.get("environment", "production"),
            "resilience": inp.get("resilience", "ha"),
            "data_planes": inp.get("data_planes", 1),
        }],
    }


def brief(**overrides) -> dict:
    base = {"cloud": "aws", "traffic": {"peak_rps": 3000}}
    base.update(overrides)
    return base


def env0(b: dict) -> dict:
    return E.size_estate(b)["environments"][0]


class RegressionCaseTest(unittest.TestCase):
    def test_every_regression_case_matches(self):
        for index, case in enumerate(CASES):
            exp = case["expected"]
            with self.subTest(case=index, input=case["input"]):
                result = E.size_estate(brief_from_case_input(case["input"]))
                env = result["environments"][0]
                planes = env["planes"]
                cp = env["control_plane"]
                first = planes[0]
                exact = {
                    "avgRps": env["traffic"]["avg_rps"],
                    "gwPerPlane": first["gateways"]["count"],
                    "gwTotal": sum(p["gateways"]["count"] for p in planes),
                    "connPool": first["gateways"]["conn_pool"],
                    "redisGB": first["redis"]["gb"],
                    "redisCost": sum(p["costs"]["redis"] for p in planes),
                    "redisTierNote": first["redis"]["tier"],
                    "cpRedisGB": cp["cp_redis_gb"],
                    "cpRedisCost": cp["costs"]["cp_redis"],
                    "pumpInstances": first["pump"]["instances"],
                    "dbStorageGB": cp["db"]["storage_gb"],
                    "dbRamGB": cp["db"]["ram_gb"],
                    "dbCost": cp["costs"]["analytics_store"],
                    "dbLine": cp["db"]["line"],
                    "k8sFee": sum(p["costs"]["k8s"] for p in planes) + cp["costs"]["k8s"],
                    "gwCost": sum(p["costs"]["gateways"] for p in planes),
                    "cpCost": cp["costs"]["control_plane"],
                    "portalCost": cp["costs"]["portal"],
                    "portalDbCost": cp["costs"]["portal_db"],
                    "inetCost": E.js_round(env["transfer"]["internet_cost"]),
                    "clientEgressCost": E.js_round(env["transfer"]["internet_cost"] + env["transfer"]["private_cost"]),
                    "interDcCost": E.js_round(env["transfer"]["inter_dc_cost"]),
                    # Pump priced off Kubernetes is outside the cases' cost basis.
                    "total": env["total_monthly"] - sum(p["costs"]["pump"] for p in planes),
                    "sens30": first["redis"]["sensitivity"]["30s"],
                    "sens300": first["redis"]["sensitivity"]["5min"],
                    "sens3600": first["redis"]["sensitivity"]["1h"],
                    "n1": first["gateways"]["n1_capacity_rps"],
                }
                for key, value in exact.items():
                    self.assertEqual(value, exp[key], f"{key}: engine {value!r} != expected {exp[key]!r}")
                close = {
                    "nonAggGB": cp["db"]["nonagg_gb"],
                    "aggGB": cp["db"]["agg_gb"],
                    "nonAggPerDayGB": cp["db"]["nonagg_per_day_gb"],
                    "clientEgressGB": env["transfer"]["client_egress_gb"],
                    "inetGB": env["transfer"]["internet_gb"],
                    "privateGB": env["transfer"]["private_gb"],
                    "interDcGB": env["transfer"]["inter_dc_gb"],
                    "analyticsBuffer": first["redis"]["parts"]["analytics_buffer"],
                    "keysGB": first["redis"]["parts"]["keys"],
                    "cacheGB": first["redis"]["parts"]["cache"],
                    "emergencyGB": first["redis"]["parts"]["emergency_config"],
                }
                for key, value in close.items():
                    self.assertTrue(math.isclose(value, exp[key], rel_tol=1e-9, abs_tol=1e-9), f"{key}: engine {value} != expected {exp[key]}")
                # Equal shares: every plane must match the first, not just the first alone.
                for other in planes[1:]:
                    self.assertEqual(other["gateways"]["count"], first["gateways"]["count"])
                    self.assertEqual(other["redis"]["gb"], first["redis"]["gb"])
                    self.assertEqual(other["redis"]["tier"], first["redis"]["tier"])


class FormulaTest(unittest.TestCase):
    """The core formulas, written out independently of the engine's helpers."""

    def test_gateway_count_and_n1(self):
        g = env0(brief(traffic={"peak_rps": 20000}))["planes"][0]["gateways"]
        rate = 3420  # AWS full middleware, published
        base = math.ceil(20000 / (rate * 0.4) / 4)
        self.assertEqual(g["count"], base + 1)
        self.assertEqual(g["n1_capacity_rps"], math.floor(base * 4 * rate * 0.4 + 0.5))

    def test_connection_pool_has_the_published_floor(self):
        g = env0(brief(traffic={"peak_rps": 200000}, workload={"upstream_latency_ms": 400}))["planes"][0]["gateways"]
        per_gateway = 200000 / g["count"]
        self.assertEqual(g["conn_pool"], max(500, math.ceil(per_gateway / (1000 / 400))))
        self.assertEqual(env0(brief())["planes"][0]["gateways"]["conn_pool"], 500)

    def test_basic_record_uses_the_published_two_kilobytes(self):
        p = env0(brief())["planes"][0]
        self.assertEqual(p["redis"]["record_kb"], 2)
        self.assertAlmostEqual(p["redis"]["parts"]["analytics_buffer"], 3000 * 2 * 30 / GIB)


class FindingSZ01DetailedRecordTest(unittest.TestCase):
    def test_detailed_record_covers_base64_encoding(self):
        """2 KB request and 8 KB response: the bodies alone base64 to 4*ceil(n/3) bytes each."""
        w = {"detailed_analytics": True, "avg_request_kb": 2, "avg_response_kb": 8}
        record_bytes = E.analytics_record_kb(w) * 1024
        bodies = 4 * math.ceil(2048 / 3) + 4 * math.ceil(8192 / 3)
        self.assertEqual(bodies, 13656)
        self.assertGreaterEqual(record_bytes, bodies + 1024)  # plus a basic record
        # On Gateway 5.16.0 a detailed record for this request and response is 15,387 bytes.
        self.assertGreaterEqual(record_bytes, 15387)

    def test_detail_reaches_buffer_store_and_transfer(self):
        b = brief(workload={"detailed_analytics": True, "multi_dc_analytics": "control-plane"},
                  environments=[{"name": "P", "data_planes": 2}])
        env = env0(b)
        record_kb = 2 + 4 / 3 * (2 + 8 + 0.5)
        p = env["planes"][1]
        self.assertAlmostEqual(p["redis"]["record_kb"], record_kb)
        self.assertAlmostEqual(p["inter_plane_gb"], p["monthly_requests"] * record_kb / GIB)
        self.assertAlmostEqual(env["control_plane"]["db"]["nonagg_per_day_gb"], 1000 * 86400 * (2 + 4 / 3 * 10.5) / GIB)


class FindingSZ02RemoteControlPlaneTest(unittest.TestCase):
    def test_single_plane_with_a_remote_control_plane_is_distributed(self):
        env = env0(brief(environments=[{"name": "P", "control_plane": {"cloud": "gcp", "region": "eu"}}]))
        cp = env["control_plane"]
        self.assertTrue(env["multi_dc"])
        self.assertTrue(cp["mdcb"] and cp["remote"])
        self.assertGreater(cp["cp_redis_gb"], 0)
        self.assertFalse(env["planes"][0]["hosts_control_plane"])
        self.assertFalse(env["planes"][0]["redis"]["persistent"])
        self.assertGreater(env["transfer"]["inter_dc_gb"], 0)

    def test_every_plane_ships_to_a_third_cloud_control_plane(self):
        b = brief(traffic={"avg_rps": 2000, "peak_rps": 6000},
                  workload={"multi_dc_analytics": "control-plane"},
                  environments=[{"name": "P", "control_plane": {"cloud": "azure", "region": "us"},
                                 "data_planes": [{"cloud": "aws", "traffic_share": 0.6}, {"cloud": "gcp", "traffic_share": 0.4}]}])
        env = env0(b)
        monthly = 2000 * 86400 * 30.4
        self.assertAlmostEqual(env["transfer"]["inter_dc_gb"], monthly * 2 / GIB)
        aws, gcp = env["planes"]
        self.assertAlmostEqual(env["transfer"]["inter_dc_cost"], aws["inter_plane_gb"] * 0.09 + gcp["inter_plane_gb"] * 0.12)

    def test_remote_control_plane_does_not_inherit_an_aws_only_target(self):
        cp = env0(brief(target="fargate", environments=[{"name": "P", "control_plane": {"cloud": "gcp"}}]))["control_plane"]
        self.assertEqual((cp["cloud"], cp["target"], cp["remote"]), ("gcp", "kubernetes", True))

    def test_placement_matching_a_later_plane_puts_the_control_plane_beside_it(self):
        env = env0(brief(environments=[{"name": "P", "control_plane": {"region": "eu"},
                                        "data_planes": [{"name": "us"}, {"name": "eu", "region": "eu"}]}]))
        self.assertFalse(env["control_plane"]["remote"])
        self.assertTrue(env["planes"][1]["hosts_control_plane"])
        self.assertNotIn("inter_plane_gb", env["planes"][1])

    def test_same_cloud_other_region_is_remote_and_conflicts_are_refused(self):
        env = env0(brief(environments=[{"name": "P", "control_plane": {"region": "eu"}}]))
        self.assertTrue(env["control_plane"]["remote"])
        local = env0(brief(environments=[{"name": "P", "control_plane": {"target": "vms"}}]))
        self.assertFalse(local["control_plane"]["remote"])
        self.assertFalse(local["multi_dc"])
        with self.assertRaises(E.BriefError):
            E.size_estate(brief(environments=[{"name": "P", "control_plane": {"cloud": "gcp"},
                                               "data_planes": [{"hosts_control_plane": True}]}]))


class FindingSZ03AnalyticsStoreFeasibilityTest(unittest.TestCase):
    def mongo(self, avg: float, days: int) -> dict:
        return env0(brief(traffic={"avg_rps": avg, "peak_rps": avg * 3},
                          workload={"storage_engine": "mongodb", "request_log_retention_days": days, "active_api_keys": 1000}))

    def test_ram_follows_the_published_rule(self):
        env = self.mongo(10, 7)
        db = env["control_plane"]["db"]
        docs = 10 * 86400 * 7
        expected = max(4, math.ceil(2 * (docs * 0.3 / GIB + db["agg_gb"])))
        self.assertEqual(db["ram_gb"], expected)
        self.assertTrue(db["feasible"])
        tier = next(t for t in D.ATLAS_TIERS if t[1] >= expected)
        self.assertIn(tier[0], db["line"])

    def test_catalogue_exhaustion_is_infeasible_not_the_largest_tier(self):
        for avg, days in ((639, 7), (100000, 90)):
            env = self.mongo(avg, days)
            db = env["control_plane"]["db"]
            self.assertGreater(db["ram_gb"], 64)
            self.assertFalse(db["feasible"])
            self.assertEqual(env["control_plane"]["costs"]["analytics_store"], 0)
            self.assertNotIn("M60 (", db["line"])
            self.assertFalse(env["fully_priced"])
            self.assertTrue(any(r for r in env["unpriced_items"] if "analytics store" in r))
            self.assertFalse(any(r["item"] == "Analytics store" for r in env["cost_rows"]))

    def test_unfit_store_is_flagged_even_when_the_host_records_no_analytics(self):
        b = {"cloud": "aws", "traffic": {"peak_rps": 3000},
             "workload": {"storage_engine": "mongodb", "multi_dc_analytics": "control-plane"},
             "environments": [{"name": "Prod", "workload": {"analytics": "disabled"}},
                              {"name": "Stage", "posture": "non-production", "traffic": {"avg_rps": 20000, "peak_rps": 60000},
                               "control_plane": {"mode": "shared", "shared_with": "Prod"}}]}
        prod = E.size_estate(b)["environments"][0]
        self.assertFalse(prod["control_plane"]["db"]["feasible"])
        self.assertTrue(any(n["tone"] == "risk" and "No listed MongoDB Atlas tier fits" in n["text"] for n in prod["notes"]))

    def test_postgresql_compute_limit_is_stated(self):
        env = env0(brief())
        self.assertTrue(any("PostgreSQL compute is not modelled" in n["text"] for n in env["notes"]))


class FindingSZ04PumpBacklogTest(unittest.TestCase):
    def test_without_a_peak_duration_pump_keeps_up_with_the_peak(self):
        pump = env0(brief(traffic={"peak_rps": 45000, "avg_rps": 1000}))["planes"][0]["pump"]
        self.assertEqual(pump["basis"], "peak")
        self.assertGreaterEqual(pump["capacity_records_per_s"], 45000)
        self.assertEqual(pump["backlog_records"], 0)

    def test_with_a_peak_duration_the_backlog_fits_the_buffer_and_drains(self):
        w = {"peak_duration_seconds": 600, "analytics_buffer_seconds": 300}
        pump = env0(brief(traffic={"peak_rps": 45000, "avg_rps": 1000}, workload=w))["planes"][0]["pump"]
        capacity = pump["instances"] * D.PUMP_RECORDS_PER_S_PER_INSTANCE
        backlog = max(0, 45000 - capacity) * 600
        self.assertEqual(pump["backlog_records"], backlog)
        self.assertLessEqual(backlog, 45000 * 300)
        # One instance fewer would overflow the buffer.
        self.assertGreater(max(0, 45000 - (capacity - D.PUMP_RECORDS_PER_S_PER_INSTANCE)) * 600, 45000 * 300)
        self.assertGreater(capacity, 1000)
        self.assertAlmostEqual(pump["drain_seconds"], backlog / (capacity - 1000))

    def test_buffer_longer_than_the_gateway_expiry_is_flagged(self):
        env = env0(brief(workload={"analytics_buffer_seconds": 300}))
        self.assertTrue(any("storage_expiration_time to at least 300" in n["text"] for n in env["notes"]))
        self.assertFalse(any("storage_expiration_time" in n["text"] for n in env0(brief())["notes"]))


class FindingSZ05RedisSensitivityTest(unittest.TestCase):
    def test_sensitivity_at_the_configured_buffer_equals_the_base_cost(self):
        for peak, seconds, key in ((3000, 30, "30s"), (20000, 300, "5min"), (100000, 3600, "1h")):
            with self.subTest(peak=peak, seconds=seconds):
                p = env0(brief(traffic={"peak_rps": peak}, workload={"analytics_buffer_seconds": seconds}))["planes"][0]
                self.assertEqual(p["redis"]["sensitivity"][key], p["costs"]["redis"])

    def test_sharded_sensitivity_counts_every_shard(self):
        p = env0(brief(traffic={"peak_rps": 100000}, workload={"analytics_buffer_seconds": 3600}))["planes"][0]
        self.assertGreater(p["redis"]["shards"], 1)
        largest = D.AWS_REDIS_NODES[-1][1]
        self.assertGreater(p["redis"]["sensitivity"]["1h"], 3 * largest)

    def test_topology_steps_at_the_shard_boundary(self):
        for need, shards in ((270, 1), (271, 2), (540, 2), (541, 3)):
            plan = E.redis_plan(need, "aws", True, 2, 1)
            self.assertEqual(plan["shards"], shards)
            self.assertLessEqual(need / shards, D.AWS_REDIS_NODES[-1][0] * D.AWS_REDIS_USABLE)


class FindingSZ06SharedScenarioTest(unittest.TestCase):
    def estate(self, guest_workload: dict) -> dict:
        return {"cloud": "aws", "traffic": {"peak_rps": 3000},
                "environments": [{"name": "Prod"},
                                 {"name": "Guest", "posture": "non-production", "traffic": {"peak_rps": 300},
                                  "workload": guest_workload, "control_plane": {"mode": "shared", "shared_with": "Prod"}}]}

    def test_scenario_matches_a_full_estate_rerun(self):
        b = self.estate({"active_api_keys": 1000000})
        b["scenarios"] = [{"name": "No guest analytics", "environment": "Guest", "overrides": {"analytics": "disabled"}}]
        result = E.size_estate(b)
        rerun = E.size_estate(self.estate({"active_api_keys": 1000000, "analytics": "disabled"}))
        comparison = {c["metric"]: c for c in result["scenarios"][0]["comparison"]}
        estate_row = comparison["Monthly cost, whole estate (USD)"]
        self.assertEqual(estate_row["variant"], rerun["estate_total_monthly"])
        self.assertEqual(estate_row["delta"], rerun["estate_total_monthly"] - result["estate_total_monthly"])
        store_row = comparison["Analytics store (GB), Prod"]
        self.assertLess(store_row["variant"], store_row["base"])

    def test_host_counts_guest_storage_ram_and_keys_once(self):
        b = self.estate({"active_api_keys": 1000000})
        b["workload"] = {"storage_engine": "mongodb"}
        prod, guest = E.size_estate(b)["environments"]
        alone = env0({"cloud": "aws", "traffic": {"peak_rps": 3000}, "workload": {"storage_engine": "mongodb"}})
        db, solo = prod["control_plane"]["db"], alone["control_plane"]["db"]
        self.assertAlmostEqual(db["guest_gb"], guest["control_plane"]["db"]["nonagg_gb"] + guest["control_plane"]["db"]["agg_gb"])
        self.assertEqual(db["storage_gb"], max(10, math.ceil((solo["nonagg_gb"] + solo["agg_gb"] + solo["config_gb"] + db["guest_gb"]) / 0.7)))
        self.assertGreater(db["ram_gb"], solo["ram_gb"])
        self.assertEqual(sum(guest["control_plane"]["costs"].values()), 0)
        # The single-plane host's Redis is the control plane's, so it holds the guest's keys.
        self.assertAlmostEqual(prod["planes"][0]["redis"]["parts"]["keys"], (10000 + 1000000) / GIB)


class FindingSZ07ControlPlanePlatformTest(unittest.TestCase):
    def test_fargate_control_plane_is_priced_as_tasks(self):
        cp = env0(brief(target="fargate"))["control_plane"]
        task = 4 * D.FARGATE_VCPU_MONTH + 8 * D.FARGATE_GB_MONTH
        self.assertEqual(cp["costs"]["control_plane"], E.js_round(2 * task))
        self.assertIn("task", cp["node_shape"])

    def test_control_plane_only_cluster_pays_its_own_fee(self):
        env = env0(brief(target="vms", environments=[{"name": "P", "control_plane": {"target": "kubernetes", "region": "eu"}}]))
        self.assertEqual(env["control_plane"]["costs"]["k8s"], 73)
        self.assertTrue(any(r["item"] == "Managed Kubernetes" for r in env["cost_rows"]))
        beside = env0(brief())
        self.assertEqual(beside["control_plane"]["costs"]["k8s"], 0)

    def test_aws_control_plane_redis_uses_the_node_ladder(self):
        cp = env0(brief(environments=[{"name": "P", "data_planes": 2}]))["control_plane"]
        self.assertEqual(cp["cp_redis_gb"], 1)
        self.assertEqual(cp["costs"]["cp_redis"], 2 * D.AWS_REDIS_NODES[0][1])


class FindingSZ13InputContractTest(unittest.TestCase):
    def rejected(self, b: dict, fragment: str) -> None:
        with self.assertRaises(E.BriefError) as ctx:
            E.size_estate(b)
        self.assertIn(fragment, str(ctx.exception))

    def test_aws_only_targets(self):
        self.rejected(brief(cloud="gcp", target="ecs"), "AWS target")
        self.rejected(brief(cloud="on-prem", target="fargate"), "AWS target")

    def test_conflicting_or_malformed_traffic(self):
        self.rejected(brief(traffic={"avg_rps": 100, "peak_rps": 500, "monthly_requests": "bad"}), "monthly_requests")
        self.rejected(brief(traffic={"avg_rps": 100, "peak_rps": 500, "monthly_requests": 1}), "disagree")
        self.rejected(brief(traffic={"daily_requests": 8640000, "monthly_requests": 1e9}), "disagree")
        self.rejected(brief(traffic={"avg_rps": 100, "peak_rps": 500, "peak_multiplier": 3}), "peak_multiplier")
        agreeing = E.size_estate(brief(traffic={"avg_rps": 100, "daily_requests": 8640000}))
        self.assertEqual(agreeing["traffic"]["avg_rps"], 100)

    def test_wrong_types_are_not_defaulted(self):
        self.rejected(brief(workload=[]), "workload must be an object")
        self.rejected(brief(environments=[]), "environments is empty")
        self.rejected(brief(environments=[{"name": "P", "workload": []}]), "must be an object")
        self.rejected(brief(environments=[{"name": "P", "control_plane": []}]), "must be an object")
        self.rejected(brief(scenarios=[{"overrides": {}}]), "overrides is empty")
        self.rejected(brief(context=["x"]), "context must be an object")

    def test_unknown_keys_at_every_level(self):
        self.rejected(brief(traffics={"peak_rps": 1}), "brief: unknown keys")
        self.rejected(brief(traffic={"peak_rps": 1, "peak_rsp": 2}), "traffic: unknown keys")
        self.rejected(brief(environments=[{"name": "P", "planes": 2}]), "unknown keys")
        self.rejected(brief(environments=[{"name": "P", "data_planes": [{"share": 1}]}]), "unknown keys")
        self.rejected(brief(environments=[{"name": "P", "control_plane": {"cloud_name": "aws"}}]), "unknown keys")
        self.rejected(brief(scenarios=[{"overrides": {"analytics": "disabled"}, "env": "P"}]), "unknown keys")

    def test_supplied_multiplier_with_peak_only_sets_the_average(self):
        result = E.size_estate(brief(traffic={"peak_rps": 1000, "peak_multiplier": 5}))
        self.assertEqual(result["traffic"]["avg_rps"], 200)
        self.assertNotIn("peak_multiplier", result["defaulted"])
        self.rejected(brief(traffic={"peak_rps": 1000, "avg_rps": 0}), "positive")

    def test_context_and_counts_are_typed(self):
        self.rejected(brief(context={"drivers": 5}), "context.drivers")
        self.rejected(brief(context={"drivers": "abc"}), "context.drivers")
        self.rejected(brief(context={"summary": ["x"]}), "context.summary")
        self.rejected(brief(workload={"active_api_keys": 1.5}), "whole number")

    def test_shared_control_plane_takes_no_placement(self):
        self.rejected(brief(environments=[{"name": "P"}, {"name": "D", "control_plane": {"mode": "shared", "shared_with": "P", "cloud": "gcp"}}]),
                      "shared control plane takes")


class FindingSZ14ProvenanceAndDefaultsTest(unittest.TestCase):
    def test_every_workload_default_becomes_a_question(self):
        self.assertEqual(set(E.WORKLOAD_DEFAULTS) - set(E.QUESTION_FOR_DEFAULT), set())
        asked = {q["input"] for q in E.size_estate(brief())["questions"]}
        for key in ("aggregate_retention_days", "developer_portal", "policies", "throughput_factor", "peak_duration_seconds"):
            self.assertIn(key, asked)

    def test_environment_traffic_keeps_its_own_defaults(self):
        result = E.size_estate(brief(traffic={"peak_rps": 3000, "avg_rps": 1000},
                                     environments=[{"name": "P", "traffic": {"peak_rps": 500}}]))
        self.assertIn("peak_multiplier", result["environments"][0]["defaulted"])
        explicit = E.size_estate(brief(traffic={"peak_rps": 3000},
                                       environments=[{"name": "P", "traffic": {"peak_rps": 500, "avg_rps": 100}}]))
        self.assertNotIn("peak_multiplier", explicit["environments"][0]["defaulted"])

    def test_purchased_node_and_requested_memory_are_distinct(self):
        g = env0(brief(cloud="gcp"))["planes"][0]["gateways"]
        self.assertEqual((g["node_ram_gb"], g["ram_gb"]), (16, 8))
        self.assertIn("16 GB", g["shape"])

    def test_burstable_non_production_uses_the_burstable_rate(self):
        b = brief(cloud="gcp", environments=[{"name": "P"}, {"name": "Dev", "posture": "non-production", "traffic": {"peak_rps": 800}}])
        dev = E.size_estate(b)["environments"][1]["planes"][0]["gateways"]
        self.assertEqual(dev["benchmark_rps_per_vcpu"], D.RPS_PER_VCPU_BURSTABLE["gcp"]["full-middleware"])
        self.assertEqual(dev["count"], math.ceil(800 / (1470 * 0.4) / 2))

    def test_edge_local_sinks_are_reported_unpriced(self):
        env = env0(brief(workload={"multi_dc_analytics": "edge-local"}, environments=[{"name": "P", "data_planes": 2}]))
        self.assertFalse(env["fully_priced"])
        self.assertIn("data-plane analytics sinks (edge-local mode)", env["unpriced_items"])

    def test_every_constant_the_engine_uses_has_a_source(self):
        used = set()
        for name in ("sizing_engine.py", "sizing_docx_content.py"):
            tree = ast.parse((HERE / name).read_text())
            used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "D"}
        structural = {"AUTH_PROFILES", "CLOUD_KEYS", "SELF_HOSTED_CLOUDS", "DEPLOYMENT_TARGETS", "AWS_ONLY_TARGETS",
                      "TARGET_ALIASES", "KB", "ENGINE_VERSION", "GUIDANCE_SNAPSHOT", "PRICE_SNAPSHOT", "PRICE_RECHECK",
                      "SOURCES", "CONTROL_PLANE_NODE_RAM_GB", "BENCHMARKS_URL", "REDIS_DOCS", "PERSISTENT_DB_DOCS",
                      "MONGO_DOCS", "PLANNING_DOCS", "GATEWAY_CONFIG_DOCS"}
        missing = sorted(used - structural - set(D.SOURCES))
        self.assertEqual(missing, [], "constants without a SOURCES entry")
        kinds = {kind for kind, _ in D.SOURCES.values()}
        self.assertLessEqual(kinds, {"published", "assumption", "price"})


if __name__ == "__main__":
    unittest.main()
