#!/usr/bin/env python3
"""Tests for tyk_state_snapshot.py. Run: python3 -m unittest test_tyk_state_snapshot.py"""

import http.server
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.parse
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tyk_state_snapshot as s  # noqa: E402


def oas(api_id, listen="/x/", strip=True, rewrite=None, detailed=False, active=True):
    x = {
        "info": {"id": api_id, "name": api_id, "state": {"active": active}},
        "upstream": {"url": "http://upstream"},
        "server": {"listenPath": {"value": listen, "strip": strip}},
    }
    if detailed:
        x["server"]["detailedActivityLogs"] = {"enabled": True}
    if rewrite:
        x["middleware"] = {"operations": {"op1": {"urlRewrite": {"enabled": True, "pattern": ".*", "rewriteTo": rewrite}}}}
    return {"openapi": "3.0.3", "info": {"title": api_id, "version": "1"}, "paths": {}, "x-tyk-api-gateway": x}


class FakeStack:
    """A fetcher that answers like the Dashboard, Gateway and Portal APIs."""

    def __init__(self):
        self.apis = {
            "orders": {"api_definition": {"api_id": "orders", "name": "Orders", "is_oas": True}, "oas": oas("orders")},
            "legacy": {"api_definition": {"api_id": "legacy", "name": "Legacy", "is_oas": False, "active": True,
                                          "proxy": {"listen_path": "/legacy/"}, "updated_at": "t1"}},
        }
        self.policies = [{"id": "p1", "_id": "m1", "name": "Gold", "last_updated": "t1",
                          "access_rights_array": [{"api_id": "b"}, {"api_id": "a"}]}]
        self.keys = [{"key_hash": "h1"}, {"key_hash": "h2"}]
        self.products = [{"ID": i, "Name": f"Product {i}"} for i in range(1, 6)]
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append(url)
        if url.endswith("/api/apis?p=-1"):
            return 200, {"apis": [{"api_definition": a["api_definition"]} for a in self.apis.values()], "pages": -1}
        if "/api/apis/oas/" in url:
            api_id = url.rsplit("/", 1)[1]
            return 200, self.apis[api_id]["oas"]
        if url.endswith("/api/portal/policies?p=-1"):
            return 200, {"Data": self.policies, "Pages": 0}
        if "/api/keys/detailed/" in url:
            return 200, {"keys": self.keys, "pages": 1}
        if url.endswith("/hello"):
            return 200, {"status": "pass", "version": "5.16.0"}
        if url.endswith("/tyk/apis"):
            return 200, [{"api_id": "legacy"}, {"x-tyk-api-gateway": {"info": {"id": "orders"}}}]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        page = int(query.get("page", ["1"])[0])
        per = int(query.get("per_page", ["20"])[0])
        if "/portal-api/products" in url:
            return 200, self.products[(page - 1) * per: page * per]
        if "/portal-api/users" in url:
            return 200, [{"ID": 7, "Email": "dev@example.com"}] if page == 1 else []
        if "/portal-api/" in url:
            return 200, []
        return 404, {"message": "not found"}


ENV = {
    "TYK_DASHBOARD_URL": "http://dash/", "TYK_DASHBOARD_API_KEY": "k",
    "TYK_GATEWAY_URL": "http://gw", "TYK_GATEWAY_SECRET": "s",
    "TYK_PORTAL_URL": "http://portal", "TYK_PORTAL_TOKEN": "t",
}


class NormaliseTests(unittest.TestCase):
    def test_drops_ignored_keys_at_any_depth_and_sorts_unordered_lists(self):
        doc = {"b": 1, "updated_at": "x", "nested": {"created_at": "y", "keep": 2},
               "access_rights_array": [{"api_id": "z"}, {"api_id": "a"}], "ordered": [2, 1]}
        out = s.normalise(doc, s.DEFAULT_IGNORED_KEYS)
        self.assertNotIn("updated_at", out)
        self.assertEqual(out["nested"], {"keep": 2})
        self.assertEqual([i["api_id"] for i in out["access_rights_array"]], ["a", "z"])
        self.assertEqual(out["ordered"], [2, 1])

    def test_changed_paths_reports_added_removed_and_values(self):
        paths = s.changed_paths({"a": 1, "b": {"c": 2}, "gone": 1}, {"a": 2, "b": {"c": 2, "d": 3}})
        self.assertIn("a: 1 -> 2", paths)
        self.assertIn("+b.d", paths)
        self.assertIn("-gone", paths)


