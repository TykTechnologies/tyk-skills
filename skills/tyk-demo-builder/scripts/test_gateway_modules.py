"""Tests for the gateway-side modules, proofs and generator fixes.

Covers the gateway-sharding, js-plugin, key-import, custom-domain and portal-catalogues
modules, the always-on HEALTH-001 proof, the Portal DCR audience check, and the generator
defect fixes (installer SIGPIPE and kubeconfig, identity discovery URLs, the validation note,
requirement ID prefixes and curated evidence, visible naming). Every generator run
here uses `--versions pinned`, so the suite needs no network.
"""

from __future__ import annotations

import http.client
import http.server
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parent
STARTER = SCRIPTS.parent / "assets" / "starter"
NEW_MODULES = ("gateway-sharding", "js-plugin", "key-import", "custom-domain", "portal-catalogues")
FULL_MODULES = "rest,api-key,jwt,portal,dcr,otel,gateway-sharding,js-plugin,key-import,custom-domain,portal-catalogues"
# Words the visible naming rule forbids in names, descriptions and hostnames.
FORBIDDEN_WORDS = re.compile(r"\b(demo|test|mock|sample|lab|evidence)\b", re.IGNORECASE)


def load_script(name: str, alias: str | None = None):
    spec = importlib.util.spec_from_file_location(alias or name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: dataclasses resolve their module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_script("init_demo_bundle", "gateway_init_demo_bundle")
validator = load_script("validate_bundle", "gateway_validate_bundle")
sanitizer = load_script("sanitize_bundle", "gateway_sanitize_bundle")


def generate(root: Path, slug: str, name: str, modules: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root), "--slug", slug,
         "--name", name, "--modules", modules, "--versions", "pinned", *extra],
        check=True, capture_output=True, text=True,
    )


def service_block(compose: str, service: str) -> str:
    """The text of one top-level compose service, up to the next two-space key."""
    match = re.search(rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^\S|\Z)", compose)
    if match is None:
        raise AssertionError(f"service {service} not found in compose.yaml")
    return match.group(1)


def read_json(path: Path):
    return json.loads(path.read_text())


class GatewayModuleSelectionTests(unittest.TestCase):
    def test_new_modules_expand_their_dependencies(self):
        self.assertEqual(generator.expand_modules("gateway-sharding", "local"), ["api-key", "gateway-sharding", "rest"])
        self.assertEqual(generator.expand_modules("js-plugin", "local"), ["api-key", "js-plugin", "rest"])
        self.assertEqual(generator.expand_modules("key-import", "local"), ["api-key", "key-import", "rest"])
        self.assertEqual(generator.expand_modules("custom-domain", "local"), ["custom-domain", "rest"])
        self.assertEqual(generator.expand_modules("portal-catalogues", "local"), ["api-key", "portal", "portal-branding", "portal-catalogues", "rest"])

    def test_generator_and_validator_agree_on_every_new_module(self):
        expected_ids = {"gateway-sharding": "SHARD-001", "js-plugin": "PLUGIN-JS-001", "key-import": "KEY-IMPORT-001",
                        "custom-domain": "DOMAIN-001", "portal-catalogues": "PORTAL-CAT-001"}
        for module in NEW_MODULES:
            with self.subTest(module=module):
                self.assertIn(module, generator.SUPPORTED)
                self.assertIn(module, validator.SUPPORTED_MODULES)
                self.assertEqual(generator.DEPENDENCIES[module], validator.MODULE_DEPENDENCIES[module])
                self.assertEqual(generator.MODULE_PROOF[module][2], expected_ids[module])
                self.assertEqual(validator.MODULE_TEST_IDS[module], expected_ids[module])

    def test_a_near_miss_module_name_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "Unsupported modules: gateway-shard"):
            generator.expand_modules("rest,gateway-shard", "local")

    def test_dcr_row_requires_the_portal_audience_proof(self):
        self.assertIn("PORTAL-DCR-AUD-001", generator.ADDITIONAL_PROOF["dcr"])


class PortAllocationTests(unittest.TestCase):
    def test_external_gateway_port_is_appended_without_moving_the_others(self):
        with mock.patch.object(generator, "port_free", return_value=True):
            ports = generator.choose_ports(30000)
        self.assertEqual(len(ports), 21)
        self.assertEqual(ports["dashboard"], 30000)
        self.assertEqual(ports["gateway"], 30002)
        self.assertEqual(ports["aistudio_router"], 30017)
        self.assertEqual(ports["gateway_external"], 30018)
        # Ports added later (vault, upstream-mtls) come after it, never before.
        self.assertEqual(list(ports)[18:], ["gateway_external", "vault", "upstream_mtls"])

    def test_an_occupied_external_port_refuses_an_explicit_base(self):
        with mock.patch.object(generator, "port_free", side_effect=lambda port: port != 30018):
            with self.assertRaisesRegex(SystemExit, "Unable to allocate"):
                generator.choose_ports(30000)

    def test_an_occupied_external_port_moves_the_automatic_block(self):
        with mock.patch.object(generator, "port_free", side_effect=lambda port: port != 23018):
            ports = generator.choose_ports(None)
        self.assertEqual(ports["dashboard"], 23020)
        self.assertEqual(ports["gateway_external"], 23038)

    def test_validator_treats_the_external_port_as_optional(self):
        self.assertIn("gateway_external", validator.OPTIONAL_PORTS)
        self.assertNotIn("gateway_external", validator.EXPECTED_PORTS)


