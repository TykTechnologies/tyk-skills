import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import check_events_def as c

BASE = {
    "openapi": "3.0.3",
    "info": {"title": "t", "version": "1"},
    "paths": {},
    "x-tyk-api-gateway": {"info": {"id": "t", "orgId": "default", "name": "t", "state": {"active": True}},
                          "upstream": {"url": ""},
                          "server": {"listenPath": {"value": "/t/", "strip": True}}},
}
PUBLISH = {"input": {"http_server": {"path": "/publish", "allowed_verbs": ["POST"], "timeout": "5s"}},
           "output": {"kafka": {"addresses": ["kafka:9092"], "topic": "orders"}}}
SUBSCRIBE = {"input": {"kafka": {"addresses": ["kafka:9092"], "topics": ["orders"], "consumer_group": "g",
                                 "start_from_oldest": False}},
             "output": {"http_server": {"stream_path": "/sse", "stream_format": "event_source"}}}
HOOK = {"enabled": True, "trigger": "RatelimitExceeded", "type": "webhook", "name": "h",
        "url": "http://hooks:9000/x", "method": "POST", "cooldownPeriod": "1s",
        "bodyTemplate": "custom/event.json"}


def doc(streams=None, handlers=None, auth=None, glob=None):
    d = copy.deepcopy(BASE)
    if streams is not None:
        d["x-tyk-streaming"] = {"streams": streams}
    if handlers is not None:
        d["x-tyk-api-gateway"]["server"]["eventHandlers"] = handlers
    if auth is not None:
        d["x-tyk-api-gateway"]["server"]["authentication"] = auth
    if glob is not None:
        d["x-tyk-api-gateway"]["middleware"] = {"global": glob}
    return d


def levels(findings, needle):
    return [f[0] for f in findings if needle in f[1] or needle in f[2]]


class StreamsTest(unittest.TestCase):
    def test_clean_publish_stream(self):
        self.assertEqual(c.check(doc({"p": PUBLISH})), [])

    def test_clean_subscribe_stream(self):
        self.assertEqual(levels(c.check(doc({"s": SUBSCRIBE})), "round-robin"), ["NOTE"])

    def test_undocumented_connector_warns(self):
        s = copy.deepcopy(PUBLISH)
        s["output"] = {"nats": {"urls": ["nats://n:4222"], "subject": "x"}}
        self.assertEqual(levels(c.check(doc({"p": s})), "not supported"), ["WARN"])

    def test_connector_inside_broker_is_checked(self):
        s = copy.deepcopy(PUBLISH)
        s["output"] = {"broker": {"pattern": "fan_out", "outputs": [{"kafka": {"topic": "a"}}, {"redis_list": {}}]}}
        found = c.check(doc({"p": s}))
        self.assertEqual(levels(found, "redis_list"), ["WARN"])
        self.assertEqual(levels(found, "output.kafka"), [])

    def test_label_and_processors_are_not_connectors(self):
        s = copy.deepcopy(PUBLISH)
        s["input"]["label"] = "in"
        s["input"]["processors"] = [{"mapping": "root = this"}]
        self.assertEqual(c.check(doc({"p": s})), [])

    def test_raw_stream_path_notes_event_source(self):
        s = copy.deepcopy(SUBSCRIBE)
        del s["output"]["http_server"]["stream_format"]
        self.assertEqual(levels(c.check(doc({"s": s})), "stream_format: event_source"), ["NOTE"])

    def test_default_start_from_oldest_notes_replay(self):
        s = copy.deepcopy(SUBSCRIBE)
        del s["input"]["kafka"]["start_from_oldest"]
        self.assertEqual(levels(c.check(doc({"s": s})), "oldest message"), ["NOTE"])

    def test_per_client_group_has_no_round_robin_note(self):
        s = copy.deepcopy(SUBSCRIBE)
        s["input"]["kafka"]["consumer_group"] = "feed-$tyk_meta.partner"
        self.assertEqual(levels(c.check(doc({"s": s})), "round-robin"), [])

    def test_context_variable_without_switch_warns(self):
        s = copy.deepcopy(SUBSCRIBE)
        s["input"]["kafka"]["consumer_group"] = "feed-$tyk_context.headers_X_Client_Id"
        self.assertEqual(levels(c.check(doc({"s": s})), "empty string"), ["WARN"])
        on = c.check(doc({"s": s}, glob={"contextVariables": {"enabled": True}}))
        self.assertEqual(levels(on, "empty string"), [])

    def test_credential_reaches_stream_without_strip(self):
        auth = {"enabled": True, "securitySchemes": {"key": {"enabled": True}}}
        self.assertEqual(levels(c.check(doc({"p": PUBLISH}, auth=auth)), "stripAuthorizationData"), ["WARN"])
        auth["stripAuthorizationData"] = True
        self.assertEqual(levels(c.check(doc({"p": PUBLISH}, auth=auth)), "stripAuthorizationData"), [])

    def test_header_transform_note(self):
        glob = {"transformRequestHeaders": {"enabled": True, "add": [{"name": "X", "value": "y"}]}}
        self.assertEqual(levels(c.check(doc({"p": PUBLISH}, glob=glob)), "do not reach"), ["NOTE"])

    def test_header_transform_ignored_without_streams(self):
        glob = {"transformRequestHeaders": {"enabled": True}}
        self.assertEqual(c.check(doc(glob=glob)), [])


