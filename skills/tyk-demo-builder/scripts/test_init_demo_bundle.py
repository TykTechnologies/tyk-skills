from __future__ import annotations

import atexit
import base64
import http.server
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock


SCRIPTS = Path(__file__).resolve().parent


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_generator_functions():
    """Load the testable generator prefix even when a later generator defect exists."""
    path = SCRIPTS / "init_demo_bundle.py"
    try:
        return load_script("init_demo_bundle"), None
    except SyntaxError as exc:
        source = path.read_text()
        prefix = source.split("\ndef protocol_apis", 1)[0]
        module = type(sys)("init_demo_bundle_test_prefix")
        module.__file__ = str(path)
        exec(compile(prefix, str(path), "exec"), module.__dict__)
        return module, exc


generator, GENERATOR_LOAD_ERROR = load_generator_functions()
validator = load_script("validate_bundle")
sanitizer = load_script("sanitize_bundle")

# Every generator run in this module, including subprocesses (which inherit the environment),
# resolves "latest" from this offline fixture instead of Docker Hub, so the suite is
# deterministic and needs no network. The lists carry the noise a real listing has (rc, fips,
# arch suffixes, floating minors, `latest`) and resolve to the builder's known-good set.
DOCKER_HUB_FIXTURE_ENV = "TYK_DEMO_DOCKER_HUB_FIXTURE"
DOCKER_HUB_TAGS = {
    "tykio/tyk-dashboard": ["v5.8.16-rc3", "v5.16", "v5.16.0", "v5.16.0-rc4", "v5.15.0", "v5.15.0-rc11", "v5.99.0-fips", "v5.99.0-rc1", "v5.13.2", "v5.14.0", "v5.13.1", "latest"],
    "tykio/tyk-gateway-ee": ["v5.8.16-rc2", "v5.16.0", "v5.16", "v5.15.0", "v5.15", "v5.15.0-rc11", "v5.13.2", "v5.14.0", "v5.13.1", "v5.8.0"],
    "tykio/tyk-pump-docker-pub": ["v1.18.0", "v1.18", "v1.17.0", "v1.17", "v1.17.0-rc5", "v1.16.0", "v1.15.0"],
    "tykio/portal": ["v1.20.1", "v1.20.1-rc2", "v1.20.0", "v1.20", "v1.20.0-rc2", "v1.19.0", "v1.19", "v1.19.0-rc6", "v1.18.0", "v1.17.3"],
    "tykio/tyk-operator": ["latest", "v1.6.0", "v1.6.0-rc6", "v1.5", "v1.5.0", "v1.5.0-rc6", "1.5.0-rc6", "v1.4.2"],
    "tykio/tyk-sync": ["v2.3", "v2.3.0", "v2.3.0-rc2", "v2.2", "v2.2rc3", "v2.2.2", "v2.2.2-rc3", "v2.2.2-amd64", "v2.2.0", "v2.1.8"],
    "tykio/tyk-ai-studio-ent": ["v2.3.0", "v2.3.0-rc4", "v2.2.2", "v2.2.1", "v2.2.0-rc10.0", "v2.2.0", "v2.2", "v2.2.0-rc9", "v2.1.0", "v2.1", "v2.1.0-rc-1", "v2.0.0", "v2.0"],
    "tykio/tyk-microgateway-ent": ["v2.3.0", "v2.3.0-rc4", "v2.2.2", "v2.2.1", "v2.2.0-rc10.0", "v2.2.0", "v2.2", "v2.1.0", "v2.1", "v2.1.0-rc-1", "v2.0.0", "v2.0"],
}
_DOCKER_HUB_FIXTURE_DIR = tempfile.mkdtemp(prefix="tyk-docker-hub-fixture-")
atexit.register(shutil.rmtree, _DOCKER_HUB_FIXTURE_DIR, True)
DOCKER_HUB_FIXTURE = Path(_DOCKER_HUB_FIXTURE_DIR) / "tags.json"
DOCKER_HUB_FIXTURE.write_text(json.dumps(DOCKER_HUB_TAGS))
os.environ[DOCKER_HUB_FIXTURE_ENV] = str(DOCKER_HUB_FIXTURE)

# The known-good set for components every bundle resolves; module-owned components (AI Studio)
# are resolved only when their module is selected.
BASE_KNOWN_GOOD = {
    component: version for component, version in generator.KNOWN_GOOD_VERSIONS.items()
    if component not in getattr(generator, "MODULE_COMPONENTS", {})
}
BASE_COMPONENTS = {
    component: value for component, value in generator.COMPONENTS.items()
    if component not in getattr(generator, "MODULE_COMPONENTS", {})
}

# Placed in the bundle's operator/ only when governance and operator are selected together.
GOVERNANCE_OPERATOR_FILES = (
    "tyk-ruleset.yaml", "governed-api-compliant.yaml", "governed-api-noncompliant.yaml",
    "verify-governance.sh",
)


class ModuleSelectionTests(unittest.TestCase):
    def test_quota_expands_api_key_and_rest_dependencies(self):
        self.assertEqual(generator.expand_modules("quota", "local"), ["api-key", "quota", "rest"])

    def test_dcr_expands_transitive_portal_jwt_api_key_and_rest_dependencies(self):
        self.assertEqual(generator.expand_modules("dcr", "local"), ["api-key", "dcr", "jwt", "portal", "portal-branding", "rest"])

    def test_unknown_module_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "Unsupported modules: mystery"):
            generator.expand_modules("rest,mystery", "local")

    def test_empty_module_selection_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "Select at least one module"):
            generator.expand_modules(" , ", "local")


class PortSelectionTests(unittest.TestCase):
    def test_explicit_free_port_base_allocates_consecutive_named_ports(self):
        with mock.patch.object(generator, "port_free", return_value=True):
            ports = generator.choose_ports(24000)
        self.assertEqual(ports["dashboard"], 24000)
        self.assertEqual(ports["tempo"], 24011)

    def test_explicit_occupied_port_block_is_rejected(self):
        with mock.patch.object(generator, "port_free", side_effect=lambda port: port != 24003):
            with self.assertRaisesRegex(SystemExit, "Unable to allocate"):
                generator.choose_ports(24000)

    def test_privileged_port_base_is_rejected_without_binding(self):
        with mock.patch.object(generator, "port_free") as port_free:
            with self.assertRaisesRegex(SystemExit, "Unable to allocate"):
                generator.choose_ports(80)
        port_free.assert_not_called()


class PathAndTemplateTests(unittest.TestCase):
    def test_display_name_rejects_shell_metacharacters(self):
        with self.assertRaisesRegex(SystemExit, "safe display characters"):
            generator.validate_display_name('Demo $(touch unsafe)')

    def test_version_rejects_shell_metacharacters(self):
        with self.assertRaisesRegex(SystemExit, "safe container tag"):
            generator.validate_version('v5.13.1; echo unsafe', "--gateway-version")

    def test_safe_relative_path_rejects_symlink_outside_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir()
            outside = Path(temporary) / "outside.txt"
            outside.write_text("outside")
            link = root / "link.txt"
            link.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "path escapes bundle"):
                validator.safe_relative_path(root, link)

    def test_template_json_is_selected_for_placeholder_exemption(self):
        self.assertTrue(validator.is_template(Path("apis/mtls.oas.template.json")))

    def test_ordinary_json_is_not_selected_as_template(self):
        self.assertFalse(validator.is_template(Path("apis/demo-core.oas.json")))


class BundleFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "bundle"
        self.create_representative_bundle()

    def tearDown(self):
        self.temporary.cleanup()

    def create_representative_bundle(self) -> None:
        self.root.mkdir()
        (self.root / ".tyk-standalone-demo").write_text("generated by tyk-demo-builder\n")
        ports = {name: 41000 + offset for offset, name in enumerate([
            "dashboard", "portal", "gateway", "upstream", "identity", "websocket",
            "graphql", "kafka", "otel_grpc", "otel_http", "prometheus", "tempo",
        ])}
        (self.root / "demo.config.json").write_text(json.dumps({
            "schema_version": 1,
            "slug": "test-demo",
            "display_name": "Test Demo",
            "modules": ["rest"],
            "component_versions": {
                "gateway": "v5.13.1", "dashboard": "v5.13.1", "pump": "v1.15.0",
                "portal": "v1.17.3", "operator": "v1.5.0", "tyk_sync": "v2.1.8",
            },
            "identity_provider_mode": "local",
            "compose_profiles": [],
            "ports": ports,
            "proof": {"rest": "REST-001"},
        }) + "\n")
        (self.root / "compose.yaml").write_text(
            'services:\n  demo:\n    image: alpine:3.20\n    command: ["true"]\n'
        )
        (self.root / ".env.example").write_text("TYK_LICENSE_KEY=\n")
        (self.root / ".gitignore").write_text("\n".join(sorted(validator.REQUIRED_IGNORES)) + "\n")
        (self.root / "apis").mkdir()
        (self.root / "apis" / "test-demo-core.oas.json").write_text('{"openapi": "3.0.3"}\n')
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "verify-demo.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n")
        (self.root / "upstreams").mkdir()
        (self.root / "upstreams" / "http_api.py").write_text("print('ready')\n")
        (self.root / "notes").mkdir()


class ActualGeneratedBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        if GENERATOR_LOAD_ERROR is None:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
                 "--slug", "test-demo", "--name", "Test Demo", "--modules",
                 "streams,portal-branding,otel,go-plugin,grpc-plugin,circuit-breaker"],
                check=True, capture_output=True, text=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_generated_rest_bundle_passes_static_validation(self):
        self.assertEqual(validator.validate_bundle(self.root), [])

    def read_json(self, relative: str):
        return json.loads((self.root / relative).read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_otel_detailed_tracing_uses_tyk_513_object_shape(self):
        core = self.read_json("apis/test-demo-core.oas.json")
        tracing = core["x-tyk-api-gateway"]["server"]["detailedTracing"]
        self.assertEqual(tracing, {"enabled": True})

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_otel_generates_request_metrics_from_gateway_spans(self):
        collector = (self.root / "otel/collector.yaml").read_text()
        verify = (self.root / "scripts/verify-demo.sh").read_text()
        self.assertIn("spanmetrics:", collector)
        self.assertIn("exporters: [debug, otlp/tempo, spanmetrics]", collector)
        self.assertIn("receivers: [otlp, hostmetrics, spanmetrics]", collector)
        self.assertIn('calls_total{service_name="tyk-gateway"}', verify)
        # The spanmetrics default flush interval is a minute, which made the first
        # verification run after a cold start race the metrics scrape. Pin it so the
        # span-derived metric is deterministic rather than merely waited for longer.
        self.assertIn("metrics_flush_interval: 5s", collector)
        self.assertIn("OTEL_METRIC_BUDGET_SECONDS", verify)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_demo_config_records_the_public_schema_contract(self):
        config = self.read_json("demo.config.json")
        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(config["identity_provider_mode"], "local")
        self.assertIn("gateway", config["component_versions"])
        self.assertNotIn("identity_mode", config)
        self.assertNotIn("versions", config)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_generated_bundle_contains_no_python_bytecode(self):
        self.assertEqual(list(self.root.rglob("__pycache__")), [])
        self.assertEqual(list(self.root.rglob("*.pyc")), [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_streams_oas_has_valid_urls_and_disables_http_proxying(self):
        stream = self.read_json("apis/test-demo-streams-producer.oas.json")
        self.assertTrue(stream["servers"][0]["url"])
        upstream = stream["x-tyk-api-gateway"]["upstream"]
        self.assertTrue(upstream["url"])
        self.assertEqual(upstream["proxy"], {"enabled": False})

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_all_policies_are_active_and_portal_policies_are_partitioned(self):
        policies = [json.loads(path.read_text()) for path in self.root.glob("policies/*.json")]
        self.assertTrue(policies)
        self.assertTrue(all(policy.get("active") is True for policy in policies))
        by_name = {policy["meta_data"]["stable_name"]: policy for policy in policies}
        product = by_name["test-demo-portal-product-policy"]
        plan = by_name["test-demo-portal-plan-policy"]
        self.assertTrue(product["partitions"]["acl"])
        self.assertFalse(product["partitions"]["rate_limit"])
        self.assertFalse(product["partitions"]["quota"])
        self.assertTrue(product["access_rights"])
        self.assertFalse(plan["partitions"]["acl"])
        self.assertTrue(plan["partitions"]["rate_limit"])
        self.assertTrue(plan["partitions"]["quota"])
        self.assertEqual(plan["access_rights"], {})

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_credentials_use_stable_policy_id(self):
        configure = (self.root / "scripts/configure-demo.sh").read_text()
        self.assertIn("| .id' | head -1", configure)
        self.assertNotIn("| ._id' | head -1", configure)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_portal_uses_internal_dashboard_and_owned_resources(self):
        configure = (self.root / "portal/configure-portal.sh").read_text()
        self.assertIn("http://tyk-dashboard:3000", configure)
        self.assertIn("portal_api POST /catalogues", configure)
        self.assertIn("no owned products were correlated", configure)
        self.assertIn("no owned plans were correlated", configure)
        self.assertNotIn("product_ids=\"$(jq '[.[]?.ID]'", configure)
        self.assertNotIn("plan_ids=\"$(jq '[.[]?.ID]'", configure)
        self.assertIn('NameWithSlug == "test-demo-public-catalogue"', configure)
        self.assertIn("portal_api POST /pages", configure)
        self.assertIn("/content-blocks/", configure)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_plugin_assets_match_gateway_loader_and_verification_contract(self):
        dockerfile = (self.root / "plugins/go/Dockerfile").read_text()
        upstream = (self.root / "upstreams/http_api.py").read_text()
        self.assertIn("post_enrich_${TYK_GATEWAY_VERSION}_linux_${TYK_GATEWAY_ARCH}.so", dockerfile)
        self.assertIn("post_enrich_${TYK_GATEWAY_VERSION#v}_linux_${TYK_GATEWAY_ARCH}.so", dockerfile)
        self.assertIn('"x-demo-post-hook"', upstream)
        self.assertIn('"x-grpc-enriched"', upstream)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_circuit_breaker_path_is_a_direct_controlled_failure(self):
        core = self.read_json("apis/test-demo-core.oas.json")
        operations = core["x-tyk-api-gateway"]["middleware"]["operations"]
        breaker = operations["breakerOperation"]["circuitBreaker"]
        self.assertTrue(breaker["enabled"])
        self.assertNotIn("urlRewrite", operations["breakerOperation"])
        self.assertIn('self.path == "/v1/breaker"', (self.root / "upstreams/http_api.py").read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_external_identity_mode_omits_local_idp_and_keeps_proof_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
                 "--slug", "external-idp", "--name", "External IdP", "--modules", "dcr",
                 "--identity-mode", "external"],
                check=True, capture_output=True, text=True,
            )
            config = json.loads((root / "demo.config.json").read_text())
            jwt = json.loads((root / "apis/external-idp-jwt.oas.json").read_text())
            auth = jwt["x-tyk-api-gateway"]["server"]["authentication"]["securitySchemes"]["jwtAuth"]
            self.assertEqual(config["identity_provider_mode"], "external")
            self.assertNotIn("identity", config["compose_profiles"])
            self.assertNotIn("\n  identity:\n", (root / "compose.yaml").read_text())
            self.assertFalse((root / "upstreams/identity_service.py").exists())
            self.assertEqual(auth["allowedIssuers"], ["https://idp.example.invalid"])
            self.assertIn("External provider registration", (root / "scripts/verify-demo.sh").read_text())
            self.assertEqual(validator.validate_bundle(root), [])


class ComponentVersionResolutionTests(unittest.TestCase):
    """Defaults resolve to the newest strict stable tag per component at generation time.

    Explicit flags always win, `--versions pinned` never touches the network, and an
    unreachable Docker Hub degrades per component to the known-good set with a warning rather
    than failing generation.
    """

    TODAY = "2026-09-24"
    LATEST = "latest (Docker Hub, 2026-09-24)"

    def resolve(self, tags: dict, requested: dict | None = None, mode: str = "latest"):
        def fake_fetch(repository: str, notes=None, start_line=None) -> list[str]:
            value = tags.get(repository)
            if isinstance(value, Exception):
                raise value
            if value is None:
                raise OSError("network unreachable")
            return list(value)

        with mock.patch.object(generator, "fetch_tags", side_effect=fake_fetch) as fetch:
            result = generator.resolve_component_versions(requested or {}, mode, today=self.TODAY)
        return (*result, fetch)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_latest_picks_the_highest_strict_semver_and_ignores_prereleases(self):
        noisy = ["v5.16.0-rc1", "v5.16.0-alpha2", "v5.16.0-beta", "v5.15.0-fips", "v5.15.1-arm64",
                 "latest", "v5.15", "5.15.2", "v5.9.0", "v5.15.0", "v5.10.3"]
        # Numeric, not lexical: v5.9.0 < v5.10.3 < v5.15.0, and nothing unstable competes.
        self.assertEqual(generator.highest_stable(noisy), "v5.15.0")
        self.assertIsNone(generator.highest_stable(["latest", "v5.16.0-rc1", "v1.5"]))
        versions, sources, warnings, _ = self.resolve(DOCKER_HUB_TAGS)
        self.assertEqual(versions, BASE_KNOWN_GOOD)
        self.assertEqual(set(sources.values()), {self.LATEST})
        self.assertEqual(warnings, [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_gateway_follows_the_dashboard_version_when_that_tag_exists(self):
        tags = dict(DOCKER_HUB_TAGS)
        tags["tykio/tyk-dashboard"] = ["v5.16.0", "v5.15.0"]
        tags["tykio/tyk-gateway-ee"] = ["v5.16.1", "v5.16.0", "v5.15.0"]
        versions, sources, warnings, _ = self.resolve(tags)
        self.assertEqual((versions["dashboard"], versions["gateway"]), ("v5.16.0", "v5.16.0"))
        self.assertEqual(sources["gateway"], self.LATEST)
        self.assertEqual(warnings, [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_gateway_falls_back_to_the_highest_tag_in_the_dashboard_minor(self):
        tags = dict(DOCKER_HUB_TAGS)
        tags["tykio/tyk-dashboard"] = ["v5.16.1"]
        tags["tykio/tyk-gateway-ee"] = ["v6.0.0", "v5.16.0-rc9", "v5.16.0", "v5.15.3"]
        versions, sources, warnings, _ = self.resolve(tags)
        self.assertEqual(versions["gateway"], "v5.16.0")
        self.assertEqual(sources["gateway"], self.LATEST)
        self.assertTrue(any("has no v5.16.1 tag" in warning and "v5.16" in warning for warning in warnings), warnings)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_gateway_uses_the_newest_tag_and_warns_when_the_dashboard_line_is_missing(self):
        tags = dict(DOCKER_HUB_TAGS)
        tags["tykio/tyk-dashboard"] = ["v5.17.0"]
        tags["tykio/tyk-gateway-ee"] = ["v5.16.0", "v5.15.0", "v5.16.1-rc1"]
        versions, _, warnings, _ = self.resolve(tags)
        self.assertEqual(versions["gateway"], "v5.16.0")
        self.assertTrue(any("differs from the Dashboard" in warning for warning in warnings), warnings)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_explicit_flags_win_and_skip_the_lookup(self):
        versions, sources, warnings, fetch = self.resolve(
            DOCKER_HUB_TAGS, {"dashboard": "v5.13.1", "pump": "v1.15.0", "operator": "v1.5.0-rc6"})
        self.assertEqual((versions["dashboard"], sources["dashboard"]), ("v5.13.1", "user"))
        self.assertEqual((versions["pump"], sources["pump"]), ("v1.15.0", "user"))
        self.assertEqual((versions["operator"], sources["operator"]), ("v1.5.0-rc6", "user"))
        # The Gateway follows a user-chosen Dashboard, not the newest Gateway.
        self.assertEqual((versions["gateway"], sources["gateway"]), ("v5.13.1", "matches dashboard"))
        self.assertEqual((versions["portal"], sources["portal"]), ("v1.20.1", self.LATEST))
        looked_up = {call.args[0] for call in fetch.call_args_list}
        self.assertNotIn("tykio/tyk-dashboard", looked_up)
        self.assertNotIn("tykio/tyk-pump-docker-pub", looked_up)
        self.assertNotIn("tykio/tyk-operator", looked_up)
        self.assertEqual(warnings, [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_one_unreachable_component_falls_back_alone_with_a_named_warning(self):
        tags = dict(DOCKER_HUB_TAGS)
        tags["tykio/portal"] = OSError("timed out")
        versions, sources, warnings, _ = self.resolve(tags)
        self.assertEqual((versions["portal"], sources["portal"]), (generator.KNOWN_GOOD_VERSIONS["portal"], "known-good fallback"))
        self.assertEqual(sources["dashboard"], self.LATEST)
        self.assertEqual(len(warnings), 1)
        self.assertIn("tykio/portal", warnings[0])
        self.assertIn("portal uses the known-good v1.20.1", warnings[0])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_fully_offline_falls_back_to_the_known_good_set(self):
        versions, sources, warnings, _ = self.resolve({})
        self.assertEqual(versions, BASE_KNOWN_GOOD)
        self.assertEqual(set(sources.values()), {"known-good fallback"})
        for component in BASE_COMPONENTS:
            self.assertTrue(any(f"; {component} " in warning for warning in warnings), (component, warnings))
        # A repository with no stable tag at all is a fallback too, not a crash.
        tags = dict(DOCKER_HUB_TAGS)
        tags["tykio/tyk-sync"] = ["latest", "v2.4.0-rc1"]
        versions, sources, warnings, _ = self.resolve(tags)
        self.assertEqual((versions["tyk_sync"], sources["tyk_sync"]), ("v2.3.0", "known-good fallback"))
        self.assertTrue(any("no stable vX.Y.Z tag" in warning for warning in warnings), warnings)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_ai_studio_is_resolved_only_when_its_module_is_selected(self):
        versions, _, _, fetch = self.resolve(DOCKER_HUB_TAGS)
        self.assertNotIn("ai_studio", versions)
        looked_up = {call.args[0] for call in fetch.call_args_list}
        self.assertNotIn("tykio/tyk-ai-studio-ent", looked_up)
        self.assertNotIn(generator.AI_STUDIO_MICROGATEWAY_REPOSITORY, looked_up)
        self.assertEqual(generator.selected_components({"rest"}), list(BASE_COMPONENTS))
        self.assertIn("ai_studio", generator.selected_components({"rest", "ai-studio"}))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_ai_studio_resolves_to_a_tag_the_microgateway_also_carries(self):
        components = generator.selected_components({"ai-studio"})
        with mock.patch.object(generator, "fetch_tags", side_effect=lambda repository, notes=None, start_line=None: list(DOCKER_HUB_TAGS[repository])):
            versions, sources, warnings = generator.resolve_component_versions({}, "latest", today=self.TODAY, components=components)
        self.assertEqual((versions["ai_studio"], sources["ai_studio"]), ("v2.3.0", self.LATEST))
        self.assertEqual(warnings, [])
        # The Microgateway lags: AI Studio follows it down to the newest tag both carry.
        tags = dict(DOCKER_HUB_TAGS)
        tags["tykio/tyk-ai-studio-ent"] = ["v2.3.1", "v2.3.0"]
        with mock.patch.object(generator, "fetch_tags", side_effect=lambda repository, notes=None, start_line=None: list(tags[repository])):
            versions, _, warnings = generator.resolve_component_versions({}, "latest", today=self.TODAY, components=components)
        self.assertEqual(versions["ai_studio"], "v2.3.0")
        self.assertTrue(any("has no v2.3.1 tag" in warning for warning in warnings), warnings)
        # Pinned and explicit values never touch Docker Hub.
        versions, sources, _, fetch = self.resolve(DOCKER_HUB_TAGS, {"ai_studio": "v2.0.0"})
        self.assertNotIn("ai_studio", versions)  # default components exclude it
        with mock.patch.object(generator, "fetch_tags", side_effect=AssertionError("network used")):
            versions, sources, _ = generator.resolve_component_versions({"ai_studio": "v2.0.0"}, "pinned", components=components)
        self.assertEqual((versions["ai_studio"], sources["ai_studio"]), ("v2.0.0", "user"))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_offline_gateway_follows_an_explicit_dashboard(self):
        versions, sources, _, _ = self.resolve({}, {"dashboard": "v5.14.0"})
        self.assertEqual((versions["gateway"], sources["gateway"]), ("v5.14.0", "matches dashboard"))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_pinned_makes_no_network_calls(self):
        with mock.patch.object(generator.urllib.request, "urlopen", side_effect=AssertionError("network used")) as urlopen:
            versions, sources, warnings, fetch = self.resolve(DOCKER_HUB_TAGS, mode="pinned")
            pinned_dashboard = self.resolve(DOCKER_HUB_TAGS, {"dashboard": "v5.14.0"}, mode="pinned")
        fetch.assert_not_called()
        pinned_dashboard[3].assert_not_called()
        urlopen.assert_not_called()
        self.assertEqual(versions, BASE_KNOWN_GOOD)
        self.assertEqual(set(sources.values()), {"pinned"})
        self.assertEqual(warnings, [])
        self.assertEqual(pinned_dashboard[0]["gateway"], "v5.14.0")
        self.assertEqual(pinned_dashboard[1]["gateway"], "matches dashboard")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_known_good_set_matches_the_starter_env_example(self):
        env = (SCRIPTS.parent / "assets/starter/.env.example").read_text()
        for component, (_, _, _, variable) in BASE_COMPONENTS.items():
            self.assertIn(f"\n{variable}={generator.KNOWN_GOOD_VERSIONS[component]}\n", env)
            self.assertIsNotNone(generator.stable_key(generator.KNOWN_GOOD_VERSIONS[component]), component)

    def run_docker_hub_client(self, lines: dict, start_line=None, max_pages: int | None = None,
                              max_lines: int | None = None, repository: str = "tykio/tyk-dashboard"):
        """Serve `lines` ({"5.16": [page, page, ...]}) as Docker Hub's name=vX.Y. listing."""
        requested = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *args):
                return json.dumps(self.payload).encode()

        served: dict = {}
        current = [""]

        def fake_urlopen(request, timeout):
            url = request.full_url
            requested.append((url, timeout))
            # Docker Hub's `next` link repeats the filter; the test pages may not, so a page
            # URL without one continues the line read last.
            match = re.search(r"&name=v(\d+\.\d+)\.", url)
            line = match.group(1) if match else current[0]
            current[0] = line
            pages = lines.get(line, [{"results": [], "next": None}])
            index = served.get(line, 0)
            served[line] = index + 1
            return Response(pages[index])

        environment = {key: value for key, value in os.environ.items() if key != DOCKER_HUB_FIXTURE_ENV}
        notes: list = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, environment, clear=True))
            stack.enter_context(mock.patch.object(generator.urllib.request, "urlopen", side_effect=fake_urlopen))
            if max_pages is not None:
                stack.enter_context(mock.patch.object(generator, "DOCKER_HUB_MAX_PAGES", max_pages))
            if max_lines is not None:
                stack.enter_context(mock.patch.object(generator, "DOCKER_HUB_MAX_LINES", max_lines))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            names = generator.fetch_tags(repository, notes=notes, start_line=start_line)
        return names, requested, notes

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_docker_hub_client_reads_one_release_line_at_a_time(self):
        """Each line is read with name=vX.Y., from the known-good line forward, so a
        repository with more than the anonymous 1,000-tag listing limit still resolves."""
        base = "https://hub.docker.com/v2/repositories/tykio/tyk-dashboard/tags?page="
        lines = {
            "5.16": [{"results": [{"name": "v5.16.0-rc1"}, {"name": "v5.16.0"}], "next": base + "2"},
                     # The filter is a substring match: anything outside the line is dropped.
                     {"results": [{"name": "v5.16.1"}, {"name": "xv5.16.9"}], "next": None}],
            "5.17": [{"results": [{"name": "v5.17.0-rc2"}, {"name": "v5.17.0"}], "next": None}],
            "5.18": [{"results": [{"name": "v5.18.0-rc1"}], "next": None}],
            "6.0": [{"results": [], "next": None}],
        }
        names, requested, notes = self.run_docker_hub_client(lines)
        self.assertEqual(generator.highest_stable(names), "v5.17.0")
        self.assertNotIn("xv5.16.9", names)
        urls = [url for url, _ in requested]
        self.assertIn("tykio/tyk-dashboard/tags?page_size=100&name=v5.16.", urls[0])
        self.assertTrue(any(url == base + "2" for url in urls))
        # 5.18 has no release, so the next major's .0 line is tried once and the walk stops.
        self.assertTrue(urls[-1].endswith("&name=v6.0."), urls)
        self.assertFalse(any("&name=v5.15." in url for url in urls), "older lines are never read")
        self.assertTrue(all(timeout == generator.DOCKER_HUB_TIMEOUT_SECONDS for _, timeout in requested))
        self.assertEqual(notes, [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_docker_hub_client_follows_a_new_major_and_honours_an_explicit_start_line(self):
        lines = {
            "5.13": [{"results": [{"name": "v5.13.1"}], "next": None}],
            "5.14": [{"results": [], "next": None}],
            "6.0": [{"results": [{"name": "v6.0.0"}], "next": None}],
            "6.1": [{"results": [], "next": None}],
        }
        names, requested, _ = self.run_docker_hub_client(lines, start_line=(5, 13))
        self.assertEqual(generator.highest_stable(names), "v6.0.0")
        self.assertIn("v5.13.1", names)
        self.assertTrue(requested[0][0].endswith("&name=v5.13."))
        # 6.1 is empty and the next-major probe was already spent, so nothing after it is read.
        self.assertTrue(requested[-1][0].endswith("&name=v6.1."))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_docker_hub_client_warns_when_a_cap_truncates_the_scan(self):
        base = "https://hub.docker.com/v2/repositories/tykio/tyk-dashboard/tags?page="
        endless = [{"results": [{"name": f"v5.16.{patch}"}], "next": base + str(patch + 2)} for patch in range(5)]
        _, requested, notes = self.run_docker_hub_client({"5.16": endless}, max_pages=3)
        self.assertEqual(len([url for url, _ in requested if "5.16" in url or "page=" in url]), 3)
        self.assertTrue(any("stopped reading the tykio/tyk-dashboard v5.16 line after 3 pages" in note for note in notes), notes)
        many = {f"5.{minor}": [{"results": [{"name": f"v5.{minor}.0"}], "next": None}] for minor in range(16, 30)}
        _, requested, notes = self.run_docker_hub_client(many, max_lines=4)
        self.assertEqual(len(requested), 4)
        self.assertTrue(any("after 4 release lines" in note for note in notes), notes)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_generator_and_validator_module_metadata_agree_for_every_module(self):
        """DB-10: the generator's module tables are the source; the validator's copies must match."""
        self.assertEqual(set(generator.SUPPORTED), set(validator.SUPPORTED_MODULES))
        for module in generator.SUPPORTED:
            with self.subTest(module=module):
                self.assertEqual(set(generator.DEPENDENCIES.get(module, set())), set(validator.MODULE_DEPENDENCIES.get(module, set())))
                self.assertEqual(generator.MODULE_PROOF[module][2], validator.MODULE_TEST_IDS[module])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_known_good_lines_start_every_walk(self):
        for component, (_, _, repository, _) in generator.COMPONENTS.items():
            self.assertEqual(generator.start_line_for(repository), generator.release_line(generator.KNOWN_GOOD_VERSIONS[component]))
        self.assertEqual(generator.start_line_for(generator.AI_STUDIO_MICROGATEWAY_REPOSITORY),
                         generator.release_line(generator.KNOWN_GOOD_VERSIONS["ai_studio"]))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_gateway_lookup_failure_warns_about_a_dashboard_mismatch(self):
        tags = {"tykio/tyk-dashboard": ["v5.17.0"], "tykio/tyk-gateway-ee": OSError("rate limited")}
        for repository in ("tykio/tyk-pump-docker-pub", "tykio/portal", "tykio/tyk-operator", "tykio/tyk-sync"):
            tags[repository] = ["v1.0.0"]
        versions, sources, warnings, _ = self.resolve(tags)
        self.assertEqual(versions["dashboard"], "v5.17.0")
        self.assertEqual(versions["gateway"], generator.KNOWN_GOOD_VERSIONS["gateway"])
        self.assertTrue(any("differs from the Dashboard v5.17.0" in warning for warning in warnings), warnings)


class GeneratedComponentVersionTests(unittest.TestCase):
    """The bundle carries concrete tags and records where each came from."""

    def generate(self, root: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
             "--slug", "version-demo", "--name", "Version Demo",
             "--modules", "rest,governance,operator,portal", *extra],
            capture_output=True, text=True, check=False, env=env,
        )

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_default_bundle_records_concrete_latest_versions_and_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            # Governance and the Operator hard block pass their gates with no version flags.
            result = self.generate(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("WARNING", result.stderr)
            config = json.loads((root / "demo.config.json").read_text())
            self.assertEqual(config["component_versions"], BASE_KNOWN_GOOD)
            for component, source in config["component_version_sources"].items():
                self.assertRegex(source, r"^latest \(Docker Hub, \d{4}-\d{2}-\d{2}\)$", component)
            env = (root / ".env").read_text()
            for component, (_, _, _, variable) in BASE_COMPONENTS.items():
                self.assertIn(f"\n{variable}={generator.KNOWN_GOOD_VERSIONS[component]}\n", env)
                self.assertNotIn(f"{variable}=latest", env)
            readme = (root / "README.md").read_text()
            self.assertIn("## Component versions", readme)
            self.assertIn("| dashboard | `tykio/tyk-dashboard` | `v5.16.0` | latest (Docker Hub, ", readme)
            self.assertIn("`TYK_SYNC_VERSION` in `.env`, or `--sync-version`", readme)
            self.assertIn("rehearse-demo.sh", readme)
            self.assertEqual(validator.validate_bundle(root, check_compose=False), [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_explicit_flags_and_pinned_mode_are_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            missing = Path(temporary) / "absent-fixture.json"
            # A pinned run with an unreadable fixture proves no lookup happened at all.
            result = self.generate(root, "--versions", "pinned", "--portal-version", "v1.18.0",
                                   env=dict(os.environ, **{DOCKER_HUB_FIXTURE_ENV: str(missing)}))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("WARNING", result.stderr)
            config = json.loads((root / "demo.config.json").read_text())
            self.assertEqual(config["component_versions"]["portal"], "v1.18.0")
            self.assertEqual(config["component_version_sources"]["portal"], "user")
            self.assertEqual(config["component_version_sources"]["dashboard"], "pinned")
            self.assertIn("\nPORTAL_VERSION=v1.18.0\n", (root / ".env").read_text())
            self.assertIn("| portal | `tykio/portal` | `v1.18.0` | user |", (root / "README.md").read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_unreachable_docker_hub_still_generates_with_per_component_warnings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            partial = Path(temporary) / "partial.json"
            partial.write_text(json.dumps({key: value for key, value in DOCKER_HUB_TAGS.items() if key != "tykio/tyk-pump-docker-pub"}))
            result = self.generate(root, env=dict(os.environ, **{DOCKER_HUB_FIXTURE_ENV: str(partial)}))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("WARNING: could not read tykio/tyk-pump-docker-pub tags", result.stderr)
            self.assertIn("pump uses the known-good v1.18.0", result.stderr)
            sources = json.loads((root / "demo.config.json").read_text())["component_version_sources"]
            self.assertEqual(sources["pump"], "known-good fallback")
            self.assertTrue(sources["dashboard"].startswith("latest (Docker Hub, "))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_explicit_5_13_bundle_keeps_its_version_aware_shapes(self):
        """The previous default line still generates when requested explicitly."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
                 "--slug", "line-513", "--name", "Line 513", "--modules", "rest,jwt,otel",
                 "--dashboard-version", "v5.13.1", "--gateway-version", "v5.13.1"],
                check=True, capture_output=True, text=True,
            )
            config = json.loads((root / "demo.config.json").read_text())
            self.assertEqual(config["component_versions"]["gateway"], "v5.13.1")
            self.assertEqual(config["component_version_sources"]["gateway"], "user")
            self.assertIn("\nGATEWAY_VERSION=v5.13.1\n", (root / ".env").read_text())
            scheme = json.loads((root / "apis/line-513-jwt.oas.json").read_text())["x-tyk-api-gateway"]["server"]["authentication"]["securitySchemes"]["jwtAuth"]
            self.assertIn("jwksURIs", scheme)
            core = json.loads((root / "apis/line-513-core.oas.json").read_text())
            self.assertEqual(core["x-tyk-api-gateway"]["server"]["detailedTracing"], {"enabled": True})
            self.assertEqual(validator.validate_bundle(root, check_compose=False), [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_validator_rejects_incomplete_version_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            self.assertEqual(self.generate(root, "--versions", "pinned").returncode, 0)
            config_path = root / "demo.config.json"
            config = json.loads(config_path.read_text())
            del config["component_version_sources"]["gateway"]
            config_path.write_text(json.dumps(config) + "\n")
            errors = validator.validate_bundle(root, check_compose=False)
            self.assertTrue(any("component_version_sources" in error for error in errors), errors)


class GatewayTransportTests(unittest.TestCase):
    """The gateway listens on plain HTTP unless a selected module requires TLS.

    A self-signed gateway certificate demonstrates nothing and costs `-k` on every
    command, browser and MCP-client trust warnings, and a demo CA mounted into every
    in-network caller. Worse, a handshake failure surfaces as a misleading
    application-level error in front of an audience. mTLS is the one capability that
    genuinely cannot be shown without it: both Gateway client-certificate paths give
    up when `r.TLS` is nil, before they look at a certificate.
    """

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.http_root = Path(cls.temporary.name) / "http"
        cls.tls_root = Path(cls.temporary.name) / "tls"
        if GENERATOR_LOAD_ERROR is None:
            for root, modules in ((cls.http_root, "rest,api-key,mcp"), (cls.tls_root, "rest,api-key,mcp,mtls")):
                subprocess.run(
                    [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
                     "--slug", "transport-demo", "--name", "Transport Demo", "--modules", modules],
                    check=True, capture_output=True, text=True,
                )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def bundle_text(self, root: Path) -> dict[str, str]:
        """Every generated text file, keyed by bundle-relative path."""
        text: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                text[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
        return text

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_tls_is_derived_from_the_module_set_and_not_a_flag(self):
        self.assertEqual(generator.TLS_MODULES, {"mtls"})
        self.assertFalse(generator.gateway_tls_required({"rest", "api-key", "mcp", "portal", "otel"}))
        self.assertTrue(generator.gateway_tls_required({"rest", "mtls"}))
        # No user-facing flag: a presenter cannot ask for TLS, only for a module
        # that needs it. `--help` is the cheapest way to assert that stays true.
        help_text = subprocess.run(
            [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--help"],
            capture_output=True, text=True, check=True,
        ).stdout
        for flag in ("--tls", "--use-ssl", "--https", "--gateway-scheme"):
            self.assertNotIn(flag, help_text)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_http_bundle_records_and_configures_a_plain_listener(self):
        config = json.loads((self.http_root / "demo.config.json").read_text())
        self.assertIs(config["gateway_tls"], False)
        compose = (self.http_root / "compose.yaml").read_text()
        self.assertNotIn("TYK_GW_HTTPSERVEROPTIONS_USESSL", compose)
        self.assertNotIn("server.pem", compose)
        self.assertNotIn("NODE_EXTRA_CA_CERTS", compose)
        self.assertIn("TYK_DB_TYKAPI_HOST: http://tyk-gateway", compose)
        self.assertIn("TYK_DB_HOSTCONFIG_GENERATEHTTPS=false", (self.http_root / "config/dashboard.env").read_text())
        self.assertIn('GATEWAY_URL="${GATEWAY_URL:-http://localhost:${GATEWAY_PORT}}"', (self.http_root / "scripts/common.sh").read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_http_bundle_carries_no_insecure_flag_or_https_url(self):
        for relative, text in self.bundle_text(self.http_root).items():
            if relative in {".env", ".env.example"}:
                continue
            self.assertNotIn("curl -k", text, relative)
            self.assertNotIn("https://localhost", text, relative)
            self.assertNotIn("wss://localhost", text, relative)
        # mcp-check.py keeps --insecure as a capability; nothing in the bundle uses it.
        verify = (self.http_root / "scripts/verify-demo.sh").read_text()
        self.assertNotIn("--insecure", verify)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_http_bundle_ships_no_certificate_scaffolding(self):
        self.assertFalse((self.http_root / "scripts/generate-demo-certs.sh").exists())
        self.assertNotIn("generate-demo-certs.sh", (self.http_root / "scripts/start-demo-stack.sh").read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_http_bundle_oas_servers_advertise_the_plain_listener(self):
        port = json.loads((self.http_root / "demo.config.json").read_text())["ports"]["gateway"]
        for api_file in sorted(self.http_root.glob("apis/*.json")):
            for server in json.loads(api_file.read_text()).get("servers", []):
                self.assertTrue(server["url"].startswith(f"http://localhost:{port}"), f"{api_file.name}: {server['url']}")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_mtls_bundle_still_wires_tls_end_to_end(self):
        config = json.loads((self.tls_root / "demo.config.json").read_text())
        self.assertIs(config["gateway_tls"], True)
        compose = (self.tls_root / "compose.yaml").read_text()
        self.assertIn('TYK_GW_HTTPSERVEROPTIONS_USESSL: "true"', compose)
        self.assertIn("TYK_GW_HTTPSERVEROPTIONS_SSLCERTIFICATES: /opt/tyk-gateway/certs/server.pem", compose)
        self.assertIn("- ./generated/certs/server.pem:/opt/tyk-gateway/certs/server.pem:ro", compose)
        self.assertIn("TYK_DB_TYKAPI_HOST: https://tyk-gateway", compose)
        self.assertIn("NODE_EXTRA_CA_CERTS: /certs/ca.crt", compose)
        self.assertIn("TYK_DB_HOSTCONFIG_GENERATEHTTPS=true", (self.tls_root / "config/dashboard.env").read_text())
        self.assertIn('gw_curl() { curl -k "$@"; }', (self.tls_root / "scripts/common.sh").read_text())
        self.assertIn("--insecure", (self.tls_root / "scripts/verify-demo.sh").read_text())
        self.assertTrue((self.tls_root / "scripts/generate-demo-certs.sh").is_file())
        self.assertIn("generate-demo-certs.sh", (self.tls_root / "scripts/start-demo-stack.sh").read_text())
        port = config["ports"]["gateway"]
        core = json.loads((self.tls_root / "apis/transport-demo-core.oas.json").read_text())
        self.assertEqual(core["servers"][0]["url"], f"https://localhost:{port}/demo/transport-demo/core")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_tls_bundle_readme_states_the_listener_because_it_changes_every_command(self):
        readme = (self.tls_root / "README.md").read_text()
        self.assertIn("terminates TLS", readme)
        self.assertIn("curl -k", readme)
        self.assertNotIn("terminates TLS", (self.http_root / "README.md").read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_mcp_proxies_follow_the_demo_listen_path_convention(self):
        verify = (self.http_root / "scripts/verify-demo.sh").read_text()
        for name in ("mcp-broker", "rest-as-mcp"):
            proxy = json.loads((self.http_root / "mcp" / f"{name}.mcp.oas.json").read_text())
            listen = proxy["x-tyk-api-gateway"]["server"]["listenPath"]["value"]
            self.assertEqual(listen, f"/demo/transport-demo/{name}/")
            self.assertIn(f"${{GATEWAY_URL}}/demo/transport-demo/{name}/mcp", verify)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_mcp_status_defaults_to_an_empty_object(self):
        verify = (self.http_root / "scripts/verify-demo.sh").read_text()
        start = verify.index("  mcp_status() {")
        helper = verify[start:verify.index("\n  }\n", start) + 4]
        self.assertNotIn("{\\}", helper)
        harness = (
            f"set -euo pipefail\n{helper}\n"
            "denied='{\"status\":403}'\n"
            'printf "%s %s %s\\n" "$(mcp_status "")" "$(mcp_status "${denied}")" "$(mcp_status "not json")"\n'
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0 403 0")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_mock_mcp_server_build_is_pinned_to_a_commit(self):
        commit = generator.MOCK_MCP_SERVER_COMMIT
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        compose = (self.http_root / "compose.yaml").read_text()
        self.assertIn(f"context: https://github.com/TykTechnologies/tyk-mock-mcp-server.git#{commit}\n", compose)
        self.assertIn(f"image: tyk-mock-mcp-server:{commit[:12]}\n", compose)
        self.assertNotIn("tyk-mock-mcp-server.git#main", compose)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_both_transports_pass_static_validation(self):
        self.assertEqual(validator.validate_bundle(self.http_root), [])
        self.assertEqual(validator.validate_bundle(self.tls_root), [])


class JwtSigningKeySourceTests(unittest.TestCase):
    """jwksURIs is preferred from Gateway 5.9.0, with base64 source below it.

    The version gate is mandatory: below 5.9.0 the Gateway ignores jwksURIs entirely, so an
    ungated switch would leave a generated JWT API with no signing-key source and no error.
    """

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_version_gate_boundaries(self):
        for version in ("v5.9.0", "5.9.0", "v5.13.1", "v6.0.0"):
            self.assertTrue(generator.supports_jwks_uris(version), version)
        for version in ("v5.8.9", "5.8.0", "v4.9.0", "", "latest", "not-a-version"):
            self.assertFalse(generator.supports_jwks_uris(version), version)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_supported_gateway_emits_jwks_uris_and_no_base64_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
                 "--slug", "jwks-new", "--name", "Jwks New", "--modules", "jwt"],
                check=True, capture_output=True, text=True,
            )
            scheme = json.loads((root / "apis/jwks-new-jwt.oas.json").read_text())["x-tyk-api-gateway"]["server"]["authentication"]["securitySchemes"]["jwtAuth"]
            self.assertEqual(scheme["jwksURIs"], [{"url": "http://identity:8080/.well-known/jwks.json", "cacheTimeout": "5m"}])
            self.assertNotIn("source", scheme)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_older_gateway_falls_back_to_base64_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
                 "--slug", "jwks-old", "--name", "Jwks Old", "--modules", "jwt",
                 "--gateway-version", "v5.8.0"],
                check=True, capture_output=True, text=True,
            )
            scheme = json.loads((root / "apis/jwks-old-jwt.oas.json").read_text())["x-tyk-api-gateway"]["server"]["authentication"]["securitySchemes"]["jwtAuth"]
            self.assertNotIn("jwksURIs", scheme)
            self.assertEqual(
                base64.b64decode(scheme["source"]).decode(),
                "http://identity:8080/.well-known/jwks.json",
            )


class BaseIdentityProviderTests(unittest.TestCase):
    """Single-scheme APIs must not carry an inert baseIdentityProvider.

    Every auth middleware matches its own type OR the unset value, so on one scheme the
    field changes nothing. It matters only with two or more schemes in legacy mode, which
    is why the multi-scheme chained-auth recipe uses compliant processing instead.
    """

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        if GENERATOR_LOAD_ERROR is None:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
                 "--slug", "auth-shapes", "--name", "Auth Shapes",
                 "--modules", "api-key,basic-auth,jwt,oauth,chained-auth,websocket"],
                check=True, capture_output=True, text=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_single_scheme_apis_omit_base_identity_provider(self):
        checked = 0
        for api_file in sorted(self.root.glob("apis/*.oas.json")):
            auth = json.loads(api_file.read_text())["x-tyk-api-gateway"]["server"].get("authentication") or {}
            if len(auth.get("securitySchemes") or {}) == 1:
                checked += 1
                self.assertNotIn("baseIdentityProvider", auth, api_file.name)
        self.assertGreaterEqual(checked, 4, "expected several single-scheme APIs to inspect")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_multi_scheme_api_resolves_identity_without_the_legacy_field(self):
        auth = json.loads((self.root / "apis/auth-shapes-chained-auth.oas.json").read_text())["x-tyk-api-gateway"]["server"]["authentication"]
        self.assertGreater(len(auth["securitySchemes"]), 1)
        self.assertEqual(auth.get("securityProcessingMode"), "compliant")
        self.assertNotIn("baseIdentityProvider", auth)


class GeneratedBundleValidationTests(BundleFixture):
    def test_representative_bundle_passes_static_validation(self):
        self.assertEqual(validator.validate_bundle(self.root), [])

    def test_invalid_generated_json_is_reported(self):
        (self.root / "apis" / "test-demo-core.oas.json").write_text("{")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("invalid json" in error.lower() for error in errors), errors)

    def test_missing_configuration_contract_field_is_reported(self):
        config_path = self.root / "demo.config.json"
        config = json.loads(config_path.read_text())
        del config["component_versions"]
        config_path.write_text(json.dumps(config) + "\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("component_versions" in error for error in errors), errors)

    def test_unknown_module_is_rejected_unless_declared_custom(self):
        # A typo must not pass as a hand-added component.
        config_path = self.root / "demo.config.json"
        config = json.loads(config_path.read_text())
        config["modules"] = sorted(config["modules"] + ["goverance"])
        config_path.write_text(json.dumps(config) + "\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("unknown modules: goverance" in error for error in errors), errors)
        # Declared under custom_modules, a hand-added module is recorded, not rejected.
        config["custom_modules"] = ["goverance"]
        config_path.write_text(json.dumps(config) + "\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertEqual([error for error in errors if "module" in error], [], errors)
        config["custom_modules"] = "goverance"
        config_path.write_text(json.dumps(config) + "\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("custom_modules must be an array" in error for error in errors), errors)

    def test_required_component_versions_must_be_strict_tags(self):
        config_path = self.root / "demo.config.json"
        original = json.loads(config_path.read_text())
        for bad in ("v5.15.0 (rc2)", "5.15.0", "v5.15", "latest", "v5.15.0-", ""):
            with self.subTest(value=bad):
                config = json.loads(json.dumps(original))
                config["component_versions"]["dashboard"] = bad
                config_path.write_text(json.dumps(config) + "\n")
                errors = validator.validate_bundle(self.root, check_compose=False)
                self.assertTrue(any("component_versions" in error for error in errors), (bad, errors))
        for good in ("v5.15.0", "v1.5.0-rc2", "v2.2.0-rc10.0"):
            with self.subTest(value=good):
                config = json.loads(json.dumps(original))
                config["component_versions"]["operator"] = good
                config_path.write_text(json.dumps(config) + "\n")
                errors = validator.validate_bundle(self.root, check_compose=False)
                self.assertEqual([e for e in errors if "component_versions" in e], [], (good, errors))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_generator_refuses_a_version_the_validator_would_reject(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(Path(temporary) / "b"),
                 "--slug", "tag-demo", "--name", "Tag Demo", "--modules", "rest", "--versions", "pinned",
                 "--dashboard-version", "5.15.0"],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--dashboard-version must be a vX.Y.Z tag", result.stderr)

    def test_proof_missing_for_a_known_module_is_reported(self):
        # Permitting unknown modules must not weaken the guarantee that every
        # module the generator does ship carries its test ID.
        config_path = self.root / "demo.config.json"
        config = json.loads(config_path.read_text())
        self.assertIn("rest", config["modules"])
        config["proof"].pop("rest", None)
        config_path.write_text(json.dumps(config) + "\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("proof map" in error for error in errors), errors)

    def test_extra_ports_and_versions_are_permitted(self):
        # The same closed-list problem in two more places: a bundle that adds a
        # service declares its port, and one that adds a component declares its
        # version. Neither should fail the bundle's own gate.
        config_path = self.root / "demo.config.json"
        config = json.loads(config_path.read_text())
        config["ports"]["mystery_service"] = 24999
        config["component_versions"]["mystery"] = "v1.2.3"
        config_path.write_text(json.dumps(config) + "\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertEqual([e for e in errors if "port" in e or "component_versions" in e], [], errors)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is not installed")
    def test_invalid_yaml_is_reported_when_yaml_parser_is_available(self):
        (self.root / "broken.yaml").write_text("key: [unterminated")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("broken.yaml: invalid yaml" in error.lower() for error in errors), errors)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is not installed")
    def test_multi_document_operator_yaml_is_allowed(self):
        (self.root / "operator.yaml").write_text("apiVersion: v1\nkind: ConfigMap\n---\napiVersion: v1\nkind: Secret\n")
        self.assertEqual(validator.validate_bundle(self.root, check_compose=False), [])

    def test_invalid_bash_syntax_is_reported(self):
        (self.root / "scripts" / "broken.sh").write_text("if then\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("bash syntax check failed" in error for error in errors), errors)

    def test_invalid_python_syntax_is_reported(self):
        (self.root / "upstreams" / "broken.py").write_text("def broken(:\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("broken.py: invalid py" in error for error in errors), errors)

    def test_unresolved_placeholder_outside_template_is_reported(self):
        (self.root / "notes" / "bad.txt").write_text("value=__UNRESOLVED__\n")
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("unresolved placeholder __UNRESOLVED__" in error for error in errors), errors)

    def test_unresolved_placeholder_inside_template_is_allowed(self):
        (self.root / "apis" / "runtime.template.json").write_text('{"id": "__RUNTIME_ID__"}\n')
        self.assertEqual(validator.validate_bundle(self.root, check_compose=False), [])

    def test_runtime_file_with_group_access_is_reported(self):
        runtime = self.root / ".runtime"
        runtime.mkdir(mode=0o700)
        runtime_file = runtime / "runtime.env"
        runtime_file.write_text("TOKEN=local\n")
        runtime_file.chmod(0o640)
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("runtime secret permissions" in error for error in errors), errors)

    def test_runtime_directory_and_file_with_private_permissions_are_allowed(self):
        runtime = self.root / ".runtime"
        runtime.mkdir(mode=0o700)
        runtime_file = runtime / "runtime.env"
        runtime_file.write_text("TOKEN=local\n")
        runtime_file.chmod(0o600)
        self.assertEqual(validator.validate_bundle(self.root, check_compose=False), [])

    def test_dot_env_with_group_access_is_reported(self):
        environment_file = self.root / ".env"
        environment_file.write_text("TYK_" + "LICENSE_KEY=local\n")
        environment_file.chmod(0o640)
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any(".env: sensitive file permissions" in error for error in errors), errors)

    def test_private_key_with_group_access_is_reported(self):
        key = self.root / "generated" / "certs" / "client.key"
        key.parent.mkdir(parents=True)
        key.write_text("test-only\n")
        key.chmod(0o644)
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any("client.key: sensitive file permissions" in error for error in errors), errors)

    def test_missing_runtime_gitignore_entry_is_reported(self):
        gitignore = self.root / ".gitignore"
        gitignore.write_text(gitignore.read_text().replace(".runtime/\n", ""))
        errors = validator.validate_bundle(self.root, check_compose=False)
        self.assertTrue(any(".runtime/" in error for error in errors), errors)

    @unittest.skipUnless(validator.compose_command(), "docker compose is not available")
    def test_generated_compose_file_passes_docker_compose_config(self):
        self.assertEqual(validator.validate_compose(self.root), [])


class StartStackExitStatusTests(unittest.TestCase):
    """A healthy stack must exit 0 even when the optional Portal module is absent.

    `module_enabled portal && echo ...` as the final command makes the script exit
    non-zero whenever Portal is not selected, because the short-circuited && becomes
    the script's status. Automation then reads a working stack as a failed start.
    """

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "no-portal"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
             "--slug", "no-portal-demo", "--name", "No Portal Demo", "--modules", "rest,api-key"],
            check=True, capture_output=True, text=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_reporting_tail(self, script: str) -> subprocess.CompletedProcess[str]:
        """Run the reporting tail with Portal disabled, without Docker or licenses."""
        tail = script[script.index('echo "Dashboard:'):]
        harness = (
            "set -euo pipefail\n"
            "module_enabled() { return 1; }\n"
            'DASHBOARD_URL=http://d\nGATEWAY_URL=https://g\nPORTAL_URL=http://p\n'
            f"{tail}"
        )
        return subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)

    def test_non_portal_bundle_reports_success(self):
        script = (self.root / "scripts" / "start-demo-stack.sh").read_text()
        result = self.run_reporting_tail(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Portal:", result.stdout)
        self.assertIn("Gateway:", result.stdout)

    def test_harness_still_detects_the_short_circuit_regression(self):
        regressed = (
            'echo "Dashboard: ${DASHBOARD_URL}"\n'
            'echo "Gateway:   ${GATEWAY_URL}"\n'
            'module_enabled portal && echo "Portal:    ${PORTAL_URL}"\n'
        )
        self.assertEqual(self.run_reporting_tail(regressed).returncode, 1)

    def test_portal_bundle_still_reports_the_portal_url(self):
        script = (self.root / "scripts" / "start-demo-stack.sh").read_text()
        tail = script[script.index('echo "Dashboard:'):]
        harness = (
            "set -euo pipefail\n"
            "module_enabled() { return 0; }\n"
            'DASHBOARD_URL=http://d\nGATEWAY_URL=https://g\nPORTAL_URL=http://p\n'
            f"{tail}"
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Portal:", result.stdout)


class ConfigureRetryTests(unittest.TestCase):
    """Dashboard resource creation must tolerate the post-bootstrap propagation window."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "retry"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
             "--slug", "retry-demo", "--name", "Retry Demo", "--modules", "rest,api-key"],
            check=True, capture_output=True, text=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_common_exposes_a_retry_helper(self):
        common = (self.root / "scripts" / "common.sh").read_text()
        self.assertIn("retry_capture()", common)

    def test_dashboard_key_creation_is_retried(self):
        configure = (self.root / "scripts" / "configure-demo.sh").read_text()
        self.assertIn("retry_capture 30 2 curl -fsS \"${DASHBOARD_URL}/api/keys\"", configure)

    def test_retry_helper_returns_output_after_a_transient_failure(self):
        common = (self.root / "scripts" / "common.sh").read_text()
        helper = common[common.index("retry_capture()"):]
        harness = (
            "set -euo pipefail\n"
            f"{helper}\n"
            'COUNTER="$(mktemp)"; printf 0 >"$COUNTER"\n'
            'flaky() {\n'
            '  n=$(cat "$COUNTER"); n=$((n+1)); printf %s "$n" >"$COUNTER"\n'
            '  [ "$n" -lt 3 ] && return 22\n'
            '  printf ok\n'
            '}\n'
            'out="$(retry_capture 5 0 flaky)"\n'
            'printf "result=%s attempts=%s\\n" "$out" "$(cat "$COUNTER")"\n'
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("result=ok attempts=3", result.stdout)

    def source_common(self, env: dict[str, str], runtime_env: str | None = None) -> dict[str, str]:
        runtime = self.root / ".runtime" / "runtime.env"
        runtime.unlink(missing_ok=True)
        if runtime_env is not None:
            runtime.parent.mkdir(exist_ok=True)
            runtime.write_text(runtime_env)
        keys = ("PORTAL_ADMIN_EMAIL", "PORTAL_ADMIN_PASSWORD", "DASHBOARD_EMAIL", "DASHBOARD_PASSWORD",
                "PORTAL_API_TOKEN", "ORG_ID", "DASHBOARD_API_KEY", "DEMO_API_KEY")
        script = f'source "{self.root}/scripts/common.sh"\n' + "".join(
            f'printf "%s=%s\\n" {key} "${{{key}:-}}"\n' for key in keys)
        try:
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                                    env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **env})
        finally:
            runtime.unlink(missing_ok=True)
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_common_keeps_operator_supplied_values_and_clears_runtime_state(self):
        values = self.source_common({
            "PORTAL_ADMIN_EMAIL": "ops@example.test", "PORTAL_ADMIN_PASSWORD": "operator-pass",
            "DASHBOARD_EMAIL": "admin@example.test", "DASHBOARD_PASSWORD": "dash-pass",
            "PORTAL_API_TOKEN": "portal-token",
            # Inherited from `source .runtime/runtime.env` of a stack that no longer exists.
            "ORG_ID": "stale-org", "DASHBOARD_API_KEY": "stale-key", "DEMO_API_KEY": "stale-demo-key",
        })
        self.assertEqual(values["PORTAL_ADMIN_EMAIL"], "ops@example.test")
        self.assertEqual(values["PORTAL_ADMIN_PASSWORD"], "operator-pass")
        self.assertEqual(values["DASHBOARD_EMAIL"], "admin@example.test")
        self.assertEqual(values["DASHBOARD_PASSWORD"], "dash-pass")
        self.assertEqual(values["PORTAL_API_TOKEN"], "portal-token")
        self.assertEqual(values["ORG_ID"], "")
        self.assertEqual(values["DASHBOARD_API_KEY"], "")
        self.assertEqual(values["DEMO_API_KEY"], "")

    def test_runtime_env_still_wins_over_an_operator_export(self):
        values = self.source_common(
            {"PORTAL_ADMIN_PASSWORD": "operator-pass", "ORG_ID": "stale-org"},
            runtime_env="export PORTAL_ADMIN_PASSWORD=recorded-pass\nexport ORG_ID=live-org\n",
        )
        self.assertEqual(values["PORTAL_ADMIN_PASSWORD"], "recorded-pass")
        self.assertEqual(values["ORG_ID"], "live-org")

    def test_configure_records_portal_admin_credentials_it_used(self):
        configure = (self.root / "scripts" / "configure-demo.sh").read_text()
        self.assertIn('runtime_set PORTAL_ADMIN_EMAIL "${PORTAL_ADMIN_EMAIL:-portal-owner@retry-demo.invalid}"', configure)
        self.assertIn('runtime_set PORTAL_ADMIN_PASSWORD "${PORTAL_ADMIN_PASSWORD:-$(openssl rand -hex 18)}"', configure)

    def test_retry_helper_fails_after_exhausting_attempts(self):
        common = (self.root / "scripts" / "common.sh").read_text()
        helper = common[common.index("retry_capture()"):]
        harness = (
            "set -uo pipefail\n"
            f"{helper}\n"
            'always_fails() { return 22; }\n'
            'if retry_capture 3 0 always_fails; then echo unexpected; else echo "failed=$?"; fi\n'
        )
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)
        self.assertIn("failed=1", result.stdout)


class RequirementStatusTests(unittest.TestCase):
    """update-requirements.py must never overstate a requirement.

    The rule that matters: a row is marked `live` only when every test it lists is
    present in results.json and passing. A listed test with no result is unproven, not
    absent, so partial evidence keeps the curated status. Real failures still downgrade,
    because understating a recorded failure is the same defect in reverse.
    """

    HEADER = (
        "| Requirement | Capability | Asset | Test | Status | Evidence |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "bundle"
        subprocess.run(
            [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(self.root),
             "--slug", "status-demo", "--name", "Status Demo", "--modules", "rest"],
            check=True, capture_output=True, text=True,
        )
        (self.root / "evidence").mkdir(exist_ok=True)

    def tearDown(self):
        self.temporary.cleanup()

    def run_update(self, results: list[dict], rows: str) -> list[str]:
        (self.root / "evidence" / "results.json").write_text(json.dumps(results))
        (self.root / "notes" / "requirements-mapping.md").write_text(self.HEADER + rows)
        subprocess.run(
            [sys.executable, str(self.root / "scripts" / "update-requirements.py")],
            check=True, capture_output=True, text=True,
        )
        text = (self.root / "notes" / "requirements-mapping.md").read_text()
        return [line for line in text.splitlines() if line.startswith("| REQ-")]

    @staticmethod
    def status_of(row: str) -> str:
        return row.split("|")[5].strip().strip("`")

    def test_single_passing_test_marks_the_row_live(self):
        rows = self.run_update(
            [{"test_id": "REST-001", "status": "passed", "detail": "proxied"}],
            "| REQ-001: Proxy | Proxy | `apis/core` | `REST-001` | `blocked` |  |\n",
        )
        self.assertEqual(self.status_of(rows[0]), "live")

    def test_unrun_test_in_the_row_never_marks_live(self):
        rows = self.run_update(
            [{"test_id": "OTEL-001", "status": "passed", "detail": "spans"}],
            "| REQ-001: Vendor export | Obs | `otel` | `OTEL-001, DATADOG-001` | `blocked` | Needs an account |\n",
        )
        self.assertEqual(self.status_of(rows[0]), "blocked")
        self.assertIn("DATADOG-001 not run", rows[0])
        self.assertIn("Needs an account", rows[0])

    def test_weakest_status_wins_over_a_passing_sibling(self):
        rows = self.run_update(
            [{"test_id": "REST-001", "status": "passed", "detail": "ok"},
             {"test_id": "AUTH-KEY-001", "status": "failed", "detail": "401 expected"}],
            "| REQ-001: Key auth | Auth | `apis/key` | `REST-001, AUTH-KEY-001` | `blocked` |  |\n",
        )
        self.assertEqual(self.status_of(rows[0]), "failed")

    def test_recorded_failure_downgrades_even_with_an_unrun_sibling(self):
        rows = self.run_update(
            [{"test_id": "REST-001", "status": "failed", "detail": "502"}],
            "| REQ-001: Proxy | Proxy | `apis/core` | `REST-001, LATER-001` | `live` |  |\n",
        )
        self.assertEqual(self.status_of(rows[0]), "failed")

    def test_row_with_no_matching_results_is_left_untouched(self):
        original = "| REQ-001: Architecture | X | `x` | `NEVER-RAN-001` | `talk-track` | Architecture only |"
        rows = self.run_update(
            [{"test_id": "REST-001", "status": "passed", "detail": "ok"}],
            original + "\n",
        )
        self.assertEqual(rows[0], original)

    def test_repeated_runs_are_byte_identical(self):
        results = [{"test_id": "REST-001", "status": "passed", "detail": "ok"}]
        rows = "| REQ-001: Proxy | Proxy | `apis/core` | `REST-001, LATER-001` | `blocked` | Curated note |\n"
        self.run_update(results, rows)
        mapping = self.root / "notes" / "requirements-mapping.md"
        first = mapping.read_text()
        for _ in range(2):
            subprocess.run(
                [sys.executable, str(self.root / "scripts" / "update-requirements.py")],
                check=True, capture_output=True, text=True,
            )
        self.assertEqual(mapping.read_text(), first)
        self.assertEqual(first.count("Curated note"), 1)
        self.assertEqual(first.count("evidence/results.json"), 1)


class StaleLiveRowTests(unittest.TestCase):
    """A `live` row from an earlier run never survives a run that did not prove it again.

    Every case starts from a row an earlier run (run A) marked `live`, then applies the
    current results (run B). The machine segment must name run B and nothing of run A.
    """

    RUN_A = "2026-01-01T00:00:00Z"
    RUN_B = "2026-02-02T00:00:00Z"
    HEADER = RequirementStatusTests.HEADER
    setUp = RequirementStatusTests.setUp
    tearDown = RequirementStatusTests.tearDown
    run_update = RequirementStatusTests.run_update
    status_of = staticmethod(RequirementStatusTests.status_of)

    def live_row(self, test_ids: str, curated: str = "Curated note") -> str:
        first = test_ids.split(",")[0].strip()
        machine = f"`evidence/results.json` (run {self.RUN_A}: {first} passed)"
        evidence = f"{curated} · verified: {machine}" if curated else machine
        return f"| REQ-001: Proxy | Proxy | `apis/core` | `{test_ids}` | `live` | {evidence} |\n"

    def stamped(self, *items: tuple[str, str]) -> list[dict]:
        return [{"test_id": test_id, "status": status, "detail": "d", "run": self.RUN_B} for test_id, status in items]

    def assert_run_b_only(self, row: str) -> None:
        self.assertIn(f"run {self.RUN_B}", row)
        self.assertNotIn(self.RUN_A, row)
        self.assertIn("Curated note", row)
        self.assertEqual(row.count(" · verified: "), 1)

    def test_partial_results_downgrade_a_live_row_to_blocked(self):
        rows = self.run_update(self.stamped(("REST-001", "passed")), self.live_row("REST-001, LATER-001"))
        self.assertEqual(self.status_of(rows[0]), "blocked")
        self.assertIn("REST-001 passed", rows[0])
        self.assertIn("LATER-001 not run in this run", rows[0])
        self.assert_run_b_only(rows[0])

    def test_partial_results_keep_a_curated_talk_track_status(self):
        row = self.live_row("REST-001, LATER-001").replace("`live`", "`talk-track`")
        rows = self.run_update(self.stamped(("REST-001", "passed")), row)
        self.assertEqual(self.status_of(rows[0]), "talk-track")
        self.assertIn("LATER-001 not run in this run", rows[0])

    def test_no_results_for_the_row_downgrade_it_and_drop_the_stale_segment(self):
        rows = self.run_update(self.stamped(("HEALTH-001", "passed")), self.live_row("REST-001"))
        self.assertEqual(self.status_of(rows[0]), "blocked")
        self.assertIn("REST-001 not run in this run", rows[0])
        self.assertNotIn("REST-001 passed", rows[0])
        self.assert_run_b_only(rows[0])

    def test_an_empty_results_file_downgrades_a_live_row(self):
        rows = self.run_update([], self.live_row("REST-001", curated=""))
        self.assertEqual(self.status_of(rows[0]), "blocked")
        evidence = rows[0].strip().strip("|").split("|")[5].strip()
        self.assertTrue(evidence.startswith("`evidence/results.json` (run "), evidence)
        self.assertIn("REST-001 not run in this run", evidence)
        self.assertNotIn(self.RUN_A, evidence)

    def test_a_failed_result_downgrades_a_live_row(self):
        rows = self.run_update(self.stamped(("REST-001", "failed")), self.live_row("REST-001"))
        self.assertEqual(self.status_of(rows[0]), "failed")
        self.assertIn("REST-001 failed", rows[0])
        self.assert_run_b_only(rows[0])

    def test_results_from_a_different_run_replace_the_earlier_runs_stamp(self):
        rows = self.run_update(self.stamped(("REST-001", "passed")), self.live_row("REST-001"))
        self.assertEqual(self.status_of(rows[0]), "live")
        self.assert_run_b_only(rows[0])
        # A results file without a run field is identified by its modification time.
        results = self.root / "evidence" / "results.json"
        results.write_text(json.dumps([{"test_id": "REST-001", "status": "passed", "detail": "d"}]))
        os.utime(results, (1767225600, 1767225600))  # 2026-01-01T00:00:00Z
        subprocess.run([sys.executable, str(self.root / "scripts" / "update-requirements.py")],
                       check=True, capture_output=True, text=True)
        row = [line for line in (self.root / "notes" / "requirements-mapping.md").read_text().splitlines()
               if line.startswith("| REQ-")][0]
        self.assertIn("run 2026-01-01T00:00:00Z: REST-001 passed", row)
        self.assertNotIn(self.RUN_B, row)

    def test_complete_passing_results_keep_the_row_live(self):
        rows = self.run_update(
            self.stamped(("REST-001", "passed"), ("HEALTH-001", "passed")),
            self.live_row("REST-001, HEALTH-001"),
        )
        self.assertEqual(self.status_of(rows[0]), "live")
        self.assertIn("REST-001 passed; HEALTH-001 passed", rows[0])
        self.assert_run_b_only(rows[0])

    def test_a_companion_runner_proves_its_own_rows(self):
        # The AI Studio companion writes companion/ai-studio/evidence/results.json from its own
        # verify-ai-studio.sh; verify-demo.sh never records AISTUDIO-* in evidence/results.json.
        row = self.live_row("AISTUDIO-ROUTER-001")
        companion = self.root / "companion" / "ai-studio" / "evidence"
        companion.mkdir(parents=True, exist_ok=True)
        (companion / "results.json").write_text(json.dumps(
            [{"test_id": "AISTUDIO-ROUTER-001", "status": "passed", "detail": "routed", "run": self.RUN_B}]))
        rows = self.run_update(self.stamped(("REST-001", "passed")), row)
        self.assertEqual(self.status_of(rows[0]), "live")
        self.assertIn(f"`companion/ai-studio/evidence/results.json` (run {self.RUN_B}: AISTUDIO-ROUTER-001 passed)", rows[0])
        # Without the companion's results the row is unproven in this run.
        (companion / "results.json").unlink()
        rows = self.run_update(self.stamped(("REST-001", "passed")), row)
        self.assertEqual(self.status_of(rows[0]), "blocked")
        self.assertIn("AISTUDIO-ROUTER-001 not run in this run", rows[0])

    def test_a_main_result_wins_over_a_companion_result(self):
        companion = self.root / "companion" / "ai-studio" / "evidence"
        companion.mkdir(parents=True, exist_ok=True)
        (companion / "results.json").write_text(json.dumps([{"test_id": "REST-001", "status": "passed", "detail": "x"}]))
        rows = self.run_update(self.stamped(("REST-001", "failed")), self.live_row("REST-001"))
        self.assertEqual(self.status_of(rows[0]), "failed")
        self.assertNotIn("companion/", rows[0])


class SanitizerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def kinds(self, forbid=()):
        return [finding.kind for finding in sanitizer.scan_bundle(self.root, forbid)]

    def test_forbid_rejects_case_insensitive_term(self):
        self.write("README.md", "Demo for Acme Corp\n")
        self.assertIn("forbidden term 'acme corp'", self.kinds(["acme corp"]))

    def test_absolute_macos_user_path_is_reported(self):
        self.write("notes.md", "/" + "Users" + "/alice/demo\n")
        self.assertIn("absolute macOS user path", self.kinds())

    def test_local_file_uri_is_reported(self):
        self.write("notes.md", "file:" + "///tmp/demo.json\n")
        self.assertIn("local file URI", self.kinds())

    def test_private_key_material_is_reported(self):
        self.write("client.key", "-----BEGIN " + "PRIVATE KEY-----\nmaterial\n")
        self.assertIn("private key material", self.kinds())

    def test_token_material_is_reported(self):
        self.write("notes.md", "ghp_" + "abcdefghijklmnopqrstuvwxyz123456\n")
        self.assertIn("token material", self.kinds())

    def test_nonempty_license_assignment_is_reported(self):
        self.write("credentials.env", "TYK_" + "LICENSE_KEY=" + "real-license-value\n")
        self.assertIn("embedded credential or license", self.kinds())

    def test_generated_password_assignment_is_reported(self):
        self.write("credentials.env", "DASHBOARD_" + "PASSWORD=" + "random-generated-password\n")
        self.assertIn("embedded credential or license", self.kinds())

    def test_empty_and_variable_credentials_are_allowed(self):
        self.write(".env.example", "TYK_LICENSE_KEY=\nTOKEN=${TOKEN}\nAPI_KEY=__API_KEY__\n")
        self.assertEqual(self.kinds(), [])

    def test_runtime_secret_paths_are_excluded(self):
        self.write(".runtime/runtime.env", "TYK_" + "LICENSE_KEY=local-license\nTOKEN=ghp_" + "abcdefghijklmnopqrstuvwxyz123456\n")
        self.assertEqual(self.kinds(), [])

    def test_runtime_dot_env_is_excluded(self):
        self.write(".env", "TYK_" + "LICENSE_KEY=local-license\nTOKEN=ghp_" + "abcdefghijklmnopqrstuvwxyz123456\n")
        self.assertEqual(self.kinds(), [])

    def test_gitignored_secret_directory_is_excluded(self):
        self.write(".gitignore", "generated/certs/\n")
        self.write("generated/certs/client.key", "-----BEGIN " + "PRIVATE KEY-----\nmaterial\n")
        self.assertEqual(self.kinds(), [])

    def test_oauth_token_url_is_not_a_credential(self):
        self.write("api.json", '{"tokenUrl": "/oauth/token"}\n')
        self.assertEqual(self.kinds(), [])

    def test_github_secret_expression_is_safe(self):
        self.write("workflow.yml", "TYK_LICENSE_KEY: ${{ secrets.TYK_LICENSE_KEY }}\n")
        self.assertEqual(self.kinds(), [])

    def test_dashboard_password_setting_name_is_not_a_credential(self):
        self.write("dashboard.env", "TYK_DB_SECURITY_ALLOWADMINRESETPASSWORD=true\n")
        self.assertEqual(self.kinds(), [])

    def test_shell_password_source_identifier_is_not_a_credential(self):
        self.write("script.sh", 'password_var="DASHBOARD_PASSWORD"\n')
        self.assertEqual(self.kinds(), [])

    def test_python_private_key_filename_is_not_key_material(self):
        self.write("identity.py", 'PRIVATE_KEY = Path("identity-private.key")\n')
        self.assertEqual(self.kinds(), [])

    def test_python_access_token_output_is_not_a_credential(self):
        self.write("identity.py", 'return {"access_token": token, "token_type": "Bearer"}\n')
        self.assertEqual(self.kinds(), [])

    # ---- credential literals in data files, placeholders and code references ----------

    def test_alphanumeric_password_literal_in_an_env_file_is_reported(self):
        self.write("demo.env", "DASHBOARD_" + "PASSWORD=" + "CorrectHorseBatteryStaple123\n")
        self.assertIn("embedded credential or license", self.kinds())

    def test_hex_api_key_literal_in_json_is_reported(self):
        self.write("config.json", '{"api_' + 'key": "' + "abcdef0123456789" * 2 + '"}\n')
        self.assertIn("embedded credential or license", self.kinds())

    def test_identifier_like_literals_in_config_files_are_reported(self):
        for relative, text in (
            ("compose.yaml", "    GATEWAY_" + "SECRET: " + "SyntheticSecret42\n"),
            ("settings.toml", "api_" + "key = " + '"SyntheticKey42"\n'),
            ("settings.ini", "PASS" + "WORD=" + "SyntheticPass42\n"),
            ("notes.md", "TO" + "KEN=" + "SyntheticToken42\n"),
            ("script.sh", "DEMO_" + "TOKEN=" + "SyntheticToken42\n"),
        ):
            with self.subTest(relative=relative):
                for path in self.root.iterdir():
                    path.unlink()
                self.write(relative, text)
                self.assertIn("embedded credential or license", self.kinds())

    def test_explicit_placeholders_pass_in_every_file_type(self):
        self.write(".env.example", "\n".join((
            "A_PASSWORD=<your-password>", "B_PASSWORD=__B_PASSWORD__", "C_PASSWORD=${C_PASSWORD}",
            "D_PASSWORD=$D_PASSWORD", 'E_PASSWORD="$(openssl rand -hex 18)"', "F_PASSWORD=changeme",
            "G_TOKEN=${{ secrets.G_TOKEN }}", "H_API_KEY=unused", "I_PRIVATE_KEY=server.key", "J_SECRET=",
        )) + "\n")
        self.write("config.json", '{"password": "<set-me>", "api_key": "${API_KEY}"}\n')
        self.assertEqual(self.kinds(), [])

    def test_code_references_pass_only_in_source_code(self):
        self.write("app.py", 'PASSWORD = os.environ["DEMO_PASSWORD"]\nAPI_KEY = load_api_key()\nSECRET = settings.secret\n')
        self.write("app.js", "const TOKEN = process.env.TOKEN;\n")
        self.assertEqual(self.kinds(), [])
        # A quoted literal is a string, not a reference, even in source code.
        self.write("app.py", "PASS" + "WORD = " + '"SyntheticPass42"\n')
        self.assertIn("embedded credential or license", self.kinds())

    def test_minted_json_key_values_are_reported_but_key_names_are_not(self):
        self.write("export.json", '{"keys": [{"key": "migrated-' + "0123456789abcdef" * 2 + '"}]}\n')
        self.assertIn("embedded key value", self.kinds())
        self.write("export.json", '{"key": "Authorization", "other": {"key": "X-Api-Key"}, "k": {"key": "<minted by scripts/import-keys.sh>"}}\n')
        self.assertEqual(self.kinds(), [])

    def test_compose_list_form_and_shell_declarations_are_reported(self):
        # Values are assembled so this test file never holds a line the scan itself would report.
        value = "Synthetic" + "Pass42"
        self.write("compose.yaml", "services:\n  db:\n    environment:\n      - POSTGRES_" + "PASSWORD=" + value + "\n")
        self.assertEqual(self.kinds(), ["embedded credential or license"])
        (self.root / "compose.yaml").unlink()
        for declaration in ("local", "readonly", "declare -r", "typeset"):
            self.write("run.sh", declaration + " API_" + 'KEY="' + value + '"\n')
            self.assertEqual(self.kinds(), ["embedded credential or license"], declaration)

    def test_json_names_ending_in_a_credential_word_are_reported(self):
        value = "0123456789abcdef" * 2
        for name in ("node_secret", "admin_secret", "client_secret", "auth_token", "secret_key", "apiKey", "clientSecret"):
            self.write("config.json", '{"' + name + '": "' + value + '"}\n')
            self.assertEqual(self.kinds(), ["embedded credential or license"], name)
        # A name that merely contains a credential word is not a credential field, a kebab-case
        # module name is not one either, and a secret-store reference or stub value is not a secret.
        self.write("config.json", '{"secret_name": "tyk-operator-conf", "token_type": "Bearer", "key_id": "' + value + '",'
                   ' "api-key": "AUTH-KEY-001", "aws_secret_access_key": "$SECRET/AWS_SECRET_ACCESS_KEY"}\n')
        self.write("configure.sh", "LLM_A_API_" + 'KEY="${LLM_A_API_KEY:-stub-key}"\n')
        self.assertEqual(self.kinds(), [])

    def test_env_names_without_a_separator_or_with_short_suffixes_are_reported(self):
        value = "Synthetic" + "Pass42"
        for name in ("TYK_DB_ADMIN" + "SECRET", "DB_" + "PASS", "JWT_SIGNING_" + "KEY", "AWS_SECRET_ACCESS_" + "KEY"):
            self.write("stack.env", name + "=" + value + "\n")
            self.assertEqual(self.kinds(), ["embedded credential or license"], name)

    def test_a_literal_default_inside_a_variable_reference_is_reported(self):
        value = "Synthetic" + "Pass42"
        self.write("compose.yaml", "      POSTGRES_" + "PASSWORD: ${POSTGRES_" + "PASSWORD:-" + value + "}\n")
        self.assertEqual(self.kinds(), ["embedded credential or license"])
        # A reference with no default, an empty default, or a required-variable message is safe.
        self.write("compose.yaml", "      A_" + "PASSWORD: ${A_PASSWORD}\n      B_" + "PASSWORD: ${B_PASSWORD:-}\n"
                   "      C_" + "PASSWORD: ${C_PASSWORD:?set it in .env}\n")
        self.assertEqual(self.kinds(), [])

    def test_strict_runtime_refuses_a_symbolic_link(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "creds.env").write_text("DEMO_" + "PASSWORD=" + "SyntheticPass42\n")
        self.write("README.md", "clean\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "linked.env").symlink_to(outside / "creds.env")
        (self.root / "notes" / "linkdir").symlink_to(outside, target_is_directory=True)
        # The scan does not read through a link, so the plain run is clean.
        self.assertEqual(self.kinds(), [])
        self.assertEqual(sanitizer.symlinks_present(self.root), [Path("notes/linkdir"), Path("notes/linked.env")])
        strict = subprocess.run([sys.executable, str(SCRIPTS / "sanitize_bundle.py"), str(self.root), "--strict-runtime"],
                                capture_output=True, text=True)
        self.assertEqual(strict.returncode, 1)
        self.assertIn("notes/linked.env: symbolic link", strict.stderr)
        self.assertIn("notes/linkdir: symbolic link", strict.stderr)
        self.assertNotIn("SyntheticPass42", strict.stderr + strict.stdout)

    def test_strict_runtime_lists_the_imported_keys_file(self):
        self.write("migration/imported-keys.json", '{"keys": []}\n')
        self.assertIn(Path("migration/imported-keys.json"), sanitizer.runtime_artifacts(self.root))

    def test_strict_runtime_refuses_an_ignored_but_present_secret_file(self):
        self.write(".gitignore", "notes/private/\n*.secret\n")
        self.write("notes/private/creds.md", "DEMO_" + "PASSWORD=" + "SyntheticPass42\n")
        self.write("upstream.secret", "anything\n")
        self.write("README.md", "clean\n")
        # The plain scan skips ignored files, so it alone cannot vouch for an archive.
        self.assertEqual(self.kinds(), [])
        self.assertEqual(sanitizer.ignored_present(self.root), [Path("notes/private"), Path("upstream.secret")])
        strict = subprocess.run([sys.executable, str(SCRIPTS / "sanitize_bundle.py"), str(self.root), "--strict-runtime"],
                                capture_output=True, text=True)
        self.assertEqual(strict.returncode, 1)
        self.assertIn("notes/private: git-ignored", strict.stderr)
        self.assertIn("upstream.secret: git-ignored", strict.stderr)
        self.assertNotIn("SyntheticPass42", strict.stderr + strict.stdout)
        plain = subprocess.run([sys.executable, str(SCRIPTS / "sanitize_bundle.py"), str(self.root)], capture_output=True, text=True)
        self.assertEqual(plain.returncode, 0)
        self.assertIn("2 git-ignored path(s) were not scanned", plain.stdout)
        # Once removed, the strict run passes.
        shutil.rmtree(self.root / "notes" / "private")
        (self.root / "upstream.secret").unlink()
        strict = subprocess.run([sys.executable, str(SCRIPTS / "sanitize_bundle.py"), str(self.root), "--strict-runtime"],
                                capture_output=True, text=True)
        self.assertEqual(strict.returncode, 0, strict.stderr)

    def test_findings_do_not_include_secret_values(self):
        sensitive_value = "never-print-this-" + "secret"
        self.write("credentials.env", "API_" + f"KEY={sensitive_value}\n")
        self.assertNotIn(sensitive_value, str(sanitizer.scan_bundle(self.root)))

    def test_strict_runtime_detects_ignored_local_state_without_reading_it(self):
        self.write(".env", "TYK_" + "LICENSE_KEY=local\n")
        self.assertIn(Path(".env"), sanitizer.runtime_artifacts(self.root))

    def test_strict_runtime_detects_companion_secrets_and_state(self):
        self.write("companion/ai-studio/secret.env", "TYK_AI_" + "LICENSE=local\n")
        self.write("companion/ai-studio/.runtime/state.env", "x=1\n")
        self.write("companion/ai-studio/data/ai-studio.db", "sqlite\n")
        self.write("companion/ai-studio/secret.env.example", "TYK_AI_LICENSE=\n")
        self.write("companion/ai-studio/admin.env", "AISTUDIO_ADMIN_" + "PASSWORD=local\n")
        artifacts = sanitizer.runtime_artifacts(self.root)
        for expected in ("companion/ai-studio/secret.env", "companion/ai-studio/admin.env", "companion/ai-studio/.runtime", "companion/ai-studio/data"):
            self.assertIn(Path(expected), artifacts)
        self.assertNotIn(Path("companion/ai-studio/secret.env.example"), artifacts)

    def test_clean_companion_has_no_runtime_artifacts(self):
        self.write("companion/ai-studio/compose.yaml", "services: {}\n")
        self.write("companion/ai-studio/secret.env.example", "TYK_AI_LICENSE=\n")
        self.assertEqual(sanitizer.runtime_artifacts(self.root), [])

    def test_multi_line_command_substitution_is_not_a_credential(self):
        self.assertTrue(sanitizer.opens_unclosed_substitution('"$(curl -fsS "https://example.test/api" \\'))
        self.assertTrue(sanitizer.opens_unclosed_substitution('"$(jq -r .value'))
        self.assertFalse(sanitizer.opens_unclosed_substitution('"$(openssl rand -hex 18)"'))
        self.assertFalse(sanitizer.opens_unclosed_substitution("plain-secret-value"))
        self.write("script.sh", 'DEMO_SECRET="$(curl -fsS "https://example.test/api" \\\n  | jq -r .value)"\n')
        self.assertEqual(self.kinds(), [])
        # A literal on the same kind of line is still reported.
        self.write("script.sh", "DEMO_" + "SECRET=literal-" + "value-here\n")
        self.assertIn("embedded credential or license", self.kinds())


class GovernanceModuleTests(unittest.TestCase):
    """The governance module is version-gated and ships its own before/after specs.

    Governance reached GA in Dashboard 5.15.0. Below that release every /api/rulesets and
    /api/governance path is absent, so an ungated bundle would configure nothing and report
    a 404 that says nothing about the real cause.
    """

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        if GENERATOR_LOAD_ERROR is None:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
                 "--slug", "gov-demo", "--name", "Gov Demo", "--modules", "rest,governance",
                 "--dashboard-version", "v5.15.0", "--gateway-version", "v5.15.0"],
                check=True, capture_output=True, text=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def read_json(self, relative: str):
        return json.loads((self.root / relative).read_text())

    def test_governance_expands_rest(self):
        self.assertEqual(generator.expand_modules("governance", "local"), ["governance", "rest"])

    def test_dashboard_version_gate_boundaries(self):
        for version in ("v5.15.0", "5.15.0", "v5.15.0-rc9", "v5.16.0", "v6.0.0"):
            self.assertTrue(generator.supports_governance(version), version)
        for version in ("v5.14.9", "v5.13.1", "5.9.0", "v4.9.0", "", "latest", "not-a-version"):
            self.assertFalse(generator.supports_governance(version), version)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_sub_5_15_dashboard_is_rejected_with_the_remedy(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output",
                 str(Path(temporary) / "bundle"), "--slug", "gov-old", "--name", "Gov Old",
                 "--modules", "rest,governance", "--dashboard-version", "v5.14.0"],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Dashboard v5.15.0 or later", result.stderr)
        self.assertIn("--dashboard-version v5.15.0", result.stderr)

    def test_operator_version_gate_boundaries(self):
        """TykRuleset and its admission webhook arrived in v1.5.0; a pre-release sorts below its release."""
        for version in ("v1.5.0", "v1.5.1", "v1.6.0", "v1.6.0-rc1", "v2.0.0"):
            self.assertTrue(generator.supports_governance_operator(version), version)
        for version in ("v1.5.0-rc1", "v1.5.0-rc6", "v1.4.1", "v1.4.9", "v1.0.0", "", "latest", "v1.5"):
            self.assertFalse(generator.supports_governance_operator(version), version)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_sub_5_15_gateway_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output",
                 str(Path(temporary) / "bundle"), "--slug", "gov-gw", "--name", "Gov Gw",
                 "--modules", "rest,governance", "--dashboard-version", "v5.15.0",
                 "--gateway-version", "v5.13.1"],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Gateway v5.15.0 or later", result.stderr)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_operator_below_1_5_is_rejected_only_with_governance(self):
        def generate(modules: str, operator_version: str) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as temporary:
                return subprocess.run(
                    [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output",
                     str(Path(temporary) / "bundle"), "--slug", "gov-op", "--name", "Gov Op",
                     "--modules", modules, "--dashboard-version", "v5.15.0",
                     "--gateway-version", "v5.15.0", "--operator-version", operator_version],
                    capture_output=True, text=True, check=False,
                )

        rejected = generate("governance,operator", "v1.5.0-rc6")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("requires Tyk Operator v1.5.0 or later", rejected.stderr)
        self.assertIn("--operator-version v1.6.0", rejected.stderr)
        self.assertEqual(generate("governance,operator", "v1.5.0").returncode, 0)
        self.assertEqual(generate("governance,operator", "v1.6.0").returncode, 0)
        self.assertNotEqual(generate("governance,operator", "v1.4.1").returncode, 0)
        # The gate belongs to the combination, not to the operator module on its own.
        self.assertEqual(generate("rest,operator", "v1.4.1").returncode, 0)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_governance_bundle_passes_static_validation(self):
        self.assertEqual(validator.validate_bundle(self.root), [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_legacy_api_is_deliberately_non_compliant(self):
        legacy = self.read_json("apis/gov-demo-governed-legacy.oas.json")
        self.assertTrue(legacy["servers"][0]["url"].startswith("http://"))
        self.assertNotIn("components", legacy)
        self.assertNotIn("security", legacy)
        self.assertNotIn("authentication", legacy["x-tyk-api-gateway"]["server"])
        self.assertNotIn("rateLimit", legacy["x-tyk-api-gateway"]["upstream"])
        self.assertNotIn("description", legacy["info"])
        operations = [
            operation for path in legacy["paths"].values() for operation in path.values()
        ]
        self.assertTrue(operations)
        self.assertTrue(all("operationId" not in operation for operation in operations))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_standard_api_carries_the_fixes_the_rulesets_assert(self):
        standard = self.read_json("apis/gov-demo-governed-standard.oas.json")
        gateway = standard["x-tyk-api-gateway"]
        self.assertTrue(standard["servers"][0]["url"].startswith("https://"))
        self.assertIn("description", standard["info"])
        self.assertEqual(standard["components"]["securitySchemes"]["authToken"]["type"], "apiKey")
        self.assertTrue(gateway["server"]["authentication"]["enabled"])
        self.assertTrue(gateway["upstream"]["rateLimit"]["enabled"])
        operations = [
            operation for path in standard["paths"].values() for operation in path.values()
        ]
        self.assertTrue(operations)
        self.assertTrue(all(operation.get("operationId") for operation in operations))
        # The OWASP template rejects an integer schema without a format and without both
        # bounds, so the rate-limit header has to carry all four fields.
        header = operations[0]["responses"]["200"]["headers"]["X-RateLimit-Limit"]["schema"]
        self.assertEqual(header, {"type": "integer", "format": "int32", "minimum": 0, "maximum": 10000})

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_governed_api_names_carry_no_category_tag(self):
        """A `#tag` category silently bypasses the pre-save Deployment Warning.

        Evaluation and the compliance view honour it, but the modal reads
        /api/apis/oas/{id}/categories, sees an empty list, and skips the warning, so the
        category must come from the categories endpoint instead.
        """
        checked = 0
        for name in ("legacy", "standard"):
            api = self.read_json(f"apis/gov-demo-governed-{name}.oas.json")
            checked += 1
            self.assertNotIn("#", api["info"]["title"])
            self.assertNotIn("#", api["x-tyk-api-gateway"]["info"]["name"])
        self.assertEqual(checked, 2)
        assign = (self.root / "governance/assign-categories.sh").read_text()
        self.assertIn("/api/apis/oas/${api_id}/categories", assign)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_custom_ruleset_keeps_the_quoted_tyk_extension_jsonpath(self):
        """A bare apostrophe here would close the single-quoted jq program.

        The shell would then concatenate the fragments into the unquoted JSONPath
        $[x-tyk-api-gateway].upstream, which matches nothing and lints nothing, with no
        syntax error anywhere to reveal it.
        """
        source = (self.root / "governance/configure-governance.sh").read_text()
        blocks = []
        for name in ("owasp_definition", "standards_definition"):
            start = source.index(f'{name}="$(jq')
            blocks.append(source[start:source.index("}')\"", start) + 4])
        # The harness goes in its own temporary directory: the fixture bundle is shared by
        # every test in this class, and a test that writes into it can only make the next
        # one harder to trust.
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / "definition-harness.sh"
            harness.write_text(
                '#!/usr/bin/env bash\nset -euo pipefail\nquote="\'"\n'
                + "\n".join(blocks)
                + '\nprintf \'%s\\n%s\\n\' "${owasp_definition}" "${standards_definition}"\n'
            )
            result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, check=True)
        owasp, standards = (json.loads(line) for line in result.stdout.splitlines())
        self.assertEqual(owasp["rules"]["owasp-security-hosts-https-oas3"], "warn")
        self.assertEqual(owasp["extends"], [["spectral:owasp", "all"]])
        rules = standards["rules"]
        self.assertEqual(rules["upstream-rate-limit-required"]["given"], "$['x-tyk-api-gateway'].upstream")
        self.assertEqual(rules["authentication-required"]["given"], "$['x-tyk-api-gateway'].server")
        for rule in rules.values():
            self.assertEqual(rule["severity"], "error")
            self.assertTrue(rule["howToFix"])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_verification_records_the_license_block_and_both_proof_ids(self):
        verify = (self.root / "scripts/verify-demo.sh").read_text()
        config = self.read_json("demo.config.json")
        self.assertEqual(config["proof"]["governance"], "GOV-001")
        self.assertIn('record GOV-001 blocked "Dashboard license lacks the governance claim"', verify)
        self.assertIn("record GOV-002 passed", verify)
        self.assertIn("gov-demo-payments", verify)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_operator_ruleset_is_written_only_alongside_the_operator_module(self):
        self.assertFalse((self.root / "operator/tyk-ruleset.yaml").exists())
        # Governance alone gets no operator directory at all, so none of the hard-block
        # companions can leak into a bundle that has no cluster to run them against.
        for name in GOVERNANCE_OPERATOR_FILES:
            self.assertFalse((self.root / "operator" / name).exists(), name)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
                 "--slug", "gov-operator", "--name", "Gov Operator",
                 "--modules", "governance,operator",
                 "--dashboard-version", "v5.15.0", "--gateway-version", "v5.15.0",
                 "--operator-version", "v1.6.0"],
                check=True, capture_output=True, text=True,
            )
            definition = (root / "operator/tyk-ruleset.yaml").read_text()
            self.assertIn("kind: TykRuleset", definition)
            self.assertIn("action: block", definition)
            # The Operator plane has its own category, apart from the Dashboard rulesets' one.
            categories = definition.split("  categories:\n", 1)[1].split("\n  #", 1)[0]
            self.assertEqual(categories.split(), ["-", "gov-operator-gitops"])
            # apply-operator.sh renders every operator/*.yaml with the namespace
            # substituted but applies only four of them, so the ruleset stays a deliberate
            # step and the instructions must point at the rendered copy.
            self.assertIn(".runtime/operator/tyk-ruleset.yaml", definition)
            self.assertNotIn("tyk-ruleset.yaml", (root / "operator/apply-operator.sh").read_text())
            self.assertEqual(validator.validate_bundle(root), [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_requirement_row_lists_both_governance_proof_ids(self):
        """update-requirements.py marks a row live only when every listed test passed.

        Listing GOV-002 in the cell makes the CI-gate proof a precondition of the
        governance claim rather than a line buried in results.json.
        """
        mapping = (self.root / "notes/requirements-mapping.md").read_text()
        row = next(line for line in mapping.splitlines() if "Demonstrate governance" in line)
        cell = [part.strip() for part in row.strip().strip("|").split("|")][3]
        self.assertEqual(cell, "`GOV-001`, `GOV-002`")
        # Parsed the way update-requirements.py's parse_test_ids parses it.
        self.assertEqual(
            [part.strip().strip("`").strip() for part in cell.split(",") if part.strip()],
            ["GOV-001", "GOV-002"],
        )

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_shipped_workflow_is_green_on_a_fresh_bundle(self):
        """The bundle ships a deliberately non-compliant spec as its "before" state.

        Linting it in the gating loop would make the very first run of the copied workflow
        red, which teaches everyone to ignore the check. It is excluded there and asserted
        to fail in its own negative-control step instead.
        """
        workflow = (self.root / "governance/github/governance-check.yml").read_text()
        self.assertIn("! -name '*-governed-legacy.oas.json'", workflow)
        self.assertIn("Assert the gate rejects the known-bad specification", workflow)
        self.assertIn("gov-demo-governed-legacy.oas.json", workflow)
        # The negative control has to demand the specific non-compliant code: accepting any
        # non-zero would let a could-not-evaluate 3 masquerade as a working gate.
        self.assertIn('if [ "${status}" -eq 2 ]; then', workflow)
        # Workflow expressions are resolved before the shell sees the script, so one inside
        # a run: block splices attacker-controlled text straight into the command line.
        in_script = False
        for line in workflow.splitlines():
            stripped = line.strip()
            if stripped.endswith("run: |"):
                in_script = True
                continue
            if in_script and stripped and not line.startswith(" " * 10):
                in_script = False
            if in_script:
                self.assertNotIn("${{", line, f"workflow expression inside run: {line!r}")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_import_reasserts_categories_because_they_live_outside_the_oas(self):
        imports = (self.root / "scripts/import-apis.sh").read_text()
        self.assertIn("module_enabled governance", imports)
        self.assertIn("governance/assign-categories.sh", imports)


class DashboardStubHandler(http.server.BaseHTTPRequestHandler):
    """A Dashboard that answers every governance call, or 403s every one of them."""

    forbid = False

    def respond(self):
        if type(self).forbid:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = urllib.parse.urlparse(self.path).path
        if path.endswith("/categories"):
            body = {"categories": ["gov-runtime-payments"]}
        elif path.startswith("/api/rulesets"):
            body = {"rulesets": [], "pages": 1}
        else:
            body = {}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - name fixed by http.server
        self.respond()

    def do_PUT(self):  # noqa: N802 - name fixed by http.server
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.respond()

    def do_POST(self):  # noqa: N802 - name fixed by http.server
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.respond()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler signature
        del format, args


class GovernanceLicenceRecoveryTests(unittest.TestCase):
    """A licence installed after an unlicensed run must actually re-enable the module.

    The documented recovery is "install a licence carrying the claim, then re-run
    configure-demo.sh". The earlier run leaves GOVERNANCE_LICENSED=false in the runtime
    env, so unless a successful probe clears it the module stays disabled forever and
    GOV-001 keeps reporting blocked against a perfectly good stack.
    """

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        if GENERATOR_LOAD_ERROR is None:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
                 "--slug", "gov-runtime", "--name", "Gov Runtime", "--modules", "rest,governance",
                 "--dashboard-version", "v5.15.0", "--gateway-version", "v5.15.0"],
                check=True, capture_output=True, text=True,
            )
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DashboardStubHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    def setUp(self):
        DashboardStubHandler.forbid = False

    def seed_runtime(self, licensed: str | None) -> Path:
        runtime = self.root / ".runtime"
        runtime.mkdir(exist_ok=True)
        runtime.chmod(0o700)
        lines = ["export DASHBOARD_API_KEY=stub-token\n", "export ORG_ID=stub-org\n"]
        if licensed is not None:
            lines.append(f"export GOVERNANCE_LICENSED={licensed}\n")
        env_file = runtime / "runtime.env"
        env_file.write_text("".join(lines))
        env_file.chmod(0o600)
        return env_file

    def run_configure(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.root / "governance/configure-governance.sh")],
            capture_output=True, text=True, check=False,
            env=dict(
                os.environ,
                DASHBOARD_URL=self.url,
                GOVERNANCE_RETRY_ATTEMPTS="2",
                GOVERNANCE_RETRY_DELAY="0",
            ),
        )

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_successful_run_clears_a_stale_unlicensed_marker(self):
        env_file = self.seed_runtime("false")
        result = self.run_configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GOVERNANCE_LICENSED=true", env_file.read_text())
        self.assertNotIn("GOVERNANCE_LICENSED=false", env_file.read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_first_successful_run_records_the_licence(self):
        env_file = self.seed_runtime(None)
        self.assertEqual(self.run_configure().returncode, 0)
        self.assertIn("GOVERNANCE_LICENSED=true", env_file.read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_persistent_403_records_unlicensed_and_exits_zero(self):
        """An absent claim is a licensing fact, not a broken demo.

        Exiting non-zero here would fail configure-demo.sh and take the rest of the bundle
        down with it, so the run succeeds and verification reports GOV-001 blocked.
        """
        env_file = self.seed_runtime(None)
        DashboardStubHandler.forbid = True
        result = self.run_configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GOVERNANCE_LICENSED=false", env_file.read_text())
        self.assertIn("governance", result.stderr.lower())


def ruleset(name: str, action: str, errors: int, warns: int) -> dict:
    return {"id": name, "name": name, "action": action, "error_count": errors, "warn_count": warns}


# Canned sandbox bodies keyed by the category the check asks for. Every one of them is an
# HTTP 200: the sandbox reports non-compliance in the body, never in the status line, which
# is exactly why a gate that reads the status code is worthless.
SANDBOX_SCENARIOS = {
    "clean": {"rulesets": [ruleset("Baseline", "warn", 0, 0)], "issues": []},
    "warn-only": {
        "rulesets": [ruleset("Baseline", "warn", 0, 2)],
        "issues": [{"severity": "warn", "rule_id": "owasp-string-limit", "message": "unbounded string", "path": "paths"}],
    },
    "errors": {
        "rulesets": [ruleset("Baseline", "warn", 3, 1)],
        "issues": [{"severity": "error", "rule_id": "owasp-no-numeric-ids", "message": "numeric id", "path": "paths"}],
    },
    # A clean gating ruleset plus an advisory one that found errors. The Dashboard calls
    # this API non-compliant; the docs' reference script calls it clean.
    "mixed-advisory-errors": {
        "rulesets": [ruleset("Baseline", "warn", 0, 0), ruleset("Gateway Standards", "none", 2, 0)],
        "issues": [{"severity": "error", "rule_id": "upstream-rate-limit-required", "message": "missing", "path": "x-tyk"}],
    },
    "only-advisory-errors": {
        "rulesets": [ruleset("Gateway Standards", "none", 2, 0)],
        "issues": [{"severity": "error", "rule_id": "authentication-required", "message": "missing", "path": "x-tyk"}],
    },
    "empty": {"rulesets": [], "issues": []},
    "truncated": {"rulesets": [ruleset("Baseline", "warn", 0, 0)], "issues": [], "truncated": True},
    "error-field": {"rulesets": [ruleset("Baseline", "warn", 0, 0)], "issues": [], "error": "evaluator exploded"},
}

SANDBOX_REQUESTS: list[dict] = []


class SandboxStubHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - name fixed by http.server
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SANDBOX_REQUESTS.append(query)
        scenario = (query.get("categories") or [""])[0]
        if scenario == "server-error":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = dict(SANDBOX_SCENARIOS.get(scenario, SANDBOX_SCENARIOS["errors"]))
        body.setdefault("truncated", False)
        body.setdefault("evaluated_apis", 1)
        body.setdefault("elapsed_ms", 4)
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler signature
        del format, args


class GovernanceCheckExitCodeTests(unittest.TestCase):
    """The CI gate's exit codes are the contract, so they are tested by running it.

    0 compliant, 1 warnings only, 2 non-compliant, 3 could not evaluate. The sandbox
    answers HTTP 200 for a non-compliant document, so every one of these bodies arrives
    with a success status and the verdict has to come from the body.
    """

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        if GENERATOR_LOAD_ERROR is None:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
                 "--slug", "gate-demo", "--name", "Gate Demo", "--modules", "rest,governance",
                 "--dashboard-version", "v5.15.0", "--gateway-version", "v5.15.0"],
                check=True, capture_output=True, text=True,
            )
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SandboxStubHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    def run_check(self, scenario: str, *extra: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ, TYK_DASHBOARD_URL=self.url, TYK_DASHBOARD_TOKEN="stub-token")
        return subprocess.run(
            [str(self.root / "governance/governance-check.sh"),
             str(self.root / "apis/gate-demo-governed-standard.oas.json"),
             "--categories", scenario, *extra],
            capture_output=True, text=True, check=False, env=environment,
        )

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_compliant_specification_exits_zero(self):
        self.assertEqual(self.run_check("clean").returncode, 0)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_warnings_only_exits_one(self):
        self.assertEqual(self.run_check("warn-only").returncode, 1)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_error_severity_exits_two(self):
        self.assertEqual(self.run_check("errors").returncode, 2)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_empty_ruleset_list_cannot_evaluate(self):
        """An unknown category returns 200 with zero rulesets and zero findings.

        A gate that reads only the counts would pass forever, which is the whole reason
        this exit code exists.
        """
        result = self.run_check("empty")
        self.assertEqual(result.returncode, 3)
        self.assertIn("No ruleset matched", result.stderr)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_truncated_response_cannot_evaluate(self):
        self.assertEqual(self.run_check("truncated").returncode, 3)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_error_field_cannot_evaluate(self):
        self.assertEqual(self.run_check("error-field").returncode, 3)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_http_failure_cannot_evaluate(self):
        self.assertEqual(self.run_check("server-error").returncode, 3)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_advisory_ruleset_errors_gate_by_default(self):
        """The Dashboard decides compliance on severity and never reads `action`.

        A ruleset at action: none still makes the API non-compliant there, so the default
        gate counts it too. --honor-action reproduces the docs script, which does not, and
        therefore passes a specification the Dashboard rejects.
        """
        self.assertEqual(self.run_check("mixed-advisory-errors").returncode, 2)
        self.assertEqual(self.run_check("mixed-advisory-errors", "--honor-action").returncode, 0)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_honor_action_with_no_gating_ruleset_cannot_evaluate(self):
        """Filtering away every ruleset checks nothing, so it must not report a pass."""
        self.assertEqual(self.run_check("only-advisory-errors").returncode, 2)
        result = self.run_check("only-advisory-errors", "--honor-action")
        self.assertEqual(result.returncode, 3)
        self.assertIn("advisory", result.stderr)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_strictness_maps_the_result_client_side_only(self):
        self.assertEqual(self.run_check("warn-only", "--strictness", "strict").returncode, 1)
        self.assertEqual(self.run_check("warn-only", "--strictness", "standard").returncode, 0)
        self.assertEqual(self.run_check("errors", "--strictness", "standard").returncode, 2)
        self.assertEqual(self.run_check("errors", "--strictness", "permissive").returncode, 0)
        # Evaluation failures survive every strictness: permissive means "I accept the
        # findings", not "I accept not knowing what they are".
        self.assertEqual(self.run_check("empty", "--strictness", "permissive").returncode, 3)
        # The sandbox contract has no strictness parameter; sending one would imply
        # server-side behaviour that does not exist.
        self.assertTrue(SANDBOX_REQUESTS)
        self.assertFalse(any("strictness" in request for request in SANDBOX_REQUESTS))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_help_exits_zero_and_usage_error_cannot_evaluate(self):
        check = str(self.root / "governance/governance-check.sh")
        helped = subprocess.run([check, "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(helped.returncode, 0)
        self.assertIn("Usage: governance-check.sh", helped.stdout)
        for arguments in ([], [str(self.root / "apis/gate-demo-governed-standard.oas.json")],
                          ["/nonexistent.json", "--categories", "clean"]):
            result = subprocess.run(
                [check, *arguments], capture_output=True, text=True, check=False,
                env=dict(os.environ, TYK_DASHBOARD_URL=self.url, TYK_DASHBOARD_TOKEN="stub-token"),
            )
            self.assertEqual(result.returncode, 3, arguments)


def generate_bundle(root: Path, slug: str, modules: str, *extra: str) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
         "--slug", slug, "--name", slug.replace("-", " ").title(), "--modules", modules, *extra],
        check=True, capture_output=True, text=True,
    )


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def ruleset_raw(yaml_text: str) -> dict:
    """The JSON under `ruleset.raw: |-` in a TykRuleset manifest, without a YAML parser."""
    lines = yaml_text.split("raw: |-\n", 1)[1].splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip())
    body = [line[indent:] for line in lines if line.strip()]
    return json.loads("\n".join(body))


def configure_governance_definitions(source: str) -> list[dict]:
    """Evaluate the ruleset definitions exactly as configure-governance.sh builds them."""
    blocks = []
    for name in ("owasp_definition", "standards_definition"):
        start = source.index(f'{name}="$(jq')
        blocks.append(source[start:source.index("}')\"", start) + 4])
    with tempfile.TemporaryDirectory() as temporary:
        harness = Path(temporary) / "definition-harness.sh"
        harness.write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\nquote="\'"\n'
            + "\n".join(blocks)
            + '\nprintf \'%s\\n%s\\n\' "${owasp_definition}" "${standards_definition}"\n'
        )
        result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines()]


@unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
class OperatorModuleTests(unittest.TestCase):
    """Operator assets, the governance hard-block companions, and their evidence contract.

    Every behavioural test copies what it needs into its own temporary directory: the two
    generated fixture bundles are shared by the whole class and are never written to.
    """

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.combined = base / "combined"
        cls.operator_only = base / "operator-only"
        generate_bundle(cls.combined, "op-gov", "rest,governance,operator",
                        "--dashboard-version", "v5.15.0", "--gateway-version", "v5.15.0",
                        "--operator-version", "v1.5.0")
        generate_bundle(cls.operator_only, "op-only", "rest,operator")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.scratch = Path(self.work.name)

    def mapping_cells(self, root: Path, module: str) -> str:
        mapping = (root / "notes/requirements-mapping.md").read_text()
        row = next(line for line in mapping.splitlines() if f"Demonstrate {module} |" in line)
        return [part.strip() for part in row.strip().strip("|").split("|")][3]

    # ---- generation -------------------------------------------------------------------

    def test_combined_bundle_places_the_hard_block_companions_in_operator(self):
        for name in GOVERNANCE_OPERATOR_FILES:
            path = self.combined / "operator" / name
            self.assertTrue(path.is_file(), name)
            self.assertNotIn("__SLUG__", path.read_text(), name)
            self.assertNotIn("__DISPLAY_NAME__", path.read_text(), name)
        verify = self.combined / "operator/verify-governance.sh"
        self.assertTrue(verify.stat().st_mode & stat.S_IXUSR)
        for name in ("governed-api-compliant.yaml", "governed-api-noncompliant.yaml"):
            manifest = (self.combined / "operator" / name).read_text()
            self.assertIn("kind: TykOasApiDefinition", manifest)
            self.assertIn("- op-gov-gitops", manifest)
            self.assertNotIn("payments", manifest)
            self.assertIn("name: op-gov-team-context", manifest)
        # apply-operator.sh renders every operator/*.yaml but applies only its own four, so
        # the non-compliant twin is never applied by accident.
        self.assertNotIn("governed-api", (self.combined / "operator/apply-operator.sh").read_text())
        self.assertEqual(validator.validate_bundle(self.combined), [])

    def test_operator_only_bundle_omits_the_hard_block_companions(self):
        for name in GOVERNANCE_OPERATOR_FILES:
            self.assertFalse((self.operator_only / "operator" / name).exists(), name)
        for name in ("operator-env.sh", "install-operator-kind.sh", "verify-reconciliation.sh", "operator-lib.sh"):
            path = self.operator_only / "operator" / name
            self.assertTrue(path.is_file(), name)
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, name)
            self.assertNotIn("__SLUG__", path.read_text(), name)
        self.assertEqual(validator.validate_bundle(self.operator_only), [])

    def test_security_policy_links_the_oas_api_kind(self):
        """access_rights_array kind defaults to the classic ApiDefinition and never links."""
        policy = (self.operator_only / "operator/security-policy.yaml").read_text()
        section = policy.split("access_rights_array:", 1)[1].split("meta_data:", 1)[0]
        self.assertIn("- kind: TykOasApiDefinition", section)
        self.assertIn("name: op-only-operator-api", section)

    def test_ruleset_definitions_use_literal_messages_and_no_off_extends(self):
        """Vacuum neither interpolates {{description}} nor re-enables rules from an "off" set.

        A template shows up verbatim in the Dashboard issues table and in the admission
        denial, and an "off" extends plus an override evaluates nothing at all.
        """
        definitions = configure_governance_definitions(
            (self.combined / "governance/configure-governance.sh").read_text())
        definitions.append(ruleset_raw((self.combined / "operator/tyk-ruleset.yaml").read_text()))
        self.assertEqual(len(definitions), 3)
        for definition in definitions:
            self.assertNotIn("{{", json.dumps(definition))
            for entry in definition.get("extends", []):
                values = entry if isinstance(entry, list) else [entry]
                self.assertNotIn("off", values, definition)
            for rule in definition.get("rules", {}).values():
                if isinstance(rule, dict):
                    self.assertTrue(rule.get("message"), rule)
        block = definitions[2]["rules"]["operation-operationid-required"]
        self.assertEqual(block["severity"], "error")
        self.assertEqual(block["message"], "Operation has no operationId.")

    def test_requirement_rows_list_gov_003_only_with_operator(self):
        self.assertEqual(self.mapping_cells(self.combined, "governance"), "`GOV-001`, `GOV-002`, `GOV-003`")
        self.assertEqual(self.mapping_cells(self.combined, "operator"), "`OPERATOR-001`")
        self.assertEqual(self.mapping_cells(self.operator_only, "operator"), "`OPERATOR-001`")
        readme = (self.combined / "README.md").read_text()
        self.assertIn("./operator/verify-governance.sh", readme)

    # ---- verify-demo.sh evidence pickup ----------------------------------------------

    FAKE_CURL = r'''#!/usr/bin/env bash
# Answers 200 for a URL listed in FAKE_LIVE_URLS and 404 otherwise, as -w '%{http_code}' would.
url=""
for arg in "$@"; do [[ "${arg}" == http* ]] && url="${arg}"; done
echo "curl ${url}" >>"${FAKE_CURL_LOG}"
for live in ${FAKE_LIVE_URLS:-}; do [[ "${url}" == "${live}" ]] && { printf 200; exit 0; }; done
printf 404
'''

    def run_evidence_harness(self, root: Path, evidence: dict[str, object],
                             live: tuple[str, ...] = ("/api/apis/oas/api-1", "/api/rulesets/rs-1")) -> list[str]:
        source = (root / "scripts/verify-demo.sh").read_text()
        start = source.index("# Operator proofs need a Kubernetes cluster")
        end = source.index("# Tyk Sync delivery runs out of band")
        bundle = self.scratch / "bundle"
        (bundle / "evidence").mkdir(parents=True, exist_ok=True)
        for name, content in evidence.items():
            text = content if isinstance(content, str) else json.dumps(content)
            (bundle / "evidence" / name).write_text(text)
        modules = json.loads((root / "demo.config.json").read_text())["modules"]
        fake_bin = self.scratch / "bin"
        write_executable(fake_bin / "curl", self.FAKE_CURL)
        dashboard = "http://dashboard.invalid"
        harness = self.scratch / "harness.sh"
        harness.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"ROOT_DIR={json.dumps(str(bundle))}\n"
            f"MODULES={json.dumps(' '.join(modules))}\n"
            f"DASHBOARD_URL={dashboard}\nDASHBOARD_API_KEY=stub-token\nORG_ID=org-1\n"
            'record() { printf "%s|%s|%s\\n" "$1" "$2" "$3"; }\n'
            'module_enabled() { [[ " ${MODULES} " == *" $1 "* ]]; }\n'
            + source[start:end]
        )
        self.curl_log = self.scratch / "curl.log"
        result = subprocess.run(
            ["bash", str(harness)], capture_output=True, text=True, check=True,
            env=dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                     FAKE_CURL_LOG=str(self.curl_log),
                     FAKE_LIVE_URLS=" ".join(dashboard + path for path in live)),
        )
        return result.stdout.splitlines()

    @staticmethod
    def reconciliation_evidence(**overrides: object) -> dict:
        return {"proof": "OPERATOR-001", "status": "passed", "detail": "reconciled", "org_id": "org-1",
                "recorded_at": "2026-09-24T00:00:00Z", "api": {"id": "api-1"}, **overrides}

    @staticmethod
    def governance_evidence(**overrides: object) -> dict:
        return {"proof": "GOV-003", "status": "passed", "detail": "blocked", "org_id": "org-1",
                "recorded_at": "2026-09-24T00:00:00Z", "ruleset": {"id": "rs-1"}, **overrides}

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_verify_demo_blocks_operator_proofs_until_evidence_exists(self):
        lines = self.run_evidence_harness(self.combined, {})
        self.assertEqual(lines, [
            "OPERATOR-001|blocked|Run operator/install-operator-kind.sh, then source operator/operator-env.sh and ./operator/apply-operator.sh",
            "GOV-003|blocked|Run operator/verify-governance.sh after apply-operator.sh",
        ])
        # Operator without governance never mentions GOV-003.
        self.assertEqual([line.split("|")[0] for line in self.run_evidence_harness(self.operator_only, {})], ["OPERATOR-001"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_verify_demo_records_same_stack_evidence_status_and_detail(self):
        lines = self.run_evidence_harness(self.combined, {
            "operator-reconciliation.json": self.reconciliation_evidence(),
            "operator-governance.json": self.governance_evidence(status="failed", reason="webhook admitted it", detail=None),
        })
        self.assertEqual(lines, ["OPERATOR-001|passed|reconciled", "GOV-003|failed|webhook admitted it"])
        # The pass was confirmed against this Dashboard; the failure needed no confirmation.
        self.assertEqual(self.curl_log.read_text().split(), ["curl", "http://dashboard.invalid/api/apis/oas/api-1"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_verify_demo_blocks_evidence_from_a_different_org(self):
        lines = self.run_evidence_harness(self.combined, {
            "operator-reconciliation.json": self.reconciliation_evidence(org_id="org-2"),
            "operator-governance.json": self.governance_evidence(org_id=None),
        })
        self.assertEqual([line.split("|")[:2] for line in lines], [["OPERATOR-001", "blocked"], ["GOV-003", "blocked"]])
        for line in lines:
            self.assertIn("Operator evidence is from a different stack; re-run", line)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_verify_demo_blocks_a_pass_whose_objects_are_gone_from_the_dashboard(self):
        """Same org id, but the database was purged: the API and ruleset no longer exist."""
        lines = self.run_evidence_harness(self.combined, {
            "operator-reconciliation.json": self.reconciliation_evidence(),
            "operator-governance.json": self.governance_evidence(),
        }, live=())
        self.assertEqual([line.split("|")[:2] for line in lines], [["OPERATOR-001", "blocked"], ["GOV-003", "blocked"]])
        self.assertIn("different stack", lines[0])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_verify_demo_never_passes_unreadable_or_mismatched_evidence(self):
        lines = self.run_evidence_harness(self.combined, {
            "operator-reconciliation.json": "not json",
            "operator-governance.json": self.reconciliation_evidence(),
        })
        self.assertEqual([line.split("|")[:2] for line in lines], [["OPERATOR-001", "blocked"], ["GOV-003", "blocked"]])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_verify_demo_records_the_started_marker_as_failed(self):
        lines = self.run_evidence_harness(self.combined, {
            "operator-reconciliation.json": {"proof": "OPERATOR-001", "status": "failed",
                                             "reason": "run started but did not complete", "org_id": "org-1",
                                             "recorded_at": "2026-09-24T00:00:00Z"},
        })
        self.assertEqual(lines[0], "OPERATOR-001|failed|run started but did not complete")

    # ---- verify-reconciliation.sh and apply-operator.sh with a fake kubectl -----------

    FAKE_RECONCILE_KUBECTL = r"""#!/usr/bin/env bash
args="$*"
echo "kubectl ${args}" >>"${FAKE_STATE}/calls.log"
case "${args}" in
  "config current-context") echo "${FAKE_CURRENT_CONTEXT:-kind-test}" ;;
  *"create --dry-run=client -o json -f "*tyk-oas-api.yaml)
    jq -n --arg data "${FAKE_OAS_DATA:-oas-v1}" --arg spec "${FAKE_API_SPEC:-api-v1}" '{kind: "List", items: [
      {kind: "ConfigMap", metadata: {name: "op-only-operator-oas"}, data: {"api.json": $data}},
      {kind: "TykOasApiDefinition", metadata: {name: "op-only-operator-api"}, spec: {marker: $spec}}]}' ;;
  *"create --dry-run=client -o json -f "*security-policy.yaml)
    jq -n --arg spec "${FAKE_POLICY_SPEC:-policy-v1}" '{kind: "SecurityPolicy", metadata: {name: "op-only-operator-policy"}, spec: {marker: $spec}}' ;;
  *"get tykoasapidefinition"*"-o json") cat "${FAKE_API_JSON}" ;;
  *"get securitypolicy"*"-o json")
    # The policy links to the API only from the FAKE_LINK_AFTER-th read onwards.
    count=$(( $(cat "${FAKE_STATE}/policy-reads" 2>/dev/null || echo 0) + 1 ))
    echo "${count}" >"${FAKE_STATE}/policy-reads"
    if (( count >= ${FAKE_LINK_AFTER:-1} )); then cat "${FAKE_POLICY_JSON}"
    else jq 'del(.status.linked_apis)' "${FAKE_POLICY_JSON}"; fi ;;
  *"-o yaml"*) echo "status: {}" ;;
  *"io/owner"*) printf platform ;;
  *"io/team"*) printf api-platform ;;
  *"get crd"*) [[ "${FAKE_CRDS_MISSING:-0}" != 1 ]] ;;
  *"-o jsonpath="*) printf '' ;;
  *"create secret"*) echo "apiVersion: v1" ;;
  *"apply -f -"*) cat >/dev/null ;;
  *"apply -f "*)
    [[ "${FAKE_APPLY_FAILS:-0}" != 1 ]] || exit 1
    # tyk-operator v1.5.0 bumps metadata.generation on every apply, even an identical one,
    # while its status hashes stay put; the verification must not treat that as a change.
    for file in "${FAKE_API_JSON}" "${FAKE_POLICY_JSON}"; do
      jq '.metadata.generation += 1' "${file}" >"${file}.next" && mv "${file}.next" "${file}"
    done ;;
  *) echo "unexpected kubectl ${args}" >&2; exit 1 ;;
esac
"""

    def prepare_reconciliation_bundle(self, api_status: dict, policy_status: dict,
                                      record_context: bool = True) -> Path:
        bundle = self.scratch / "bundle"
        (bundle / "operator").mkdir(parents=True)
        for name in ("verify-reconciliation.sh", "operator-lib.sh", "apply-operator.sh"):
            shutil.copy2(self.operator_only / "operator" / name, bundle / "operator" / name)
        for name in ("upstream.yaml", "operator-context.yaml", "tyk-oas-api.yaml", "security-policy.yaml"):
            shutil.copy2(self.operator_only / "operator" / name, bundle / "operator" / name)
        runtime = bundle / ".runtime"
        runtime.mkdir(mode=0o700)
        if record_context:
            (runtime / "operator-kube-context").write_text("kind-test\n")
        write_executable(self.scratch / "bin" / "kubectl", self.FAKE_RECONCILE_KUBECTL)
        (self.scratch / "state").mkdir()
        (self.scratch / "api.json").write_text(json.dumps({"metadata": {"generation": 1}, "status": api_status}))
        (self.scratch / "policy.json").write_text(json.dumps({"metadata": {"generation": 1}, "status": policy_status}))
        return bundle

    def run_operator_script(self, bundle: Path, script: str, **env: str):
        result = subprocess.run(
            [str(bundle / "operator" / script)], capture_output=True, text=True, check=False,
            env=dict(os.environ, PATH=f"{self.scratch / 'bin'}{os.pathsep}{os.environ['PATH']}",
                     FAKE_API_JSON=str(self.scratch / "api.json"),
                     FAKE_POLICY_JSON=str(self.scratch / "policy.json"),
                     FAKE_STATE=str(self.scratch / "state"),
                     TYK_OPERATOR_ORG="org-1",
                     **{"TYK_OPERATOR_VERIFY_TIMEOUT": "30", "TYK_OPERATOR_VERIFY_INTERVAL": "0", **env}),
        )
        evidence = json.loads((bundle / "evidence/operator-reconciliation.json").read_text())
        return result, evidence

    CONVERGED_API = {"id": "api-1", "latestCRDSpecHash": "cr-a", "latestTykSpecHash": "tyk-b",
                     "latestConfigMapHash": "cm-a", "latestTransaction": {"status": "Successful"}}
    CONVERGED_POLICY = {"pol_id": "pol-1", "latestCRDSpecHash": "cr-c", "latestTykSpecHash": "tyk-d",
                        "latestTransaction": {"status": "Successful"},
                        "linked_apis": [{"name": "op-only-operator-api", "namespace": "default"}]}

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_reconciliation_converges_on_successful_transactions_despite_differing_hashes(self):
        """The CR hash and the Dashboard read-back hash are never equal on a live cluster."""
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY)
        result, evidence = self.run_operator_script(bundle, "verify-reconciliation.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence["proof"], "OPERATOR-001")
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["api"]["id"], "api-1")
        self.assertEqual(evidence["policy"]["id"], "pol-1")
        self.assertEqual(evidence["org_id"], "org-1")
        self.assertRegex(evidence["recorded_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        # Every call went to the recorded context, never the current one.
        calls = (self.scratch / "state/calls.log").read_text().splitlines()
        self.assertTrue(calls)
        self.assertTrue(all(call.startswith("kubectl --context kind-test ") for call in calls), calls)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_reconciliation_waits_for_a_late_policy_link(self):
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY)
        result, evidence = self.run_operator_script(bundle, "verify-reconciliation.sh", FAKE_LINK_AFTER="3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence["status"], "passed")
        self.assertGreaterEqual(int((self.scratch / "state/policy-reads").read_text()), 3)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_reconciliation_timeout_writes_failed_evidence_with_the_operator_error(self):
        api = {**self.CONVERGED_API, "latestTransaction": {"status": "Failed", "error": "dashboard said no"}}
        bundle = self.prepare_reconciliation_bundle(api, {k: v for k, v in self.CONVERGED_POLICY.items() if k != "linked_apis"})
        result, evidence = self.run_operator_script(bundle, "verify-reconciliation.sh", TYK_OPERATOR_VERIFY_TIMEOUT="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(evidence["proof"], "OPERATOR-001")
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["org_id"], "org-1")
        self.assertIn("did not converge", evidence["reason"])
        self.assertIn("api transaction=Failed", evidence["reason"])
        self.assertIn("api error: dashboard said no", evidence["reason"])
        self.assertIn("policy not linked", evidence["reason"])

    OWNERS = dict(TYK_OPERATOR_AUTH="auth", TYK_OPERATOR_URL="http://dashboard.invalid",
                  TYK_USER_OWNER_ID="user-1", TYK_TEAM_OWNER_ID="group-1")

    def set_status(self, name: str, **fields: str) -> None:
        path = self.scratch / name
        document = json.loads(path.read_text())
        document["status"].update(fields)
        path.write_text(json.dumps(document))

    def verified_digests(self, bundle: Path) -> dict:
        path = bundle / ".runtime/operator/.verified-digests.json"
        return json.loads(path.read_text())["resources"] if path.exists() else {}

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_identical_reapply_passes_despite_a_bumped_generation(self):
        """On v1.5.0 an identical re-apply bumps generation and moves no status hash."""
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY)
        first, evidence = self.run_operator_script(bundle, "apply-operator.sh", **self.OWNERS)
        self.assertEqual(first.returncode, 0, first.stderr)
        recorded = self.verified_digests(bundle)
        self.assertEqual(set(recorded), {"tykoasapidefinition/op-only-operator-api",
                                         "configmap/op-only-operator-oas",
                                         "securitypolicy/op-only-operator-policy"})
        self.assertEqual(recorded["configmap/op-only-operator-oas"]["status_hashes"], {"latestConfigMapHash": "cm-a"})
        self.assertEqual(stat.S_IMODE((bundle / ".runtime/operator/.verified-digests.json").stat().st_mode), 0o600)
        # Nothing hashed moves on the second apply, only the generation; the default timeout
        # of 30s would be hit if the verification waited for a hash, so a prompt pass proves it
        # did not.
        second, evidence = self.run_operator_script(bundle, "apply-operator.sh", TYK_OPERATOR_VERIFY_TIMEOUT="5",
                                                    **self.OWNERS)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(json.loads((self.scratch / "policy.json").read_text())["metadata"]["generation"], 9)
        self.assertEqual(self.verified_digests(bundle), recorded)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_changed_content_with_a_stale_hash_times_out_and_keeps_the_old_baseline(self):
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY)
        self.assertEqual(self.run_operator_script(bundle, "apply-operator.sh", **self.OWNERS)[0].returncode, 0)
        baseline = self.verified_digests(bundle)
        # The policy spec changed, but the Operator has not reconciled it: its hash is unchanged.
        result, evidence = self.run_operator_script(bundle, "apply-operator.sh", FAKE_POLICY_SPEC="policy-v2",
                                                    TYK_OPERATOR_VERIFY_TIMEOUT="1", **self.OWNERS)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("a changed resource still shows the status hash of its previous content", evidence["reason"])
        # A failed run leaves the previous baseline, so the change is detected again next time.
        self.assertEqual(self.verified_digests(bundle), baseline)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_changed_content_with_a_moved_hash_passes_and_records_the_new_baseline(self):
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY)
        self.assertEqual(self.run_operator_script(bundle, "apply-operator.sh", **self.OWNERS)[0].returncode, 0)
        baseline = self.verified_digests(bundle)
        # A ConfigMap data change is reconciled through the API's latestConfigMapHash.
        self.set_status("api.json", latestConfigMapHash="cm-b")
        result, evidence = self.run_operator_script(bundle, "apply-operator.sh", FAKE_OAS_DATA="oas-v2",
                                                    TYK_OPERATOR_VERIFY_TIMEOUT="5", **self.OWNERS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence["status"], "passed")
        recorded = self.verified_digests(bundle)
        key = "configmap/op-only-operator-oas"
        self.assertNotEqual(recorded[key]["digest"], baseline[key]["digest"])
        self.assertEqual(recorded[key]["status_hashes"], {"latestConfigMapHash": "cm-b"})
        self.assertEqual(recorded["securitypolicy/op-only-operator-policy"],
                         baseline["securitypolicy/op-only-operator-policy"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_a_reconciled_change_after_a_failed_run_is_not_waited_for_again(self):
        """The baseline is the hash recorded at the last success, not one read before this apply."""
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY)
        self.assertEqual(self.run_operator_script(bundle, "apply-operator.sh", **self.OWNERS)[0].returncode, 0)
        # Run 2: the Operator reconciles the spec change, but the policy link is lost, so it fails.
        self.set_status("api.json", latestCRDSpecHash="cr-z")
        policy = json.loads((self.scratch / "policy.json").read_text())
        linked = policy["status"].pop("linked_apis")
        (self.scratch / "policy.json").write_text(json.dumps(policy))
        failed, _ = self.run_operator_script(bundle, "apply-operator.sh", FAKE_API_SPEC="api-v2",
                                             TYK_OPERATOR_VERIFY_TIMEOUT="1", **self.OWNERS)
        self.assertEqual(failed.returncode, 1)
        # Run 3: the same content again, nothing new to reconcile, and the link is back.
        self.set_status("policy.json", linked_apis=linked)
        passed, evidence = self.run_operator_script(bundle, "apply-operator.sh", FAKE_API_SPEC="api-v2",
                                                    TYK_OPERATOR_VERIFY_TIMEOUT="5", **self.OWNERS)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(self.verified_digests(bundle)["tykoasapidefinition/op-only-operator-api"]["status_hashes"],
                         {"latestCRDSpecHash": "cr-z"})

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_a_first_failed_run_records_no_baseline(self):
        bundle = self.prepare_reconciliation_bundle(
            self.CONVERGED_API, {k: v for k, v in self.CONVERGED_POLICY.items() if k != "linked_apis"})
        result, _ = self.run_operator_script(bundle, "apply-operator.sh", TYK_OPERATOR_VERIFY_TIMEOUT="1", **self.OWNERS)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((bundle / ".runtime/operator/.verified-digests.json").exists())
        self.assertTrue((bundle / ".runtime/operator/.pending-digests.json").exists())

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_reconciliation_refuses_an_unrecorded_non_kind_context(self):
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY, record_context=False)
        refused, evidence = self.run_operator_script(bundle, "verify-reconciliation.sh", FAKE_CURRENT_CONTEXT="shared-prod")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Refusing to use non-kind context shared-prod", refused.stderr)
        self.assertEqual(evidence["status"], "failed")
        self.assertNotIn("get tykoasapidefinition", (self.scratch / "state/calls.log").read_text())
        allowed, evidence = self.run_operator_script(
            bundle, "verify-reconciliation.sh", FAKE_CURRENT_CONTEXT="shared-prod", ALLOW_NON_KIND_CONTEXT="true")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(evidence["kube_context"], "shared-prod")

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_apply_leaves_the_started_marker_when_it_aborts(self):
        bundle = self.prepare_reconciliation_bundle(self.CONVERGED_API, self.CONVERGED_POLICY)
        (bundle / "evidence").mkdir()
        (bundle / "evidence/operator-reconciliation.json").write_text(json.dumps(
            {"proof": "OPERATOR-001", "status": "passed", "org_id": "org-1"}))
        owners = self.OWNERS
        result, evidence = self.run_operator_script(bundle, "apply-operator.sh", FAKE_APPLY_FAILS="1", **owners)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["reason"], "run started but did not complete")
        self.assertEqual(evidence["org_id"], "org-1")
        # A precondition failure replaces the marker with its own reason.
        result, evidence = self.run_operator_script(bundle, "apply-operator.sh", FAKE_CRDS_MISSING="1", **owners)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Tyk Operator CRDs are missing", evidence["reason"])

    # ---- verify-governance.sh with a fake kubectl ------------------------------------

    FAKE_GOVERNANCE_KUBECTL = r'''#!/usr/bin/env bash
args="$*"
echo "kubectl ${args}" >>"${FAKE_STATE}/calls.log"
case "${args}" in
  *"get crd tykrulesets.tyk.tyk.io"*) exit 0 ;;
  *"create --dry-run=client -o json -f "*tyk-ruleset.yaml)
    jq -n --arg rules "${FAKE_RULESET_SPEC:-rules-v1}" '{kind: "TykRuleset", metadata: {name: "op-gov-governed-standards"}, spec: {rules: $rules}}' ;;
  *" delete "*)
    [[ "${FAKE_DELETE_FAILS:-0}" == 1 ]] && exit 1
    rm -f "${FAKE_STATE}/bad"; exit 0 ;;
  *"apply -f "*noncompliant*)
    if [[ "${FAKE_ADMIT_BAD:-0}" == 1 ]]; then touch "${FAKE_STATE}/bad"; exit 0; fi
    echo 'configmap/op-gov-governed-block-bad-oas created'
    echo 'Error from server (Forbidden): error when creating "governed-api-noncompliant.yaml": admission webhook "vtykoasapidefinition.kb.io" denied the request: governance preflight blocked this API (1 error(s)):' >&2
    [[ "${FAKE_NO_VIOLATIONS:-0}" == 1 ]] || echo '  - operation-operationid-required (paths): Operation has no operationId.' >&2
    echo 'Fix the violations or relax the ruleset action; see the ruleset in the Dashboard.' >&2
    exit 1 ;;
  *"apply -f "*tyk-ruleset*)
    [[ -f "${FAKE_STATE}/good" ]] || { echo "ruleset applied before its category existed" >&2; exit 1; }
    # Like the Operator's other CRs, every apply bumps the generation, identical or not.
    echo $(( $(cat "${FAKE_STATE}/generation" 2>/dev/null || echo 1) + 1 )) >"${FAKE_STATE}/generation"; exit 0 ;;
  *"apply -f "*compliant*)
    [[ "${FAKE_REJECT_GOOD:-0}" == 1 ]] && { echo 'admission webhook denied: something else' >&2; exit 1; }
    touch "${FAKE_STATE}/good"; exit 0 ;;
  *"latestTransaction.error"*) printf '%s' "${FAKE_TRANSACTION_ERROR:-}" ;;
  *"get tykruleset"*"metadata.generation"*) cat "${FAKE_STATE}/generation" 2>/dev/null || printf 1 ;;
  *"get tykruleset"*"latestCRDSpecHash"*) printf spec-1 ;;
  *"get tykruleset"*"latestSourceHash"*) printf source-1 ;;
  *"get tykruleset"*"-o json") printf '{"metadata":{"generation":%s},"status":{"rulesetId":"rs-1","latestTransaction":{"status":"Successful"},"latestCRDSpecHash":"spec-1","latestSourceHash":"%s"}}' "$(cat "${FAKE_STATE}/generation" 2>/dev/null || echo 1)" "${FAKE_SYNCED_SOURCE:-source-1}" ;;
  *"get tykruleset"*"rulesetId"*) printf rs-1 ;;
  *"get tykoasapidefinition op-gov-governed-block-good -o json")
    [[ -f "${FAKE_STATE}/good" ]] || exit 1
    printf '{"status":{"id":"good-1","latestTransaction":{"status":"%s"}}}' "${FAKE_GOOD_TRANSACTION:-Successful}" ;;
  *"get tykoasapidefinition op-gov-governed-block-good"*) printf passed ;;
  *"get tykoasapidefinition op-gov-governed-block-bad"*) [[ -f "${FAKE_STATE}/bad" ]] ;;
  *) echo "unexpected kubectl ${args}" >&2; exit 1 ;;
esac
'''

    def run_governance_proof(self, admit_bad: bool, **extra_env: str):
        bundle = self.scratch / "bundle"
        if (bundle / "operator").is_dir():
            # A re-run over the same bundle and cluster state.
            return self.invoke_governance_proof(bundle, admit_bad, **extra_env)
        (bundle / "operator").mkdir(parents=True)
        shutil.copy2(self.combined / "operator/verify-governance.sh", bundle / "operator/verify-governance.sh")
        shutil.copy2(self.combined / "operator/operator-lib.sh", bundle / "operator/operator-lib.sh")
        rendered = bundle / ".runtime/operator"
        rendered.mkdir(parents=True)
        (bundle / ".runtime/operator-kube-context").write_text("kind-test\n")
        for name in ("tyk-ruleset.yaml", "governed-api-compliant.yaml", "governed-api-noncompliant.yaml"):
            shutil.copy2(self.combined / "operator" / name, rendered / name)
        write_executable(self.scratch / "bin" / "kubectl", self.FAKE_GOVERNANCE_KUBECTL)
        (self.scratch / "state").mkdir()
        return self.invoke_governance_proof(bundle, admit_bad, **extra_env)

    def invoke_governance_proof(self, bundle: Path, admit_bad: bool, **extra_env: str):
        fake_bin = self.scratch / "bin"
        state = self.scratch / "state"
        result = subprocess.run(
            [str(bundle / "operator/verify-governance.sh")], capture_output=True, text=True, check=False,
            env=dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                     FAKE_STATE=str(state), FAKE_ADMIT_BAD="1" if admit_bad else "0",
                     TYK_OPERATOR_ORG="org-1",
                     **{"TYK_OPERATOR_VERIFY_TIMEOUT": "5", "TYK_OPERATOR_VERIFY_INTERVAL": "0", **extra_env}),
        )
        evidence = json.loads((bundle / "evidence/operator-governance.json").read_text())
        self.governance_calls = (state / "calls.log").read_text().splitlines()
        return result, evidence

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_applies_the_compliant_twin_before_the_ruleset(self):
        # The fake refuses a ruleset applied before the compliant twin, as the Dashboard
        # refuses a ruleset whose category no API carries.
        result, evidence = self.run_governance_proof(admit_bad=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        order = [call for call in self.governance_calls if " apply -f " in call or " delete " in call]
        self.assertIn("delete tykoasapidefinition op-gov-governed-block-good", order[2])
        self.assertIn("governed-api-compliant.yaml", order[3])
        self.assertIn("tyk-ruleset.yaml", order[4])
        self.assertIn("governed-api-noncompliant.yaml", order[5])
        self.assertTrue(all(call.startswith("kubectl --context kind-test ") for call in self.governance_calls))
        self.assertEqual(evidence["org_id"], "org-1")
        self.assertIn("recorded_at", evidence)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_fails_a_denial_that_names_no_violation(self):
        result, evidence = self.run_governance_proof(admit_bad=False, FAKE_NO_VIOLATIONS="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("did not name operation-operationid-required", evidence["reason"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_leaves_the_started_marker_on_an_early_abort(self):
        result, evidence = self.run_governance_proof(admit_bad=False, FAKE_DELETE_FAILS="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(evidence, {**evidence, "proof": "GOV-003", "status": "failed",
                                    "reason": "run started but did not complete", "org_id": "org-1"})

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_keeps_kubectl_output_when_the_compliant_twin_is_rejected(self):
        result, evidence = self.run_governance_proof(admit_bad=False, FAKE_REJECT_GOOD="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("rejected at admission: admission webhook denied: something else", evidence["reason"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_timeout_carries_the_operator_error(self):
        result, evidence = self.run_governance_proof(
            admit_bad=False, FAKE_GOOD_TRANSACTION="Failed", FAKE_TRANSACTION_ERROR="upstream url invalid",
            TYK_OPERATOR_VERIFY_TIMEOUT="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out waiting for the compliant API", evidence["reason"])
        self.assertIn("(last error: upstream url invalid)", evidence["reason"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_passes_when_the_webhook_denies_the_noncompliant_api(self):
        result, evidence = self.run_governance_proof(admit_bad=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence["proof"], "GOV-003")
        self.assertEqual(evidence["status"], "passed")
        self.assertTrue(evidence["detail"])
        self.assertEqual(evidence["ruleset"]["id"], "rs-1")
        self.assertFalse(evidence["denied"]["saved"])
        self.assertIn("governance preflight blocked", evidence["denied"]["message"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_fails_when_the_noncompliant_api_is_admitted(self):
        result, evidence = self.run_governance_proof(admit_bad=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("was admitted", evidence["reason"])

    def ruleset_baseline(self) -> dict:
        path = self.scratch / "bundle/.runtime/operator/.verified-digests.json"
        return json.loads(path.read_text())["resources"]["tykruleset/op-gov-governed-standards"]

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_passes_an_identical_ruleset_reapply_without_a_hash_move(self):
        """An identical re-apply bumps the generation and moves no hash; it must not wait."""
        first, _ = self.run_governance_proof(admit_bad=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        baseline = self.ruleset_baseline()
        self.assertEqual(baseline["status_hashes"], {"latestCRDSpecHash": "spec-1", "latestSourceHash": "source-1"})
        second, evidence = self.run_governance_proof(admit_bad=False, TYK_OPERATOR_VERIFY_TIMEOUT="2")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual((self.scratch / "state/generation").read_text().strip(), "3")
        self.assertEqual(self.ruleset_baseline(), baseline)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_waits_for_a_changed_ruleset_to_resync(self):
        """Admission uses the ruleset the Dashboard last received, so an edit must resync first."""
        self.assertEqual(self.run_governance_proof(admit_bad=False)[0].returncode, 0)
        baseline = self.ruleset_baseline()
        result, evidence = self.run_governance_proof(
            admit_bad=False, FAKE_RULESET_SPEC="rules-v2", FAKE_SYNCED_SOURCE="source-1",
            TYK_OPERATOR_VERIFY_TIMEOUT="2")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("timed out waiting for TykRuleset", evidence["reason"])
        # The failed run keeps the old baseline, so the next run still waits for the resync.
        self.assertEqual(self.ruleset_baseline(), baseline)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_governance_proof_accepts_a_rule_body_resync_via_the_source_hash(self):
        """A rule-body edit moves latestSourceHash only; that alone proves the resync."""
        self.assertEqual(self.run_governance_proof(admit_bad=False)[0].returncode, 0)
        baseline = self.ruleset_baseline()
        result, evidence = self.run_governance_proof(
            admit_bad=False, FAKE_RULESET_SPEC="rules-v2", FAKE_SYNCED_SOURCE="source-2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["denied"]["violations"],
                         ["operation-operationid-required (paths): Operation has no operationId."])
        recorded = self.ruleset_baseline()
        self.assertNotEqual(recorded["digest"], baseline["digest"])
        self.assertEqual(recorded["status_hashes"]["latestSourceHash"], "source-2")

    # ---- install-operator-kind.sh preconditions with fake cluster tools --------------

    FAKE_INSTALL_TOOL = r"""#!/usr/bin/env bash
tool="$(basename "$0")"
echo "${tool} $*" >>"${FAKE_STATE}/argv.log"
args="$*"
case "${tool}:${args}" in
  "kind:get clusters") [[ -f "${FAKE_STATE}/cluster" ]] && echo op-gov-operator ;;
  "kind:create cluster"*) [[ "${FAKE_KIND_FORBIDDEN:-0}" == 1 ]] && exit 99; touch "${FAKE_STATE}/cluster" ;;
  kind:*) [[ "${FAKE_KIND_FORBIDDEN:-0}" == 1 ]] && exit 99 ;;
  "kubectl:config current-context") echo "${FAKE_CURRENT_CONTEXT:-some-context}" ;;
  "kubectl:config get-contexts -o name") [[ -f "${FAKE_STATE}/cluster" ]] && echo kind-op-gov-operator; echo some-context ;;
  *"-n cert-manager get deployment"*) [[ -f "${FAKE_STATE}/cert-manager" ]] ;;
  *"apply -f https://github.com/cert-manager"*) touch "${FAKE_STATE}/cert-manager" ;;
  *"apply -f -"*) cat >/dev/null ;;
  *"create secret generic tyk-operator-conf"*)
    # Record what the Secret would contain, so the test can prove the licence reached it
    # through the file and not through argv.
    file="${args##*--from-env-file=}"; file="${file%% *}"
    cp "${file}" "${FAKE_STATE}/secret-env" ;;
  *"get deployment -o name"*) echo deployment.apps/tyk-operator-controller-manager ;;
  *"get validatingwebhookconfigurations -o json"*) echo '{"items":[{"webhooks":[{"name":"vtykoasapidefinition.kb.io"}]}]}' ;;
  "helm:upgrade --install"*)
    failures="$(cat "${FAKE_STATE}/helm-failures" 2>/dev/null || echo 0)"
    if (( failures < ${FAKE_HELM_FAILURES:-0} )); then
      echo $(( failures + 1 )) >"${FAKE_STATE}/helm-failures"
      echo "${FAKE_HELM_ERROR:-Error: failed calling webhook \"webhook.cert-manager.io\": connection refused}" >&2
      exit 1
    fi
    [[ -f "${FAKE_STATE}/release" ]] && echo upgraded || touch "${FAKE_STATE}/release" ;;
  *) exit 0 ;;
esac
"""

    def prepare_installer_bundle(self, operator_version: str) -> Path:
        bundle = self.scratch / "bundle"
        (bundle / "operator").mkdir(parents=True)
        (bundle / "scripts").mkdir()
        shutil.copy2(self.combined / "operator/install-operator-kind.sh", bundle / "operator/install-operator-kind.sh")
        shutil.copy2(self.combined / "scripts/common.sh", bundle / "scripts/common.sh")
        shutil.copy2(self.combined / "demo.config.json", bundle / "demo.config.json")
        # The generated .env carries every port common.sh derives URLs from; later lines win.
        (bundle / ".env").write_text((self.combined / ".env").read_text()
                                     + f"OPERATOR_VERSION={operator_version}\nTYK_LICENSE_KEY=licence-value\n")
        runtime = bundle / ".runtime"
        runtime.mkdir(mode=0o700)
        (runtime / "runtime.env").write_text("export DASHBOARD_API_KEY=key-value\nexport ORG_ID=org-value\n")
        for tool in ("kind", "kubectl", "helm"):
            write_executable(self.scratch / "bin" / tool, self.FAKE_INSTALL_TOOL)
        (self.scratch / "state").mkdir(exist_ok=True)
        return bundle

    def run_installer(self, bundle: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
        result = subprocess.run(
            [str(bundle / "operator/install-operator-kind.sh")], capture_output=True, text=True, check=False,
            env=dict(os.environ, PATH=f"{self.scratch / 'bin'}{os.pathsep}{os.environ['PATH']}",
                     FAKE_STATE=str(self.scratch / "state"), **env),
        )
        log = self.scratch / "state/argv.log"
        return result, (log.read_text() if log.exists() else "")

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_installer_retries_helm_once_after_a_transient_webhook_failure(self):
        bundle = self.prepare_installer_bundle("v1.5.0")
        result, calls = self.run_installer(bundle, FAKE_HELM_FAILURES="1", TYK_OPERATOR_HELM_RETRY_DELAY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retrying once", result.stderr)
        self.assertEqual(calls.count("helm upgrade --install"), 2)
        self.assertIn("create --dry-run=server -f -", calls)  # the cert-manager webhook probe ran

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_installer_reports_helm_error_text_when_the_retry_also_fails(self):
        bundle = self.prepare_installer_bundle("v1.5.0")
        result, calls = self.run_installer(bundle, FAKE_HELM_FAILURES="2", TYK_OPERATOR_HELM_RETRY_DELAY="0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("helm install of tyk-operator failed", result.stderr)
        self.assertIn("webhook.cert-manager.io", result.stderr)
        self.assertNotIn("licence-value", result.stdout + result.stderr + calls)
        self.assertEqual(list((bundle / ".runtime/operator").iterdir()), [])  # licence file removed

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_installer_does_not_retry_a_non_transient_helm_failure(self):
        bundle = self.prepare_installer_bundle("v1.5.0")
        result, calls = self.run_installer(bundle, FAKE_HELM_FAILURES="1", FAKE_HELM_ERROR="Error: chart not found",
                                           TYK_OPERATOR_HELM_RETRY_DELAY="0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls.count("helm upgrade --install"), 1)
        self.assertIn("chart not found", result.stderr)
        self.assertNotIn("retrying", result.stderr)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_digest_record_drops_a_stale_record_when_hashes_become_incomplete(self):
        root = self.scratch / "digest-root"
        (root / ".runtime/operator").mkdir(parents=True)
        lib = self.combined / "operator/operator-lib.sh"
        verified = root / ".runtime/operator/.verified-digests.json"
        verified.write_text(json.dumps({"resources": {
            "securitypolicy/p": {"digest": "D1", "status_hashes": {"latestCRDSpecHash": "H1"}},
            "configmap/c": {"digest": "C1", "status_hashes": {"latestConfigMapHash": "M1"}}}}))
        pending = {"resources": {
            "securitypolicy/p": {"digest": "D2", "source": "policy", "fields": ["latestCRDSpecHash"]},
            "configmap/c": {"digest": "C2", "source": "api", "fields": ["latestConfigMapHash"]}}}
        state = {"policy": {"status": {"latestCRDSpecHash": ""}},
                 "api": {"status": {"latestConfigMapHash": "M2"}}}
        result = subprocess.run(
            ["bash", "-c", 'ROOT_DIR="$1"; source "$2"; digests_record "$3" "$4"', "_",
             str(root), str(lib), json.dumps(pending), json.dumps(state)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = json.loads(verified.read_text())["resources"]
        self.assertNotIn("securitypolicy/p", recorded)  # stale D1/H1 removed, not kept
        self.assertEqual(recorded["configmap/c"], {"digest": "C2", "status_hashes": {"latestConfigMapHash": "M2"}})

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_installer_refuses_an_rc_tag_without_a_chart_version_before_touching_a_cluster(self):
        bundle = self.prepare_installer_bundle("v1.5.0-rc6")
        result, calls = self.run_installer(bundle)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no published tyk-operator Helm chart for rc tags", result.stderr)
        self.assertEqual(calls, "")

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_installer_targets_the_kind_context_explicitly_and_reruns_cleanly(self):
        bundle = self.prepare_installer_bundle("v1.5.0")
        first, calls = self.run_installer(bundle, FAKE_CURRENT_CONTEXT="shared-prod")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("kind create cluster --name op-gov-operator", calls)
        self.assertIn("--set image.tag=v1.5.0", calls)
        self.assertIn("--version 1.5.0", calls)
        self.assertIn("--kube-context kind-op-gov-operator", calls)
        # Every kubectl call names the kind context; none relies on (or switches) the current one.
        kubectl_calls = [line for line in calls.splitlines() if line.startswith("kubectl ")
                         and not line.startswith("kubectl config ")]
        self.assertTrue(kubectl_calls)
        self.assertTrue(all(line.startswith("kubectl --context kind-op-gov-operator ") for line in kubectl_calls))
        self.assertNotIn("use-context", calls)
        self.assertNotIn("shared-prod", calls.replace("config current-context", ""))
        context_file = bundle / ".runtime/operator-kube-context"
        self.assertEqual(context_file.read_text().strip(), "kind-op-gov-operator")
        self.assertEqual(stat.S_IMODE(context_file.stat().st_mode), 0o600)
        # The licence and the Dashboard key reach the Secret through a private file only.
        self.assertIn("TYK_OPERATOR_LICENSEKEY=licence-value", (self.scratch / "state/secret-env").read_text())
        for secret in ("licence-value", "key-value"):
            self.assertNotIn(secret, calls)
            self.assertNotIn(secret, first.stdout + first.stderr)
        self.assertEqual(list((bundle / ".runtime/operator").iterdir()), [])
        self.assertEqual(calls.count("apply -f https://github.com/cert-manager"), 1)

        # A second run reuses the cluster, skips cert-manager, and upgrades the release in place.
        (self.scratch / "state/argv.log").unlink()
        second, calls = self.run_installer(bundle)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("kind create cluster", calls)
        self.assertNotIn("apply -f https://github.com/cert-manager", calls)
        self.assertIn("cert-manager is already installed", second.stdout)
        self.assertIn("helm upgrade --install tyk-operator tyk-helm/tyk-operator", calls)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_installer_uses_the_current_context_without_kind_when_allowed(self):
        bundle = self.prepare_installer_bundle("v1.5.0")
        result, calls = self.run_installer(bundle, ALLOW_NON_KIND_CONTEXT="true",
                                           FAKE_CURRENT_CONTEXT="disposable-lab", FAKE_KIND_FORBIDDEN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(line.startswith("kind ") for line in calls.splitlines()))
        self.assertIn("--kube-context disposable-lab", calls)
        self.assertEqual((bundle / ".runtime/operator-kube-context").read_text().strip(), "disposable-lab")

    # ---- operator-env.sh against a Dashboard stub ------------------------------------

    def prepare_env_bundle(self, with_runtime: bool = True) -> Path:
        bundle = self.scratch / "bundle"
        (bundle / "operator").mkdir(parents=True)
        (bundle / "scripts").mkdir()
        shutil.copy2(self.operator_only / "operator/operator-env.sh", bundle / "operator/operator-env.sh")
        shutil.copy2(self.operator_only / "scripts/common.sh", bundle / "scripts/common.sh")
        # The generated .env carries every port common.sh derives URLs from; later lines win.
        (bundle / ".env").write_text((self.operator_only / ".env").read_text() + "DASHBOARD_PORT=3999\n")
        if with_runtime:
            runtime = bundle / ".runtime"
            runtime.mkdir(mode=0o700)
            (runtime / "runtime.env").write_text(
                "export DASHBOARD_API_KEY=stub-token\nexport ORG_ID=org-1\nexport DASHBOARD_USER_ID=user-1\n")
        return bundle

    def source_env(self, bundle: Path, shell: str = "bash", extra: str = "") -> subprocess.CompletedProcess[str]:
        script = (
            f"cd {json.dumps(str(bundle))}\n{extra}"
            "source operator/operator-env.sh; rc=$?\n"
            'echo "rc=${rc}"\n'
            'echo "team=${TYK_TEAM_OWNER_ID:-} user=${TYK_USER_OWNER_ID:-} org=${TYK_OPERATOR_ORG:-} url=${TYK_OPERATOR_URL:-}"\n'
            'echo "auth-set=${TYK_OPERATOR_AUTH:+yes}"\n'
            'case $- in *e*) echo errexit=on ;; *) echo errexit=off ;; esac\n'
            'set -o | grep -q "pipefail *on" && echo pipefail=on || echo pipefail=off\n'
            'echo still-running\n'
        )
        return subprocess.run([shell, "-c", script], capture_output=True, text=True, check=False,
                              env=dict(os.environ, DASHBOARD_URL=UserGroupStubHandler.url))

    @classmethod
    def start_usergroup_stub(cls):
        if UserGroupStubHandler.stub_server is None:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UserGroupStubHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            UserGroupStubHandler.stub_server = server
            UserGroupStubHandler.url = f"http://127.0.0.1:{server.server_address[1]}"

    def reset_stub(self):
        self.start_usergroup_stub()
        UserGroupStubHandler.groups = [{"id": "other-1", "name": "unrelated"}]
        UserGroupStubHandler.posts = 0

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_operator_env_creates_the_owner_group_once_and_keeps_the_callers_shell_options(self):
        self.reset_stub()
        bundle = self.prepare_env_bundle()
        first = self.source_env(bundle)
        second = self.source_env(bundle)
        for result in (first, second):
            self.assertIn("rc=0", result.stdout, result.stderr)
            self.assertIn("team=grp-1 user=user-1 org=org-1 url=http://host.docker.internal:3999", result.stdout)
            self.assertIn("auth-set=yes", result.stdout)
            self.assertIn("errexit=off", result.stdout)
            self.assertIn("pipefail=off", result.stdout)
            self.assertNotIn("stub-token", result.stdout + result.stderr)
        self.assertIn("Created Dashboard user group op-only-api-platform", first.stderr)
        self.assertIn("Reusing Dashboard user group op-only-api-platform", second.stderr)
        self.assertEqual(UserGroupStubHandler.posts, 1)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_operator_env_keeps_an_existing_operator_url(self):
        self.reset_stub()
        bundle = self.prepare_env_bundle()
        result = self.source_env(bundle, extra="TYK_OPERATOR_URL=http://dashboard.example.invalid:1\n")
        self.assertIn("url=http://dashboard.example.invalid:1", result.stdout, result.stderr)

    @unittest.skipUnless(shutil.which("jq"), "jq is required")
    def test_operator_env_fails_without_bootstrap_and_does_not_exit_the_shell(self):
        self.reset_stub()
        bundle = self.prepare_env_bundle(with_runtime=False)
        result = self.source_env(bundle)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("still-running", result.stdout)
        self.assertIn("team= ", result.stdout)
        self.assertIn("bootstrap-demo-stack.sh", result.stderr)
        self.assertEqual(UserGroupStubHandler.posts, 0)

    def test_operator_env_keeps_the_key_off_argv_and_evaluates_only_tyk_exports(self):
        source = (self.operator_only / "operator/operator-env.sh").read_text()
        self.assertNotIn('"authorization: ${DASHBOARD_API_KEY}"', source)
        self.assertEqual(source.count('-H "@${header_file}"'), 2)
        self.assertIn("grep -E '^export TYK_[A-Z_]+='", source)
        # The zsh-only expansion is only ever seen by zsh, through eval.
        self.assertIn("eval '_tyk_operator_env_script=\"${(%):-%x}\"'", source)

    def test_purge_removes_operator_evidence_but_keeps_other_results(self):
        bundle = self.scratch / "bundle"
        shutil.copytree(self.operator_only, bundle)
        evidence = bundle / "evidence"
        evidence.mkdir(exist_ok=True)
        for name in ("operator-reconciliation.json", "operator-governance.json", "results.json"):
            (evidence / name).write_text("{}")
        write_executable(self.scratch / "bin" / "docker", "#!/usr/bin/env bash\nexit 0\n")
        env = dict(os.environ, PATH=f"{self.scratch / 'bin'}{os.pathsep}{os.environ['PATH']}")
        kept = subprocess.run([str(bundle / "scripts/reset-demo-stack.sh")], capture_output=True, text=True,
                              check=False, env=env)
        self.assertEqual(kept.returncode, 0, kept.stderr)
        self.assertTrue((evidence / "operator-reconciliation.json").exists())
        purged = subprocess.run([str(bundle / "scripts/reset-demo-stack.sh"), "--purge", "--yes"], capture_output=True,
                                text=True, check=False, env=env)
        self.assertEqual(purged.returncode, 0, purged.stderr)
        self.assertEqual(sorted(path.name for path in evidence.iterdir()), ["results.json"])

    @unittest.skipUnless(shutil.which("jq") and shutil.which("zsh"), "jq and zsh are required")
    def test_operator_env_can_be_sourced_from_zsh(self):
        self.reset_stub()
        bundle = self.prepare_env_bundle()
        result = self.source_env(bundle, shell="zsh")
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertIn("team=grp-1", result.stdout)


class UserGroupStubHandler(http.server.BaseHTTPRequestHandler):
    """A Dashboard that lists and creates user groups for operator-env.sh."""

    # Not `server`: BaseRequestHandler already sets that per request.
    stub_server: "http.server.ThreadingHTTPServer | None" = None
    url = ""
    groups: list[dict] = []
    posts = 0

    def send_json(self, status: int, body: object) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorised(self) -> bool:
        if self.headers.get("authorization") == "stub-token":
            return True
        self.send_json(403, {"Status": "Error"})
        return False

    def do_GET(self):  # noqa: N802 - name fixed by http.server
        if self.authorised():
            self.send_json(200, {"groups": type(self).groups, "pages": 1})

    def do_POST(self):  # noqa: N802 - name fixed by http.server
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not self.authorised():
            return
        type(self).posts += 1
        type(self).groups = [*type(self).groups, {"id": "grp-1", "name": payload.get("name")}]
        self.send_json(200, {"Status": "OK", "Message": "User group created", "Meta": "grp-1"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler signature
        del format, args


if __name__ == "__main__":
    unittest.main()


class ModuleVersionSupportTests(unittest.TestCase):
    """Refuse module/version combinations whose failure mode is silent.

    Neither of these produces an error at runtime. An MCP module on a pre-5.15
    gateway finds no /api/mcps resource; an MCP product on Portal 1.17.1-1.18.0 is
    dropped by ExcludeMCPProxyPolicies and the IsMCPProxyAPI short-circuit, so the
    plan syncs and the product simply is not there. Both cost far more to diagnose
    after the fact than a refusal at generate time, which is why these are hard
    failures rather than warnings.
    """

    class Args:
        def __init__(self, gateway_version="v5.15.0", portal_version="v1.19.0", dashboard_version="v5.15.0"):
            self.gateway_version = gateway_version
            self.portal_version = portal_version
            self.dashboard_version = dashboard_version

    def test_version_tuple_parses_and_orders(self):
        self.assertEqual(generator.version_tuple("v5.15.0"), (5, 15, 0))
        self.assertEqual(generator.version_tuple("1.19"), (1, 19, 0))
        # A release candidate sorts as its base version: Portal 1.19.0-rc1 already
        # carries the MCP publication fix, so it must not be refused.
        self.assertEqual(generator.version_tuple("v1.19.0-rc3"), (1, 19, 0))
        self.assertEqual(generator.version_tuple("nonsense"), (0, 0, 0))

    def test_mcp_refused_below_gateway_5_15(self):
        with self.assertRaises(SystemExit) as ctx:
            generator.check_module_version_support({"rest", "mcp"}, self.Args(gateway_version="v5.13.1"))
        self.assertIn("Gateway 5.15.0 or later", str(ctx.exception))

    def test_mcp_allowed_on_gateway_5_15(self):
        generator.check_module_version_support({"rest", "mcp"}, self.Args())
        generator.check_module_version_support({"rest", "mcp"}, self.Args(gateway_version="v5.16.0", dashboard_version="v5.15.1"))

    def test_mcp_refused_below_dashboard_5_15(self):
        # MCP proxies are a Dashboard resource: a new Gateway behind an old Dashboard has
        # nowhere to import them.
        with self.assertRaises(SystemExit) as ctx:
            generator.check_module_version_support({"rest", "mcp"}, self.Args(dashboard_version="v5.14.0"))
        self.assertIn("Dashboard 5.15.0 or later", str(ctx.exception))
        self.assertNotIn("Gateway 5.15.0", str(ctx.exception))

    def test_mcp_refusal_names_both_components_when_both_are_old(self):
        with self.assertRaises(SystemExit) as ctx:
            generator.check_module_version_support({"mcp"}, self.Args(gateway_version="v5.13.1", dashboard_version="v5.13.1"))
        self.assertIn("Gateway 5.15.0 or later", str(ctx.exception))
        self.assertIn("Dashboard 5.15.0 or later", str(ctx.exception))

    def test_mcp_portal_refused_on_silently_broken_portal(self):
        # Everything below 1.19.0 is refused, not only the 1.17.1-1.18.x window.
        for broken in ("v1.16.0", "v1.17.0", "v1.17.1", "v1.17.3", "v1.18.0", "v1.18.9"):
            with self.subTest(portal=broken):
                with self.assertRaises(SystemExit) as ctx:
                    generator.check_module_version_support(
                        {"mcp", "portal"}, self.Args(portal_version=broken))
                self.assertIn("Portal 1.19.0 or later", str(ctx.exception))

    def test_mcp_portal_allowed_from_1_19_including_rc(self):
        for ok in ("v1.19.0", "v1.19.0-rc3", "v1.20.0"):
            with self.subTest(portal=ok):
                generator.check_module_version_support(
                    {"mcp", "portal"}, self.Args(portal_version=ok))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_generator_refuses_old_dashboard_or_portal_for_mcp(self):
        with tempfile.TemporaryDirectory() as temporary:
            for extra, expected in (
                (("--modules", "rest,mcp", "--dashboard-version", "v5.14.0"), "Dashboard 5.15.0 or later"),
                (("--modules", "rest,mcp,portal", "--portal-version", "v1.16.0"), "Portal 1.19.0 or later"),
            ):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(Path(temporary) / "b"),
                     "--slug", "guard-demo", "--name", "Guard Demo", "--versions", "pinned", *extra],
                    capture_output=True, text=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0, extra)
                self.assertIn(expected, result.stderr)
                self.assertFalse((Path(temporary) / "b").exists())

    def test_portal_version_only_gates_when_mcp_is_selected(self):
        """Portal 1.18.0 is fine on its own - the defect is specific to MCP products."""
        generator.check_module_version_support({"rest", "portal"}, self.Args(portal_version="v1.18.0"))

    def test_shipped_defaults_satisfy_their_own_guard(self):
        """The known-good set the generator ships must not trip the guard it ships with.

        This is the regression that prompted the guard: the default Portal was
        v1.17.3, inside the range that silently drops MCP products, so
        `--modules mcp,portal` on defaults hit exactly the failure the skill
        documents. Defaults now resolve from Docker Hub, and the known-good set is
        both the offline default (`--versions pinned`) and the fallback.
        """
        known_good = generator.KNOWN_GOOD_VERSIONS
        args = self.Args(gateway_version=known_good["gateway"], portal_version=known_good["portal"])
        args.dashboard_version = known_good["dashboard"]
        generator.check_module_version_support({"mcp", "portal"}, args)
        self.assertGreaterEqual(generator.version_tuple(args.gateway_version), (5, 15, 0))
        self.assertGreaterEqual(generator.version_tuple(args.portal_version), (1, 19, 0))


@contextlib.contextmanager
def without_pyyaml():
    """Make `import yaml` fail, forcing validate_bundle onto its dependency-free fallback."""
    with mock.patch.dict(sys.modules, {"yaml": None}):
        yield


class FallbackYamlParserTests(unittest.TestCase):
    """The PyYAML-free structural check must accept valid YAML and reject broken YAML.

    The builder's documented prerequisite is plain python3, so this is the path real
    machines take. It used to treat an apostrophe inside a `#` comment as an opening
    quote, which failed every generated `mcp,mtls` and `ai-studio` bundle.
    """

    def check(self, text: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "doc.yaml"
            path.write_text(text)
            with without_pyyaml():
                validator.load_yaml(path)

    def assert_rejected(self, text: str, fragment: str) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.check(text)
        self.assertIn(fragment, str(ctx.exception))

    def test_fallback_path_is_really_taken(self):
        # A YAML error PyYAML would describe differently proves which parser answered.
        with self.assertRaises(ValueError) as ctx:
            self.check("key: [a, b\n")
        self.assertEqual(str(ctx.exception), "unterminated YAML flow delimiter: expected ]")

    def test_apostrophes_in_comments_are_ignored(self):
        self.check(
            "# The gateway's certificate isn't trusted here\n"
            "services:\n"
            "  demo:  # it's the demo's only service\n"
            "    image: alpine:3.20 # don't pin to latest\n"
            "#it's a comment at column zero\n"
        )

    def test_apostrophes_in_plain_scalars_are_literal(self):
        self.check(
            "description: it's the operator's job\n"
            "items:\n"
            "  - don't 'quote' me\n"
            "url: http://example.test/#anchor's\n"
        )

    def test_real_quoted_scalars_and_escapes_are_accepted(self):
        self.check(
            "a: 'it''s quoted'\n"
            'b: "say \\"hi\\" # not a comment"\n'
            "c: 'a # inside single quotes'\n"
            'd: ["sh", "-c", "echo it\'s fine"]\n'
            "e: {'k': \"v\", n: [1, 2]}\n"
            "f: \"multi\n  line\"\n"
        )

    def test_block_scalars_are_content(self):
        self.check(
            "command: |\n"
            "  echo \"it's [unbalanced {\n"
            "  # not a comment, and ' is fine\n"
            "\n"
            "  done\n"
            "next: >-\n"
            "  folded ' text ]\n"
            "list:\n"
            "  - |\n"
            "    x = '\n"
            "after: ok\n"
        )

    def test_unterminated_quotes_are_rejected(self):
        self.assert_rejected("key: 'never closed\nother: value\n", "unterminated YAML quote")
        self.assert_rejected('key: "never closed\n', "unterminated YAML quote")
        self.assert_rejected("- 'item\n", "unterminated YAML quote")
        self.assert_rejected("key: [\"a\", 'b]\n", "unterminated YAML quote")

    def test_unbalanced_flow_delimiters_are_rejected(self):
        self.assert_rejected("key: [a, b\n", "unterminated YAML flow delimiter")
        self.assert_rejected("key: {a: 1\n", "unterminated YAML flow delimiter")
        self.assert_rejected("key: [a, b}\n", "unbalanced YAML flow delimiter")
        self.assert_rejected("key: ]\n", "unbalanced YAML flow delimiter")

    def test_tab_indentation_is_rejected(self):
        self.assert_rejected("services:\n\tdemo: {}\n", "tabs are not allowed")
        self.check("key: 'a\tb'  # tab inside a value is fine\n")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_generated_mcp_mtls_and_ai_studio_bundles_validate_without_pyyaml(self):
        with tempfile.TemporaryDirectory() as temporary:
            for slug, modules in (("fb-mtls", "rest,api-key,mcp,mtls"), ("fb-studio", "rest,mcp,ai-studio,portal,websocket")):
                root = Path(temporary) / slug
                subprocess.run(
                    [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root),
                     "--slug", slug, "--name", "Fallback Demo", "--modules", modules],
                    check=True, capture_output=True, text=True,
                )
                with without_pyyaml():
                    self.assertEqual(validator.validate_bundle(root, check_compose=False), [], modules)


class AiStudioCompanionTests(unittest.TestCase):
    """The ai-studio module writes a self-contained companion stack with minted secrets."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        cls.companion = cls.root / "companion" / "ai-studio"
        if GENERATOR_LOAD_ERROR is None:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
                 "--slug", "studio-demo", "--name", "Studio Demo", "--modules", "rest,ai-studio"],
                check=True, capture_output=True, text=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def secret_values(self, path: Path | None = None) -> dict[str, str]:
        values = {}
        for line in (path or self.companion / "secret.env").read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key] = value
        return values

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_secret_env_is_minted_private_and_paired(self):
        secret = self.companion / "secret.env"
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
        values = self.secret_values()
        for key in ("GRPC_AUTH_TOKEN", "EDGE_AUTH_TOKEN", "MICROGATEWAY_ENCRYPTION_KEY", "ENCRYPTION_KEY",
                    "JWT_SECRET", "TYK_AI_SECRET_KEY"):
            self.assertTrue(values.get(key), key)
        # The admin login is host-only: admin.env, never secret.env (an env_file of both containers).
        self.assertNotIn("AISTUDIO_ADMIN_PASSWORD", values)
        admin = self.companion / "admin.env"
        self.assertEqual(stat.S_IMODE(admin.stat().st_mode), 0o600)
        self.assertTrue(self.secret_values(admin).get("AISTUDIO_ADMIN_PASSWORD"))
        self.assertEqual(values["GRPC_AUTH_TOKEN"], values["EDGE_AUTH_TOKEN"])
        self.assertEqual(values["MICROGATEWAY_ENCRYPTION_KEY"], values["ENCRYPTION_KEY"])
        self.assertEqual(len(values["ENCRYPTION_KEY"]), 32)
        # The licence is the one value a human adds.
        self.assertFalse(values.get("TYK_AI_LICENSE", "").strip())
        # The example stays empty, so it can be committed.
        example = self.secret_values(self.companion / "secret.env.example")
        self.assertFalse(example.get("GRPC_AUTH_TOKEN"))
        self.assertEqual(validator.validate_ai_studio_secrets(self.root), [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_admin_password_meets_ai_studio_complexity(self):
        for _ in range(20):
            password = generator.ai_studio_admin_password()
            self.assertGreaterEqual(len(password), 8)
            self.assertRegex(password, r"[A-Z]")
            self.assertRegex(password, r"[a-z]")
            self.assertRegex(password, r"[0-9]")
            self.assertRegex(password, r"[!@#$%^&*(),.?\":{}|<>]")

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_validator_rejects_broken_secret_env(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "copy"
            shutil.copytree(self.root, root)
            secret = root / "companion" / "ai-studio" / "secret.env"
            original = secret.read_text()
            values = self.secret_values(secret)

            def errors_for(text: str, mode: int = 0o600) -> list[str]:
                secret.write_text(text)
                secret.chmod(mode)
                return validator.validate_ai_studio_secrets(root)

            self.assertTrue(any("0600" in e for e in errors_for(original, 0o644)))
            self.assertTrue(any("GRPC_AUTH_TOKEN and EDGE_AUTH_TOKEN" in e for e in errors_for(
                original.replace(f"EDGE_AUTH_TOKEN={values['EDGE_AUTH_TOKEN']}", "EDGE_AUTH_TOKEN=drifted"))))
            self.assertTrue(any("MICROGATEWAY_ENCRYPTION_KEY and ENCRYPTION_KEY" in e for e in errors_for(
                original.replace(f"\nENCRYPTION_KEY={values['ENCRYPTION_KEY']}", "\nENCRYPTION_KEY=" + "x" * 32))))
            self.assertTrue(any("exactly 32 characters" in e for e in errors_for(
                original.replace(values["ENCRYPTION_KEY"], "short"))))
            self.assertTrue(any("JWT_SECRET is empty" in e for e in errors_for(
                original.replace(f"JWT_SECRET={values['JWT_SECRET']}", "JWT_SECRET="))))
            secret.unlink()
            self.assertTrue(any("missing" in e for e in validator.validate_ai_studio_secrets(root)))

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_companion_is_separate_pinned_and_loopback_only(self):
        compose = (self.companion / "compose.yaml").read_text()
        config = json.loads((self.root / "demo.config.json").read_text())
        tag = config["component_versions"]["ai_studio"]
        self.assertIn(f"image: tykio/tyk-ai-studio-ent:{tag}\n", compose)
        self.assertIn(f"image: tykio/tyk-microgateway-ent:{tag}\n", compose)
        self.assertIn("name: studio-demo-aistudio", compose)
        ports = config["ports"]
        for name in ("aistudio_admin", "aistudio_proxy", "aistudio_router"):
            self.assertIn(f'"127.0.0.1:{ports[name]}:', compose, name)
        self.assertNotRegex(compose, r'- "\d+:\d+"')
        # Nothing AI Studio is bolted onto the Gateway bundle's compose.
        self.assertNotIn("ai-studio", (self.root / "compose.yaml").read_text())

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_companion_cites_no_private_repository_or_local_layout(self):
        # Non-public repository and image names never appear in this tree, not even in a
        # test. Instead: every TykTechnologies repository the companion names must be one
        # of the public repositories below, and the sanitizer's --forbid check, extended
        # by a maintainer through TYK_FORBIDDEN_TERMS (comma separated), must find nothing.
        public = {"tyk", "tyk-pump", "tyk-charts", "tyk-identity-broker", "portal-default-theme",
                  "ai-studio", "tyk-docs", "tyk-install", "tyk-skills", "tyk-mock-mcp-server"}
        cited = re.compile(r"TykTechnologies/([A-Za-z0-9_.-]+)")
        for path in sorted(self.companion.rglob("*")):
            if path.is_file():
                text = path.read_text(errors="ignore")
                for name in cited.findall(text):
                    self.assertIn(name.removesuffix(".git").rstrip("."), public,
                                  f"{path.relative_to(self.root)} names a repository that is not public")
                self.assertNotIn("tyk/repo/", text, f"{path.relative_to(self.root)} cites a local checkout layout")
        terms = ["example-private-repo", *filter(None, os.environ.get("TYK_FORBIDDEN_TERMS", "").split(","))]
        leaks = [finding for finding in sanitizer.scan_bundle(self.companion, terms)
                 if finding.kind.startswith(("forbidden term", "absolute macOS user path"))]
        self.assertEqual(leaks, [])
        # The guard is live: a planted name from the configurable list is flagged.
        planted = self.companion / "planted-note.txt"
        planted.write_text("read example-private-repo at its release tag\n")
        try:
            flagged = sanitizer.scan_bundle(self.companion, terms)
        finally:
            planted.unlink()
        self.assertIn("forbidden term 'example-private-repo'", {finding.kind for finding in flagged})

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_configure_fails_on_rejected_admin_calls(self):
        # The admin helpers moved to lib/admin.sh, shared by configure, verify and proofs/.
        self.assertIn('source "${ROOT_DIR}/lib/admin.sh"', (self.companion / "configure-ai-studio.sh").read_text())
        script = (self.companion / "lib" / "admin.sh").read_text()
        helper = script[script.index("api() {"):script.index("jqr() {")]

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status = 200 if self.path == "/ok" else 403
                body = b'{"ok":true}' if status == 200 else b'{"error":"forbidden - referer not supplied"}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                harness = (
                    "set -euo pipefail\n"
                    f'ADMIN_URL="http://127.0.0.1:{server.server_address[1]}"\n'
                    f'JAR="{temporary}/jar"; : >"$JAR"\n'
                    f"{helper}\n"
                    'printf "ok=%s\\n" "$(api GET /ok)"\n'
                    'if api POST /forbidden "{}" >/dev/null; then echo "forbidden=accepted"; else echo "forbidden=rejected"; fi\n'
                )
                result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ok={"ok":true}', result.stdout)
        self.assertIn("forbidden=rejected", result.stdout)
        self.assertIn("POST /forbidden returned HTTP 403", result.stderr)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_configure_exits_non_zero_when_the_router_never_answers_200(self):
        script = (self.companion / "configure-ai-studio.sh").read_text()
        tail = script[script.index('echo "==> waiting for the edge to serve the router and the unified endpoint"'):]
        check = 'if [[ "${code_router}" != 200 || "${code_unified}" != 200 ]]; then'
        self.assertIn(check, tail)
        self.assertLess(tail.index(check), tail.index("Done. Credentials"))
        self.assertIn("exit 1", tail[:tail.index("Done. Credentials")])


class PolicyAndResetTests(unittest.TestCase):
    """Policy throttle defaults and reset-demo-stack.sh --purge behaviour."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        if GENERATOR_LOAD_ERROR is None:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(cls.root),
                 "--slug", "reset-demo", "--name", "Reset Demo", "--modules", "rest,api-key,portal,operator",
                 "--versions", "pinned"],
                check=True, capture_output=True, text=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_every_policy_disables_throttling_explicitly(self):
        policies = sorted((self.root / "policies").glob("*.json"))
        self.assertTrue(policies)
        for path in policies:
            policy = json.loads(path.read_text())
            self.assertEqual(policy.get("throttle_interval"), -1, path.name)
            self.assertEqual(policy.get("throttle_retry_limit"), -1, path.name)

    def run_reset(self, *args: str) -> subprocess.CompletedProcess[str]:
        bin_dir = self.root.parent / "bin"
        bin_dir.mkdir(exist_ok=True)
        docker = bin_dir / "docker"
        docker.write_text("#!/usr/bin/env bash\nexit 0\n")
        docker.chmod(0o755)
        environment = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return subprocess.run([str(self.root / "scripts" / "reset-demo-stack.sh"), *args],
                              capture_output=True, text=True, check=False, env=environment)

    def seed_state(self) -> tuple[Path, Path]:
        runtime = self.root / ".runtime" / "runtime.env"
        runtime.parent.mkdir(exist_ok=True)
        for stale in runtime.parent.glob("runtime.env.stale-*"):
            stale.unlink()
        runtime.write_text("export ORG_ID=old-org\n")
        evidence = self.root / "evidence" / "operator-reconciliation.json"
        evidence.parent.mkdir(exist_ok=True)
        evidence.write_text("{}\n")
        return runtime, evidence

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_purge_archives_runtime_env_and_removes_operator_evidence(self):
        runtime, evidence = self.seed_state()
        result = self.run_reset("--purge", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(runtime.exists())
        archived = list(runtime.parent.glob("runtime.env.stale-*"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(), "export ORG_ID=old-org\n")
        self.assertFalse(evidence.exists())
        self.assertIn("Archived runtime.env", result.stdout)

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_purge_without_confirmation_deletes_nothing(self):
        """A non-interactive --purge without --yes refuses: deleting the volumes needs the owner's agreement."""
        runtime, evidence = self.seed_state()
        result = self.run_reset("--purge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--purge --yes", result.stderr)
        self.assertTrue(runtime.exists())
        self.assertTrue(evidence.exists())
        self.assertEqual(list(runtime.parent.glob("runtime.env.stale-*")), [])

    @unittest.skipIf(GENERATOR_LOAD_ERROR is not None, f"generator does not compile: {GENERATOR_LOAD_ERROR}")
    def test_plain_reset_keeps_runtime_env_and_evidence(self):
        runtime, evidence = self.seed_state()
        result = self.run_reset()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(runtime.exists())
        self.assertTrue(evidence.exists())
        self.assertEqual(list(runtime.parent.glob("runtime.env.stale-*")), [])
