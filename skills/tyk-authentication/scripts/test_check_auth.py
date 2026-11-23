import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import check_auth as c

BASE = {
    "openapi": "3.0.3",
    "info": {"title": "t", "version": "1"},
    "paths": {"/orders": {"get": {"operationId": "listOrders", "responses": {"200": {"description": "OK"}}}}},
    "components": {"securitySchemes": {}},
    "security": [],
    "x-tyk-api-gateway": {"info": {"id": "t", "orgId": "default", "name": "t", "state": {"active": True}},
                          "upstream": {"url": "http://upstream"},
                          "server": {"listenPath": {"value": "/t/", "strip": True},
                                     "authentication": {"enabled": True, "securitySchemes": {}}}},
}
HDR = {"enabled": True, "name": "Authorization"}


def doc(schemes=None, tyk=None, security=None, **auth):
    d = copy.deepcopy(BASE)
    d["components"]["securitySchemes"] = schemes or {}
    d["security"] = security if security is not None else [{n: []} for n in (schemes or {})][:1]
    a = d["x-tyk-api-gateway"]["server"]["authentication"]
    a["securitySchemes"] = tyk or {}
    a.update(auth)
    return d


def levels(findings, needle):
    return [f[0] for f in findings if needle in f[2]]


JWT_OAS = {"jwt": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}


class Basic(unittest.TestCase):
    def test_basic_without_header_is_error(self):
        f = c.check(doc({"b": {"type": "http", "scheme": "basic"}}, {"b": {"enabled": True}}))
        self.assertEqual(levels(f, "Basic scheme"), ["ERROR"])

    def test_basic_with_header_is_clean(self):
        f = c.check(doc({"b": {"type": "http", "scheme": "basic"}}, {"b": {"enabled": True, "header": HDR}}))
        self.assertEqual(f, [])


class JWT(unittest.TestCase):
    def full(self, **extra):
        cfg = {"enabled": True, "header": HDR, "signingMethod": "rsa",
               "jwksURIs": [{"url": "http://idp/jwks", "cacheTimeout": "60s"}],
               "allowedIssuers": ["i"], "allowedAudiences": ["a"]}
        cfg.update(extra)
        return cfg

    def test_clean(self):
        self.assertEqual(c.check(doc(JWT_OAS, {"jwt": self.full()})), [])

    def test_no_source(self):
        cfg = self.full()
        cfg.pop("header")
        self.assertEqual(levels(c.check(doc(JWT_OAS, {"jwt": cfg})), "JWT scheme with no"), ["ERROR"])

    def test_base_policy_claims_without_field_name(self):
        f = c.check(doc(JWT_OAS, {"jwt": self.full(basePolicyClaims=["pol"])}))
        self.assertEqual(levels(f, "policyFieldName"), ["WARN"])
        f = c.check(doc(JWT_OAS, {"jwt": self.full(basePolicyClaims=["pol"], policyFieldName="pol")}))
        self.assertEqual(levels(f, "policyFieldName"), [])

    def test_missing_iss_and_aud(self):
        cfg = self.full()
        cfg.pop("allowedIssuers")
        cfg.pop("allowedAudiences")
        f = c.check(doc(JWT_OAS, {"jwt": cfg}))
        self.assertEqual(levels(f, "allowedIssuers"), ["WARN"])
        self.assertEqual(levels(f, "allowedAudiences"), ["WARN"])

    def test_allowed_subjects_needs_skip_kid(self):
        f = c.check(doc(JWT_OAS, {"jwt": self.full(allowedSubjects=["partner-a"])}))
        self.assertEqual(levels(f, "skipKid"), ["WARN"])
        f = c.check(doc(JWT_OAS, {"jwt": self.full(allowedSubjects=["partner-a"], skipKid=True)}))
        self.assertEqual(levels(f, "skipKid"), [])

    def test_cache_timeout_note(self):
        f = c.check(doc(JWT_OAS, {"jwt": self.full(jwksURIs=[{"url": "http://idp/jwks"}])}))
        self.assertEqual(levels(f, "cacheTimeout"), ["NOTE"])


class OAuth2ScopeCheck(unittest.TestCase):
    def make(self, strip=False, op_opt_in=False):
        schemes = dict(JWT_OAS)
        schemes["idp"] = {"type": "oauth2", "flows": {"clientCredentials": {"tokenUrl": "http://idp/t", "scopes": {}}}}
        tyk = {"jwt": {"enabled": True, "header": HDR, "signingMethod": "rsa", "jwksURIs": [{"url": "u", "cacheTimeout": "1m"}],
                       "allowedIssuers": ["i"], "allowedAudiences": ["a"]},
               "idp": {"enabled": True, "header": HDR, "scopeCheck": {"enabled": True, "scopeSource": "operation"}}}
        d = doc(schemes, tyk, security=[{"jwt": []}], stripAuthorizationData=strip)
        d["paths"]["/orders"]["get"]["security"] = [{"idp": ["orders:read"]}]
        if op_opt_in:
            d["x-tyk-api-gateway"]["middleware"] = {"operations": {"listOrders": {"scopeCheck": {"enabled": True}}}}
        return d

    def test_strip_is_error(self):
        self.assertEqual(levels(c.check(self.make(strip=True, op_opt_in=True)), "stripAuthorizationData"), ["ERROR"])

    def test_operation_needs_opt_in(self):
        self.assertEqual(levels(c.check(self.make()), "not enforced"), ["WARN"])
        self.assertEqual(c.check(self.make(op_opt_in=True)), [])


class Others(unittest.TestCase):
    def test_hmac(self):
        f = c.check(doc(hmac={"enabled": True}))
        self.assertEqual(levels(f, "HMAC with no"), ["ERROR"])
        self.assertEqual(levels(f, "allowedClockSkew"), ["WARN"])
        f = c.check(doc(hmac={"enabled": True, "header": HDR, "allowedClockSkew": 5000}))
        self.assertEqual(f, [])

    def test_two_security_entries_in_legacy(self):
        schemes = {"k": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
        schemes.update(JWT_OAS)
        tyk = {"k": {"enabled": True}}
        f = c.check(doc(schemes, tyk, security=[{"k": []}, {"jwt": []}]))
        self.assertEqual(levels(f, "legacy mode"), ["WARN"])
        f = c.check(doc(schemes, tyk, security=[{"k": []}, {"jwt": []}], securityProcessingMode="compliant"))
        self.assertEqual(levels(f, "legacy mode"), [])

    def test_external_oauth_note(self):
        schemes = {"o": {"type": "oauth2", "flows": {}}}
        f = c.check(doc(schemes, {"o": {"enabled": True, "header": HDR, "providers": [{"introspection": {"enabled": True}}]}}))
        self.assertEqual(levels(f, "deprecated"), ["NOTE"])

    def test_client_certificates_without_auth(self):
        d = copy.deepcopy(BASE)
        d["x-tyk-api-gateway"]["server"]["authentication"] = {"enabled": False}
        d["x-tyk-api-gateway"]["server"]["clientCertificates"] = {"enabled": True, "allowlist": ["x"]}
        self.assertEqual(levels(c.check(d), "TLS level"), ["NOTE"])


class Main(unittest.TestCase):
    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as fh:
                json.dump(doc({"b": {"type": "http", "scheme": "basic"}}, {"b": {"enabled": True}}), fh)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(c.main([bad]), 1)
                self.assertEqual(c.main([os.path.join(d, "missing.json")]), 2)


if __name__ == "__main__":
    unittest.main()
