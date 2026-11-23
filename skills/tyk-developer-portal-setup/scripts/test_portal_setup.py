import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portal_setup as ps  # noqa: E402


class ListAllTest(unittest.TestCase):
    def test_reads_pages_until_a_short_page(self):
        calls = []
        data = list(range(250))

        def fetch(path):
            calls.append(path)
            page = int(path.rsplit("page=", 1)[1])
            return data[(page - 1) * 100 : page * 100]

        self.assertEqual(ps.list_all(fetch, "/products"), data)
        self.assertEqual(calls[0], "/products?per_page=100&page=1")
        self.assertEqual(len(calls), 3)

    def test_exact_multiple_reads_one_empty_page(self):
        data = list(range(100))
        pages = []

        def fetch(path):
            page = int(path.rsplit("page=", 1)[1])
            pages.append(page)
            return data[(page - 1) * 100 : page * 100]

        self.assertEqual(len(ps.list_all(fetch, "/users?x=1")), 100)
        self.assertEqual(pages, [1, 2])

    def test_rejects_non_list(self):
        with self.assertRaises(ValueError):
            ps.list_all(lambda path: {"errors": []}, "/products")


class ClassifyPolicyTest(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(ps.classify_policy({"partitions": {"acl": True}}), "product")
        self.assertEqual(ps.classify_policy({"partitions": {"rate_limit": True, "quota": True}}), "plan")
        self.assertEqual(ps.classify_policy({"partitions": {"quota": True}}), "plan")
        self.assertEqual(ps.classify_policy({"partitions": {"acl": True, "rate_limit": True}}), "both")
        self.assertEqual(ps.classify_policy({"partitions": {"acl": False, "per_api": False}}), "neither")
        self.assertEqual(ps.classify_policy({}), "neither")


class InventoryProblemsTest(unittest.TestCase):
    def test_flags_what_hides_products(self):
        products = [
            {"ID": 1, "Name": "a", "Catalogues": None, "APIDetails": [{"APIType": "REST", "OASDocument": "x.json"}]},
            {"ID": 2, "Name": "b", "Catalogues": ["Public"], "APIDetails": [{"APIType": "REST", "OASDocument": "", "OASUrl": ""}]},
            {"ID": 3, "Name": "c", "Catalogues": ["Public"], "DCREnabled": True, "Templates": None, "Scopes": "",
             "APIDetails": [{"APIType": "REST", "OASUrl": "https://example.com/oas.json"}]},
            {"ID": 4, "Name": "mcp", "Catalogues": ["Public"], "APIDetails": [{"APIType": "MCP"}]},
        ]
        plans = [{"ID": 1, "Name": "free", "Catalogues": None}, {"ID": 2, "Name": "gold", "Catalogues": ["1"]}]
        catalogues = [
            {"ID": 3, "Name": "Partners", "VisibilityStatus": "Private", "Organisations": None,
             "OrgCatalogues": [{"TeamID": 24, "Organisation": ""}]},
            {"ID": 2, "Name": "Private Catalogue", "VisibilityStatus": "Private", "Organisations": None, "OrgCatalogues": []},
            {"ID": 4, "Name": "Ok", "VisibilityStatus": "Private", "Organisations": ["Partner A"], "OrgCatalogues": []},
        ]
        problems = ps.inventory_problems(products, plans, catalogues)
        text = "\n".join(problems)
        self.assertIn("product 1 'a' is in no catalogue", text)
        self.assertIn("product 2 'b' has no API specification", text)
        self.assertNotIn("product 3 'c' has no API specification", text)
        self.assertIn("product 3 'c' has DCR on but no client type", text)
        self.assertIn("product 3 'c' has DCR on but no scope", text)
        self.assertNotIn("'mcp'", text)
        self.assertIn("plan 1 'free' is in no catalogue", text)
        self.assertNotIn("'gold'", text)
        self.assertIn("catalogue 3 'Partners' has a team audience stored without an organisation", text)
        self.assertIn("catalogue 2 'Private Catalogue' is private with no audience", text)
        self.assertNotIn("'Ok'", text)


SETTINGS_PAGE = """
<meta name="csrf-token" content="tok123">
<form class="qor-form" action="/admin/general" method="POST">
<input name="_method" value="PUT" type="hidden">
<input type="radio" name="QorResource.APIProductAccessFlow" value="direct">
<input type="radio" name="QorResource.APIProductAccessFlow" value="cart" checked>
<label><input name="QorResource.RegistrationAllow" value="true" type="checkbox" value="false" checked>
<input type="hidden" name="QorResource.RegistrationAllow" value="false"></label>
<label><input name="QorResource.OrganizationAutoApprove" value="true" type="checkbox" value="false">
<input type="hidden" name="QorResource.OrganizationAutoApprove" value="false"></label>
<input type="text" name="QorResource.EmailAdmin" value="">
<input type="password" name="QorResource.EmailSMTPPass" value="">
<select name="QorResource.PortalState"><option value="1" selected>Live</option></select>
<textarea name="QorResource.RobotsTxtContent"></textarea>
<input type="submit" name="submit" value="Save">
</form>
"""


class SettingsFormTest(unittest.TestCase):
    def setUp(self):
        parsed = ps.parse_page(SETTINGS_PAGE)
        self.csrf = parsed.csrf_meta
        self.fields = parsed.forms[0]["fields"]

    def test_reads_effective_values(self):
        self.assertEqual(self.csrf, "tok123")
        values = ps.current_settings(self.fields)
        self.assertEqual(values["QorResource.RegistrationAllow"], "true")
        self.assertEqual(values["QorResource.OrganizationAutoApprove"], "false")
        self.assertEqual(values["QorResource.APIProductAccessFlow"], "cart")
        self.assertEqual(values["QorResource.PortalState"], "1")

    def test_turning_a_box_off_sends_only_false(self):
        body = ps.settings_submission(self.fields, {"QorResource.RegistrationAllow": "false"})
        self.assertEqual([v for n, v in body if n == "QorResource.RegistrationAllow"], ["false"])

    def test_turning_a_box_on_sends_true_then_false(self):
        body = ps.settings_submission(self.fields, {"QorResource.OrganizationAutoApprove": "true"})
        self.assertEqual([v for n, v in body if n == "QorResource.OrganizationAutoApprove"], ["true", "false"])
        self.assertEqual([v for n, v in body if n == "QorResource.RegistrationAllow"], ["true", "false"])

    def test_radio_change_and_untouched_fields(self):
        body = dict(ps.settings_submission(self.fields, {"QorResource.APIProductAccessFlow": "direct"}))
        self.assertEqual(body["QorResource.APIProductAccessFlow"], "direct")
        self.assertEqual(body["_method"], "PUT")
        self.assertNotIn("submit", body)

    def test_empty_robots_box_is_filled(self):
        body = dict(ps.settings_submission(self.fields, {"QorResource.EmailAdmin": "api@example.com"}))
        self.assertEqual(body["QorResource.RobotsTxtContent"], ps.ROBOTS_DEFAULT)
        self.assertEqual(body["QorResource.EmailAdmin"], "api@example.com")


APP_PAGE_TOKEN = """
<h6 class="heading-small text-default mr-4 mb-1">Token</h6><span>A unique ID</span>
<input type="password" class="input-as-span content-field" value="eyJvcmciOiJ4In0=" disabled>
<h6 class="heading-small text-default mr-4 mb-1">Token Hash</h6>
<input type="password" class="input-as-span content-field" value="abcdef0123" disabled>
"""

APP_PAGE_OAUTH = """
<h6 class="heading-small text-default mr-4 mb-1">Client ID</h6>
<input type="password" class="input-as-span content-field" value="2322a267-client" disabled>
<h6 class="heading-small text-default mr-4 mb-1">Secret</h6>
<input type="password" class="input-as-span content-field" value="s3cr3t-value" disabled>
<h6 class="heading-small text-default mr-4 mb-1">Token endpoint auth method:</h6>
"""


class CredentialTest(unittest.TestCase):
    def test_auth_token(self):
        self.assertEqual(ps.credential_from_app_page(APP_PAGE_TOKEN), {"Token": "eyJvcmciOiJ4In0="})

    def test_oauth_client(self):
        cred = ps.credential_from_app_page(APP_PAGE_OAUTH)
        self.assertEqual(cred, {"Client ID": "2322a267-client", "Secret": "s3cr3t-value"})

    def test_nothing_issued(self):
        self.assertEqual(ps.credential_from_app_page("<p>pending approval</p>"), {})


class HelpersTest(unittest.TestCase):
    def test_multipart_carries_every_field(self):
        body, ctype = ps.multipart([("a", "1"), ("a", "2"), ("client_name", "app")])
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        text = body.decode()
        self.assertEqual(text.count('name="a"'), 2)
        self.assertIn("app", text)

    def test_settings_leave_mail_fields_to_branding(self):
        self.assertFalse([name for name in ps.SETTINGS.values() if "Email" in name])

    def test_redact(self):
        out = ps.redact('{"Secret": "abcdef123456", "Name": "x"}')
        self.assertNotIn("abcdef123456", out)
        self.assertIn('"Name": "x"', out)


if __name__ == "__main__":
    unittest.main()