class SnapshotTests(unittest.TestCase):
    def test_reads_every_section_and_paginates_portal(self):
        fake = FakeStack()
        snap, errors = s.take_snapshot(ENV, fetch=fake)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(snap["dashboard"]["apis"]), ["legacy", "orders"])
        self.assertNotIn("updated_at", snap["dashboard"]["apis"]["legacy"]["definition"])
        self.assertEqual(snap["keys"]["count"], 2)
        self.assertEqual(snap["gateway"]["version"], "5.16.0")
        self.assertEqual(snap["gateway"]["loaded_api_ids"], ["legacy", "orders"])
        self.assertEqual(len(snap["portal"]["products"]), 5)
        self.assertEqual(snap["portal"]["users"], {"count": 1, "ids": ["7"]})
        self.assertNotIn("dev@example.com", json.dumps(snap))
        self.assertTrue(any("dash/api/apis?p=-1" in c for c in fake.calls), "trailing slash on the base URL is trimmed")

    def test_short_variable_names_are_read_when_tyk_names_are_unset(self):
        short = {"DASH": "http://dash/", "DASHBOARD_API_KEY": "k", "GW": "http://gw", "GW_SECRET": "s",
                 "PORTAL_URL": "http://portal", "PORTAL_API_TOKEN": "t"}
        snap, errors = s.take_snapshot(short, fetch=FakeStack())
        self.assertEqual(errors, [])
        self.assertIn("dashboard", snap)
        self.assertEqual(snap["gateway"]["version"], "5.16.0")
        self.assertIn("portal", snap)

    def test_tyk_name_wins_over_short_name(self):
        self.assertEqual(s.setting({"TYK_GATEWAY_URL": "http://a", "GW": "http://b"}, "TYK_GATEWAY_URL", "GW"), "http://a")
        self.assertEqual(s.setting({}, "TYK_GATEWAY_URL", "GW"), "")

    def test_portal_pages_follow_per_page(self):
        fake = FakeStack()
        out = s.collect_portal("http://portal", "t", fake, s.DEFAULT_IGNORED_KEYS, per_page=2)
        self.assertEqual(sorted(out["products"]), ["1", "2", "3", "4", "5"])

    def test_portal_stops_when_page_parameter_is_ignored(self):
        def fetch(url, headers):
            return 200, [{"ID": 1}, {"ID": 2}]
        out = s.collect_portal("http://portal", "t", fetch, (), per_page=2)
        self.assertEqual(sorted(out["products"]), ["1", "2"])

    def test_section_error_is_recorded_not_fatal(self):
        fake = FakeStack()

        def fetch(url, headers):
            if "/api/keys/" in url:
                return 404, {"Status": "Error", "Message": "Could not retrieve keys"}
            return fake(url, headers)
        snap, errors = s.take_snapshot(ENV, fetch=fetch)
        self.assertIn("keys", snap["errors"])
        self.assertIn("Could not retrieve keys", snap["errors"]["keys"])
        self.assertIn("dashboard", snap)
        self.assertEqual(len(errors), 1)

    def test_nothing_configured(self):
        snap, errors = s.take_snapshot({}, fetch=FakeStack())
        self.assertTrue(errors)


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.before, _ = s.take_snapshot(ENV, fetch=FakeStack())

    def test_identical_snapshots(self):
        lines, notes = s.diff_snapshots(self.before, json.loads(json.dumps(self.before)))
        self.assertEqual((lines, notes), ([], []))

    def test_policy_array_order_is_not_a_difference(self):
        fake = FakeStack()
        fake.policies[0]["access_rights_array"].reverse()
        after, _ = s.take_snapshot(ENV, fetch=fake)
        self.assertEqual(s.diff_snapshots(self.before, after)[0], [])

    def test_removed_changed_keys_and_not_loaded(self):
        fake = FakeStack()
        del fake.apis["legacy"]
        fake.apis["orders"]["oas"]["x-tyk-api-gateway"]["server"]["listenPath"]["value"] = "/y/"
        fake.apis["new"] = {"api_definition": {"api_id": "new", "name": "New", "is_oas": True}, "oas": oas("new")}
        fake.keys = fake.keys[:1]
        after, _ = s.take_snapshot(ENV, fetch=fake)
        lines, _ = s.diff_snapshots(self.before, after)
        text = "\n".join(lines)
        self.assertIn("api: removed legacy", text)
        self.assertIn("api: added new", text)
        self.assertIn('listenPath.value: "/x/" -> "/y/"', text)
        self.assertIn("keys: count 2 -> 1", text)
        self.assertIn("Dashboard API new is not loaded by the Gateway", text)

    def test_inactive_api_need_not_be_loaded(self):
        fake = FakeStack()
        fake.apis["off"] = {"api_definition": {"api_id": "off", "name": "Off", "is_oas": True}, "oas": oas("off", active=False)}
        after, _ = s.take_snapshot(ENV, fetch=fake)
        self.assertNotIn("not loaded", "\n".join(s.diff_snapshots(self.before, after)[0]))

    def test_new_fields_become_notes_when_allowed(self):
        fake = FakeStack()
        fake.apis["legacy"]["api_definition"]["proxy"]["dns_discovery"] = {"enabled": False}
        after, _ = s.take_snapshot(ENV, fetch=fake)
        strict, _ = s.diff_snapshots(self.before, after)
        self.assertEqual(len(strict), 1)
        lines, notes = s.diff_snapshots(self.before, after, allow_new_fields=True)
        self.assertEqual(lines, [])
        self.assertIn("+proxy.dns_discovery", notes[0])

    def test_removed_field_is_never_only_a_note(self):
        fake = FakeStack()
        del fake.apis["legacy"]["api_definition"]["proxy"]["listen_path"]
        after, _ = s.take_snapshot(ENV, fetch=fake)
        lines, _ = s.diff_snapshots(self.before, after, allow_new_fields=True)
        self.assertIn("-proxy.listen_path", "\n".join(lines))


