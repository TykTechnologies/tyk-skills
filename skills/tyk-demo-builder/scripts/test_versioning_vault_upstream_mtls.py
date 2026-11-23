"""Tests for the versioning, vault and upstream-mtls modules and the paginated listing fix.

Covers module selection and proof wiring, the generated compose services and gateway
settings, the child-version marker the importer and the validator read, the importer itself
and create-policies.sh against a fake Dashboard that paginates its list endpoints the way
5.15.1 does, configure-vault.sh against a fake Vault, configure-upstream-mtls.sh against a
fake certificate store, and the two bundled upstreams. Every generator run uses
`--versions pinned`, so the suite needs no network.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock


SCRIPTS = Path(__file__).resolve().parent
STARTER = SCRIPTS.parent / "assets" / "starter"
NEW_MODULES = {"versioning": "VERSION-001", "vault": "SECRETS-VAULT-001", "upstream-mtls": "UPSTREAM-MTLS-001"}
FORBIDDEN_WORDS = re.compile(r"\b(demo|test|mock|sample|lab|evidence)\b", re.IGNORECASE)
HAVE_TOOLS = all(shutil.which(tool) for tool in ("jq", "curl", "openssl"))


def load_script(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_script("init_demo_bundle", "vvu_init_demo_bundle")
validator = load_script("validate_bundle", "vvu_validate_bundle")
sanitizer = load_script("sanitize_bundle", "vvu_sanitize_bundle")


def generate(root: Path, slug: str, name: str, modules: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root), "--slug", slug,
         "--name", name, "--modules", modules, "--versions", "pinned", *extra],
        check=True, capture_output=True, text=True,
    )


def service_block(compose: str, service: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^\S|\Z)", compose)
    if match is None:
        raise AssertionError(f"service {service} not found in compose.yaml")
    return match.group(1)


def read_json(path: Path):
    return json.loads(path.read_text())


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """A ThreadingHTTPServer on a free loopback port, stopped by close()."""

    def __init__(self, handler):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------------------
# A fake Dashboard with the 5.15.1 behaviours the scripts depend on.
# ---------------------------------------------------------------------------------------

class FakeDashboard:
    """List endpoints return one page of ten unless p=-1; a duplicate policy name is a 400;
    a versioned API updated without info.dbId gains a ghost row with an empty id, and without
    info.orgId is refused; a version is linked only through the base_api_id query."""

    PAGE = 10
    ORG = "fake-org"

    def __init__(self):
        self.lock = threading.Lock()
        self.policies: dict[str, dict] = {}
        self.apis: dict[str, dict] = {}
        self.ghost_rows = 0
        self.log: list[tuple[str, str, dict]] = []
        self.counter = 0
        dashboard = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                length = int(self.headers.get("content-length", "0"))
                return json.loads(self.rfile.read(length) or b"{}")

            def handle_any(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(parsed.query))
                with dashboard.lock:
                    dashboard.log.append((self.command, parsed.path, query))
                    status, payload = dashboard.route(self.command, parsed.path, query, self.body if self.command in {"POST", "PUT"} else None)
                self.reply(status, payload)

            do_GET = do_POST = do_PUT = do_DELETE = handle_any

        self.server = Server(Handler)
        self.url = self.server.url

    def next_id(self) -> str:
        self.counter += 1
        return f"{self.counter:024x}"

    def page(self, items: list, query: dict) -> list:
        return items if query.get("p") == "-1" else items[: self.PAGE]

    def route(self, method, path, query, body):
        if path == "/api/portal/policies/" and method == "GET":
            return 200, {"Data": self.page(list(self.policies.values()), query), "Pages": 2}
        if path == "/api/portal/policies/" and method == "POST":
            policy = body()
            if any(existing["name"] == policy["name"] for existing in self.policies.values()):
                return 400, {"Status": "Error", "Message": "This policy name has already been used. Enter a unique policy name"}
            policy["_id"] = self.next_id()
            self.policies[policy["_id"]] = policy
            return 200, {"Status": "OK", "Message": policy["_id"]}
        if path.startswith("/api/portal/policies/"):
            database_id = path.rsplit("/", 1)[-1]
            if database_id not in self.policies:
                return 404, {"Message": "not found"}
            if method == "PUT":
                self.policies[database_id] = {**body(), "_id": database_id}
            elif method == "DELETE":
                del self.policies[database_id]
            return 200, {"Status": "OK"}
        if path == "/api/apis" and method == "GET":
            rows = [{"api_definition": {"id": api["dbId"], "api_id": api_id}} for api_id, api in self.apis.items()]
            rows += [{"api_definition": {"id": None, "api_id": None}} for _ in range(self.ghost_rows)]
            return 200, {"apis": self.page(rows, query), "pages": 2}
        if path == "/api/apis/oas" and method == "POST":
            return self.create_api(body(), query)
        if path.startswith("/api/apis/oas/"):
            api_id = path.rsplit("/", 1)[-1]
            if api_id not in self.apis:
                return 400, {"Message": "Could not retrieve API detail"}
            if method == "GET":
                return 200, self.readback(api_id)
            if method == "PUT":
                return self.update_api(api_id, body())
        if path.startswith("/api/apis/") and method == "DELETE":
            api_id = path.rsplit("/", 1)[-1]
            self.apis.pop(api_id, None)
            return 200, {"Status": "OK"}
        return 404, {"Message": f"unexpected {method} {path}"}

    def readback(self, api_id: str) -> dict:
        api = self.apis[api_id]
        document = json.loads(json.dumps(api["doc"]))
        info = document["x-tyk-api-gateway"]["info"]
        info.update({"dbId": api["dbId"], "orgId": self.ORG})
        if api.get("versioning"):
            info["versioning"] = api["versioning"]
        else:
            info.pop("versioning", None)
        return document

    def create_api(self, document: dict, query: dict):
        api_id = document["x-tyk-api-gateway"]["info"]["id"]
        if api_id in self.apis:
            return 409, {"Message": "API with this ID already exists"}
        base_id = query.get("base_api_id")
        if base_id:
            if base_id not in self.apis:
                return 400, {"Message": f"{base_id} is not a valid Base API version"}
            base = self.apis[base_id]
            if not query.get("base_api_version_name") and not base.get("versioning"):
                return 400, {"Message": f"you need to provide a version name for the new Base API: {base_id}"}
            versioning = base.get("versioning") or {"enabled": True, "name": query["base_api_version_name"], "default": "self",
                                                   "location": "header", "key": "x-api-version", "versions": []}
            versioning["versions"].append({"name": query["new_version_name"], "id": api_id})
            base["versioning"] = versioning
        self.apis[api_id] = {"doc": document, "dbId": self.next_id(), "child_of": base_id}
        return 200, {"Status": "OK", "ID": api_id}

    def update_api(self, api_id: str, document: dict):
        api = self.apis[api_id]
        info = document["x-tyk-api-gateway"]["info"]
        if api.get("versioning") or api.get("child_of"):
            if not info.get("dbId"):
                self.ghost_rows += 1
                return 200, {"Status": "OK"}
            if not info.get("orgId"):
                return 400, {"Message": "Failed to retrieve organisation"}
            if api.get("versioning") and not info.get("versioning"):
                api["versioning"] = None  # an update without the readback's versioning unlinks it
        info.pop("dbId", None)
        info.pop("orgId", None)
        versioning = info.pop("versioning", None)
        if versioning is not None and api.get("versioning") is not None:
            api["versioning"] = versioning
        api["doc"] = document
        return 200, {"Status": "OK"}

    def requests(self, method: str, prefix: str) -> list[tuple[str, dict]]:
        return [(path, query) for verb, path, query in self.log if verb == method and path.startswith(prefix)]


def prepare_bundle(root: Path, dashboard_url: str) -> dict[str, str]:
    """Runtime state a configuration script needs, pointed at a fake Dashboard."""
    runtime = root / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    (runtime / "runtime.env").write_text("export DASHBOARD_API_KEY=fake-key\nexport ORG_ID=fake-org\n")
    (runtime / "runtime.env").chmod(0o600)
    return dict(os.environ, DASHBOARD_URL=dashboard_url)


def run_script(root: Path, relative: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(root / relative)], capture_output=True, text=True, env=env, check=False, timeout=120)


# ---------------------------------------------------------------------------------------


class ModuleSelectionTests(unittest.TestCase):
    def test_new_modules_need_only_rest(self):
        for module in NEW_MODULES:
            with self.subTest(module=module):
                self.assertEqual(generator.expand_modules(module, "local"), sorted({module, "rest"}))

    def test_generator_and_validator_agree_on_every_new_module(self):
        for module, test_id in NEW_MODULES.items():
            with self.subTest(module=module):
                self.assertIn(module, generator.SUPPORTED)
                self.assertIn(module, validator.SUPPORTED_MODULES)
                self.assertEqual(generator.DEPENDENCIES[module], validator.MODULE_DEPENDENCIES[module])
                self.assertEqual(generator.MODULE_PROOF[module][2], test_id)
                self.assertEqual(validator.MODULE_TEST_IDS[module], test_id)

    def test_versioning_is_refused_with_tyk_sync_and_cicd(self):
        """DB-07: tyk-sync never reads the importer's version link, so SYNC-001 cannot vouch for it."""
        for modules in ("rest,versioning,tyk-sync", "versioning,cicd"):
            with self.subTest(modules=modules), tempfile.TemporaryDirectory() as temporary:
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(Path(temporary) / "b"),
                     "--slug", "northwind", "--name", "Northwind", "--modules", modules, "--versions", "pinned"],
                    capture_output=True, text=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("versioning,tyk-sync cannot be combined", result.stderr)

    def test_a_near_miss_module_name_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, "Unsupported modules: hashicorp-vault"):
            generator.expand_modules("rest,hashicorp-vault", "local")

    def test_new_ports_are_appended_and_optional(self):
        with mock.patch.object(generator, "port_free", return_value=True):
            ports = generator.choose_ports(30000)
        self.assertEqual((ports["vault"], ports["upstream_mtls"]), (30019, 30020))
        self.assertEqual(ports["gateway_external"], 30018)
        for name in ("vault", "upstream_mtls"):
            self.assertIn(name, validator.OPTIONAL_PORTS)
            self.assertNotIn(name, validator.EXPECTED_PORTS)

    def test_an_occupied_vault_port_refuses_an_explicit_base(self):
        with mock.patch.object(generator, "port_free", side_effect=lambda port: port != 30019):
            with self.assertRaisesRegex(SystemExit, "Unable to allocate"):
                generator.choose_ports(30000)

    def test_hostnames_and_vault_paths_are_valid_and_dot_free(self):
        self.assertEqual(generator.secure_upstream_host("northwind"), "northwind-secure-upstream")
        long_host = generator.secure_upstream_host("a" * 45 + "-bc")
        self.assertLessEqual(len(long_host), 63)
        self.assertRegex(long_host, r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
        # The gateway splits $secret_vault.<path>.<field> at the FIRST dot.
        self.assertNotIn(".", generator.vault_secret_path("northwind"))
        self.assertNotIn(".", generator.VAULT_SECRET_FIELD)
        self.assertEqual(generator.vault_data_path("northwind"), "secret/data/northwind/upstream")


class GeneratedBundleTests(unittest.TestCase):
    MODULES = "rest,api-key,versioning,vault,upstream-mtls,gateway-sharding"

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.root = base / "full"
        cls.plain = base / "plain"
        generate(cls.root, "northwind", "Northwind", cls.MODULES)
        generate(cls.plain, "contoso", "Contoso", "rest,api-key")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def text(self, relative: str, root: Path | None = None) -> str:
        return ((root or self.root) / relative).read_text()

    def copy_bundle(self, temporary: str) -> Path:
        copy = Path(temporary) / "bundle"
        shutil.copytree(self.root, copy)
        return copy

    # ---- wiring --------------------------------------------------------------------------

    def test_bundle_passes_validation_and_sanitising(self):
        self.assertEqual(validator.validate_bundle(self.root), [])
        self.assertEqual(sanitizer.scan_bundle(self.root), [])

    def test_proof_map_profiles_and_requirement_rows(self):
        config = read_json(self.root / "demo.config.json")
        for module, test_id in NEW_MODULES.items():
            self.assertEqual(config["proof"][module], test_id)
        self.assertIn("vault", config["compose_profiles"])
        self.assertIn("upstream-mtls", config["compose_profiles"])
        mapping = self.text("notes/requirements-mapping.md")
        for test_id in NEW_MODULES.values():
            self.assertRegex(mapping, rf"\| `{test_id}` \| `blocked` \| Pending verification \|")

    def test_verify_records_every_new_proof_passed_and_failed(self):
        verify = self.text("scripts/verify-demo.sh")
        for test_id in NEW_MODULES.values():
            self.assertIn(f"record {test_id} passed", verify)
            self.assertIn(f"record {test_id} failed", verify)

    def test_plain_bundle_ships_none_of_the_new_assets(self):
        for relative in ("scripts/configure-vault.sh", "scripts/generate-upstream-certs.sh",
                         "scripts/configure-upstream-mtls.sh", "upstreams/mtls_upstream.py",
                         "apis/upstream-mtls.oas.template.json"):
            self.assertFalse((self.plain / relative).exists(), relative)
        compose = self.text("compose.yaml", self.plain)
        self.assertNotIn("vault:", compose)
        self.assertNotIn("secure-upstream", compose)
        self.assertNotIn("SSL_CERT_DIR", compose)
        self.assertIn("VAULT_TOKEN=\n", self.text(".env", self.plain))

    # ---- vault ----------------------------------------------------------------------------

    def test_vault_runs_in_dev_mode_on_the_host_loopback(self):
        vault = service_block(self.text("compose.yaml"), "vault")
        self.assertIn("image: hashicorp/vault:1.17.6", vault)
        self.assertIn("VAULT_DEV_ROOT_TOKEN_ID: ${VAULT_TOKEN}", vault)
        self.assertIn("VAULT_DEV_LISTEN_ADDRESS: 0.0.0.0:8200", vault)
        self.assertIn("cap_add: [IPC_LOCK]", vault)
        self.assertIn('ports: ["127.0.0.1:${VAULT_PORT}:8200"]', vault)
        self.assertIn("healthcheck:", vault)

    def test_every_gateway_registers_the_vault_store(self):
        compose = self.text("compose.yaml")
        for gateway in ("tyk-gateway", "tyk-gateway-external"):
            block = service_block(compose, gateway)
            with self.subTest(gateway=gateway):
                self.assertIn("TYK_GW_KV_VAULT_ADDRESS: http://vault:8200", block)
                self.assertIn("TYK_GW_KV_VAULT_TOKEN: ${VAULT_TOKEN}", block)
                self.assertIn('TYK_GW_KV_VAULT_KVVERSION: "2"', block)
                self.assertIn("vault: {condition: service_healthy}", block)

    def test_vault_token_is_minted_into_env_only(self):
        token = re.search(r"(?m)^VAULT_TOKEN=(.*)$", self.text(".env")).group(1)
        self.assertRegex(token, r"^[0-9a-f]{48}$")
        self.assertIn("VAULT_TOKEN=\n", self.text(".env.example"))
        for path in self.root.rglob("*"):
            if path.is_file() and path.name != ".env":
                self.assertNotIn(token, path.read_text(errors="ignore"), path)
        with tempfile.TemporaryDirectory() as temporary:
            twin = Path(temporary) / "twin"
            generate(twin, "northwind", "Northwind", "vault")
            twin_token = re.search(r"(?m)^VAULT_TOKEN=(.*)$", (twin / ".env").read_text()).group(1)
        self.assertNotEqual(token, twin_token)

    def test_vault_api_injects_the_reference_and_never_the_value(self):
        api = read_json(self.root / "apis" / "northwind-vault-credentials.oas.json")
        gateway = api["x-tyk-api-gateway"]
        self.assertEqual(gateway["info"]["name"], "Northwind Vault Credentials")
        headers = gateway["middleware"]["global"]["transformRequestHeaders"]
        self.assertTrue(headers["enabled"])
        self.assertEqual(headers["add"], [{"name": "X-Upstream-Credential", "value": "$secret_vault.secret/northwind/upstream.api_key"}])
        # The upstream URL stays bare: a path on it stops response body transforms matching.
        self.assertEqual(gateway["upstream"]["url"], "http://demo-upstream:8080")

    def test_configure_and_verify_use_the_kv_v2_data_path(self):
        self.assertIn('data_path="secret/data/northwind/upstream"', self.text("scripts/configure-vault.sh"))
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn('vault_api GET "secret/data/northwind/upstream"', verify)
        self.assertIn('vault_api POST "secret/data/northwind/upstream"', verify)
        self.assertIn('"${ROOT_DIR}/scripts/configure-vault.sh"', self.text("scripts/configure-demo.sh"))

    def test_vault_proof_hashes_rotates_and_checks_the_client_side(self):
        verify = self.text("scripts/verify-demo.sh")
        section = verify[verify.index("if module_enabled vault; then"):verify.index("if module_enabled upstream-mtls; then")]
        self.assertIn("x-upstream-credential-sha256", section)
        self.assertIn("-H 'X-Upstream-Credential: supplied-by-the-client'", section)
        self.assertIn('grep -F -f <(printf', section)
        # The rotation is followed by exactly one request, not a retry loop.
        rotation = section[section.index("rotated="):section.index("rotated_ok=false")]
        self.assertIn('code_of "${bodies}/vault-rotated.json"', rotation)
        self.assertNotIn("retry_code", rotation)
        # Secrets never reach a command line: no --arg/--data with the value, no grep -- value.
        self.assertNotRegex(section, r'--(?:arg|data)[^\n]*\$\{(?:current|rotated)\}')
        self.assertNotRegex(section, r'grep -F -- "\$\{(?:current|rotated)\}"')

    def test_vault_api_helper_keeps_the_token_off_the_command_line(self):
        common = self.text("scripts/common.sh")
        self.assertIn("-H @<(printf 'X-Vault-Token: %s\\n' \"${VAULT_TOKEN:-}\")", common)
        self.assertNotIn('X-Vault-Token: ${VAULT_TOKEN', common)

    # ---- upstream-mtls ----------------------------------------------------------------------

    def test_upstream_mtls_template_maps_host_port_for_mtls_and_bare_host_for_pinning(self):
        api = read_json(self.root / "apis" / "upstream-mtls.oas.template.json")
        upstream = api["x-tyk-api-gateway"]["upstream"]
        self.assertEqual(api["x-tyk-api-gateway"]["info"]["id"], "northwind-upstream-mtls")
        self.assertEqual(upstream["url"], "https://northwind-secure-upstream:8443")
        self.assertEqual(upstream["mutualTLS"]["domainToCertificateMapping"][0]["domain"], "northwind-secure-upstream:8443")
        self.assertEqual(upstream["certificatePinning"]["domainToPublicKeysMapping"][0]["domain"], "northwind-secure-upstream")
        self.assertTrue(upstream["mutualTLS"]["enabled"] and upstream["certificatePinning"]["enabled"])
        # A template, so it never matches apis/*.oas.json and is never imported unrendered.
        self.assertNotIn("upstream-mtls.oas.template.json", [path.name for path in (self.root / "apis").glob("*.oas.json")])

    def test_every_gateway_trusts_the_upstream_ca(self):
        compose = self.text("compose.yaml")
        for gateway in ("tyk-gateway", "tyk-gateway-external"):
            block = service_block(compose, gateway)
            with self.subTest(gateway=gateway):
                self.assertIn("SSL_CERT_DIR: /etc/ssl/certs:/opt/tyk-upstream-ca", block)
                self.assertIn("./generated/certs/upstream/ca-dir:/opt/tyk-upstream-ca:ro", block)
        upstream = service_block(compose, "secure-upstream")
        self.assertIn("aliases: [northwind-secure-upstream]", upstream)
        self.assertIn("./generated/certs/upstream:/certs:ro", upstream)
        self.assertIn('ports: ["${BIND_ADDRESS:-127.0.0.1}:${UPSTREAM_MTLS_PORT}:8443"]', upstream)

    def test_certificates_are_minted_before_compose_starts(self):
        start = self.text("scripts/start-demo-stack.sh")
        self.assertLess(start.index("generate-upstream-certs.sh"), start.index("compose up -d"))
        self.assertIn("/CN=Northwind Upstream CA", self.text("scripts/generate-upstream-certs.sh"))
        self.assertIn("/CN=Northwind Gateway", self.text("scripts/generate-upstream-certs.sh"))

    def test_upstream_mtls_proof_swaps_to_a_stored_key_and_restores(self):
        verify = self.text("scripts/verify-demo.sh")
        section = verify[verify.index("if module_enabled upstream-mtls; then"):verify.index("if module_enabled portal; then\n  # portal_read")]
        self.assertIn("UPSTREAM_MTLS_DECOY_PIN_ID", section)
        self.assertIn('retry_code 500 30', section)
        self.assertIn("There was a problem proxying the request", section)
        self.assertLess(section.index("put_upstream_api || true"), section.index('put_upstream_api <"${api_file}"'))
        self.assertIn('"${client_cn}" == "Northwind Gateway"', section)
        self.assertIn('"${direct_without}" != 200 && "${direct_with}" == 200', section)

    # ---- versioning -----------------------------------------------------------------------

    def test_versioning_files_link_only_through_the_child_marker(self):
        base = read_json(self.root / "apis" / "northwind-versioned.oas.json")
        child = read_json(self.root / "apis" / "northwind-versioned-v2.oas.json")
        self.assertNotIn("x-demo-version-of", base)
        # A base that hand-writes info.versioning is not linked by the Dashboard.
        self.assertNotIn("versioning", base["x-tyk-api-gateway"]["info"])
        self.assertEqual(child["x-demo-version-of"], {"base_api_id": "northwind-versioned", "base_api_version_name": "v1",
                                                      "new_version_name": "v2", "set_default": False})
        for api, version in ((base, "v1"), (child, "v2")):
            added = api["x-tyk-api-gateway"]["middleware"]["global"]["transformRequestHeaders"]["add"]
            self.assertEqual(added, [{"name": "X-Service-Version", "value": version}])
        self.assertNotEqual(base["x-tyk-api-gateway"]["server"]["listenPath"], child["x-tyk-api-gateway"]["server"]["listenPath"])

    def test_marker_keys_are_the_dashboard_query_parameters(self):
        script = self.text("scripts/import-apis.sh")
        for key in ("base_api_id", "base_api_version_name", "new_version_name", "set_default"):
            self.assertIn(f"{key}=\\(", script)

    def test_versioning_proof_checks_routing_link_and_rows(self):
        verify = self.text("scripts/verify-demo.sh")
        self.assertIn("dashboard_get '/api/apis?p=-1'", verify)
        self.assertIn('select(.id == null or .id == "")', verify)
        self.assertIn('-H "${version_key}: v2"', verify)
        self.assertIn('"${unknown_code}" != 200', verify)

    def test_validator_checks_child_markers(self):
        cases = {
            "missing base": ({"base_api_id": "northwind-nowhere"}, "which no file in apis/ defines"),
            "itself": ({"base_api_id": "northwind-versioned-v2"}, "names the API itself as its base"),
            "missing key": ({"new_version_name": ""}, "needs non-empty base_api_id, base_api_version_name and new_version_name"),
            "set_default": ({"set_default": "no"}, "set_default must be true or false"),
        }
        for case, (change, message) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                copy = self.copy_bundle(temporary)
                path = copy / "apis" / "northwind-versioned-v2.oas.json"
                document = read_json(path)
                document["x-demo-version-of"].update(change)
                path.write_text(json.dumps(document))
                errors = validator.validate_bundle(copy, check_compose=False)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(message, errors[0])
        with tempfile.TemporaryDirectory() as temporary:
            copy = self.copy_bundle(temporary)
            base_path = copy / "apis" / "northwind-versioned.oas.json"
            base = read_json(base_path)
            base["x-demo-version-of"] = {"base_api_id": "northwind-core", "base_api_version_name": "v1", "new_version_name": "v0"}
            base_path.write_text(json.dumps(base))
            errors = validator.validate_bundle(copy, check_compose=False)
        self.assertTrue(any("which is itself a child version" in error for error in errors), errors)

    def test_validator_requires_the_vault_and_upstream_ca_settings(self):
        compose = self.text("compose.yaml")
        primary = service_block(compose, "tyk-gateway")
        cases = {
            "vault service": (compose.replace("  vault:\n" + service_block(compose, "vault"), ""), "compose.yaml: module vault needs a vault service"),
            "vault address": (compose.replace(primary, primary.replace("      TYK_GW_KV_VAULT_ADDRESS: http://vault:8200\n", "")),
                              "compose.yaml: tyk-gateway must set TYK_GW_KV_VAULT_ADDRESS (vault)"),
            "ca dir": (compose.replace(primary, primary.replace("      SSL_CERT_DIR: /etc/ssl/certs:/opt/tyk-upstream-ca\n", "")),
                       "compose.yaml: tyk-gateway must set SSL_CERT_DIR (upstream-mtls)"),
        }
        for case, (text, expected) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                self.assertNotEqual(text, compose)
                copy = self.copy_bundle(temporary)
                (copy / "compose.yaml").write_text(text)
                self.assertEqual(validator.validate_bundle(copy, check_compose=False), [expected])

    def test_validator_reports_missing_module_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = self.copy_bundle(temporary)
            (copy / "apis" / "northwind-versioned-v2.oas.json").unlink()
            (copy / "scripts" / "configure-vault.sh").unlink()
            (copy / "upstreams" / "mtls_upstream.py").unlink()
            errors = validator.validate_bundle(copy, check_compose=False)
        for expected in ("apis/northwind-versioned-v2.oas.json: required asset for module versioning",
                         "scripts/configure-vault.sh: required asset for module vault",
                         "upstreams/mtls_upstream.py: required asset for module upstream-mtls"):
            self.assertTrue(any(expected in error for error in errors), (expected, errors))

    # ---- naming and documentation ---------------------------------------------------------

    def test_visible_names_follow_the_display_name_rule(self):
        for name in ("northwind-versioned", "northwind-versioned-v2", "northwind-vault-credentials"):
            api = read_json(self.root / "apis" / f"{name}.oas.json")
            gateway_name = api["x-tyk-api-gateway"]["info"]["name"]
            self.assertTrue(gateway_name.startswith("Northwind "), gateway_name)
            self.assertIsNone(FORBIDDEN_WORDS.search(gateway_name))
            self.assertIsNone(FORBIDDEN_WORDS.search(api["info"]["description"]))
        template = read_json(self.root / "apis" / "upstream-mtls.oas.template.json")
        self.assertEqual(template["x-tyk-api-gateway"]["info"]["name"], "Northwind Secure Upstream")
        self.assertIsNone(FORBIDDEN_WORDS.search(template["info"]["description"]))
        self.assertIsNone(FORBIDDEN_WORDS.search(generator.secure_upstream_host("northwind")))

    def test_readme_documents_each_module(self):
        readme = self.text("README.md")
        for heading in ("## API versioning", "## Upstream credentials from Vault", "## Upstream mutual TLS and certificate pinning"):
            self.assertIn(heading, readme)
        self.assertIn("$secret_vault.secret/northwind/upstream.api_key", readme)
        self.assertNotIn("## API versioning", self.text("README.md", self.plain))

    def test_skill_and_references_document_the_modules(self):
        """The skill lists every module and its reference keeps what the module itself needs.

        General product behaviour (error flags, JWT claim rules, OpenTelemetry settings and the
        like) belongs to the sibling skills the references hand off to, so it is not asserted
        here.
        """
        skill = (SCRIPTS.parent / "SKILL.md").read_text()
        modules_block = skill[skill.index("## Modules"):skill.index("## Versions")]
        for module in NEW_MODULES:
            self.assertRegex(modules_block, rf"(?<![\w-]){re.escape(module)}(?![\w-])")
        references = "\n".join(path.read_text() for path in (SCRIPTS.parent / "references").glob("*.md"))
        for marker in ("base_api_version_name", "TYK_GW_KV_VAULT_KVVERSION", "upstream.certificatePinning",
                       "VERSION-001", "SECRETS-VAULT-001", "UPSTREAM-MTLS-001", "?p=-1"):
            self.assertIn(marker, references)


@unittest.skipUnless(HAVE_TOOLS, "jq, curl and openssl are required")
class PaginatedListingTests(unittest.TestCase):
    """create-policies.sh and import-apis.sh read every page of the Dashboard's lists."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="pagination-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.root = self.scratch / "bundle"
        generate(self.root, "paged", "Paged", "rest,api-key,versioning")
        self.dashboard = FakeDashboard()
        self.addCleanup(self.dashboard.server.close)
        self.env = prepare_bundle(self.root, self.dashboard.url)

    def add_policies(self, count: int) -> None:
        for index in range(count):
            policy = {"id": f"{index:024x}", "name": f"Paged Tier {index:02d}", "active": True, "org_id": "",
                      "access_rights": {}, "meta_data": {"demo_owner": "paged", "stable_name": f"paged-tier-{index:02d}"}}
            (self.root / "policies" / f"{policy['id']}.json").write_text(json.dumps(policy))

    def test_more_policies_than_one_page_are_updated_not_created_twice(self):
        self.add_policies(11)
        for run in (1, 2):
            result = run_script(self.root, "scripts/create-policies.sh", self.env)
            self.assertEqual(result.returncode, 0, f"run {run}: {result.stderr}")
        self.assertEqual(len(self.dashboard.policies), 12)
        # The second run found all twelve, including the two beyond the first page.
        self.assertEqual(len(self.dashboard.requests("POST", "/api/portal/policies/")), 12)
        self.assertEqual(len(self.dashboard.requests("PUT", "/api/portal/policies/")), 12)

    def test_list_calls_read_all_pages_and_the_create_url_has_no_query(self):
        self.add_policies(11)
        run_script(self.root, "scripts/create-policies.sh", self.env)
        self.assertTrue(self.dashboard.requests("GET", "/api/portal/policies/"))
        for _path, query in self.dashboard.requests("GET", "/api/portal/policies/"):
            self.assertEqual(query.get("p"), "-1")
        for _path, query in self.dashboard.requests("POST", "/api/portal/policies/"):
            self.assertEqual(query, {})
        for relative in ("scripts/create-policies.sh", "scripts/configure-demo.sh"):
            text = (self.root / relative).read_text()
            self.assertNotRegex(text, r'curl -fsS "\$\{DASHBOARD_URL\}/api/portal/policies/" -H "authorization: \$\{DASHBOARD_API_KEY\}"\)')

    def test_the_first_page_alone_reproduces_the_duplicate_name_refusal(self):
        # Negative control: the fake refuses a duplicate name exactly like 5.15.1, so the
        # pre-fix lookup (first page only) fails on the second run.
        self.add_policies(11)
        script = self.root / "scripts" / "create-policies.sh"
        script.write_text(script.read_text().replace("/api/portal/policies/?p=-1", "/api/portal/policies/"))
        self.assertEqual(run_script(self.root, "scripts/create-policies.sh", self.env).returncode, 0)
        second = run_script(self.root, "scripts/create-policies.sh", self.env)
        self.assertNotEqual(second.returncode, 0)

    def test_api_pruning_reads_every_page(self):
        # Eleven owned strays: with one page of ten, pruning would leave one behind.
        for index in range(11):
            api_id = f"paged-retired-{index:02d}"
            self.dashboard.apis[api_id] = {"doc": {"x-demo-owner": "paged", "x-tyk-api-gateway": {"info": {"id": api_id}}}, "dbId": self.dashboard.next_id()}
        result = run_script(self.root, "scripts/import-apis.sh", self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse([api_id for api_id in self.dashboard.apis if api_id.startswith("paged-retired-")])
        for _path, query in self.dashboard.requests("GET", "/api/apis"):
            if _path == "/api/apis":
                self.assertEqual(query.get("p"), "-1")


@unittest.skipUnless(HAVE_TOOLS, "jq, curl and openssl are required")
class VersionedImportTests(unittest.TestCase):
    """import-apis.sh against a Dashboard that links versions only through base_api_id."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="versioned-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.root = self.scratch / "bundle"
        generate(self.root, "verso", "Verso", "rest,versioning")
        self.dashboard = FakeDashboard()
        self.addCleanup(self.dashboard.server.close)
        self.env = prepare_bundle(self.root, self.dashboard.url)

    def import_apis(self) -> None:
        result = run_script(self.root, "scripts/import-apis.sh", self.env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_base_first_then_the_child_through_the_versioning_call(self):
        self.import_apis()
        posts = self.dashboard.requests("POST", "/api/apis/oas")
        created = [query.get("base_api_id") for _path, query in posts]
        self.assertEqual(created[-1], "verso-versioned")
        child_query = posts[-1][1]
        self.assertEqual(child_query, {"base_api_id": "verso-versioned", "base_api_version_name": "v1",
                                       "new_version_name": "v2", "set_default": "false"})
        self.assertTrue(all(query == {} for _path, query in posts[:-1]))
        versioning = self.dashboard.apis["verso-versioned"]["versioning"]
        self.assertEqual(versioning["versions"], [{"name": "v2", "id": "verso-versioned-v2"}])

    def test_reimport_updates_in_place_with_no_ghost_row_and_keeps_the_link(self):
        for _ in range(3):
            self.import_apis()
        self.assertEqual(self.dashboard.ghost_rows, 0)
        self.assertEqual(sorted(self.dashboard.apis), ["verso-core", "verso-versioned", "verso-versioned-v2"])
        self.assertEqual(self.dashboard.apis["verso-versioned"]["versioning"]["versions"], [{"name": "v2", "id": "verso-versioned-v2"}])
        puts = [path for path, _query in self.dashboard.requests("PUT", "/api/apis/oas/")]
        self.assertIn("/api/apis/oas/verso-versioned", puts)
        self.assertIn("/api/apis/oas/verso-versioned-v2", puts)
        # The child is never posted again once it exists.
        self.assertEqual(len([query for _path, query in self.dashboard.requests("POST", "/api/apis/oas") if query]), 1)

    def test_without_the_readback_merge_the_fake_records_a_ghost(self):
        # Negative control: the same import with the merge removed reproduces the 5.15.1 ghost.
        script = self.root / "scripts" / "import-apis.sh"
        text = script.read_text()
        start = text.index('  if jq -e \'."x-tyk-api-gateway".info.versioning != null')
        end = text.index('    body="${merged_file}"\n  fi\n') + len('    body="${merged_file}"\n  fi\n')
        script.write_text(text[:start] + text[end:])
        self.import_apis()
        self.import_apis()
        self.assertGreater(self.dashboard.ghost_rows, 0)

    def test_pruning_keeps_child_versions_and_unowned_apis(self):
        self.import_apis()
        (self.root / "apis" / "verso-versioned-v2.oas.json").unlink()
        self.dashboard.apis["someone-elses"] = {"doc": {"x-demo-owner": "other", "x-tyk-api-gateway": {"info": {"id": "someone-elses"}}}, "dbId": self.dashboard.next_id()}
        self.dashboard.apis["verso-retired"] = {"doc": {"x-demo-owner": "verso", "x-tyk-api-gateway": {"info": {"id": "verso-retired"}}}, "dbId": self.dashboard.next_id()}
        self.import_apis()
        self.assertIn("verso-versioned-v2", self.dashboard.apis)
        self.assertIn("someone-elses", self.dashboard.apis)
        self.assertNotIn("verso-retired", self.dashboard.apis)

    def test_a_child_whose_base_is_missing_fails_loudly(self):
        child = self.root / "apis" / "verso-versioned-v2.oas.json"
        document = read_json(child)
        document["x-demo-version-of"]["base_api_id"] = "verso-nowhere"
        child.write_text(json.dumps(document))
        result = run_script(self.root, "scripts/import-apis.sh", self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Import failed for verso-versioned-v2: HTTP 400", result.stderr)


class FakeVault:
    """Just enough of the Vault HTTP API: health, and KV v2 read and write under secret/data/."""

    def __init__(self, token: str):
        self.data: dict[str, dict] = {}
        self.writes = 0
        vault = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def handle_any(self):
                path = urllib.parse.urlsplit(self.path).path
                # Like Vault itself, health answers without a token.
                if path == "/v1/sys/health":
                    return self.reply(200, {"initialized": True, "sealed": False})
                if self.headers.get("X-Vault-Token") != token:
                    return self.reply(403, {"errors": ["permission denied"]})
                key = path.removeprefix("/v1/")
                if self.command == "GET":
                    if key not in vault.data:
                        return self.reply(404, {"errors": []})
                    return self.reply(200, {"data": {"data": vault.data[key]}})
                length = int(self.headers.get("content-length", "0"))
                vault.data[key] = json.loads(self.rfile.read(length))["data"]
                vault.writes += 1
                return self.reply(200, {"data": {"version": vault.writes}})

            do_GET = do_POST = handle_any

        self.server = Server(Handler)
        self.url = self.server.url


@unittest.skipUnless(HAVE_TOOLS, "jq, curl and openssl are required")
class ConfigureVaultTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="vault-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.root = self.scratch / "bundle"
        generate(self.root, "keeper", "Keeper", "vault")
        token = re.search(r"(?m)^VAULT_TOKEN=(.*)$", (self.root / ".env").read_text()).group(1)
        self.vault = FakeVault(token)
        self.addCleanup(self.vault.server.close)
        self.env = dict(os.environ, VAULT_URL=self.vault.url)

    def test_seeds_once_and_never_overwrites(self):
        first = run_script(self.root, "scripts/configure-vault.sh", self.env)
        self.assertEqual(first.returncode, 0, first.stderr)
        value = self.vault.data["secret/data/keeper/upstream"]["api_key"]
        self.assertRegex(value, r"^[0-9a-f]{48}$")
        self.vault.data["secret/data/keeper/upstream"]["api_key"] = "rotated-by-an-operator"
        second = run_script(self.root, "scripts/configure-vault.sh", self.env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("left unchanged", second.stdout)
        self.assertEqual(self.vault.data["secret/data/keeper/upstream"]["api_key"], "rotated-by-an-operator")
        self.assertEqual(self.vault.writes, 1)
        for output in (first.stdout + first.stderr, second.stdout + second.stderr):
            self.assertNotIn(value, output)

    def test_a_wrong_token_fails_instead_of_seeding(self):
        env = dict(self.env)
        (self.root / ".env").write_text(re.sub(r"(?m)^VAULT_TOKEN=.*$", "VAULT_TOKEN=not-the-token", (self.root / ".env").read_text()))
        result = run_script(self.root, "scripts/configure-vault.sh", env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Vault refused the upstream credential write", result.stderr)
        self.assertEqual(self.vault.writes, 0)


@unittest.skipUnless(HAVE_TOOLS, "jq, curl and openssl are required")
class ConfigureUpstreamMtlsTests(unittest.TestCase):
    """configure-upstream-mtls.sh reads the ID of an already stored certificate from the error."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="upstream-mtls-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.root = self.scratch / "bundle"
        generate(self.root, "pinned", "Pinned", "upstream-mtls")
        stored: dict[str, str] = {}
        self.uploads = 0
        test = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                content = self.rfile.read(length)
                test.uploads += 1
                # The file part alone: curl picks a new multipart boundary per request.
                part = content.split(b"\r\n\r\n", 1)[-1].rsplit(b"\r\n--", 1)[0]
                digest = hashlib.sha256(part).hexdigest()[:16]
                if digest in stored:
                    status, payload = 403, {"Status": "Error", "Message": f"Could not create certificate: Certificate with fake-org{digest} id already exists"}
                else:
                    stored[digest] = "fake-org" + digest
                    status, payload = 200, {"id": stored[digest], "message": "Certificate created"}
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = Server(Handler)
        self.addCleanup(self.server.close)
        self.env = prepare_bundle(self.root, self.server.url)
        generated = subprocess.run(["bash", str(self.root / "scripts" / "generate-upstream-certs.sh")], capture_output=True, text=True, check=False)
        self.assertEqual(generated.returncode, 0, generated.stderr)

    def runtime(self) -> dict[str, str]:
        values = {}
        for line in (self.root / ".runtime" / "runtime.env").read_text().splitlines():
            key, _, value = line.removeprefix("export ").partition("=")
            values[key] = value
        return values

    def test_uploads_render_the_api_and_reruns_reuse_the_ids(self):
        first = run_script(self.root, "scripts/configure-upstream-mtls.sh", self.env)
        self.assertEqual(first.returncode, 0, first.stderr)
        ids = {key: value for key, value in self.runtime().items() if key.startswith("UPSTREAM_MTLS_")}
        self.assertEqual(set(ids), {"UPSTREAM_MTLS_CLIENT_CERT_ID", "UPSTREAM_MTLS_PIN_ID", "UPSTREAM_MTLS_DECOY_PIN_ID"})
        self.assertEqual(len(set(ids.values())), 3)
        rendered = read_json(self.root / "apis" / "pinned-upstream-mtls.oas.json")
        upstream = rendered["x-tyk-api-gateway"]["upstream"]
        self.assertEqual(upstream["mutualTLS"]["domainToCertificateMapping"][0]["certificate"], ids["UPSTREAM_MTLS_CLIENT_CERT_ID"])
        self.assertEqual(upstream["certificatePinning"]["domainToPublicKeysMapping"][0]["publicKeys"], [ids["UPSTREAM_MTLS_PIN_ID"]])
        second = run_script(self.root, "scripts/configure-upstream-mtls.sh", self.env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual({key: value for key, value in self.runtime().items() if key.startswith("UPSTREAM_MTLS_")}, ids)
        self.assertEqual(self.uploads, 6)

    def test_generated_material_is_private_except_the_ca(self):
        cert_dir = self.root / "generated" / "certs" / "upstream"
        for path in list(cert_dir.glob("*.key")) + list(cert_dir.glob("*.pem")):
            self.assertEqual(path.stat().st_mode & 0o077, 0, path.name)
        hashed = list((cert_dir / "ca-dir").iterdir())
        self.assertEqual(len(hashed), 1)
        self.assertRegex(hashed[0].name, r"^[0-9a-f]{8}\.0$")
        self.assertEqual(hashed[0].read_text(), (cert_dir / "ca.crt").read_text())
        self.assertEqual(validator.validate_sensitive_permissions(self.root), [])
        # The private key never sits in a public-key pin file.
        self.assertNotIn("PRIVATE KEY", (cert_dir / "server-public-key.pem").read_text())
        self.assertNotIn("PRIVATE KEY", (cert_dir / "decoy-public-key.pem").read_text())
        self.assertIn("PRIVATE KEY", (cert_dir / "gateway-client.pem").read_text())


@unittest.skipUnless(HAVE_TOOLS, "jq, curl and openssl are required")
class BundledUpstreamTests(unittest.TestCase):
    """The client-certificate upstream and the echo upstream, run from temporary copies."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.root = base / "bundle"
        generate(cls.root, "echoes", "Echoes", "upstream-mtls")
        subprocess.run(["bash", str(cls.root / "scripts" / "generate-upstream-certs.sh")], check=True, capture_output=True)
        cls.certs = cls.root / "generated" / "certs" / "upstream"
        cls.processes = []

    @classmethod
    def tearDownClass(cls):
        for process in cls.processes:
            process.terminate()
            process.wait(timeout=10)
        cls.temporary.cleanup()

    def start(self, source: Path, replacements: dict[str, str], port: int) -> None:
        text = source.read_text()
        for old, new in replacements.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        copy = Path(self.temporary.name) / f"{source.stem}-{port}.py"
        copy.write_text(text)
        process = subprocess.Popen([sys.executable, str(copy)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        for _ in range(50):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return
            time.sleep(0.1)
        self.fail(f"{source.name} did not start")

    def test_mtls_upstream_requires_a_client_certificate_and_reports_its_name(self):
        port = free_port()
        self.start(STARTER / "upstreams" / "mtls_upstream.py",
                   {'Path("/certs")': f"Path({str(self.certs)!r})", '("0.0.0.0", 8443)': f'("127.0.0.1", {port})'}, port)
        url = f"https://localhost:{port}/v1/echo"
        anonymous = ssl.create_default_context(cafile=str(self.certs / "ca.crt"))
        with self.assertRaises((ssl.SSLError, ConnectionError, urllib.error.URLError)):
            urllib.request.urlopen(url, context=anonymous, timeout=5).read()
        identified = ssl.create_default_context(cafile=str(self.certs / "ca.crt"))
        identified.load_cert_chain(self.certs / "gateway-client.crt", self.certs / "gateway-client.key")
        body = json.loads(urllib.request.urlopen(url, context=identified, timeout=5).read())
        self.assertEqual(body["client_cn"], "Echoes Gateway")
        self.assertEqual(body["path"], "/v1/echo")
        # A client certificate from another CA is refused as well.
        other_dir = Path(self.temporary.name) / "other"
        other_dir.mkdir(exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Elsewhere",
                        "-keyout", str(other_dir / "o.key"), "-out", str(other_dir / "o.crt")], check=True, capture_output=True)
        stranger = ssl.create_default_context(cafile=str(self.certs / "ca.crt"))
        stranger.load_cert_chain(other_dir / "o.crt", other_dir / "o.key")
        with self.assertRaises((ssl.SSLError, ConnectionError, urllib.error.URLError)):
            urllib.request.urlopen(url, context=stranger, timeout=5).read()

    def test_echo_reports_the_version_and_only_a_hash_of_the_credential(self):
        port = free_port()
        log_dir = Path(self.temporary.name) / "logs"
        self.start(STARTER / "upstreams" / "http_api.py",
                   {'Path("/var/log/demo/upstream.jsonl")': f"Path({str(log_dir / 'upstream.jsonl')!r})",
                    '("0.0.0.0", 8080)': f'("127.0.0.1", {port})'}, port)
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/echo", headers={
            "X-Upstream-Credential": "value-from-vault", "X-Service-Version": "v2"})
        raw = urllib.request.urlopen(request, timeout=5).read().decode()
        body = json.loads(raw)
        self.assertEqual(body["headers"]["x-service-version"], "v2")
        self.assertEqual(body["headers"]["x-upstream-credential-sha256"], hashlib.sha256(b"value-from-vault").hexdigest())
        self.assertNotIn("value-from-vault", raw)
        plain = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/echo", timeout=5).read())
        self.assertEqual(plain["headers"]["x-upstream-credential-sha256"], "")


if __name__ == "__main__":
    unittest.main()