class NamingHelperTests(unittest.TestCase):
    def test_gateway_hostnames_are_readable_and_valid_labels(self):
        self.assertEqual(generator.gateway_hostname("northwind"), "northwind-gateway")
        self.assertEqual(generator.gateway_hostname("northwind", "internal"), "northwind-gateway-internal")
        self.assertEqual(generator.gateway_hostname("northwind", "external"), "northwind-gateway-external")
        long_slug = "a" * 45 + "-bc"
        for role in ("", "internal", "external"):
            name = generator.gateway_hostname(long_slug, role)
            self.assertLessEqual(len(name), 63)
            self.assertNotIn("--", name)
            self.assertRegex(name, r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")

    def test_javascript_identifier_is_camel_case_and_starts_with_a_letter(self):
        self.assertEqual(generator.js_identifier("promo-forward-gateway"), "promoForwardGatewayCredentialMapper")
        self.assertEqual(generator.js_identifier("northwind"), "northwindCredentialMapper")
        self.assertTrue(generator.js_identifier("9-lives").isidentifier())
        self.assertTrue(generator.js_identifier("9-lives")[0].isalpha())

    def test_imported_key_values_are_minted_not_derived_from_the_slug(self):
        values = {generator.imported_key_value() for _ in range(20)}
        self.assertEqual(len(values), 20)
        for value in values:
            self.assertRegex(value, r"^migrated-[0-9a-f]{32}$")
        # Nothing about the bundle goes in, so nothing about the bundle can reproduce a key.
        self.assertEqual(generator.imported_key_value.__code__.co_argcount, 0)


class JavaScriptDriverGateTests(unittest.TestCase):
    def test_goja_driver_when_gateway_and_dashboard_are_5_15_0_or_later(self):
        for version in ("v5.15.0", "v5.15.1", "v5.16.0", "v6.0.0"):
            self.assertEqual(generator.javascript_plugin_driver(version, version), "javascript", version)
        self.assertEqual(generator.javascript_plugin_driver("v5.15.1", "v5.16.0"), "javascript")

    def test_otto_driver_below_5_15_0(self):
        for version in ("v5.14.9", "v5.13.1", "v5.8.0", ""):
            self.assertEqual(generator.javascript_plugin_driver(version, version), "otto", version)

    def test_a_mixed_pair_below_5_15_0_on_either_side_gets_otto(self):
        # A 5.14 Dashboard stores the API, and its Tyk OAS schema has no "javascript" driver.
        self.assertEqual(generator.javascript_plugin_driver("v5.15.0", "v5.14.0"), "otto")
        self.assertEqual(generator.javascript_plugin_driver("v5.14.0", "v5.15.0"), "otto")
        self.assertEqual(generator.goja_blockers({"gateway": "v5.15.0", "dashboard": "v5.14.0"}), ["Dashboard v5.14.0"])
        self.assertEqual(generator.goja_blockers({"gateway": "v5.14.2", "dashboard": "v5.14.0"}), ["Gateway v5.14.2", "Dashboard v5.14.0"])

    def test_a_bundle_with_a_5_14_dashboard_and_a_5_15_gateway_uses_otto_and_names_the_dashboard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "mixed"
            result = generate(root, "mixed-js", "Mixed JS", "js-plugin",
                              "--gateway-version", "v5.15.0", "--dashboard-version", "v5.14.0")
            api = read_json(root / "apis" / "mixed-js-compat.oas.json")
            readme = (root / "README.md").read_text()
            self.assertEqual(validator.validate_bundle(root, check_compose=False), [])
        self.assertEqual(api["x-tyk-api-gateway"]["middleware"]["global"]["pluginConfig"], {"driver": "otto"})
        self.assertIn("Dashboard v5.14.0", result.stdout)
        self.assertIn('driver "otto"', result.stdout)
        self.assertNotIn("Gateway v5.15.0", result.stdout)
        self.assertIn("This bundle targets Dashboard v5.14.0", readme)
        self.assertIn('`pluginConfig.driver: "otto"`', readme)


class GatewayBundleTests(unittest.TestCase):
    """One bundle with every gateway-side module (plus otel), and one plain bundle to compare."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.root = base / "full"
        cls.plain = base / "plain"
        cls.full_result = generate(cls.root, "northwind", "Northwind", FULL_MODULES)
        generate(cls.plain, "contoso", "Contoso", "rest,api-key")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def text(self, relative: str, root: Path | None = None) -> str:
        return ((root or self.root) / relative).read_text()

    # ---- validation and sanitising ---------------------------------------------------

    def test_full_bundle_passes_static_validation_and_sanitising(self):
        self.assertEqual(validator.validate_bundle(self.root), [])
        self.assertEqual(sanitizer.scan_bundle(self.root), [])

    def test_validator_rejects_a_missing_module_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "bundle"
            shutil.copytree(self.root, copy)
            (copy / "plugins" / "js" / "northwind-credential-mapper.js").unlink()
            (copy / "apis" / "northwind-internal-services.oas.json").unlink()
            errors = validator.validate_bundle(copy, check_compose=False)
        self.assertTrue(any("required asset for module js-plugin" in error for error in errors), errors)
        self.assertTrue(any("northwind-internal-services.oas.json: required asset for module gateway-sharding" in error for error in errors), errors)

    def copy_bundle(self, temporary: str) -> Path:
        copy = Path(temporary) / "bundle"
        shutil.copytree(self.root, copy)
        return copy

    def test_validator_requires_the_jsvm_switch_for_js_plugin(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = self.copy_bundle(temporary)
            env = copy / "config" / "gateway.env"
            env.write_text(env.read_text().replace("TYK_GW_ENABLEJSVM=true\n", "TYK_GW_ENABLEJSVM=false\n"))
            errors = validator.validate_bundle(copy, check_compose=False)
            env.write_text(env.read_text().replace("TYK_GW_ENABLEJSVM=false\n", ""))
            removed = validator.validate_bundle(copy, check_compose=False)
        for result in (errors, removed):
            self.assertIn("config/gateway.env: module js-plugin needs TYK_GW_ENABLEJSVM=true for every gateway", result)
            self.assertEqual(len(result), 1, result)

    def test_validator_requires_the_custom_domain_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = self.copy_bundle(temporary)
            env = copy / "config" / "gateway.env"
            env.write_text(env.read_text().replace("TYK_GW_ENABLECUSTOMDOMAINS=true\n", "TYK_GW_ENABLECUSTOMDOMAINS=\"false\"\n"))
            errors = validator.validate_bundle(copy, check_compose=False)
        self.assertEqual(errors, ["config/gateway.env: module custom-domain needs TYK_GW_ENABLECUSTOMDOMAINS=true for every gateway"])

    def test_validator_requires_a_segmented_external_gateway_for_sharding(self):
        compose = self.text("compose.yaml")
        external = service_block(compose, "tyk-gateway-external")
        cases = {
            "service": (compose.replace("  tyk-gateway-external:\n" + external, ""),
                        "compose.yaml: module gateway-sharding needs a tyk-gateway-external service"),
            "segmented": (compose.replace('TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED: "true"', 'TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED: "false"'),
                          "compose.yaml: tyk-gateway-external must set TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED to true (gateway-sharding)"),
            "tags": (compose.replace("      TYK_GW_DBAPPCONFOPTIONS_TAGS: external\n", ""),
                     "compose.yaml: tyk-gateway-external must set TYK_GW_DBAPPCONFOPTIONS_TAGS (gateway-sharding)"),
        }
        for case, (text, expected) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                self.assertNotEqual(text, compose)
                copy = self.copy_bundle(temporary)
                (copy / "compose.yaml").write_text(text)
                self.assertEqual(validator.validate_bundle(copy, check_compose=False), [expected])
        with tempfile.TemporaryDirectory() as temporary:
            copy = self.copy_bundle(temporary)
            config = read_json(copy / "demo.config.json")
            del config["ports"]["gateway_external"]
            (copy / "demo.config.json").write_text(json.dumps(config))
            self.assertEqual(validator.validate_bundle(copy, check_compose=False),
                             ["demo.config.json: module gateway-sharding needs ports.gateway_external"])

    def test_validator_content_checks_ignore_modules_that_are_not_selected(self):
        config = read_json(self.plain / "demo.config.json")
        self.assertEqual(validator.validate_module_configuration(self.plain, config), [])
        self.assertIn("TYK_GW_ENABLEJSVM=false\n", self.text("config/gateway.env", self.plain))

    def test_validator_reports_a_malformed_modules_list_instead_of_crashing(self):
        # The proof-map check reads the known-module set unconditionally; a malformed
        # modules value used to raise NameError there instead of reporting cleanly.
        config = read_json(self.root / "demo.config.json")
        for bad in ([], "rest", [""], ["rest", 7], None):
            with self.subTest(modules=bad):
                errors = validator.validate_config({**config, "modules": bad})
                self.assertTrue(any("modules must be a non-empty string array" in error for error in errors), errors)

    def test_validator_checks_the_requirement_prefix(self):
        config = read_json(self.root / "demo.config.json")
        self.assertEqual(config["requirement_id_prefix"], "REQ")
        self.assertEqual(validator.validate_config({**config, "requirement_id_prefix": "ACME"}), [])
        for bad in ("", "ACME-", "1ACME", "A B", 7):
            with self.subTest(prefix=bad):
                errors = validator.validate_config({**config, "requirement_id_prefix": bad})
                self.assertTrue(any("requirement_id_prefix" in error for error in errors), errors)

    # ---- gateway-sharding ---------------------------------------------------------------

    def test_sharding_adds_a_segmented_twin_of_the_primary_gateway(self):
        compose = self.text("compose.yaml")
        primary = service_block(compose, "tyk-gateway")
        external = service_block(compose, "tyk-gateway-external")
        self.assertIn("hostname: northwind-gateway-internal", primary)
        self.assertIn("hostname: northwind-gateway-external", external)
        self.assertIn('TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED: "true"', external)
        self.assertIn("TYK_GW_DBAPPCONFOPTIONS_TAGS: external", external)
        self.assertNotIn("NODEISSEGMENTED", primary)
        self.assertIn('ports: ["${BIND_ADDRESS:-127.0.0.1}:${GATEWAY_EXTERNAL_PORT}:8080"]', external)
        for block in (primary, external):
            self.assertIn("image: tykio/tyk-gateway-ee:${GATEWAY_VERSION}", block)
            self.assertIn("env_file: [./config/gateway.env]", block)
            self.assertIn("./plugins:/opt/tyk-gateway/plugins:ro", block)
            self.assertIn("TYK_GW_OPENTELEMETRY_TRACES_ENDPOINT: otel-collector:4317", block)

    def test_sharding_records_the_external_port_everywhere(self):
        port = read_json(self.root / "demo.config.json")["ports"]["gateway_external"]
        self.assertIn(f"GATEWAY_EXTERNAL_PORT={port}\n", self.text(".env"))
        self.assertIn(f"GATEWAY_EXTERNAL_PORT={port}\n", self.text(".env.example"))
        self.assertIn("GATEWAY_EXTERNAL_URL=", self.text("scripts/common.sh"))
        self.assertIn('"${GATEWAY_EXTERNAL_URL}/hello"', self.text("scripts/start-demo-stack.sh"))
        self.assertIn("gateways+=(tyk-gateway-external)", self.text("scripts/rehearse-demo.sh"))
        self.assertIn('compose restart "${gateways[@]}"', self.text("scripts/rehearse-demo.sh"))

    def test_sharding_generates_two_tagged_key_protected_apis(self):
        external = read_json(self.root / "apis" / "northwind-external-services.oas.json")
        internal = read_json(self.root / "apis" / "northwind-internal-services.oas.json")
        for api, tag, name in ((external, "external", "Northwind External Services"), (internal, "internal", "Northwind Internal Services")):
            gateway = api["x-tyk-api-gateway"]
            self.assertEqual(gateway["info"]["name"], name)
            self.assertEqual(gateway["server"]["gatewayTags"], {"enabled": True, "tags": [tag]})
            self.assertTrue(gateway["server"]["authentication"]["enabled"])
        port = read_json(self.root / "demo.config.json")["ports"]["gateway_external"]
        self.assertIn(f":{port}/", external["servers"][0]["url"])
        # Untagged APIs load only on the unsegmented gateway, so nothing else carries a tag.
        core = read_json(self.root / "apis" / "northwind-core.oas.json")
        self.assertNotIn("gatewayTags", core["x-tyk-api-gateway"]["server"])

    def test_sharding_proof_covers_placement_admin_readback_and_the_node_list(self):
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn("record SHARD-001 passed", verify)
        self.assertIn('"${internal_on_external}" == 404', verify)
        self.assertIn('"${GATEWAY_EXTERNAL_URL}/tyk/apis"', verify)
        self.assertIn("/api/system/nodes", verify)
        self.assertIn('index("northwind-gateway-internal") != null and index("northwind-gateway-external") != null', verify)

    def test_sharding_with_tls_puts_the_segmented_gateway_on_the_same_listener(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tls"
            generate(root, "tls-shard", "Secure Shard", "gateway-sharding,mtls")
            compose = (root / "compose.yaml").read_text()
            external = service_block(compose, "tyk-gateway-external")
            api = read_json(root / "apis" / "tls-shard-external-services.oas.json")
            common = (root / "scripts" / "common.sh").read_text()
            certificates = (root / "scripts" / "generate-demo-certs.sh").read_text()
        # The CA is uploaded to the Dashboard certificate list, so its subject is visible.
        self.assertIn('-subj "/CN=Secure Shard Client CA"', certificates)
        self.assertNotIn("Standalone", certificates)
        self.assertIn('TYK_GW_HTTPSERVEROPTIONS_USESSL: "true"', external)
        self.assertIn("./generated/certs/server.pem:/opt/tyk-gateway/certs/server.pem:ro", external)
        self.assertTrue(api["servers"][0]["url"].startswith("https://localhost:"))
        self.assertIn('GATEWAY_EXTERNAL_URL="${GATEWAY_EXTERNAL_URL:-https://localhost:', common)

    def test_plain_bundle_has_one_unsegmented_gateway_with_a_readable_hostname(self):
        compose = self.text("compose.yaml", self.plain)
        self.assertNotIn("tyk-gateway-external:", compose)
        self.assertIn("hostname: contoso-gateway\n", service_block(compose, "tyk-gateway"))
        self.assertNotIn("NODEISSEGMENTED", compose)
        # The port is allocated in every bundle, so offsets never move when a module is added.
        self.assertIn("gateway_external", read_json(self.plain / "demo.config.json")["ports"])

    # ---- js-plugin ----------------------------------------------------------------------

    def test_js_plugin_is_loaded_by_path_with_the_goja_driver(self):
        api = read_json(self.root / "apis" / "northwind-compat.oas.json")
        gateway = api["x-tyk-api-gateway"]
        middleware = gateway["middleware"]["global"]
        self.assertEqual(gateway["info"]["name"], "Northwind Compatibility Services")
        self.assertEqual(gateway["server"]["listenPath"]["value"], "/demo/northwind/compat/")
        self.assertEqual(middleware["pluginConfig"], {"driver": "javascript"})
        plugin = middleware["prePlugins"][0]
        self.assertEqual(plugin["path"], "/opt/tyk-gateway/plugins/js/northwind-credential-mapper.js")
        self.assertEqual(plugin["functionName"], "northwindCredentialMapper")
        source = self.text("plugins/js/northwind-credential-mapper.js")
        self.assertIn(f"var {plugin['functionName']} = new TykJS.TykMiddleware.NewMiddleware", source)

    def test_js_plugin_uses_only_what_goja_provides(self):
        source = self.text("plugins/js/northwind-credential-mapper.js")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("//"))
        self.assertNotIn("console.", code)
        self.assertNotRegex(code, r"(?<![A-Za-z0-9])_\.")
        self.assertIn("log(", code)
        self.assertIn('request.DeleteParams.push("user_key")', code)
        self.assertIn('request.SetHeaders["Authorization"] = credential', code)

    def test_js_plugin_enables_the_jsvm_on_every_gateway(self):
        self.assertIn("TYK_GW_ENABLEJSVM=true\n", self.text("config/gateway.env"))
        self.assertIn("TYK_GW_ENABLEJSVM=false\n", self.text("config/gateway.env", self.plain))
        self.assertFalse((self.plain / "plugins" / "js").exists())

    def test_js_plugin_proof_asserts_mapping_negatives_and_the_engine(self):
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn("?user_key=${PLATFORM_API_KEY}&page=2", verify)
        self.assertIn('.path == "/v1/echo?page=2"', verify)
        self.assertIn('-H "app_key: ${PLATFORM_API_KEY}"', verify)
        self.assertIn('"${none_code}" == 401', verify)
        self.assertIn("prefix=jsvm-goja", verify)
        self.assertIn('if [[ "${driver}" == javascript ]]', verify)

    def test_every_bundle_creates_the_plugins_directory_compose_mounts(self):
        for root in (self.root, self.plain):
            self.assertIn("./plugins:/opt/tyk-gateway/plugins:ro", self.text("compose.yaml", root))
            self.assertTrue((root / "plugins" / "README.md").is_file())

    def test_older_gateway_gets_the_otto_driver_and_a_note(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "otto"
            result = generate(root, "legacy-js", "Legacy JS", "js-plugin",
                              "--gateway-version", "v5.14.0", "--dashboard-version", "v5.14.0")
            api = read_json(root / "apis" / "legacy-js-compat.oas.json")
            self.assertEqual(api["x-tyk-api-gateway"]["middleware"]["global"]["pluginConfig"], {"driver": "otto"})
            self.assertIn('driver "otto"', result.stdout)
            self.assertIn('`pluginConfig.driver: "otto"`', (root / "README.md").read_text())
            self.assertEqual(validator.validate_bundle(root, check_compose=False), [])
        self.assertNotIn("otto", self.full_result.stdout)

    # ---- key-import ---------------------------------------------------------------------

    def test_key_import_ships_two_neutral_records_on_a_dedicated_no_quota_policy(self):
        records = read_json(self.root / "migration" / "imported-keys.json")
        self.assertEqual(len(records["keys"]), 2)
        self.assertEqual(len({record["key"] for record in records["keys"]}), 2)
        for record in records["keys"]:
            self.assertGreaterEqual(len(record["key"]), 3)
            self.assertTrue(record["alias"].startswith("Northwind "))
            self.assertEqual(record["meta_data"]["source"], "previous-gateway")
        policies = {policy["meta_data"]["stable_name"]: policy for policy in
                    (read_json(path) for path in (self.root / "policies").glob("*.json"))}
        migrated = policies[records["policy_stable_name"]]
        self.assertEqual(migrated["quota_max"], -1)
        self.assertIn("northwind-api-key", migrated["access_rights"])
        self.assertNotEqual(records["policy_stable_name"], "northwind-api-key-policy")

    def test_key_import_script_uses_the_custom_key_endpoint_and_asserts_the_alias(self):
        script = self.text("scripts/import-keys.sh")
        self.assertIn('"${DASHBOARD_URL}/api/keys/${key}"', script)
        self.assertIn("method=POST", script)
        self.assertIn("method=PUT", script)
        self.assertIn(".data.alias == $alias", script)
        self.assertIn("min_token_length", script)
        self.assertIn('"${ROOT_DIR}/scripts/import-keys.sh"', self.text("scripts/configure-demo.sh"))
        self.assertFalse((self.plain / "scripts" / "import-keys.sh").exists())

    def test_key_import_proof_checks_accept_reject_alias_and_reports_hashing(self):
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn('"${random_code}" == 403', verify)
        self.assertIn('"${missing_code}" == 401', verify)
        self.assertIn("/api/system/node/${node_path}", verify)
        self.assertIn(".data.hash_keys", verify)

    def test_imported_keys_are_minted_per_bundle_and_documented_as_credentials(self):
        keys = [record["key"] for record in read_json(self.root / "migration" / "imported-keys.json")["keys"]]
        with tempfile.TemporaryDirectory() as temporary:
            twin = Path(temporary) / "twin"
            generate(twin, "northwind", "Northwind", "key-import")
            twin_keys = [record["key"] for record in read_json(twin / "migration" / "imported-keys.json")["keys"]]
            # The minted values are live credentials once imported, so the file is git-ignored
            # (the plain scan skips it) and is a runtime artifact the strict scan refuses. (This
            # test used to expect an empty strict list with the file present, which let the keys
            # travel with a shared bundle.)
            shutil.rmtree(twin / ".runtime", ignore_errors=True)
            (twin / ".env").unlink()
            self.assertEqual(sanitizer.scan_bundle(twin), [])
            self.assertEqual(sanitizer.runtime_artifacts(twin), [Path("migration/imported-keys.json")])
            self.assertIn("migration/imported-keys.json", (twin / ".gitignore").read_text().splitlines())
            # The shareable example carries the records without values and passes both scans.
            example = read_json(twin / "migration" / "imported-keys.example.json")
            self.assertEqual({record["key"] for record in example["keys"]}, {generator.IMPORTED_KEY_PLACEHOLDER})
            (twin / "migration" / "imported-keys.json").unlink()
            self.assertEqual(sanitizer.scan_bundle(twin), [])
            self.assertEqual(sanitizer.runtime_artifacts(twin), [])
            self.assertEqual(sanitizer.ignored_present(twin), [])
            self.assertEqual(validator.validate_bundle(twin, check_compose=False), [])
        # Same slug and name, different keys: nothing about the bundle reproduces them.
        self.assertTrue(set(keys).isdisjoint(twin_keys))
        for key in keys + twin_keys:
            self.assertRegex(key, r"^migrated-[0-9a-f]{32}$")
            self.assertNotIn("northwind", key)
        readme = self.text("README.md")
        self.assertIn("minted at random when this bundle\nwas generated", readme)
        self.assertIn("live, no-quota consumer credentials", readme)

    def test_import_keys_mints_fresh_values_when_the_private_file_is_missing(self):
        script = self.text("scripts/import-keys.sh")
        start = script.index('export_file="${ROOT_DIR}/migration/imported-keys.json"')
        end = script.index('[[ -f "${export_file}" ]] ||')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "migration").mkdir()
            shutil.copy2(self.root / "migration" / "imported-keys.example.json", root / "migration")
            result = subprocess.run(["bash", "-c", f"set -euo pipefail\nROOT_DIR={shlex.quote(str(root))}\n" + script[start:end]],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            minted = read_json(root / "migration" / "imported-keys.json")
            example = read_json(root / "migration" / "imported-keys.example.json")
            self.assertEqual([record["alias"] for record in minted["keys"]], [record["alias"] for record in example["keys"]])
            for record in minted["keys"]:
                self.assertRegex(record["key"], r"^migrated-[0-9a-f]{32}$")
            self.assertEqual((root / "migration" / "imported-keys.json").stat().st_mode & 0o777, 0o600)

    # ---- custom-domain ------------------------------------------------------------------

    def test_custom_domain_api_keeps_the_legacy_host_and_path(self):
        api = read_json(self.root / "apis" / "northwind-legacy-path.oas.json")
        server = api["x-tyk-api-gateway"]["server"]
        self.assertEqual(api["x-tyk-api-gateway"]["info"]["name"], "Northwind Legacy Path")
        self.assertEqual(server["customDomain"], {"enabled": True, "name": "api.legacy.northwind.invalid"})
        self.assertEqual(server["listenPath"], {"value": "/api/v1/records/", "strip": True})
        self.assertFalse(server["authentication"]["enabled"])
        self.assertIn("TYK_GW_ENABLECUSTOMDOMAINS=true\n", self.text("config/gateway.env"))
        self.assertIn("TYK_GW_ENABLECUSTOMDOMAINS=false\n", self.text("config/gateway.env", self.plain))

    def test_custom_domain_proof_checks_the_host_the_strip_and_both_negatives(self):
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn('-H "Host: ${legacy_host}"', verify)
        self.assertIn('.path == "/42?fields=name"', verify)
        self.assertIn('"${without_host}" == 404 && "${other_host}" == 404', verify)

    # ---- portal-catalogues and the public-catalogue fix ---------------------------------

    def test_portal_catalogues_adds_a_partner_product_policy_and_api(self):
        policies = {policy["meta_data"]["stable_name"]: policy for policy in
                    (read_json(path) for path in (self.root / "policies").glob("*.json"))}
        partner = policies["northwind-partner-product-policy"]
        self.assertTrue(partner["partitions"]["acl"])
        self.assertEqual(list(partner["access_rights"]), ["northwind-partner-services"])
        self.assertTrue((self.root / "apis" / "northwind-partner-services.oas.json").is_file())
        script = self.text("portal/configure-catalogues.sh")
        for fragment in ("Northwind Internal Teams", "Northwind Partners", 'VisibilityStatus:"Private"',
                         "/audiences", "IsDocumentationOnly:true", "/spec-details", ".ReferenceID == $r"):
            self.assertIn(fragment, script)
        self.assertIn('"${ROOT_DIR}/portal/configure-catalogues.sh"', self.text("scripts/configure-demo.sh"))

    def test_public_catalogue_holds_only_the_generated_products(self):
        portal = self.text("portal/configure-portal.sh")
        self.assertIn("._id", portal)
        self.assertIn(".ReferenceID // empty", portal)
        self.assertIn('"northwind-portal-product-policy"', portal)
        self.assertIn('--argjson products "${public_product_ids}"', portal)
        self.assertNotIn('--argjson products "${product_ids}"', portal)

    def test_portal_catalogue_proof_logs_in_as_each_developer(self):
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn("/auth/password/login", verify)
        self.assertIn("/portal/catalogue-products", verify)
        self.assertIn('index("Northwind Internal Catalogue") != null and index("Northwind Partner Catalogue") == null', verify)

    # ---- Portal DCR audience check ------------------------------------------------------

    def test_dcr_wires_the_portal_to_the_bundled_identity_service(self):
        script = self.text("portal/configure-dcr.sh")
        for fragment in ('Type:"Other"', "http://identity:8080/.well-known/openid-configuration", "/client-types",
                         "DCREnabled:true, Templates:[$t]", "/provision", "/approve", "restore_jwt_api",
                         "evidence/portal-dcr-events.json"):
            self.assertIn(fragment, script)
        # Restored before each event and once more afterwards.
        self.assertGreaterEqual(script.count("\nrestore_jwt_api\n") + script.count("    restore_jwt_api\n"), 3)
        policies = {policy["meta_data"]["stable_name"]: policy for policy in
                    (read_json(path) for path in (self.root / "policies").glob("*.json"))}
        self.assertTrue(policies["northwind-jwt-product-policy"]["partitions"]["acl"])

    def test_dcr_row_and_proof_cover_the_audience_check(self):
        mapping = self.text("notes/requirements-mapping.md")
        row = next(line for line in mapping.splitlines() if "Demonstrate dcr" in line)
        self.assertIn("`DCR-001`, `PORTAL-DCR-AUD-001`", row)
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn("record PORTAL-DCR-AUD-001 failed", verify)
        self.assertIn("allowedAudiences", verify)
        self.assertIn(".jwksCacheTimeouts", verify)

    def test_external_identity_keeps_the_portal_dcr_check_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "external"
            generate(root, "ext-idp", "External Identity", "dcr", "--identity-mode", "external")
            self.assertIn("External identity provider: Portal DCR wiring", (root / "scripts/verify-demo.sh").read_text())
            self.assertIn("identity_provider_mode' \"${ROOT_DIR}/demo.config.json\")\" == local", (root / "scripts/configure-demo.sh").read_text())
            # The script targets the bundled identity service, which this bundle does not run.
            self.assertFalse((root / "portal" / "configure-dcr.sh").exists())
            self.assertTrue((root / "portal" / "configure-portal.sh").is_file())
            readme = (root / "README.md").read_text()
            mapping = (root / "notes" / "requirements-mapping.md").read_text()
            self.assertEqual(validator.validate_bundle(root, check_compose=False), [])
        self.assertNotIn("configure-dcr.sh", readme)
        self.assertNotIn("bundled identity service, enables DCR", readme)
        self.assertIn("## Portal DCR\n\nThis bundle uses an external identity provider", readme)
        self.assertIn("`PORTAL-DCR-AUD-001` record `blocked`", readme)
        row = next(line for line in mapping.splitlines() if "Demonstrate dcr" in line)
        self.assertIn("`DCR-001`, `PORTAL-DCR-AUD-001` | `blocked`", row)

    def test_local_identity_ships_the_portal_dcr_wiring_and_its_readme_section(self):
        self.assertTrue((self.root / "portal" / "configure-dcr.sh").is_file())
        readme = self.text("README.md")
        self.assertIn("## Portal DCR and the JWT API", readme)
        self.assertIn("`portal/configure-dcr.sh` connects Portal DCR to the bundled identity service", readme)
        self.assertNotIn("This bundle uses an external identity provider", readme)
        self.assertIn('"${ROOT_DIR}/portal/configure-dcr.sh"', self.text("scripts/configure-demo.sh"))

    # ---- HEALTH-001 ---------------------------------------------------------------------

    def test_every_bundle_runs_the_health_proof(self):
        for root in (self.root, self.plain):
            verify = self.text("scripts/verify-demo.sh", root)
            for fragment in ("record HEALTH-001 passed", '/hello")"', ".details.redis.status", ".details.dashboard.status",
                             "http://tyk-pump:8083/health", "x-tyk-authorization: not-the-node-secret"):
                self.assertIn(fragment, verify)
        self.assertIn("/live", self.text("scripts/verify-demo.sh"))

    def test_health_proof_checks_the_admin_api_of_every_gateway(self):
        verify = self.text("scripts/verify-demo.sh")
        admin = verify[verify.index("# Every gateway's admin API"):verify.index("if ${health_ok}; then")]
        self.assertIn('for index in "${!gateway_targets[@]}"; do', admin)
        self.assertIn('"${gateway_targets[index]}/tyk/apis"', admin)
        self.assertNotIn('"${GATEWAY_URL}/tyk/apis"', admin)
        # The sharding bundle puts the segmented gateway in that list.
        self.assertIn('gateway_targets+=("${GATEWAY_EXTERNAL_URL}")', verify)

    def test_health_row_sits_directly_under_rest(self):
        rows = [line for line in self.text("notes/requirements-mapping.md", self.plain).splitlines() if line.startswith("| REQ-")]
        labels = [row.split("|")[1].strip() for row in rows]
        rest = labels.index("REQ-002: Demonstrate rest")
        self.assertEqual(labels[rest + 1], "REQ-003: Demonstrate platform health")
        self.assertIn("`HEALTH-001`", rows[rest + 1])

    def test_health_row_comes_first_without_rest(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            generator.requirement_mapping(target, ["api-key"])
            rows = [line for line in (target / "notes/requirements-mapping.md").read_text().splitlines() if line.startswith("| REQ-")]
        self.assertIn("REQ-001: Demonstrate platform health", rows[0])
        self.assertIn("REQ-002: Demonstrate api-key", rows[1])

    # ---- visible naming -------------------------------------------------------------

    def visible_values(self) -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        for path in sorted((self.root / "apis").glob("*.oas.json")):
            api = read_json(path)
            values += [(path.name, api["info"]["title"]), (path.name, api["info"].get("description", "")),
                       (path.name, api["x-tyk-api-gateway"]["info"]["name"])]
        for path in sorted((self.root / "policies").glob("*.json")):
            policy = read_json(path)
            values.append((path.name, policy["name"]))
            values += [(path.name, right["api_name"]) for right in policy["access_rights"].values()]
        for record in read_json(self.root / "migration/imported-keys.json")["keys"]:
            values.append(("imported-keys.json", record["alias"]))
        return values

    def test_generated_names_derive_from_the_display_name(self):
        for source, value in self.visible_values():
            with self.subTest(source=source, value=value):
                self.assertNotRegex(value, FORBIDDEN_WORDS)
                self.assertNotIn("Standalone", value)
                self.assertIn("Northwind", value)

    def test_administrators_are_named_after_the_bundle_on_an_invalid_domain(self):
        bootstrap = self.text("scripts/bootstrap-demo-stack.sh")
        self.assertIn('\\"first_name\\":\\"Northwind\\",\\"last_name\\":\\"Administrator\\"', bootstrap)
        self.assertIn("admin@northwind.invalid", bootstrap)
        self.assertNotIn(".demo", bootstrap)
        self.assertIn("${PORTAL_ADMIN_FIRST_NAME:-Northwind}", self.text("portal/configure-portal.sh"))
        self.assertIn("portal-owner@northwind.invalid", self.text("scripts/configure-demo.sh"))
        for script in ("portal/configure-catalogues.sh", "portal/configure-dcr.sh"):
            emails = re.findall(r"[a-z-]+@[a-z0-9.-]+", self.text(script))
            self.assertTrue(emails)
            self.assertTrue(all(email.endswith("@northwind.invalid") for email in emails), emails)

    def test_theme_author_derives_from_the_display_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "branded"
            generate(root, "fabrikam", "Fabrikam", "portal-branding")
            builder = (root / "portal/build-theme.py").read_text()
        self.assertIn('manifest["author"] = "Fabrikam Platform Team"', builder)
        self.assertNotRegex(builder.split('manifest["author"] = ', 1)[1].splitlines()[0], FORBIDDEN_WORDS)


class RenderValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "evidence").mkdir()
        (self.root / "notes").mkdir()
        shutil.copy2(STARTER / "scripts" / "render-validation.py", self.root / "scripts" / "render-validation.py")
        self.note = self.root / "notes" / "validation.md"

    def tearDown(self):
        self.temporary.cleanup()

    def render(self, results: list[dict]) -> str:
        (self.root / "evidence" / "results.json").write_text(json.dumps(results))
        subprocess.run([sys.executable, str(self.root / "scripts" / "render-validation.py")], check=True, capture_output=True, text=True)
        return self.note.read_text()

    def test_curated_text_around_the_markers_survives(self):
        first = self.render([{"test_id": "REST-001", "status": "failed", "detail": "502"}])
        curated = "# Validation\n\nRun `curl -i ...` and cite gateway/api.go.\n\n"
        block = first[first.index("<!-- BEGIN"):first.index("<!-- END GENERATED RESULTS -->") + len("<!-- END GENERATED RESULTS -->")]
        self.note.write_text(curated + block + "\n\n## Findings\n\nKept verbatim.\n")
        text = self.render([{"test_id": "REST-001", "status": "passed", "detail": "a | b"}])
        self.assertTrue(text.startswith(curated))
        self.assertIn("## Findings\n\nKept verbatim.\n", text)
        self.assertIn("| `REST-001` | `passed` | a \\| b |", text)
        self.assertNotIn("`failed`", text)
        self.assertEqual(text.count("<!-- BEGIN GENERATED RESULTS"), 1)

    def test_a_note_without_markers_keeps_its_text_and_gains_the_block(self):
        self.note.write_text("# Validation\n\nHand-written commands.\n")
        text = self.render([{"test_id": "HEALTH-001", "status": "passed", "detail": "ok"}])
        self.assertTrue(text.startswith("# Validation\n\nHand-written commands.\n"))
        self.assertIn("<!-- BEGIN GENERATED RESULTS", text)
        again = self.render([{"test_id": "HEALTH-001", "status": "passed", "detail": "ok"}])
        self.assertEqual(again.count("Hand-written commands."), 1)
        self.assertEqual(again.count("<!-- BEGIN GENERATED RESULTS"), 1)

    def test_an_older_fully_generated_note_is_replaced(self):
        self.note.write_text("# Validation\n\nGenerated: 2026-01-01T00:00:00Z\n\n| Test | Status | Detail |\n| --- | --- | --- |\n| `REST-001` | `passed` | old |\n")
        text = self.render([{"test_id": "REST-001", "status": "passed", "detail": "new"}])
        self.assertNotIn("old", text)
        self.assertIn("new", text)

    def render_status(self, results: list[dict]) -> subprocess.CompletedProcess[str]:
        (self.root / "evidence" / "results.json").write_text(json.dumps(results))
        return subprocess.run([sys.executable, str(self.root / "scripts" / "render-validation.py")], capture_output=True, text=True)

    def test_malformed_marker_layouts_are_refused_and_the_note_is_left_untouched(self):
        begin = "<!-- BEGIN GENERATED RESULTS (scripts/verify-demo.sh) -->"
        end = "<!-- END GENERATED RESULTS -->"
        curated = "# Validation\n\nCurated commands and citations.\n\n"
        layouts = {
            "dangling BEGIN": f"{curated}{begin}\nold table\n\n## Findings\n\nKept.\n",
            "dangling END": f"{curated}old table\n{end}\n\n## Findings\n\nKept.\n",
            "two pairs": f"{curated}{begin}\none\n{end}\n\nMiddle text.\n\n{begin}\ntwo\n{end}\n",
            "reversed order": f"{curated}{end}\nold table\n{begin}\n\n## Findings\n",
        }
        for case, text in layouts.items():
            with self.subTest(case=case):
                self.note.write_text(text)
                before = self.note.stat().st_mtime_ns
                result = self.render_status([{"test_id": "REST-001", "status": "passed", "detail": "ok"}])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exactly one", result.stderr)
                self.assertIn("left untouched", result.stderr)
                self.assertEqual(self.note.read_text(), text)
                self.assertEqual(self.note.stat().st_mtime_ns, before)
        # No temporary file is left behind by a refused or a completed write.
        self.assertEqual([path.name for path in self.note.parent.iterdir()], ["validation.md"])

    def test_curated_text_survives_repeated_runs_after_a_dangling_begin_is_fixed(self):
        # The reproduced defect: a dangling BEGIN made the script append a new block, and the
        # next run paired the old BEGIN with the new END and deleted the curated text between.
        first = self.render([{"test_id": "REST-001", "status": "passed", "detail": "ok"}])
        curated_top = "# Validation\n\nTop notes.\n\n"
        block = first[first.index("<!-- BEGIN"):]
        self.note.write_text(curated_top + block.split("<!-- END")[0] + "\nCurated middle.\n")
        self.assertNotEqual(self.render_status([{"test_id": "REST-001", "status": "passed", "detail": "x"}]).returncode, 0)
        self.assertIn("Curated middle.", self.note.read_text())
        self.note.write_text(curated_top + block + "\n\nCurated tail.\n")
        for _ in range(3):
            text = self.render([{"test_id": "REST-001", "status": "passed", "detail": "ok"}])
        self.assertTrue(text.startswith(curated_top))
        self.assertTrue(text.endswith("\n\nCurated tail.\n"))
        self.assertEqual(text.count("<!-- BEGIN GENERATED RESULTS"), 1)

    def test_the_note_is_written_in_utf8_and_keeps_its_mode(self):
        self.note.write_text("# Validation\n\nGeprüft: café ✓\n", encoding="utf-8")
        self.note.chmod(0o640)
        text = self.render([{"test_id": "REST-001", "status": "passed", "detail": "Zürich → 200"}])
        self.assertIn("Geprüft: café ✓", text)
        self.assertIn("Zürich → 200", self.note.read_bytes().decode("utf-8"))
        self.assertEqual(self.note.stat().st_mode & 0o777, 0o640)

    def test_verify_demo_renders_through_the_marker_script(self):
        verify = (STARTER / "scripts" / "verify-demo.sh").read_text()
        self.assertIn('python3 "${ROOT_DIR}/scripts/render-validation.py"', verify)
        self.assertNotIn('} >"${ROOT_DIR}/notes/validation.md"', verify)


class RequirementPrefixTests(unittest.TestCase):
    HEADER = "| Requirement | Capability | Asset | Test | Status | Evidence |\n| --- | --- | --- | --- | --- | --- |\n"
    RUN = "2026-01-01T00:00:00Z"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "evidence").mkdir()
        (self.root / "notes").mkdir()
        shutil.copy2(STARTER / "scripts" / "update-requirements.py", self.root / "scripts" / "update-requirements.py")

    def tearDown(self):
        self.temporary.cleanup()

    def run_update(self, rows: str, results: list[dict], prefix: str | None = None) -> list[str]:
        config = {"slug": "prefix-check"}
        if prefix is not None:
            config["requirement_id_prefix"] = prefix
        (self.root / "demo.config.json").write_text(json.dumps(config))
        # verify-demo.sh stamps every result with the run that wrote it; the machine segment
        # names that run.
        results = [{"run": self.RUN, **item} for item in results]
        (self.root / "evidence" / "results.json").write_text(json.dumps(results))
        (self.root / "notes" / "requirements-mapping.md").write_text(self.HEADER + rows)
        subprocess.run([sys.executable, str(self.root / "scripts" / "update-requirements.py")], check=True, capture_output=True, text=True)
        return (self.root / "notes" / "requirements-mapping.md").read_text().splitlines()[2:]

    def test_own_prefixed_rows_are_updated(self):
        rows = self.run_update(
            "| ACME-014: Sharding | Sharding | `apis` | `SHARD-001` | `blocked` | |\n"
            "| REQ-002: Health | Health | `verify` | `HEALTH-001` | `blocked` | |\n"
            "| OTHER-1: Untouched | X | `x` | `SHARD-001` | `blocked` | |\n",
            [{"test_id": "SHARD-001", "status": "passed", "detail": "ok"}, {"test_id": "HEALTH-001", "status": "passed", "detail": "ok"}],
            prefix="ACME",
        )
        self.assertIn("`live`", rows[0])
        self.assertIn("`live`", rows[1])
        self.assertIn("`blocked`", rows[2])

    def test_without_a_configured_prefix_only_req_rows_are_updated(self):
        rows = self.run_update(
            "| ACME-014: Sharding | Sharding | `apis` | `SHARD-001` | `blocked` | |\n"
            "| REQ-001: Sharding | Sharding | `apis` | `SHARD-001` | `blocked` | |\n",
            [{"test_id": "SHARD-001", "status": "passed", "detail": "ok"}],
        )
        self.assertIn("`blocked`", rows[0])
        self.assertIn("`live`", rows[1])

    def test_curated_evidence_is_never_dropped(self):
        curated = "`evidence/results.json` plus screenshot evidence/screenshots/nodes.png"
        rows = self.run_update(
            f"| REQ-001: Sharding | Sharding | `apis` | `SHARD-001` | `blocked` | {curated} |\n",
            [{"test_id": "SHARD-001", "status": "passed", "detail": "ok"}],
        )
        self.assertIn(curated, rows[0])
        self.assertIn("`evidence/results.json` (run 2026-01-01T00:00:00Z: SHARD-001 passed)", rows[0])

    def test_an_earlier_machine_segment_and_the_placeholder_are_replaced(self):
        rows = self.run_update(
            "| REQ-001: A | A | `a` | `SHARD-001` | `blocked` | `evidence/results.json` (SHARD-001 failed) |\n"
            "| REQ-002: B | B | `b` | `SHARD-001` | `blocked` | Pending verification |\n",
            [{"test_id": "SHARD-001", "status": "passed", "detail": "ok"}],
        )
        for row in rows:
            evidence = row.strip().strip("|").split("|")[5].strip()
            self.assertEqual(evidence, "`evidence/results.json` (run 2026-01-01T00:00:00Z: SHARD-001 passed)")

    def test_an_old_machine_cell_with_parentheses_in_its_detail_is_replaced(self):
        rows = self.run_update(
            "| REQ-001: A | A | `a` | `SHARD-001` | `blocked` | `evidence/results.json` (SHARD-001 failed (HTTP 404)) |\n"
            "| REQ-002: B | B | `b` | `SHARD-001` | `blocked` | `evidence/results.json` (SHARD-001 failed (node list (empty))) |\n",
            [{"test_id": "SHARD-001", "status": "passed", "detail": "ok"}],
        )
        for row in rows:
            evidence = row.strip().strip("|").split("|")[5].strip()
            self.assertEqual(evidence, "`evidence/results.json` (run 2026-01-01T00:00:00Z: SHARD-001 passed)")

    def test_curated_text_after_a_parenthesised_segment_is_kept(self):
        curated = "`evidence/results.json` (SHARD-001 failed) plus screenshot (nodes.png)"
        rows = self.run_update(
            f"| REQ-001: A | A | `a` | `SHARD-001` | `blocked` | {curated} |\n",
            [{"test_id": "SHARD-001", "status": "passed", "detail": "ok"}],
        )
        self.assertIn(curated + " · verified: `evidence/results.json` (run 2026-01-01T00:00:00Z: SHARD-001 passed)", rows[0])

    def test_the_mapping_is_written_atomically_in_utf8(self):
        rows = self.run_update(
            "| REQ-001: Zürich routing | Weiterleitung | `a` | `SHARD-001` | `blocked` | Geprüft ✓ |\n",
            [{"test_id": "SHARD-001", "status": "passed", "detail": "ok"}],
        )
        self.assertIn("Zürich routing", rows[0])
        self.assertIn("Geprüft ✓ · verified:", rows[0])
        self.assertEqual(sorted(path.name for path in (self.root / "notes").iterdir()), ["requirements-mapping.md"])


class IdentityDiscoveryTests(unittest.TestCase):
    """Discovery URLs follow the caller's Host, so the Portal's DCR call reaches the service."""

    @classmethod
    def setUpClass(cls):
        cls.identity = load_script_from(STARTER / "upstreams" / "identity_service.py", "gateway_identity_service")
        class QuietHandler(cls.identity.Handler):
            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def discovery(self, host: str) -> dict:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.putrequest("GET", "/.well-known/openid-configuration", skip_host=True)
        connection.putheader("Host", host)
        connection.endheaders()
        body = json.loads(connection.getresponse().read())
        connection.close()
        return body

    def test_container_callers_get_the_compose_service_address(self):
        document = self.discovery("identity:8080")
        self.assertEqual(document["registration_endpoint"], "http://identity:8080/register")
        self.assertEqual(document["jwks_uri"], "http://identity:8080/.well-known/jwks.json")
        self.assertEqual(document["issuer"], "http://identity:8080")

    def test_host_callers_get_the_published_port(self):
        self.assertEqual(self.discovery("localhost:23024")["registration_endpoint"], "http://localhost:23024/register")

    def test_an_unsafe_host_header_falls_back_to_the_issuer(self):
        self.assertEqual(self.identity.advertised_base_url("evil.example/../x"), "http://identity:8080")
        self.assertEqual(self.identity.advertised_base_url(""), "http://identity:8080")

    def test_an_explicit_override_wins(self):
        with mock.patch.object(self.identity, "PUBLIC_BASE_URL", "https://login.example.invalid"):
            self.assertEqual(self.identity.advertised_base_url("identity:8080"), "https://login.example.invalid")

    def test_generated_compose_no_longer_pins_a_localhost_base_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "jwt"
            generate(root, "idp-check", "Identity Check", "jwt")
            identity = service_block((root / "compose.yaml").read_text(), "identity")
        self.assertNotRegex(identity, r"(?m)^\s+PUBLIC_BASE_URL:")
        self.assertIn("ISSUER: http://identity:8080", identity)


def load_script_from(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: dataclasses resolve their module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FAKE_CLUSTER_TOOL = r"""#!/usr/bin/env bash
tool="$(basename "$0")"
echo "${tool} $*" >>"${FAKE_STATE}/argv.log"
args="$*"
case "${tool}:${args}" in
  "kind:get clusters") [[ -f "${FAKE_STATE}/cluster" ]] && echo promo-operator; exit 0 ;;
  "kind:create cluster"*) touch "${FAKE_STATE}/cluster"; exit 0 ;;
  kind:*) exit 0 ;;
  "kubectl:config current-context") echo "${FAKE_CURRENT_CONTEXT:-workstation-context}" ;;
  # The kind context comes first and is followed by a long tail, the shape that makes a
  # `| grep -q` pipeline fail under pipefail: grep exits early and the writer gets SIGPIPE.
  "kubectl:config get-contexts -o name")
    [[ -f "${FAKE_STATE}/cluster" ]] && echo kind-promo-operator
    yes other-context | head -n 200000 ;;
  *"-n cert-manager get deployment"*) exit 0 ;;
  *"apply -f -"*) cat >/dev/null ;;
  *"create secret generic"*) exit 0 ;;
  *"get deployment -o name"*) echo deployment.apps/tyk-operator-controller-manager ;;
  *) exit 0 ;;