class EventHandlerTest(unittest.TestCase):
    def test_clean_local_webhook(self):
        self.assertEqual(c.check(doc(handlers=[HOOK])), [])

    def test_wrong_case_trigger_is_error(self):
        h = dict(HOOK, trigger="RateLimitExceeded")
        found = c.check(doc(handlers=[h]))
        self.assertEqual(levels(found, "not a Tyk OAS trigger"), ["ERROR"])
        self.assertIn("refuses the definition", found[0][2])

    def test_later_handler_is_stored_not_refused(self):
        log = {"enabled": True, "trigger": "AuthFailure", "type": "log", "name": "l", "logPrefix": "p"}
        found = c.check(doc(handlers=[log, dict(HOOK, trigger="RateLimitExceeded")]))
        self.assertEqual([f[0] for f in found], ["ERROR"])
        self.assertIn("eventHandlers[1]", found[0][1])
        self.assertIn("checks only the first handler", found[0][2])
        self.assertNotIn("refuses", found[0][2])

    def test_trigger_exceeded_is_accepted(self):
        self.assertEqual(c.check(doc(handlers=[dict(HOOK, trigger="TriggerExceeded")])), [])

    def test_oauth2_events_are_not_triggers(self):
        for name in ("OAuth2ScopeCheckFailed", "OAuth2ExchangeSucceeded", "OAuth2ExchangeFailed"):
            found = c.check(doc(handlers=[dict(HOOK, trigger=name)]))
            self.assertEqual(levels(found, "not accepted as a Tyk OAS trigger"), ["ERROR"], name)

    def test_log_handler_needs_log_prefix(self):
        h = {"enabled": True, "trigger": "AuthFailure", "type": "log", "name": "l"}
        self.assertEqual(levels(c.check(doc(handlers=[h])), "logPrefix is required"), ["ERROR"])

    def test_cooldown_pattern(self):
        for value in ("500ms", "1.5s", "0", "1d"):
            h = dict(HOOK, cooldownPeriod=value)
            self.assertEqual(levels(c.check(doc(handlers=[h])), "does not match"), ["ERROR"], value)
        for value in ("1s", "2m", "1h30m10s"):
            self.assertEqual(c.check(doc(handlers=[dict(HOOK, cooldownPeriod=value)])), [], value)

    def test_global_webhook_needs_cooldown(self):
        h = {"enabled": True, "trigger": "AuthFailure", "type": "webhook", "id": "abc"}
        self.assertEqual(levels(c.check(doc(handlers=[h])), "cooldownPeriod is required"), ["ERROR"])

    def test_unknown_type_is_error(self):
        h = dict(HOOK, type="email")
        self.assertEqual(levels(c.check(doc(handlers=[h])), ".type"), ["ERROR"])

    def test_missing_body_template_is_error(self):
        h = dict(HOOK)
        del h["bodyTemplate"]
        self.assertEqual(levels(c.check(doc(handlers=[h])), "bodyTemplate is required"), ["ERROR"])

    def test_global_webhook_needs_no_template_or_url(self):
        h = {"enabled": True, "trigger": "AuthFailure", "type": "webhook", "id": "abc", "cooldownPeriod": "1s"}
        self.assertEqual(c.check(doc(handlers=[h])), [])

    def test_templates_prefix_warns(self):
        h = dict(HOOK, bodyTemplate="templates/custom/event.json")
        self.assertEqual(levels(c.check(doc(handlers=[h])), "template_path"), ["WARN"])

    def test_default_template_notes_credential(self):
        h = dict(HOOK, bodyTemplate="/opt/tyk-gateway/templates/default_webhook.json")
        self.assertEqual(levels(c.check(doc(handlers=[h])), "Meta.Key"), ["NOTE"])

    def test_zero_cooldown_warns(self):
        for value in ("0s", "", "0m0s"):
            h = dict(HOOK, cooldownPeriod=value)
            self.assertEqual(levels(c.check(doc(handlers=[h])), "never sent again"), ["WARN"], value)

    def test_missing_cooldown_is_error(self):
        h = dict(HOOK)
        del h["cooldownPeriod"]
        self.assertEqual(levels(c.check(doc(handlers=[h])), "cooldownPeriod is"), ["ERROR"])

    def test_relative_url_warns(self):
        h = dict(HOOK, url="hooks:9000/x")
        self.assertEqual(levels(c.check(doc(handlers=[h])), "unsupported protocol scheme"), ["WARN"])

    def test_log_handler_needs_name(self):
        h = {"enabled": True, "trigger": "AuthFailure", "type": "log", "logPrefix": "x"}
        self.assertEqual(levels(c.check(doc(handlers=[h])), "name is required"), ["ERROR"])
        h["name"] = "auth-log"
        self.assertEqual(c.check(doc(handlers=[h])), [])


class MainTest(unittest.TestCase):
    def run_main(self, document):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(document, fh)
            path = fh.name
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = c.main([path])
            return code, buf.getvalue()
        finally:
            os.unlink(path)

    def test_exit_codes(self):
        code, out = self.run_main(doc({"p": PUBLISH}))
        self.assertEqual(code, 0)
        self.assertIn("0 error(s), 0 warning(s), 0 note(s)", out)
        code, out = self.run_main(doc(handlers=[dict(HOOK, trigger="Nope")]))
        self.assertEqual(code, 1)

    def test_not_tyk_oas(self):
        code, out = self.run_main({"openapi": "3.0.3"})
        self.assertEqual(code, 1)
        self.assertIn("not a Tyk OAS API definition", out)

    def test_unreadable_file(self):
        self.assertEqual(c.main(["/nonexistent/file.json"]), 2)
        self.assertEqual(c.main([]), 2)


if __name__ == "__main__":
    unittest.main()
