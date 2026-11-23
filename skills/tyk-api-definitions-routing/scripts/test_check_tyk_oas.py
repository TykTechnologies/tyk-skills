"""Tests for check_tyk_oas.py and build_extension_fields.py (standard library only).

Each rule mirrors a mistake on Tyk Gateway and Dashboard 5.16.0: the definition is either
rejected with the quoted message, or loaded and does not behave as written.
"""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import build_extension_fields  # noqa: E402
import check_tyk_oas  # noqa: E402

EXT = "x-tyk-api-gateway"


def definition() -> dict:
    """A definition that loads and behaves as written."""
    return {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1.0.0"},
        "paths": {
            "/orders": {"get": {"operationId": "listOrders", "responses": {"200": {"description": "OK"}}}},
            "/orders/{id}": {
                "get": {
                    "operationId": "getOrder",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        EXT: {
            "info": {"id": "orders", "name": "Orders", "state": {"active": True}},
            "upstream": {"url": "http://orders.example.com:8080"},
            "server": {"listenPath": {"value": "/orders-api/", "strip": True}},
        },
    }


def findings(document, **kwargs):
    return check_tyk_oas.check(document, **kwargs).items


def messages(document, level=None, **kwargs) -> list[str]:
    return [
        f"{item['where']}: {item['message']}"
        for item in findings(document, **kwargs)
        if level is None or item["level"] == level
    ]


class CleanDefinitions(unittest.TestCase):
    def test_minimal_definition_has_no_findings(self):
        self.assertEqual(findings(definition()), [])

    def test_smallest_loadable_definition(self):
        document = {
            "openapi": "3.0.3",
            "info": {"title": "t", "version": "1"},
            "paths": {},
            EXT: {
                "info": {"name": "tiny", "state": {"active": True}},
                "upstream": {"url": "http://example.com"},
                "server": {"listenPath": {"value": "/tiny/"}},
            },
        }
        self.assertEqual(findings(document), [])

    def test_streams_api_without_upstream_is_noted_not_refused(self):
        document = {
            "openapi": "3.0.3",
            "info": {"title": "t", "version": "1"},
            "paths": {},
            "x-tyk-streaming": {"streams": {}},
            EXT: {
                "info": {"name": "events", "state": {"active": True}},
                "upstream": {"url": ""},
                "server": {"listenPath": {"value": "/events/"}},
            },
        }
        self.assertEqual(messages(document, level="ERROR"), [])
        self.assertTrue(any("upstream.url" in m for m in messages(document, level="NOTE")))

    def test_openapi_3_1_is_accepted(self):
        for version in ("3.1.0", "3.1.1", "3.1"):
            document = definition()
            document["openapi"] = version
            self.assertEqual(messages(document, "ERROR"), [], version)

    def test_full_operation_middleware_is_known(self):
        document = definition()
        document[EXT]["middleware"] = {
            "global": {"cache": {"enabled": True, "timeout": 5}, "contextVariables": {"enabled": True}},
            "operations": {
                "listOrders": {
                    "allow": {"enabled": True},
                    "cache": {"enabled": True, "timeout": 5},
                    "enforceTimeout": {"enabled": True, "value": 3},
                    "rateLimit": {"enabled": True, "rate": 3, "per": "10s"},
                    "circuitBreaker": {
                        "enabled": True, "threshold": 0.5, "sampleSize": 4,
                        "coolDownPeriod": 3, "halfOpenStateEnabled": False,
                    },
                    "transformRequestHeaders": {"enabled": True, "add": [{"name": "X-Id", "value": "$tyk_context.request_id"}]},
                    "mockResponse": {"enabled": False, "fromOASExamples": {"enabled": False}},
                },
                "getOrder": {
                    "allow": {"enabled": True},
                    "validateRequest": {"enabled": True, "errorResponseCode": 422},
                    "urlRewrite": {"enabled": True, "pattern": "/orders/(\\d+)", "rewriteTo": "/v2/orders/$1"},
                },
            },
        }
        self.assertEqual(findings(document), [])

    def test_null_values_from_a_readback_are_ignored(self):
        document = definition()
        document[EXT]["upstream"]["authentication"] = {
            "enabled": True,
            "oauth": {
                "enabled": True,
                "allowedAuthorizeTypes": ["clientCredentials"],
                "clientCredentials": {"clientId": "a", "clientSecret": "b", "tokenUrl": "http://idp.example.com/token", "header": None},
            },
        }
        self.assertEqual(messages(document, "ERROR"), [])


class RejectedByTheGateway(unittest.TestCase):
    def test_classic_definition(self):
        classic = {"name": "c", "api_id": "c", "proxy": {"listen_path": "/c/", "target_url": "http://example.com"}}
        self.assertIn("classic", messages(classic, "ERROR")[0])

    def test_swagger_2(self):
        document = {"swagger": "2.0", "info": {"title": "s", "version": "1"}, "paths": {}}
        text = "\n".join(messages(document, "ERROR", plain=True))
        self.assertIn("Swagger", text)

    def test_unsupported_openapi_versions(self):
        for version in ("3.2.0", "2.0", "3.0", "3"):
            document = definition()
            document["openapi"] = version
            self.assertTrue(any(m.startswith("openapi:") for m in messages(document, "ERROR")), version)

    def test_missing_extension(self):
        document = definition()
        del document[EXT]
        self.assertIn("the payload should contain x-tyk-api-gateway", messages(document, "ERROR")[0])

    def test_plain_document_must_not_carry_the_extension(self):
        self.assertIn("import payload should not contain", messages(definition(), "ERROR", plain=True)[0])

    def test_plain_document_without_extension_is_clean(self):
        document = definition()
        del document[EXT]
        self.assertEqual(findings(document, plain=True), [])

    def test_required_fields(self):
        cases = {
            "upstream": lambda d: d[EXT].pop("upstream"),
            "server": lambda d: d[EXT].pop("server"),
            "info": lambda d: d[EXT].pop("info"),
            "name": lambda d: d[EXT]["info"].pop("name"),
            "state": lambda d: d[EXT]["info"].pop("state"),
            "value": lambda d: d[EXT]["server"]["listenPath"].pop("value"),
        }
        for field, mutate in cases.items():
            document = definition()
            mutate(document)
            self.assertTrue(any(f"`{field}` is required" in m for m in messages(document, "ERROR")), field)

    def test_wrong_type(self):
        document = definition()
        document[EXT]["server"]["listenPath"]["strip"] = "yes"
        self.assertTrue(any("listenPath.strip: must be a boolean" in m for m in messages(document, "ERROR")))

    def test_enum_value(self):
        document = definition()
        document[EXT]["server"]["protocol"] = "tcp"
        self.assertTrue(any("server.protocol: must be one of" in m for m in messages(document, "ERROR")))

    def test_api_id_characters(self):
        document = definition()
        document[EXT]["info"]["id"] = "a/b"
        self.assertTrue(any("info.id" in m for m in messages(document, "ERROR")))

    def test_openapi_document_mistakes(self):
        document = definition()
        document["info"].pop("version")
        document["paths"]["/orders"]["post"] = {"operationId": "listOrders", "responses": {"200": {"description": "OK"}}}
        document["paths"]["/items/{sku}"] = {"get": {"operationId": "getItem", "responses": {"200": {"description": "OK"}}}}
        document["paths"]["/noresp"] = {"get": {"operationId": "noResp"}}
        text = "\n".join(messages(document, "ERROR"))
        self.assertIn("info.version", text)
        self.assertIn("ids must be unique", text)
        self.assertIn("['sku'] are in the path but not declared", text)
        self.assertIn("needs a `responses` object", text)

    def test_path_parameter_through_component_reference(self):
        document = definition()
        document["components"] = {"parameters": {"Id": {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}}}
        document["paths"]["/orders/{id}"]["get"]["parameters"] = [{"$ref": "#/components/parameters/Id"}]
        self.assertEqual(findings(document), [])

    def test_mutual_tls_scheme_path_items_and_references(self):
        document = definition()
        document["openapi"] = "3.1.0"
        document["components"] = {
            "securitySchemes": {"mtls": {"type": "mutualTLS"}},
            "pathItems": {"P": {}},
            "schemas": {"E": {"type": "object"}},
        }
        document["webhooks"] = {
            "evt": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/E"}}}},
                             "responses": {"200": {"description": "OK"}}}}
        }
        document["paths"]["/orders"]["get"]["responses"]["200"] = {"$ref": "./responses.json#/Ok"}
        text = "\n".join(messages(document, "ERROR"))
        self.assertIn("`mutualTLS` security scheme is rejected", text)
        self.assertIn("components.pathItems", text)
        self.assertIn("inside `webhooks` is not resolved", text)
        self.assertIn("only references inside the document", text)

    def test_two_upstream_authentication_modes(self):
        document = definition()
        document[EXT]["upstream"]["authentication"] = {
            "enabled": True,
            "basicAuth": {"enabled": True, "username": "u", "password": "p"},
            "oauth": {"enabled": True, "allowedAuthorizeTypes": ["clientCredentials"],
                      "clientCredentials": {"clientId": "a", "clientSecret": "b", "tokenUrl": "http://idp.example.com/token"}},
        }
        self.assertTrue(any("multiple upstream authentication modes" in m for m in messages(document, "ERROR")))


class AcceptedButWrong(unittest.TestCase):
    def test_invented_fields_are_reported(self):
        document = definition()
        document[EXT]["server"]["listenPath"]["stripPath"] = True
        document[EXT]["proxy"] = {"listen_path": "/x/"}
        document[EXT]["graphql"] = {"enabled": True}
        document[EXT]["middleware"] = {"operations": {"listOrders": {"rateLimiting": {"enabled": True}}}}
        text = "\n".join(messages(document, "ERROR"))
        for where in ("server.listenPath.stripPath", f"{EXT}.proxy", f"{EXT}.graphql", "operations.listOrders.rateLimiting"):
            self.assertIn(where, text)
        self.assertIn("silently drops it", text)

    def test_middleware_for_unknown_operation(self):
        document = definition()
        document[EXT]["middleware"] = {"operations": {"ghostOp": {"allow": {"enabled": True}}}}
        self.assertTrue(any("ghostOp: no operation in `paths`" in m for m in messages(document, "ERROR")))

    def test_listen_path(self):
        document = definition()
        document[EXT]["server"]["listenPath"]["value"] = "orders api/"
        text = "\n".join(messages(document, "ERROR"))
        self.assertIn("must start with `/`", text)
        self.assertIn("contains a space", text)

    def test_upstream_url_without_scheme(self):
        for url in ("orders.example.com:8080", "/just/path", ""):
            document = definition()
            document[EXT]["upstream"]["url"] = url
            self.assertTrue(any("is not an absolute URL" in m for m in messages(document, "ERROR")), url)

    def test_upstream_url_placeholders_and_load_balancing_are_accepted(self):
        document = definition()
        document[EXT]["upstream"]["url"] = "$kv{env:ORDERS_URL}"
        self.assertEqual(messages(document, "ERROR"), [])
        document[EXT]["upstream"] = {"loadBalancing": {"enabled": True, "targets": [{"url": "http://a.example.com", "weight": 1}]}}
        self.assertEqual(messages(document, "ERROR"), [])
        document[EXT]["upstream"] = {"loadBalancing": {"enabled": True, "targets": []}}
        self.assertTrue(any("no targets" in m for m in messages(document, "ERROR")))

    def test_partial_allow_list(self):
        document = definition()
        document[EXT]["middleware"] = {"operations": {"listOrders": {"allow": {"enabled": True}}}}
        warnings = messages(document, "WARN")
        self.assertTrue(any("getOrder" in m and "403" in m for m in warnings))

    def test_operation_cache_without_global_cache(self):
        document = definition()
        document[EXT]["middleware"] = {"operations": {"listOrders": {"cache": {"enabled": True, "timeout": 5}}}}
        self.assertTrue(any("middleware.global.cache.enabled" in m for m in messages(document, "ERROR")))

    def test_context_variables_not_enabled(self):
        document = definition()
        document[EXT]["middleware"] = {
            "operations": {"listOrders": {"transformRequestHeaders": {"enabled": True, "add": [{"name": "X-Id", "value": "$tyk_context.request_id"}]}}}
        }
        self.assertTrue(any("context variables are not enabled" in m for m in messages(document, "WARN")))

    def test_response_body_transform_with_upstream_path(self):
        document = definition()
        document[EXT]["upstream"]["url"] = "http://orders.example.com:8080/base"
        document[EXT]["middleware"] = {
            "operations": {"listOrders": {"transformResponseBody": {"enabled": True, "format": "json", "body": "e30="}}}
        }
        self.assertTrue(any("response body transform is not applied" in m for m in messages(document, "WARN")))

    def test_rewrite_pattern_anchored_without_listen_path(self):
        document = definition()
        document[EXT]["middleware"] = {
            "operations": {"getOrder": {"urlRewrite": {"enabled": True, "pattern": "^/orders/(\\d+)$", "rewriteTo": "/v2/$1"}}}
        }
        self.assertTrue(any("^/orders-api/" in m for m in messages(document, "WARN")))
        document[EXT]["middleware"]["operations"]["getOrder"]["urlRewrite"]["pattern"] = "^/orders-api/orders/(\\d+)$"
        self.assertEqual(messages(document, "WARN"), [])

    def test_paths_repeating_the_listen_path_and_regex_parameters(self):
        document = definition()
        document["paths"]["/orders-api/extra"] = {"get": {"operationId": "extra", "responses": {"200": {"description": "OK"}}}}
        document["paths"]["/items/{sku:[0-9]+}"] = {
            "get": {"operationId": "item", "parameters": [{"name": "sku", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}}}
        }
        text = "\n".join(messages(document, "WARN"))
        self.assertIn("repeats the listen path", text)
        self.assertIn("is ignored when the Gateway matches", text)
        self.assertEqual(messages(document, "ERROR"), [])

    def test_operation_without_operation_id(self):
        document = definition()
        del document["paths"]["/orders"]["get"]["operationId"]
        self.assertTrue(any("no `operationId`" in m for m in messages(document, "WARN")))

    def test_authentication_wiring(self):
        document = definition()
        document["components"] = {"securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}}
        document["security"] = [{"apiKey": []}]
        self.assertTrue(any("the API is open to everyone" in m for m in messages(document, "WARN")))

        document[EXT]["server"]["authentication"] = {"enabled": True}
        self.assertTrue(any("every request is refused with 401" in m for m in messages(document, "ERROR")))

        document[EXT]["server"]["authentication"] = {"enabled": True, "securitySchemes": {"apiKey": {"enabled": True}}}
        self.assertEqual(findings(document), [])

        document[EXT]["server"]["authentication"]["securitySchemes"] = {"missing": {"enabled": True}}
        self.assertTrue(any("no security scheme with this name" in m for m in messages(document, "WARN")))

    def test_compliant_mode_with_base_identity_provider(self):
        document = definition()
        document["components"] = {"securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}}
        document["security"] = [{"apiKey": []}]
        document[EXT]["server"]["authentication"] = {
            "enabled": True, "securityProcessingMode": "compliant", "baseIdentityProvider": "auth_token",
            "securitySchemes": {"apiKey": {"enabled": True}},
        }
        self.assertTrue(any("Session state is missing" in m for m in messages(document, "WARN")))

    def test_pinning_domain_with_port_and_upstream_authentication_note(self):
        document = definition()
        document[EXT]["upstream"]["certificatePinning"] = {
            "enabled": True, "domainToPublicKeysMapping": [{"domain": "orders.example.com:8443", "publicKeys": ["abc"]}]
        }
        document[EXT]["upstream"]["authentication"] = {"enabled": True, "basicAuth": {"enabled": True, "username": "u", "password": "p"}}
        self.assertTrue(any("use the bare host name" in m for m in messages(document, "ERROR")))
        self.assertTrue(any("Enterprise Edition Gateway" in m for m in messages(document, "NOTE")))

    def test_inactive_api_and_mcp_fields(self):
        document = definition()
        document[EXT]["info"]["state"]["active"] = False
        document[EXT]["middleware"] = {"mcpTools": {"anything": {}}}
        self.assertTrue(any("still serves it" in m for m in messages(document, "WARN")))
        self.assertEqual(messages(document, "ERROR"), [])
        self.assertTrue(any("MCP proxy settings are not checked" in m for m in messages(document, "NOTE")))


class CommandLine(unittest.TestCase):
    def run_cli(self, document, *flags):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "api.json"
            path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = check_tyk_oas.main([*flags, str(path)])
            return code, stdout.getvalue(), stderr.getvalue()

    def test_exit_codes(self):
        self.assertEqual(self.run_cli(definition())[0], 0)
        broken = definition()
        broken[EXT]["server"]["listenPath"]["stripPath"] = True
        code, stdout, _ = self.run_cli(broken)
        self.assertEqual(code, 1)
        self.assertIn("1 error(s)", stdout)
        self.assertEqual(self.run_cli("{not json")[0], 2)

    def test_strict_turns_warnings_into_failure(self):
        document = definition()
        document[EXT]["info"]["state"]["active"] = False
        self.assertEqual(self.run_cli(document)[0], 0)
        self.assertEqual(self.run_cli(document, "--strict")[0], 1)

    def test_notes_never_fail(self):
        document = definition()
        document[EXT]["upstream"]["authentication"] = {"enabled": True, "basicAuth": {"enabled": True, "username": "u", "password": "p"}}
        self.assertEqual(self.run_cli(document, "--strict")[0], 0)

    def test_json_output(self):
        broken = copy.deepcopy(definition())
        del broken[EXT]
        code, stdout, _ = self.run_cli(broken, "--json")
        report = json.loads(stdout)
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["findings"][0]["level"], "ERROR")


class FieldMap(unittest.TestCase):
    def test_shipped_map_matches_gateway_5_16(self):
        fields = check_tyk_oas.load_fields()
        self.assertEqual(fields["version"], "v5.16.0")
        self.assertEqual(sorted(fields["root"]["f"]), ["errorOverrides", "info", "middleware", "server", "upstream"])
        self.assertEqual(fields["root"]["r"], ["info", "upstream", "server"])
        operation = fields["defs"]["X-Tyk-Operation"]["f"]
        for name in ("allow", "block", "ignoreAuthentication", "validateRequest", "mockResponse", "urlRewrite",
                     "transformRequestMethod", "cache", "enforceTimeout", "circuitBreaker", "rateLimit"):
            self.assertIn(name, operation)

    def test_builder_converts_schema_shapes(self):
        schema = {
            "properties": {"info": {"$ref": "#/definitions/X-Info"}, "tags": {"type": "array", "items": {"type": "string"}}},
            "required": ["info"],
            "definitions": {
                "X-Info": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "mode": {"enum": ["a", "b"]},
                        "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                        "extra": {"type": "object"},
                        "list": {"type": ["array", "null"], "items": [{"$ref": "#/definitions/X-Item"}]},
                    },
                    "required": ["name"],
                },
                "X-Item": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                "X-Ops": {"type": "object", "patternProperties": {"\\S+": {"$ref": "#/definitions/X-Info"}}},
            },
        }
        built = build_extension_fields.build(schema, "v0.0.0")
        self.assertEqual(built["root"], {"f": {"info": {"ref": "X-Info"}, "tags": {"arr": {"t": "string"}}}, "r": ["info"]})
        info = built["defs"]["X-Info"]
        self.assertEqual(info["r"], ["name"])
        self.assertEqual(info["f"]["mode"], {"t": "string", "enum": ["a", "b"]})
        self.assertEqual(info["f"]["labels"], {"map": {"t": "string"}})
        self.assertEqual(info["f"]["extra"], {"any": 1})
        self.assertEqual(info["f"]["list"], {"arr": {"ref": "X-Item"}})
        self.assertEqual(built["defs"]["X-Item"], {"any": 1})
        self.assertEqual(built["defs"]["X-Ops"], {"map": {"ref": "X-Info"}})


if __name__ == "__main__":
    unittest.main()