esac
"""


@unittest.skipUnless(shutil.which("jq"), "jq is required")
class OperatorInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.generated = Path(cls.temporary.name) / "generated"
        generate(cls.generated, "promo", "Promo", "rest,operator")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="installer-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.bundle = self.scratch / "bundle"
        (self.bundle / "operator").mkdir(parents=True)
        (self.bundle / "scripts").mkdir()
        for relative in ("operator/install-operator-kind.sh", "operator/operator-lib.sh", "scripts/common.sh", "demo.config.json"):
            shutil.copy2(self.generated / relative, self.bundle / relative)
        (self.bundle / ".env").write_text((self.generated / ".env").read_text() + "OPERATOR_VERSION=v1.5.0\nTYK_LICENSE_KEY=licence-value\n")
        runtime = self.bundle / ".runtime"
        runtime.mkdir(mode=0o700)
        (runtime / "runtime.env").write_text("export DASHBOARD_API_KEY=key-value\nexport ORG_ID=org-value\n")
        (self.scratch / "bin").mkdir()
        (self.scratch / "state").mkdir()
        for tool in ("kind", "kubectl", "helm"):
            path = self.scratch / "bin" / tool
            path.write_text(FAKE_CLUSTER_TOOL)
            path.chmod(0o755)

    def run_installer(self) -> tuple[subprocess.CompletedProcess[str], str]:
        result = subprocess.run(
            [str(self.bundle / "operator/install-operator-kind.sh")], capture_output=True, text=True, check=False,
            env=dict(os.environ, PATH=f"{self.scratch / 'bin'}{os.pathsep}{os.environ['PATH']}",
                     FAKE_STATE=str(self.scratch / "state"), FAKE_CURRENT_CONTEXT="workstation-context"),
        )
        return result, (self.scratch / "state/argv.log").read_text()

    def test_a_long_context_list_does_not_break_the_context_check(self):
        result, _ = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tyk-operator v1.5.0 is ready in kind-promo-operator", result.stdout)

    def test_kind_credentials_go_to_a_bundle_local_kubeconfig(self):
        result, calls = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--kubeconfig " + str(self.bundle / ".runtime" / "operator-kubeconfig"), calls)
        self.assertNotIn("use-context", calls)
        self.assertNotIn("set current-context", calls)

    def test_no_pipeline_in_the_installer_ends_in_grep_quiet(self):
        script = (STARTER / "operator" / "install-operator-kind.sh").read_text()
        self.assertNotRegex(script, r"\|\s*grep -[A-Za-z]*q")

    def test_later_scripts_read_the_bundle_local_kubeconfig(self):
        lib = (STARTER / "operator" / "operator-lib.sh").read_text()
        self.assertIn('KIND_KUBECONFIG_FILE="${ROOT_DIR}/.runtime/operator-kubeconfig"', lib)
        self.assertIn('export KUBECONFIG="${KIND_KUBECONFIG_FILE}"', lib)

    def test_the_kubectl_wrapper_targets_the_bundle_cluster_whatever_is_current(self):
        for relative in ("operator/kubectl.sh",):
            shutil.copy2(self.generated / relative, self.bundle / relative)
        runtime = self.bundle / ".runtime"
        (runtime / "operator-kubeconfig").write_text("apiVersion: v1\nkind: Config\n")
        (runtime / "operator-kube-context").write_text("kind-promo-operator\n")
        fake = self.scratch / "bin" / "kubectl"
        fake.write_text('#!/usr/bin/env bash\necho "KUBECONFIG=${KUBECONFIG:-unset} $*" >>"${FAKE_STATE}/argv.log"\n')
        fake.chmod(0o755)
        result = subprocess.run(
            [str(self.bundle / "operator/kubectl.sh"), "get", "tykruleset", "-A"], capture_output=True, text=True, check=False,
            env=dict(os.environ, PATH=f"{self.scratch / 'bin'}{os.pathsep}{os.environ['PATH']}",
                     FAKE_STATE=str(self.scratch / "state"), KUBECONFIG="/nonexistent/presenter-kubeconfig"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.scratch / "state/argv.log").read_text().splitlines()
        self.assertEqual(calls, [f"KUBECONFIG={runtime / 'operator-kubeconfig'} --context kind-promo-operator get tykruleset -A"])

    def test_the_kubectl_wrapper_refuses_a_non_kind_current_context_without_a_record(self):
        shutil.copy2(self.generated / "operator/kubectl.sh", self.bundle / "operator/kubectl.sh")
        fake = self.scratch / "bin" / "kubectl"
        fake.write_text('#!/usr/bin/env bash\n[[ "$*" == "config current-context" ]] && { echo shared-prod; exit 0; }\necho "$*" >>"${FAKE_STATE}/argv.log"\n')
        fake.chmod(0o755)
        result = subprocess.run(
            [str(self.bundle / "operator/kubectl.sh"), "apply", "-f", "x.yaml"], capture_output=True, text=True, check=False,
            env=dict(os.environ, PATH=f"{self.scratch / 'bin'}{os.pathsep}{os.environ['PATH']}", FAKE_STATE=str(self.scratch / "state")),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to use non-kind context shared-prod", result.stderr)
        self.assertFalse((self.scratch / "state/argv.log").exists())


# Instructions a presenter copies onto a terminal. A bare `kubectl` there reaches their own
# current context, because the kind credentials live only in .runtime/operator-kubeconfig.
KUBECTL_INSTRUCTION_FILES = (
    SCRIPTS.parent / "references" / "governance.md",
    SCRIPTS.parent / "references" / "plugins-and-operator.md",
    STARTER / "operator" / "README.md",
    STARTER / "operator" / "install-operator-kind.sh",
    STARTER / "governance-operator" / "tyk-ruleset.yaml",
    STARTER / "governance-operator" / "governed-api-compliant.yaml",
    STARTER / "governance-operator" / "governed-api-noncompliant.yaml",
    STARTER / "governance-operator" / "verify-governance.sh",
)
# `kubectl create --dry-run=client` is how the reference describes apply-operator.sh's own
# digest step (through kc, with the recorded context), not a command to type.
BARE_KUBECTL = re.compile(r"`kubectl (?!--context|create --dry-run=client)|^\s*(?:#\s*(?:or by hand:\s*)?)?kubectl (?:apply|get|delete|describe|logs|create|edit|patch)\b", re.MULTILINE)


class KubectlInstructionTests(unittest.TestCase):
    def test_no_documented_kubectl_command_relies_on_the_current_context(self):
        for path in KUBECTL_INSTRUCTION_FILES:
            with self.subTest(path=path.name):
                text = path.read_text()
                self.assertEqual([match.group(0) for match in BARE_KUBECTL.finditer(text)], [])

    def test_the_lint_catches_the_forms_it_exists_for(self):
        for sample in ("1. `kubectl get tykruleset` shows", "# or by hand: kubectl apply -f x.yaml", "kubectl get pods"):
            self.assertTrue(BARE_KUBECTL.search(sample), sample)
        for sample in ("`./operator/kubectl.sh get tykruleset`", "`KUBECONFIG=.runtime/operator-kubeconfig kubectl --context kind-x get pods`", "a bare `kubectl` reaches"):
            self.assertFalse(BARE_KUBECTL.search(sample), sample)

    def test_the_demo_script_and_readme_use_the_wrapper(self):
        script = (SCRIPTS.parent / "references" / "governance.md").read_text()
        self.assertIn("`./operator/kubectl.sh apply -f .runtime/operator/governed-api-noncompliant.yaml`", script)
        self.assertIn("`./operator/kubectl.sh get tykruleset -A`", script)
        readme = (STARTER / "operator" / "README.md").read_text()
        self.assertIn("### kubectl by hand: use ./operator/kubectl.sh", readme)
        self.assertIn(".runtime/operator-kubeconfig", readme)


class LoopbackBindingTests(unittest.TestCase):
    """Every host-published port defaults to loopback through BIND_ADDRESS.

    The identity fixture's POST /token signs any claims it is sent, so a port published on
    every interface would let any network peer mint tokens for the demo's JWT trust domain.
    """

    # Between them these cover every service the generator publishes.
    MODULE_SETS = (
        "rest,api-key,jwt,dcr,portal,websocket,graphql,streams,otel,gateway-sharding,mcp",
        "rest,api-key,upstream-mtls,vault,ai-studio",
    )
    BOUND = re.compile(r'^(?:\$\{BIND_ADDRESS:-127\.0\.0\.1\}|127\.0\.0\.1):(?:\$\{[A-Z_]+_PORT\}|\d+):\d+$')

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.roots = []
        for index, modules in enumerate(cls.MODULE_SETS):
            root = Path(cls.temporary.name) / f"bundle-{index}"
            generate(root, f"bind-check-{index}", "Bind Check", modules)
            cls.roots.append(root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @staticmethod
    def published(compose: str) -> list[str]:
        entries = []
        for match in re.finditer(r"(?m)^\s*ports:\s*(\[[^\]]*\]|\n(?:\s*-\s*.*\n)+)", compose):
            entries += re.findall(r'"([^"]+)"', match.group(1))
        return entries

    def test_every_published_port_binds_to_loopback_by_default(self):
        services = set()
        for root in self.roots:
            compose = (root / "compose.yaml").read_text()
            entries = self.published(compose)
            self.assertTrue(entries)
            for entry in entries:
                with self.subTest(bundle=root.name, entry=entry):
                    self.assertRegex(entry, self.BOUND)
            services.update(re.findall(r"\$\{([A-Z_]+_PORT)\}", " ".join(entries)))
            companion = root / "companion" / "ai-studio" / "compose.yaml"
            if companion.is_file():
                for entry in self.published(companion.read_text()):
                    self.assertTrue(entry.startswith("127.0.0.1:"), entry)
        for port in ("DASHBOARD_PORT", "GATEWAY_PORT", "UPSTREAM_PORT", "IDENTITY_PORT", "PORTAL_PORT",
                     "GATEWAY_EXTERNAL_PORT", "UPSTREAM_MTLS_PORT", "KAFKA_PORT", "MCP_MOCK_PORT"):
            self.assertIn(port, services)

    def test_the_identity_fixture_is_published_on_loopback_only(self):
        identity = service_block((self.roots[0] / "compose.yaml").read_text(), "identity")
        self.assertIn('ports: ["${BIND_ADDRESS:-127.0.0.1}:${IDENTITY_PORT}:8080"]', identity)

    def test_env_files_default_bind_address_to_loopback_and_name_the_risk(self):
        for name in (".env", ".env.example"):
            text = (self.roots[0] / name).read_text()
            self.assertEqual(re.findall(r"(?m)^BIND_ADDRESS=.*$", text), ["BIND_ADDRESS=127.0.0.1"], name)
            self.assertIn("mint tokens", text)
        readme = (self.roots[0] / "README.md").read_text()
        self.assertIn("## Network exposure", readme)
        self.assertIn("BIND_ADDRESS=0.0.0.0", readme)
        self.assertIn("signs\nwhatever claims", readme)

    def test_tyk_sync_wrapper_reaches_a_loopback_dashboard_on_linux_engines(self):
        wrapper = (self.roots[0] / "ci" / "tyk-sync-docker.sh").read_text() if (self.roots[0] / "ci" / "tyk-sync-docker.sh").is_file() \
            else (STARTER / "ci" / "tyk-sync-docker.sh").read_text()
        self.assertIn("--network host --add-host host.docker.internal:127.0.0.1", wrapper)
        self.assertIn("--add-host host.docker.internal:host-gateway", wrapper)

    @unittest.skipUnless(shutil.which("docker") and subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0,
                         "docker compose is required")
    def test_docker_compose_resolves_every_host_ip_to_loopback(self):
        for root in self.roots:
            for bind, expected in ((None, "127.0.0.1"), ("0.0.0.0", "0.0.0.0")):
                env = dict(os.environ)
                env.pop("BIND_ADDRESS", None)
                if bind:
                    env["BIND_ADDRESS"] = bind
                result = subprocess.run(["docker", "compose", "--project-directory", str(root), "config", "--format", "json"],
                                        capture_output=True, text=True, env=env, cwd=root)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = json.loads(result.stdout)
                host_ips = {port.get("host_ip") for service in config["services"].values() for port in service.get("ports", [])}
                self.assertTrue(host_ips)
                if bind:
                    # Vault and the MCP Inspector stay on loopback even when presenting remotely.
                    self.assertLessEqual(host_ips, {expected, "127.0.0.1"})
                    self.assertIn(expected, host_ips)
                else:
                    self.assertEqual(host_ips, {expected})


class GitignoreDepthTests(unittest.TestCase):
    """Ignore patterns must hold at any depth: a companion stack keeps its own evidence bodies."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        generate(cls.root, "ignore-check", "Ignore Check", "rest")
        cls.paths = {
            "evidence/bodies/health.json": True,
            "companion/ai-studio/evidence/bodies/router.json": True,
            "companion/ai-studio/.runtime/state.env": True,
            "companion/ai-studio/secret.env": True,
            ".runtime/runtime.env": True,
            "evidence/results.json": False,
            "evidence/screenshots/nodes.png": False,
            "companion/ai-studio/evidence/validation.md": False,
        }
        for relative in cls.paths:
            path = cls.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_git_ignores_bodies_runtime_and_secrets_at_any_depth(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for relative, ignored in self.paths.items():
            with self.subTest(path=relative):
                result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", relative], check=False)
                self.assertEqual(result.returncode == 0, ignored)

    def test_the_sanitiser_reads_the_patterns_the_way_git_does(self):
        patterns = sanitizer.ignore_patterns(self.root)
        self.assertIn("**/evidence/bodies/", patterns)
        for relative, ignored in self.paths.items():
            with self.subTest(path=relative):
                self.assertEqual(sanitizer.path_is_ignored(Path(relative), patterns), ignored)

    def test_the_validator_requires_the_any_depth_bodies_pattern(self):
        self.assertIn("**/evidence/bodies/", validator.REQUIRED_IGNORES)
        self.assertNotIn("evidence/bodies/", validator.REQUIRED_IGNORES)


@unittest.skipUnless(shutil.which("jq"), "jq is required")
class VerifyResultsAlwaysWrittenTests(unittest.TestCase):
    """verify-demo.sh writes results.json, the note and the rows on every exit."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.generated = Path(cls.temporary.name) / "generated"
        generate(cls.generated, "always", "Always", "rest,api-key,portal")
        cls.verify = (cls.generated / "scripts" / "verify-demo.sh").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="verify-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.bundle = self.scratch / "bundle"
        for relative in ("scripts/render-validation.py", "scripts/update-requirements.py", "notes/requirements-mapping.md", "demo.config.json"):
            (self.bundle / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.generated / relative, self.bundle / relative)
        (self.bundle / "evidence").mkdir()

    def run_slice(self, body: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        """The verify-demo.sh preamble (record and the EXIT trap) followed by `body`."""
        start = self.verify.index("results='[]'")
        end = self.verify.index("# -- end of the results trap --")
        harness = self.scratch / "harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"ROOT_DIR={json.dumps(str(self.bundle))}\n"
            'module_enabled() { [[ " ${MODULES:-} " == *" $1 "* ]]; }\n'
            + self.verify[start:end] + "\n" + body
        )
        return subprocess.run(["bash", str(harness)], capture_output=True, text=True, check=False,
                              env=dict(os.environ, **(extra_env or {})))

    def results(self) -> list[dict]:
        return read_json(self.bundle / "evidence" / "results.json")

    def test_an_aborted_run_still_writes_results_the_note_and_the_rows(self):
        result = self.run_slice(
            'record REST-001 passed "first check ran"\n'
            'value="$(curl -fsS "http://127.0.0.1:9/unreachable" -H "authorization: ${SECRET_VALUE}")"\n'
            'record HEALTH-001 passed "never reached"\n',
            {"SECRET_VALUE": "do-not-print-me"},
        )
        self.assertNotEqual(result.returncode, 0)
        results = self.results()
        self.assertEqual([item["test_id"] for item in results], ["REST-001", "VERIFY-RUN-001"])
        self.assertEqual(results[1]["status"], "failed")
        self.assertIn("stopped at line", results[1]["detail"])
        self.assertIn("${SECRET_VALUE}", results[1]["detail"])
        self.assertNotIn("do-not-print-me", json.dumps(results))
        note = (self.bundle / "notes" / "validation.md").read_text()
        self.assertIn("`VERIFY-RUN-001` | `failed`", note)
        mapping = (self.bundle / "notes" / "requirements-mapping.md").read_text()
        self.assertIn("REST-001 passed", mapping)

    def test_a_completed_run_exits_with_the_failure_flag_and_no_abort_row(self):
        result = self.run_slice('record REST-001 failed "502"\nverify_completed=true\nexit "${failed}"\n')
        self.assertEqual(result.returncode, 1)
        self.assertEqual([item["test_id"] for item in self.results()], ["REST-001"])
        passed = self.run_slice('record REST-001 passed "ok"\nverify_completed=true\nexit "${failed}"\n')
        self.assertEqual(passed.returncode, 0, passed.stderr)

    def test_every_result_and_requirement_row_names_the_run(self):
        result = self.run_slice('record REST-001 passed "ok"\nverify_completed=true\nexit "${failed}"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        runs = {item.get("run") for item in self.results()}
        self.assertEqual(len(runs), 1)
        run = runs.pop()
        self.assertRegex(run, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        mapping = (self.bundle / "notes" / "requirements-mapping.md").read_text()
        self.assertIn(f"(run {run}: REST-001 passed", mapping)

    def test_a_portal_error_records_portal_001_and_the_run_continues(self):
        start = self.verify.index("if module_enabled portal; then\n  # portal_read")
        end = self.verify.index("if module_enabled portal-branding; then")
        result = self.run_slice(
            self.verify[start:end] + 'record HEALTH-001 passed "after the portal block"\nverify_completed=true\nexit "${failed}"\n',
            {"MODULES": "portal", "PORTAL_URL": "http://127.0.0.1:9", "PORTAL_API_TOKEN": "unused"},
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        ids = [(item["test_id"], item["status"]) for item in self.results()]
        self.assertEqual(ids, [("PORTAL-001", "failed"), ("HEALTH-001", "passed")])

    def test_the_end_of_verify_demo_leaves_the_writing_to_the_trap(self):
        self.assertIn("trap 'finish_verification \"$?\" \"${BASH_COMMAND}\" \"${LINENO}\"' EXIT", self.verify)
        tail = self.verify[self.verify.rindex("verify_completed=true"):]
        self.assertEqual(tail.strip(), 'verify_completed=true\nexit "${failed}"')
        self.assertLess(self.verify.index("trap 'finish_verification"), self.verify.index("# HEALTH-001, for every bundle"))


@unittest.skipUnless(shutil.which("jq"), "jq is required")
class CredentialsStayOffTheCommandLineTests(unittest.TestCase):
    """Passwords and keys reach curl on stdin and jq through its environment, never argv."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        generate(cls.root, "argv-check", "Argv Check", "rest,api-key,basic-auth,jwt,portal,dcr,portal-catalogues")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def text(self, relative: str) -> str:
        return (self.root / relative).read_text()

    def test_no_credential_is_expanded_into_a_curl_or_jq_argument(self):
        scripts = ("scripts/verify-demo.sh", "scripts/configure-demo.sh", "scripts/bootstrap-demo-stack.sh",
                   "portal/configure-portal.sh", "portal/configure-catalogues.sh", "portal/configure-dcr.sh")
        argv_secret = re.compile(r'--(?:data(?:-binary|-urlencode)?|arg) [a-z_]*\s*"[^"]*\$\{?[A-Za-z_]*(?:password|PASSWORD|secret|SECRET|DASHBOARD_API_KEY)')
        for relative in scripts:
            with self.subTest(script=relative):
                self.assertEqual(argv_secret.findall(self.text(relative)), [])
        self.assertNotIn('--data-binary "$3"', self.text("portal/configure-catalogues.sh"))
        self.assertNotIn('--data-binary "$3"', self.text("portal/configure-dcr.sh"))
        self.assertIn('--data-urlencode "password@-"', self.text("scripts/verify-demo.sh"))
        self.assertIn("Secret:env.provider_secret", self.text("portal/configure-portal.sh"))

    def run_portal_json(self, script: str) -> tuple[str, str]:
        """Call the script's own portal_json with a password payload against a recording curl."""
        source = self.text(script)
        start = source.index("portal_api() {")
        json_at = source.index("portal_json()", start)
        line_end = source.index("\n", json_at)
        # A one-line definition ends on its own line; a block ends at the next column-0 brace.
        end = line_end + 1 if source[json_at:line_end].rstrip().endswith("}") else source.index("\n}\n", json_at) + 3
        bin_dir = Path(self.temporary.name) / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = Path(self.temporary.name) / f"{Path(script).stem}.log"
        (bin_dir / "curl").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >"{log}.argv"\ncat >"{log}.stdin"\n')
        (bin_dir / "curl").chmod(0o755)
        harness = Path(self.temporary.name) / "portal-json.sh"
        harness.write_text("#!/usr/bin/env bash\nset -euo pipefail\nPORTAL_API_BASE=http://portal.invalid/portal-api\nPORTAL_API_TOKEN=token\n"
                           + source[start:end] + "\nportal_json POST /users '{\"Password\":\"s3cret-value\"}'\n")
        subprocess.run(["bash", str(harness)], check=True, capture_output=True, text=True,
                       env=dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}"))
        return Path(f"{log}.argv").read_text(), Path(f"{log}.stdin").read_text()

    def test_portal_json_sends_the_body_on_stdin(self):
        for script in ("portal/configure-catalogues.sh", "portal/configure-dcr.sh"):
            with self.subTest(script=script):
                argv, stdin = self.run_portal_json(script)
                self.assertNotIn("s3cret-value", argv)
                self.assertIn("--data-binary @-", argv)
                self.assertEqual(json.loads(stdin), {"Password": "s3cret-value"})


if __name__ == "__main__":
    unittest.main()
