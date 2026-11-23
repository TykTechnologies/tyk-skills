import base64
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_plugins  # noqa: E402


def api(global_mw=None, operations=None, auth=None):
    ext = {"info": {"id": "t", "orgId": "default", "name": "t", "state": {"active": True}},
           "upstream": {"url": "http://upstream:8080"},
           "server": {"listenPath": {"value": "/t/", "strip": True}},
           "middleware": {"global": global_mw or {}}}
    if operations:
        ext["middleware"]["operations"] = operations
    if auth:
        ext["server"]["authentication"] = auth
    return {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {}, "x-tyk-api-gateway": ext}


def hook(name="h", path="/opt/tyk-gateway/plugins/h.js", **extra):
    entry = {"enabled": True, "functionName": name}
    if path:
        entry["path"] = path
    entry.update(extra)
    return entry


def levels(findings):
    return [f[0] for f in findings]


def texts(findings):
    return " | ".join(f[2] for f in findings)


class CheckPluginsTest(unittest.TestCase):
    def test_clean_javascript_api(self):
        doc = api({"pluginConfig": {"driver": "javascript"},
                   "prePlugins": [hook()],
                   "postPlugins": [hook("p", requireSession=True)]})
        self.assertEqual(check_plugins.check(doc), [])

    def test_missing_driver(self):
        doc = api({"prePlugins": [hook()]})
        found = check_plugins.check(doc)
        self.assertIn("ERROR", levels(found))
        self.assertIn("Unsupported driver", texts(found))

    def test_unknown_driver(self):
        doc = api({"pluginConfig": {"driver": "goja"}, "prePlugins": [hook()]})
        self.assertIn("unknown driver 'goja'", texts(check_plugins.check(doc)))

    def test_inline_code_needs_javascript(self):
        code = base64.b64encode(b"var h = 1;").decode()
        doc = api({"pluginConfig": {"driver": "otto"}, "prePlugins": [hook(path="", code=code)]})
        found = check_plugins.check(doc)
        self.assertIn("inline code runs only on the javascript driver", texts(found))
        self.assertIn("WARN", levels(found))  # otto itself

    def test_otto_cannot_load_modern_syntax_inline(self):
        code = base64.b64encode(b"const h = new X(); h.f((a) => a);").decode()
        doc = api({"pluginConfig": {"driver": "otto"}, "prePlugins": [hook(path="", code=code)]})
        self.assertIn("otto cannot load the file", texts(check_plugins.check(doc)))

    def test_bad_base64(self):
        doc = api({"pluginConfig": {"driver": "javascript"}, "prePlugins": [hook(path="", code="@@@")]})
        self.assertIn("not valid base64", texts(check_plugins.check(doc)))

    def test_function_name_missing_from_source(self):
        code = base64.b64encode(b"var other = 1;").decode()
        doc = api({"pluginConfig": {"driver": "javascript"}, "prePlugins": [hook("h", path="", code=code)]})
        self.assertIn("does not mention 'h'", texts(check_plugins.check(doc)))

    def test_mapped_file_scanned_for_engine_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "h.js"), "w") as handle:
                handle.write("var h = 1; console.log('x'); _.map([1], function(n){return n;});")
            doc = api({"pluginConfig": {"driver": "javascript"}, "prePlugins": [hook()]})
            found = check_plugins.check(doc, [("/opt/tyk-gateway/plugins", tmp)])
            self.assertIn("console", texts(found))
            self.assertIn("underscore", texts(found))
            self.assertNotIn("ERROR", levels(found))

    def test_storage_on_otto(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "h.js"), "w") as handle:
                handle.write("var h = 1; TykStorageIncr('k', 60);")
            doc = api({"pluginConfig": {"driver": "otto"}, "prePlugins": [hook()]})
            found = check_plugins.check(doc, [("/opt/tyk-gateway/plugins/", tmp)])
            self.assertIn("TykStorage", texts(found))

    def test_unmapped_file_not_scanned(self):
        doc = api({"pluginConfig": {"driver": "javascript"}, "prePlugins": [hook()]})
        self.assertEqual(check_plugins.check(doc, [("/somewhere/else", "/nonexistent")]), [])

    def test_require_session_warning_for_js_post(self):
        doc = api({"pluginConfig": {"driver": "javascript"}, "postPlugins": [hook()]})
        self.assertIn("requireSession", texts(check_plugins.check(doc)))

    def test_require_session_not_needed_for_pre(self):
        doc = api({"pluginConfig": {"driver": "javascript"}, "prePlugins": [hook()]})
        self.assertNotIn("requireSession", texts(check_plugins.check(doc)))

    def test_go_versioned_path_warning(self):
        doc = api({"pluginConfig": {"driver": "goplugin"},
                   "postPlugins": [hook("P", "/opt/tyk-gateway/plugins/demo_v5.16.0_linux_arm64.so")]})
        self.assertIn("names a build for one Gateway version", texts(check_plugins.check(doc)))
        doc = api({"pluginConfig": {"driver": "goplugin"},
                   "postPlugins": [hook("P", "/opt/tyk-gateway/plugins/demo.so")]})
        self.assertEqual(check_plugins.check(doc), [])

    def test_custom_auth(self):
        auth = {"enabled": True, "custom": {"enabled": True,
                                            "config": {"enabled": True, "functionName": "Auth", "path": "/p/a.so"}}}
        doc = api({"pluginConfig": {"driver": "goplugin"}}, auth=auth)
        found = check_plugins.check(doc)
        self.assertEqual(levels(found), ["NOTE"])
        self.assertIn("rate_limit_pattern", texts(found))

    def test_custom_auth_without_function(self):
        auth = {"enabled": True, "custom": {"enabled": True, "config": {"enabled": True}}}
        doc = api({"pluginConfig": {"driver": "grpc"}}, auth=auth)
        self.assertIn("ERROR", levels(check_plugins.check(doc)))

    def test_custom_auth_with_auth_disabled(self):
        auth = {"enabled": False, "custom": {"enabled": True,
                                             "config": {"enabled": True, "functionName": "Auth"}}}
        doc = api({"pluginConfig": {"driver": "grpc"}}, auth=auth)
        self.assertIn("server.authentication.enabled is not true", texts(check_plugins.check(doc)))

    def test_grpc_and_python_notes(self):
        doc = api({"pluginConfig": {"driver": "grpc"}, "postPlugins": [hook("G", path="")]})
        self.assertIn("coprocess_grpc_server", texts(check_plugins.check(doc)))
        doc = api({"pluginConfig": {"driver": "python", "bundle": {"enabled": True, "path": "b.zip"}}})
        self.assertIn("Python added to the Gateway image", texts(check_plugins.check(doc)))

    def test_bundle_without_driver_is_fine(self):
        doc = api({"pluginConfig": {"bundle": {"enabled": True, "path": "b.zip"}}})
        self.assertEqual(check_plugins.check(doc), [])

    def test_virtual_endpoint(self):
        ops = {"hello": {"virtualEndpoint": {"enabled": True, "functionName": "h"}}}
        found = check_plugins.check(api({}, operations=ops))
        self.assertIn("needs a path or a base64 body", texts(found))
        self.assertIn("run on otto", texts(found))
        ops = {"hello": {"virtualEndpoint": {"enabled": True, "functionName": "h", "path": "/p/v.js"}}}
        found = check_plugins.check(api({"pluginConfig": {"driver": "javascript"}}, operations=ops))
        self.assertEqual(found, [])

    def test_virtual_endpoint_engine_follows_driver(self):
        modern = base64.b64encode(b"const veHello = (request, session, config) => TykJsResponse({}, {});").decode()
        ops = {"hello": {"virtualEndpoint": {"enabled": True, "functionName": "veHello", "body": modern}}}
        for driver in ("goplugin", "grpc", "python", "otto"):
            found = check_plugins.check(api({"pluginConfig": {"driver": driver}}, operations=ops))
            self.assertIn("virtual endpoints run on otto", texts(found), driver)
            self.assertIn("otto cannot load the file", texts(found), driver)
            self.assertIn("ERROR", levels(found), driver)
        found = check_plugins.check(api({"pluginConfig": {"driver": "javascript"}}, operations=ops))
        self.assertEqual(found, [])

    def test_virtual_endpoint_file_scanned_with_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "js"))
            with open(os.path.join(tmp, "js", "v.js"), "w") as handle:
                handle.write("function veHello(request, session, config) { console.log('x'); }")
            ops = {"hello": {"virtualEndpoint": {"enabled": True, "functionName": "veHello",
                                                 "path": "/opt/tyk-gateway/plugins/js/v.js"}}}
            maps = [("/opt/tyk-gateway/plugins", tmp)]
            found = check_plugins.check(api({"pluginConfig": {"driver": "javascript"}}, operations=ops), maps)
            self.assertIn("console", texts(found))
            found = check_plugins.check(api({"pluginConfig": {"driver": "goplugin"}}, operations=ops), maps)
            self.assertNotIn("console", texts(found))
            ops["hello"]["virtualEndpoint"]["functionName"] = "other"
            found = check_plugins.check(api({"pluginConfig": {"driver": "javascript"}}, operations=ops), maps)
            self.assertIn("does not mention 'other'", texts(found))

    def test_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.json")
            bad = os.path.join(tmp, "bad.json")
            with open(good, "w") as handle:
                json.dump(api({"pluginConfig": {"driver": "javascript"}, "prePlugins": [hook()]}), handle)
            with open(bad, "w") as handle:
                json.dump(api({"prePlugins": [hook()]}), handle)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(check_plugins.main([good]), 0)
                self.assertEqual(check_plugins.main([bad]), 1)
            self.assertIn("0 error(s), 0 warning(s), 0 note(s)", out.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check_plugins.main([os.path.join(tmp, "missing.json")]), 2)


if __name__ == "__main__":
    unittest.main()