class PreflightTests(unittest.TestCase):
    def snap(self, apis):
        return {"dashboard": {"apis": apis}}

    def test_unsafe_ids(self):
        apis = {"example:partner-a": {"is_oas": True, "definition": oas("example:partner-a")},
                "ok.id_~-1": {"is_oas": True, "definition": oas("ok.id_~-1")},
                "._~": {"is_oas": True, "definition": oas("._~")}}
        found = s.preflight_5_16(self.snap(apis))
        flagged = sorted(f.split(" ")[1] for f in found if f.startswith("unsafe-api-id"))
        self.assertEqual(flagged, ["._~", "example:partner-a"])

    def test_oas_rewrite_that_starts_with_listen_path(self):
        apis = {
            "hit": {"is_oas": True, "definition": oas("hit", "/rewrite/", True, "http://up:8080/rewrite/target")},
            "relative": {"is_oas": True, "definition": oas("relative", "/rewrite/", True, "/rewrite/target")},
            "nostrip": {"is_oas": True, "definition": oas("nostrip", "/rewrite/", False, "http://up/rewrite/target")},
            "other": {"is_oas": True, "definition": oas("other", "/rewrite/", True, "http://up/elsewhere")},
            "prefix": {"is_oas": True, "definition": oas("prefix", "/rewrite/", True, "http://up/rewritex")},
        }
        found = [f for f in s.preflight_5_16(self.snap(apis)) if f.startswith("rewrite-strip")]
        self.assertEqual(sorted(f.split(" ")[1] for f in found), ["hit", "relative"])
        self.assertTrue(all("apply_strip_listen_path_to_url_rewrite" in f for f in found))

    def test_classic_rewrite_template_and_detailed(self):
        classic = {
            "api_id": "c1", "enable_detailed_recording": True,
            "proxy": {"listen_path": "/anything/", "strip_listen_path": True},
            "version_data": {"versions": {"Default": {"extended_paths": {
                "url_rewrites": [{"path": "/x", "rewrite_to": "http://httpbin.org/anything/bla/$1"}],
                "transform": [{"path": "/t", "template_data": {"template_mode": "file", "template_source": "/etc/t.tmpl"}}],
            }}}},
        }
        found = s.preflight_5_16(self.snap({"c1": {"is_oas": False, "definition": classic}}))
        kinds = sorted(f.split(":")[0] for f in found)
        self.assertEqual(kinds, ["detailed-logs", "rewrite-strip", "template-path"])

    def test_oas_template_path(self):
        d = oas("t1")
        d["x-tyk-api-gateway"]["middleware"] = {"operations": {"op": {"transformRequestBody": {"enabled": True, "format": "json", "path": "../t.tmpl"}}}}
        found = s.preflight_5_16(self.snap({"t1": {"is_oas": True, "definition": d}}))
        self.assertTrue(any(f.startswith("template-path") and "check that it does" in f for f in found))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/json":
            body, ctype = b'{"ok": true}', "application/json"
        else:
            body, ctype = b"<!doctype html><title>Login</title>", "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class HttpAndCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_json_and_html_bodies(self):
        self.assertEqual(s.http_get_json(f"{self.base}/json", {}), (200, {"ok": True}))
        with self.assertRaises(s.FetchError) as ctx:
            s.http_get_json(f"{self.base}/api/keys/?secret=x", {})
        self.assertNotIn("secret=x", str(ctx.exception))

    def test_unreachable(self):
        with self.assertRaises(s.FetchError):
            s.http_get_json("http://127.0.0.1:9/hello", {}, timeout=2)

    def test_cli_snapshot_diff_and_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = os.path.join(tmp, "a.json")
            after = os.path.join(tmp, "b.json")
            snap, _ = s.take_snapshot(ENV, fetch=FakeStack())
            for path in (before, after):
                with open(path, "w") as fh:
                    json.dump(snap, fh)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(s.main(["diff", before, after]), 0)
            self.assertIn("no differences", out.getvalue())
            snap["dashboard"]["apis"]["orders"]["definition"]["info"]["title"] = "Renamed"
            with open(after, "w") as fh:
                json.dump(snap, fh)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(s.main(["diff", before, after]), 1)
                self.assertEqual(s.main(["preflight", before]), 0)

            env_backup = dict(os.environ)
            try:
                os.environ.clear()
                os.environ.update({"TYK_GATEWAY_URL": f"{self.base}/json-gateway"})
                out_file = os.path.join(tmp, "snap.json")
                err = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(err):
                    rc = s.main(["snapshot", "--out", out_file])
                self.assertEqual(rc, 2)  # the stub answers HTML, so the section is recorded as an error
                self.assertEqual(stat.S_IMODE(os.stat(out_file).st_mode), 0o600)
                with open(out_file) as fh:
                    self.assertIn("gateway", json.load(fh)["errors"])
            finally:
                os.environ.clear()
                os.environ.update(env_backup)


if __name__ == "__main__":
    unittest.main()
