import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_sync_repo  # noqa: E402


def oas_api(api_id="orders", listen="/orders/"):
    info = {"name": "Orders", "state": {"active": True}}
    if api_id:
        info["id"] = api_id
    return {
        "oas": {
            "openapi": "3.0.3",
            "info": {"title": "Orders", "version": "1.0.0"},
            "paths": {},
            "x-tyk-api-gateway": {
                "info": info,
                "server": {"listenPath": {"value": listen, "strip": True}},
                "upstream": {"url": "http://example.com"},
            },
        }
    }


class CheckSyncRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, doc):
        (self.root / name).write_text(json.dumps(doc), encoding="utf-8")

    def index(self, **sections):
        doc = {"type": "apidef"}
        for section, names in sections.items():
            doc[section] = [{"file": name} for name in names]
        self.write(".tyk.json", doc)

    def check(self, **kwargs):
        return check_sync_repo.check_directory(self.root, **kwargs)

    def test_valid_directory_passes(self):
        self.write("oas-orders.json", oas_api())
        self.write("policy-gold.json", {"id": "gold", "org_id": "org1", "name": "Gold"})
        self.write("asset-cors.json", {"id": "cors", "kind": "fragment", "data": {}})
        self.index(files=["oas-orders.json"], policies=["policy-gold.json"], assets=["asset-cors.json"])
        errors, warnings = self.check()
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(check_sync_repo.main([str(self.root)]), 0)

    def test_missing_index_is_exit_2(self):
        self.assertEqual(check_sync_repo.main([str(self.root)]), 2)

    def test_api_without_id_is_an_error(self):
        self.write("oas-orders.json", oas_api(api_id=None))
        self.index(files=["oas-orders.json"])
        errors, _ = self.check()
        self.assertTrue(any("info.id is missing" in line for line in errors))
        self.assertEqual(check_sync_repo.main([str(self.root)]), 1)

    def test_unwrapped_oas_document_is_an_error(self):
        self.write("oas-orders.json", oas_api()["oas"])
        self.index(files=["oas-orders.json"])
        errors, _ = self.check()
        self.assertTrue(any("must be wrapped" in line for line in errors))

    def test_listed_file_that_does_not_exist(self):
        self.index(files=["oas-gone.json"])
        errors, _ = self.check()
        self.assertTrue(any("does not exist" in line for line in errors))

    def test_unlisted_file_is_an_error_or_a_warning(self):
        self.write("oas-orders.json", oas_api())
        self.write("oas-stray.json", oas_api("stray", "/stray/"))
        self.index(files=["oas-orders.json"])
        errors, warnings = self.check()
        self.assertTrue(any("oas-stray.json: not listed" in line for line in errors))
        errors, warnings = self.check(allow_unlisted=True)
        self.assertEqual(errors, [])
        self.assertTrue(any("oas-stray.json: not listed" in line for line in warnings))

    def test_duplicate_id_and_listen_path(self):
        self.write("oas-a.json", oas_api("orders", "/orders/"))
        self.write("oas-b.json", oas_api("orders", "/orders/"))
        self.index(files=["oas-a.json", "oas-b.json"])
        errors, _ = self.check()
        self.assertTrue(any("API ID 'orders' is also used" in line for line in errors))
        self.assertTrue(any("listen path '/orders/' is also used" in line for line in errors))

    def test_classic_api_needs_api_id_and_listen_path(self):
        self.write("api-legacy.json", {"api_definition": {"name": "Legacy", "proxy": {}}})
        self.index(files=["api-legacy.json"])
        errors, _ = self.check()
        self.assertTrue(any("api_definition.api_id is missing" in line for line in errors))
        self.assertTrue(any("listen_path is missing" in line for line in errors))

    def test_policy_needs_id_and_org(self):
        self.write("policy-x.json", {"name": "No id"})
        self.index(policies=["policy-x.json"])
        errors, _ = self.check()
        self.assertTrue(any("neither id nor _id" in line for line in errors))
        self.assertTrue(any("org_id is missing" in line for line in errors))

    def test_asset_kind_is_checked(self):
        self.write("asset-x.json", {"id": "x", "kind": "something"})
        self.index(assets=["asset-x.json"])
        errors, _ = self.check()
        self.assertTrue(any("kind must be oas-template or fragment" in line for line in errors))

    def test_wrong_index_type(self):
        self.write(".tyk.json", {"type": "oas", "files": []})
        errors, _ = self.check()
        self.assertTrue(any("type must be" in line for line in errors))


if __name__ == "__main__":
    unittest.main()
