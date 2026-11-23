import copy
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sync_drift  # noqa: E402

API = {
    "oas": {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1.0.0"},
        "paths": {},
        "x-tyk-api-gateway": {
            "info": {"id": "orders", "name": "Orders", "state": {"active": True}},
            "server": {"listenPath": {"value": "/orders/", "strip": True}},
            "upstream": {"url": "http://example.com"},
        },
    }
}
POLICY = {"id": "gold", "name": "Gold", "org_id": "org-source", "rate": 60, "per": 60}


def as_dashboard(api, policy):
    """Return the two documents as a Dashboard dump would hold them."""
    api = copy.deepcopy(api)
    ext = api["oas"]["x-tyk-api-gateway"]
    ext["info"]["dbId"] = "6afc00000000000000000001"
    ext["info"]["orgId"] = "org-target"
    api["oas"]["servers"] = [{"url": "http://gateway:8080/orders"}]
    api["oas"]["components"] = {}
    api.update({"hook_references": [], "is_site": False, "sort_by": 0, "user_owners": []})
    policy = copy.deepcopy(policy)
    policy.update({"_id": "6afc00000000000000000002", "org_id": "org-target",
                   "last_updated": "1794924766", "tags": [], "is_inactive": False, "quota_renewal_rate": 0})
    return api, policy


class SyncDriftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = pathlib.Path(self.tmp.name) / "source"
        self.dashboard = pathlib.Path(self.tmp.name) / "dashboard"
        for directory in (self.source, self.dashboard):
            directory.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, directory, api=None, policy=None, extra_api=None):
        index = {"type": "apidef", "files": [], "policies": []}
        if api is not None:
            (directory / "oas-orders.json").write_text(json.dumps(api), encoding="utf-8")
            index["files"].append({"file": "oas-orders.json"})
        if extra_api is not None:
            (directory / "oas-extra.json").write_text(json.dumps(extra_api), encoding="utf-8")
            index["files"].append({"file": "oas-extra.json"})
        if policy is not None:
            (directory / "policy-gold.json").write_text(json.dumps(policy), encoding="utf-8")
            index["policies"].append({"file": "policy-gold.json"})
        (directory / ".tyk.json").write_text(json.dumps(index), encoding="utf-8")

    def run_main(self, *flags):
        return sync_drift.main([str(self.source), str(self.dashboard), *flags])

    def test_dashboard_owned_fields_are_not_drift(self):
        self.write(self.source, API, POLICY)
        self.write(self.dashboard, *as_dashboard(API, POLICY))
        self.assertEqual(self.run_main(), 0)

    def test_changed_value_is_drift(self):
        api, policy = as_dashboard(API, POLICY)
        policy["rate"] = 999
        api["oas"]["x-tyk-api-gateway"]["upstream"]["url"] = "http://drifted.example.com"
        self.write(self.source, API, POLICY)
        self.write(self.dashboard, api, policy)
        lines = sync_drift.compare(
            sync_drift.load_directory(self.source), sync_drift.load_directory(self.dashboard)
        )
        self.assertIn("CHANGED  api:orders", lines)
        self.assertIn("CHANGED  policy:gold", lines)
        self.assertTrue(any(".rate: source 60 != Dashboard 999" in line for line in lines))
        self.assertEqual(self.run_main(), 1)

    def test_true_to_false_is_drift(self):
        api, policy = as_dashboard(API, POLICY)
        api["oas"]["x-tyk-api-gateway"]["server"]["listenPath"]["strip"] = False
        self.write(self.source, API, POLICY)
        self.write(self.dashboard, api, policy)
        self.assertEqual(self.run_main(), 1)

    def test_missing_and_extra_objects(self):
        extra = copy.deepcopy(API)
        extra["oas"]["x-tyk-api-gateway"]["info"]["id"] = "other-team"
        self.write(self.source, API, POLICY)
        self.write(self.dashboard, as_dashboard(API, POLICY)[0], None, extra_api=extra)
        lines = sync_drift.compare(
            sync_drift.load_directory(self.source), sync_drift.load_directory(self.dashboard)
        )
        self.assertIn("MISSING  policy:gold: in the source, not in the Dashboard", lines)
        self.assertIn("EXTRA    api:other-team: in the Dashboard, not in the source", lines)

    def test_ignore_extra(self):
        extra = copy.deepcopy(API)
        extra["oas"]["x-tyk-api-gateway"]["info"]["id"] = "other-team"
        api, policy = as_dashboard(API, POLICY)
        self.write(self.source, API, POLICY)
        self.write(self.dashboard, api, policy, extra_api=extra)
        self.assertEqual(self.run_main(), 1)
        self.assertEqual(self.run_main("--ignore-extra"), 0)

    def test_classic_api_timestamps_are_ignored(self):
        classic = {"api_definition": {"api_id": "legacy", "name": "Legacy", "org_id": "a",
                                      "graphql": {"supergraph": {"updated_at": "2026-01-01T00:00:00Z"}}}}
        read_back = copy.deepcopy(classic)
        read_back["api_definition"].update({"id": "6afc00000000000000000003", "org_id": "b"})
        read_back["api_definition"]["graphql"]["supergraph"]["updated_at"] = "2026-02-02T00:00:00Z"
        self.write(self.source, classic)
        self.write(self.dashboard, read_back)
        self.assertEqual(self.run_main(), 0)

    def test_unusable_directory_is_exit_2(self):
        self.assertEqual(self.run_main(), 2)


if __name__ == "__main__":
    unittest.main()
