"""Tests for the ai-studio module on Tyk AI Studio 2.3.

Covers the minted METRICS_AUTH_TOKEN and the validator's requirement for it, the
>= v2.3.0 version gate, the companion file set with every generation token
resolved, the 2.3 confs (named internal hosts, telemetry off), the stub backend's
chunked-body and SSE handling (AI Studio calls the upstream with stream: true),
and tag resolution against the Docker Hub fixture. Everything runs offline.
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# The main suite loads the generator and validator and owns the offline Docker Hub
# fixture; reuse them rather than loading a second copy of each script.
import test_init_demo_bundle as base_suite  # noqa: E402

generator = base_suite.generator
validator = base_suite.validator
GENERATOR_OK = base_suite.GENERATOR_LOAD_ERROR is None
SKIP_REASON = f"generator does not compile: {base_suite.GENERATOR_LOAD_ERROR}"

SLUG = "promo-check"
DISPLAY_NAME = "Promo Check"
COMPANION_FILES = {
    "README.md", "compose.yaml", "configure-ai-studio.sh", "verify-ai-studio.sh", "start.sh",
    "secret.env.example", "secret.env", "admin.env.example", "admin.env",
    "confs/mgw-analytics.yaml", "confs/mgw-ent.env", "confs/ai-studio-ent.env",
    "keys/plugins/plugin-live.pub", "lib/admin.sh", "mock-llm/backend.py",
    "proofs/tyk-mcp-import.sh", "proofs/capture-screenshots.sh",
}
PROOF_IDS = (
    "AISTUDIO-ENT-001", "AISTUDIO-ROUTER-001", "AISTUDIO-UNIFIED-001", "AISTUDIO-FAILOVER-001",
    "AISTUDIO-BUDGET-001", "AISTUDIO-GUARDRAIL-001", "AISTUDIO-AUDIT-001", "AISTUDIO-METRICS-001",
    "AISTUDIO-TYKMCP-001",
)
# The order verify-ai-studio.sh runs (and its EXIT trap back-fills) them in.
PROOF_IDS_IN_RUN_ORDER = (
    "AISTUDIO-ENT-001", "AISTUDIO-ROUTER-001", "AISTUDIO-UNIFIED-001", "AISTUDIO-BUDGET-001",
    "AISTUDIO-GUARDRAIL-001", "AISTUDIO-AUDIT-001", "AISTUDIO-TYKMCP-001", "AISTUDIO-METRICS-001",
    "AISTUDIO-FAILOVER-001",
)


def run_generator(output: Path, *extra: str, modules: str = "rest,ai-studio") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(output),
         "--slug", SLUG, "--name", DISPLAY_NAME, "--modules", modules, *extra],
        capture_output=True, text=True, check=False,
        env=dict(os.environ, **{base_suite.DOCKER_HUB_FIXTURE_ENV: str(base_suite.DOCKER_HUB_FIXTURE)}),
    )


def env_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


class Args:
    def __init__(self, ai_studio_version=None):
        self.gateway_version = "v5.15.0"
        self.dashboard_version = "v5.15.0"
        self.portal_version = "v1.19.0"
        self.ai_studio_version = ai_studio_version


@unittest.skipUnless(GENERATOR_OK, SKIP_REASON)
class GeneratedCompanionTests(unittest.TestCase):
    """One bundle generated for AI Studio v2.3.0, inspected from every angle."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        result = run_generator(cls.root, "--ai-studio-version", "v2.3.0")
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.companion = cls.root / "companion" / "ai-studio"
        cls.config = json.loads((cls.root / "demo.config.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def read(self, relative: str) -> str:
        return (self.companion / relative).read_text()

    def test_both_images_run_the_requested_v2_3_0(self):
        self.assertEqual(self.config["component_versions"]["ai_studio"], "v2.3.0")
        compose = self.read("compose.yaml")
        self.assertIn("image: tykio/tyk-ai-studio-ent:v2.3.0\n", compose)
        self.assertIn("image: tykio/tyk-microgateway-ent:v2.3.0\n", compose)

    def test_companion_file_set_is_exact_and_scripts_are_executable(self):
        found = {str(p.relative_to(self.companion)) for p in self.companion.rglob("*") if p.is_file()}
        self.assertEqual(found, COMPANION_FILES)
        for relative in sorted(found):
            if relative.endswith(".sh") and relative != "lib/admin.sh":
                mode = (self.companion / relative).stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR, f"{relative} is not executable")

    def test_every_generation_token_is_resolved(self):
        for path in sorted(self.companion.rglob("*")):
            if path.is_file():
                leftover = re.findall(r"__[A-Z][A-Z0-9_]*__", path.read_text(errors="ignore"))
                self.assertEqual(leftover, [], f"{path.relative_to(self.companion)} keeps {leftover}")

    def test_companion_passes_the_sanitizer(self):
        # secret.env is ignored by the bundle's .gitignore; everything else ships.
        findings = base_suite.sanitizer.scan_bundle(self.root)
        companion = [str(f) for f in findings if str(f.path).startswith("companion/")]
        self.assertEqual(companion, [])

    def test_shell_scripts_parse(self):
        for path in sorted(self.companion.rglob("*.sh")):
            result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

    def test_secrets_are_minted_including_the_metrics_token(self):
        secret = self.companion / "secret.env"
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
        values = env_values(secret)
        self.assertRegex(values.get("METRICS_AUTH_TOKEN", ""), r"^[0-9a-f]{48}$")
        self.assertNotEqual(values["METRICS_AUTH_TOKEN"], values["GRPC_AUTH_TOKEN"])
        self.assertFalse(values.get("TYK_AI_LICENSE"))
        example = env_values(self.companion / "secret.env.example")
        self.assertEqual(example.get("METRICS_AUTH_TOKEN"), "")
        self.assertEqual(validator.validate_ai_studio_secrets(self.root), [])

    def test_admin_login_is_host_only(self):
        # LOW-15: secret.env is an env_file of both containers, so the admin login
        # lives in a separate 0600 admin.env that only the host scripts source.
        secret = env_values(self.companion / "secret.env")
        self.assertNotIn("AISTUDIO_ADMIN_PASSWORD", secret)
        self.assertNotIn("AISTUDIO_ADMIN_EMAIL", secret)
        admin = self.companion / "admin.env"
        self.assertEqual(stat.S_IMODE(admin.stat().st_mode), 0o600)
        password = env_values(admin)["AISTUDIO_ADMIN_PASSWORD"]
        self.assertTrue(password.startswith("Aa1!") and len(password) >= 8)
        self.assertEqual(env_values(self.companion / "admin.env.example")["AISTUDIO_ADMIN_PASSWORD"], "")
        compose = self.read("compose.yaml")
        env_files = re.findall(r"^\s+- \./(\S+\.env)\s*$", compose, flags=re.MULTILINE)
        self.assertIn("secret.env", env_files)
        self.assertNotIn("admin.env", env_files)
        self.assertIn("companion/*/admin.env", (self.root / ".gitignore").read_text().splitlines())
        for script in ("configure-ai-studio.sh", "verify-ai-studio.sh", "proofs/tyk-mcp-import.sh"):
            self.assertIn("load_admin_env", self.read(script), script)
        self.assertIn("source ./admin.env", self.read("proofs/capture-screenshots.sh"))
        # The companion's .gitignore entry keeps it out of the sanitizer's scan, and
        # --strict-runtime lists it as a local secret to remove before sharing.
        self.assertIn(Path("companion/ai-studio/admin.env"), base_suite.sanitizer.runtime_artifacts(self.root))

    def test_no_credential_is_passed_on_a_command_line(self):
        # LOW-15: request bodies (admin password, LLM api keys, the Dashboard token)
        # reach curl on stdin, and bearer tokens as -H @-, so ps never shows them.
        admin = self.read("lib/admin.sh")
        code = "\n".join(line for line in admin.splitlines() if not line.lstrip().startswith("#"))
        self.assertEqual(code.count("--data-binary @-"), 3)  # api, api_raw, aistudio_login
        self.assertNotRegex(admin, r"curl [^\n]*-d \"\$\{")
        for path in sorted(self.companion.rglob("*.sh")):
            text = path.read_text()
            self.assertNotRegex(text, r"-H \"Authorization: Bearer \$", path.name)
            # (verify's chat() still sends its prompt with -d: a model name and a prompt, no credential.)
            self.assertNotRegex(text, r"-d \"\$\{(register_body|body)\}\"", path.name)
        self.assertIn('--data-binary @- <<<"${register_body}"', self.read("configure-ai-studio.sh"))
        self.assertIn("batch", self.read("proofs/capture-screenshots.sh"))
        self.assertNotIn('"${AISTUDIO_ADMIN_PASSWORD}" >/dev/null', self.read("proofs/capture-screenshots.sh"))

    def test_secret_env_example_is_consistent_about_what_is_committed(self):
        # LOW-16: the example is committed; the private copy is gitignored.
        example = self.read("secret.env.example")
        self.assertIn("THIS EXAMPLE IS COMMITTED", example)
        self.assertNotIn("this file is gitignored", example)
        self.assertNotIn("this example file is neither", example)
        self.assertIn("THIS EXAMPLE IS COMMITTED", self.read("admin.env.example"))

    def test_no_licensing_wording_in_the_bundle(self):
        # Open review question: technical statements only.
        for relative in ("start.sh", "secret.env.example", "README.md"):
            text = self.read(relative)
            self.assertNotIn("separate Model Router", text, relative)
            self.assertNotIn("entitlement of its own", text, relative)
            self.assertNotIn("to buy", text, relative)
        # The edition split is cited from the public overview page, not asserted.
        overview = "https://tyk.io/docs/ai-management/ai-studio/overview"
        for relative in ("start.sh", "secret.env.example", "README.md"):
            self.assertIn(overview, self.read(relative), relative)
        for path in sorted(self.companion.rglob("*")):
            if path.is_file():
                text = path.read_text(errors="ignore")
                self.assertNotRegex(text, r"\b[a-z_/]+\.go\b", path.name)
                self.assertNotIn("source-" + "verified", text, path.name)

    def test_admin_identity_and_mail_follow_the_bundle(self):
        values = env_values(self.companion / "admin.env")
        self.assertEqual(values["AISTUDIO_ADMIN_EMAIL"], f"admin@{SLUG}.invalid")
        self.assertIn(f"FROM_EMAIL=noreply@{SLUG}.invalid\n", self.read("confs/ai-studio-ent.env"))
        configure = self.read("configure-ai-studio.sh")
        self.assertIn(f"{DISPLAY_NAME} Administrator", configure)
        self.assertNotIn("Demo Admin", configure)
        for path in sorted(self.companion.rglob("*")):
            if path.is_file():
                self.assertNotIn("demo.invalid", path.read_text(errors="ignore"), path.name)

    def test_edge_conf_matches_2_3(self):
        mgw = env_values(self.companion / "confs" / "mgw-ent.env")
        self.assertEqual(mgw["DATABASE_DSN"], "file:/app/data/edge-ent.db?mode=rwc")
        self.assertNotIn("ENABLE_RATE_LIMITING", mgw)
        self.assertNotIn("GATEWAY_DEFAULT_RATE_LIMIT", mgw)
        self.assertEqual(mgw["GATEWAY_UNIFIED_ROUTER_PATH"], "/v1")
        self.assertEqual(mgw["ENABLE_METRICS"], "true")
        self.assertEqual(mgw["EDGE_ID"], f"{SLUG}-edge-01")
        # The analytics pulse points at the file compose.yaml actually mounts.
        mounted = re.search(r"- \./confs/mgw-analytics\.yaml:(\S+):ro", self.read("compose.yaml")).group(1)
        self.assertEqual(mgw["PLUGINS_CONFIG_PATH"], mounted)
        for key in ("EDGE_AUTH_TOKEN", "ENCRYPTION_KEY", "JWT_SECRET", "METRICS_AUTH_TOKEN"):
            self.assertNotIn(key, mgw, f"{key} must live in secret.env, not in confs/")
        # Internal addresses are blocked by default: only the stub backends are named.
        self.assertEqual(mgw["LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS"], "llm-alpha,llm-beta")
        self.assertNotIn("ALLOW_INTERNAL_NETWORK_ACCESS", mgw)

    def test_control_plane_conf_matches_2_3(self):
        hub = env_values(self.companion / "confs" / "ai-studio-ent.env")
        self.assertEqual(hub["TELEMETRY_ENABLED"], "false")
        self.assertEqual(hub["LLM_UPSTREAM_ALLOWED_INTERNAL_HOSTS"], "llm-alpha,llm-beta")
        self.assertNotIn("ALLOW_INTERNAL_NETWORK_ACCESS", hub)
        self.assertEqual(hub["UNIFIED_ROUTER_PATH"], "/v1")
        self.assertTrue(hub["DOCS_URL_OVERRIDE"].startswith("https://tyk.io/docs/"))
        self.assertNotIn("DOCS_URL", hub)
        self.assertEqual(hub["MARKETPLACE_CACHE_DIR"], "./data/marketplace-cache")
        self.assertEqual((hub["AUDIT_ENABLED"], hub["AUDIT_STORE_TYPE"], hub["AUDIT_RETENTION_DAYS"]), ("true", "db", "90"))
        for key in ("GRPC_AUTH_TOKEN", "MICROGATEWAY_ENCRYPTION_KEY", "TYK_AI_SECRET_KEY", "METRICS_AUTH_TOKEN"):
            self.assertNotIn(key, hub)

    def test_compose_maps_the_host_for_the_dashboard_link(self):
        compose = self.read("compose.yaml")
        control_plane = compose[compose.index("  ai-studio:"):compose.index("  mgw:")]
        self.assertIn('- "host.docker.internal:host-gateway"', control_plane)
        self.assertNotIn("router.go:290", compose)
        self.assertNotIn("quickstart", compose)

    def test_start_checks_the_metrics_token(self):
        start = self.read("start.sh")
        loop = next(line for line in start.splitlines() if line.startswith("for key in "))
        self.assertIn("METRICS_AUTH_TOKEN", loop)
        self.assertIn("data/appdata/marketplace-cache", start)

    def test_configure_grants_the_router_sends_full_app_state_and_publishes_a_catalogue(self):
        configure = self.read("configure-ai-studio.sh")
        body = configure[configure.index("ensure_app() {"):configure.index("app_secret() {")]
        for field in ("'name'", "'description'", "'user_id'", "'llm_ids'", "'model_router_ids'", "'monthly_budget'"):
            self.assertIn(field, body, f"App PATCH must carry {field}")
        self.assertIn('api PATCH "/api/v1/apps/${id}" "${body}"', body)
        self.assertIn('"[${ROUTER_ID}]" null "${LEGACY_APP_NAME}")', configure)
        self.assertIn('"[]" 0)', configure)
        for call in ("POST /api/v1/catalogues", "/api/v1/catalogues/${CATALOGUE_ID}/llms",
                     "PUT \"/api/v1/model-routers/${ROUTER_ID}/catalogues\"", "/api/v1/groups/${DEFAULT_GROUP_ID}/catalogues",
                     "POST /api/v1/edges/reload-all"):
            self.assertIn(call, configure)
        for variable in ("LLM_A_VENDOR", "LLM_A_ENDPOINT", "LLM_A_API_KEY", "LLM_A_METADATA_JSON",
                         "LLM_B_VENDOR", "LLM_B_ENDPOINT", "LLM_B_API_KEY", "LLM_B_METADATA_JSON"):
            self.assertIn(f'{variable}="${{{variable}:-', configure)

    def test_configure_waits_for_a_registered_edge_before_the_reload(self):
        # reload-all answers 409 "no edges to push to" until an edge has registered,
        # and on a first start the edge can exit once before the control plane listens.
        configure = self.read("configure-ai-studio.sh")
        wait = configure.index("api GET /api/v1/edges")
        reload_call = configure.index("api POST /api/v1/edges/reload-all")
        self.assertLess(wait, reload_call)
        self.assertIn("no edge registered with the control plane", configure[wait:reload_call])

    def test_slug_clash_proof_needs_the_route_slug_message_not_any_400(self):
        # MED-7: only the route-slug refusal counts, not any 400.
        message = "route name is already used by another LLM or router"
        configure = self.read("configure-ai-studio.sh")
        self.assertIn(f'SLUG_CLASH_MESSAGE="{message}"', configure)
        self.assertIn('if [[ "${clash_code}" == 400 && "${clash_out}" == *"${SLUG_CLASH_MESSAGE}"* ]]; then', configure)
        # A bare status check may only follow as the elif that reports the wrong message.
        self.assertNotRegex(configure, r'(?m)^if \[\[ "\$\{clash_code\}" == 400 \]\]; then')
        verify = self.read("verify-ai-studio.sh")
        self.assertIn(f'SLUG_CLASH_MESSAGE="{message}"', verify)
        self.assertIn('grep -qF "${SLUG_CLASH_MESSAGE}" "${EV}/router-slug-clash.json"', verify)

    def test_configure_patches_an_existing_router_to_the_full_desired_state(self):
        # HIGH-1: a re-run must converge the router, not print "already present" and move on.
        configure = self.read("configure-ai-studio.sh")
        section = configure[configure.index('echo "==> model router'):configure.index("SLUG_CLASH_MESSAGE=")]
        self.assertIn('api POST /api/v1/model-routers "${router_body}"', section)
        self.assertIn('api PATCH "/api/v1/model-routers/${ROUTER_ID}" "${router_body}"', section)
        # One body for both paths, built before the branch, carrying the full state.
        self.assertLess(section.index("router_body="), section.index('if [[ -z "${ROUTER_ID}" ]]; then'))
        for field in ("'name'", "'slug'", "'description'", "'active':True", "'pools'", "'mappings'"):
            self.assertIn(field, section)
        # The stored pools are read back and compared with the desired ones.
        self.assertIn('api GET "/api/v1/model-routers/${ROUTER_ID}"', section)
        self.assertIn("router pools not converged", section)
        # The catalogue link and the edge reload still follow on every run.
        tail = configure[configure.index("SLUG_CLASH_MESSAGE="):]
        self.assertIn('PUT "/api/v1/model-routers/${ROUTER_ID}/catalogues"', tail)
        self.assertIn("POST /api/v1/edges/reload-all", tail)

    def test_unified_path_comes_from_the_edge_conf(self):
        # LOW-16: GATEWAY_UNIFIED_ROUTER_PATH is read from confs/mgw-ent.env, not hardcoded.
        for script in ("configure-ai-studio.sh", "verify-ai-studio.sh"):
            self.assertIn("$(conf_value confs/mgw-ent.env GATEWAY_UNIFIED_ROUTER_PATH)", self.read(script), script)
            self.assertNotIn('UNIFIED_PATH="${GATEWAY_UNIFIED_ROUTER_PATH:-/v1}"', self.read(script), script)
        self.assertIn("UNIFIED_ROUTER_PATH=", self.read("configure-ai-studio.sh"))
        admin = self.read("lib/admin.sh")
        helper = admin[admin.index("conf_value() {"):admin.index("# api METHOD PATH")]
        with tempfile.TemporaryDirectory() as temporary:
            conf = Path(temporary) / "edge.env"
            conf.write_text("# GATEWAY_UNIFIED_ROUTER_PATH=/commented\nGATEWAY_UNIFIED_ROUTER_PATH=/first\nGATEWAY_UNIFIED_ROUTER_PATH=\"/openai\"\n")
            result = subprocess.run(["bash", "-c", f'{helper}\nconf_value "$1" GATEWAY_UNIFIED_ROUTER_PATH', "_", str(conf)],
                                    capture_output=True, text=True, check=False)
        self.assertEqual(result.stdout.strip(), "/openai", result.stderr)

    def test_rejected_llm_patch_stops_configure(self):
        # MED-5: ensure_llm runs inside $(...); a 400 on its PATCH must fail the run.
        configure = self.read("configure-ai-studio.sh")
        ensure_llm = configure[configure.index("ensure_llm() {"):configure.index("LLM_A_ID=")]
        ensure_app = configure[configure.index("ensure_app() {"):configure.index("app_secret() {")]
        for body, calls in ((ensure_llm, ("api POST", "api PATCH", "id_by_name")), (ensure_app, ("api POST", "api PATCH", "id_by_name"))):
            for line in body.splitlines():
                # Re-activating a credential is reported but deliberately not fatal.
                if any(call in line for call in calls) and not line.lstrip().startswith("#") and "activate-credential" not in line:
                    self.assertIn("|| return 1", line, line)
        for caller in ("LLM_A_ID=", "LLM_B_ID=", "APP_ID=", "BUDGET_APP_ID="):
            statement = configure[configure.index(caller):]
            statement = statement[:statement.index("exit 1; }") + len("exit 1; }")]
            self.assertIn("|| {", statement, caller)
        admin = self.read("lib/admin.sh")
        helpers = admin[admin.index("# api METHOD PATH"):admin.index("# aistudio_login")]

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"data": [{"id": "7", "attributes": {"name": "Promo Check Beta"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_PATCH(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b'{"errors":[{"title":"Bad Request","detail":"invalid metadata"}]}'
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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
                    f"{helpers}\n{ensure_llm}\n"
                    'LLM_B_ID="$(ensure_llm "Promo Check Beta" openai http://llm-beta:8080 k beta-deep d \'{"aws_access_key_id":"x"}\')" \\\n'
                    '  || { echo "configure stopped"; exit 1; }\n'
                    'echo "Done. id=${LLM_B_ID}"\n'
                )
                result = subprocess.run(["/bin/bash", "-c", harness], capture_output=True, text=True, check=False)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("configure stopped", result.stdout)
        self.assertNotIn("Done.", result.stdout)
        self.assertIn("PATCH /api/v1/llms/7 returned HTTP 400", result.stderr)

    def test_verify_records_all_nine_proofs_in_the_evidence_shape(self):
        verify = self.read("verify-ai-studio.sh")
        for test_id in PROOF_IDS:
            self.assertIn(f"record {test_id} passed", verify)
        self.assertIn("{test_id:$id,status:$status,detail:$detail}", verify)
        self.assertIn('"${ROOT_DIR}/evidence/results.json"', verify)
        self.assertIn('"${ROOT_DIR}/evidence/validation.md"', verify)
        # FAILOVER always restarts the stub it stopped: the EXIT trap is installed
        # before anything runs, and the flag it reads is set before the stop.
        self.assertLess(verify.index("trap 'finish $?' EXIT"), verify.index("source ./secret.env"))
        self.assertLess(verify.index("alpha_stopped=yes"), verify.index('docker compose stop "${alpha_svc}"'))
        finish = verify[verify.index("finish() {"):verify.index("trap 'finish $?' EXIT")]
        self.assertIn('docker compose start "${alpha_svc}"', finish)

    def test_verify_guards_every_admin_read(self):
        # MED-4: under set -e an unguarded $(api ...) aborts the whole run on a 401.
        verify = self.read("verify-ai-studio.sh")
        lines = verify.splitlines()
        for index, line in enumerate(lines):
            if re.search(r'^\s*\w+="\$\(api ', line):
                statement = line
                while statement.rstrip().endswith("\\"):
                    index += 1
                    statement += lines[index]
                self.assertIn("||", statement, line)
        self.assertIn('rm -f "${RESULTS}" "${REPORT}"', verify)
        self.assertLess(verify.index('rm -f "${RESULTS}" "${REPORT}"'), verify.index("source ./secret.env"))

    def test_audit_proof_filters_for_successful_creates(self):
        # MED-6: refused slug-clash probes pile up under "Create Model Router".
        verify = self.read("verify-ai-studio.sh")
        audit = verify[verify.index("# --- AISTUDIO-AUDIT-001"):verify.index("# --- AISTUDIO-TYKMCP-001")]
        self.assertIn("status_class=2xx", audit)
        self.assertIn("sort=asc", audit)
        self.assertIn("for page in 1 2 3 4 5; do", audit)
        self.assertNotIn("page_size=100", audit)
        self.assertIn("200 <= int(r.get(\"status\") or 0) < 300", audit)
        self.assertIn("name_of(r) == n", audit)

    @unittest.skipUnless(shutil.which("jq"), "jq is not installed")
    def test_an_aborted_verify_never_leaves_earlier_passes(self):
        # MED-4: results.json and validation.md are written from the EXIT trap.
        with tempfile.TemporaryDirectory() as temporary:
            companion = Path(temporary) / "bundle" / "companion" / "ai-studio"
            shutil.copytree(self.companion, companion)
            evidence = companion / "evidence"
            evidence.mkdir()
            stale = [{"test_id": test_id, "status": "passed", "detail": "earlier run"} for test_id in PROOF_IDS]
            (evidence / "results.json").write_text(json.dumps(stale))
            (evidence / "validation.md").write_text("| `AISTUDIO-ENT-001` | `passed` | earlier run |\n")
            (companion / "secret.env").unlink()  # the run stops at its first step
            result = subprocess.run([str(companion / "verify-ai-studio.sh")], capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            rows = json.loads((evidence / "results.json").read_text())
            self.assertEqual([row["test_id"] for row in rows], list(PROOF_IDS_IN_RUN_ORDER))
            self.assertEqual({row["status"] for row in rows}, {"failed"})
            self.assertTrue(all("stopped (exit" in row["detail"] for row in rows), rows)
            report = (evidence / "validation.md").read_text()
            self.assertIn("This run stopped early", report)
            self.assertNotIn("earlier run", report)

    @unittest.skipUnless(shutil.which("jq"), "jq is not installed")
    def test_verify_against_a_dead_stack_records_no_pass(self):
        # MED-4: every admin and edge call fails; the run completes with nine rows.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            closed = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as temporary:
            companion = Path(temporary) / "bundle" / "companion" / "ai-studio"
            shutil.copytree(self.companion, companion)
            (companion / ".runtime").mkdir(mode=0o700)
            (companion / ".runtime" / "ai-studio.env").write_text(
                f"AISTUDIO_ADMIN_URL=http://127.0.0.1:{closed}\nAISTUDIO_ROUTER_URL=http://127.0.0.1:{closed}\n")
            evidence = companion / "evidence"
            evidence.mkdir()
            (evidence / "results.json").write_text(json.dumps([{"test_id": "AISTUDIO-ENT-001", "status": "passed", "detail": "earlier run"}]))
            result = subprocess.run([str(companion / "verify-ai-studio.sh")], capture_output=True, text=True, check=False,
                                    env=dict(os.environ, TYK_MCP_VERIFY_WAIT_SECONDS="0"), timeout=120)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            rows = json.loads((evidence / "results.json").read_text())
            self.assertEqual(sorted(row["test_id"] for row in rows), sorted(PROOF_IDS))
            self.assertNotIn("passed", {row["status"] for row in rows})
            self.assertNotIn("stopped (exit", json.dumps(rows))  # it finished, it did not abort

    def test_tyk_mcp_import_is_read_only_and_needs_the_mcp_module(self):
        script = self.read("proofs/tyk-mcp-import.sh")
        self.assertIn("'declared_mode': 'catalogue'", script)
        self.assertIn("'allow_internal_host': True", script)
        self.assertIn("sync?wait=true", script)
        self.assertIn("'mcp' in json.load(open(sys.argv[1])).get('modules',[])", script)
        calls = re.findall(r"\bapi(?:_raw)? (POST|PATCH|PUT|DELETE) \"?(\S+)", script)
        for method, path in calls:
            self.assertTrue(path.startswith(("/api/v1/tyk-connections", '"/api/v1/tyk-connections')), f"{method} {path}")
        for forbidden in ("/mcp-servers/register", "/push", "/policies"):
            self.assertNotIn(f"api POST \"{forbidden}", script)
            self.assertNotRegex(script, rf"api (POST|PATCH) [^\n]*{re.escape(forbidden)}")
        # Without the mcp module the import records blocked instead of failing.
        try:
            result = subprocess.run([str(self.companion / "proofs" / "tyk-mcp-import.sh")],
                                    capture_output=True, text=True, check=False, env=dict(os.environ, TYK_MCP_WAIT_SECONDS="0"))
            self.assertEqual(result.returncode, 2, result.stderr)
            status = json.loads((self.companion / "evidence" / "bodies" / "tyk-mcp-status.json").read_text())
            self.assertEqual(status["status"], "blocked")
            self.assertIn("mcp module", status["detail"])
        finally:
            shutil.rmtree(self.companion / "evidence", ignore_errors=True)
            shutil.rmtree(self.companion / ".runtime", ignore_errors=True)

    def test_bundle_readme_describes_the_2_3_companion(self):
        readme = (self.root / "README.md").read_text()
        section = readme[readme.index("## Companion stack: Tyk AI Studio"):]
        section = section[:section.find("\n## ", 5)] if "\n## " in section[5:] else section
        self.assertIn("v2.3.0", section[:120])
        self.assertIn("/v1/chat/completions", section)
        self.assertIn("LLM_A_VENDOR", section)
        self.assertIn("Enterprise features", section)
        self.assertIn("needs the `mcp` module", section)
        self.assertNotRegex(section, r"__[A-Z][A-Z0-9_]*__")


@unittest.skipUnless(GENERATOR_OK, SKIP_REASON)
class ValidatorMetricsTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        result = run_generator(cls.root, "--versions", "pinned")
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.secret = cls.root / "companion" / "ai-studio" / "secret.env"
        cls.original = cls.secret.read_text()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def tearDown(self):
        self.secret.write_text(self.original)
        self.secret.chmod(0o600)
        config = json.loads((self.root / "demo.config.json").read_text())
        config["component_versions"]["ai_studio"] = "v2.3.0"
        (self.root / "demo.config.json").write_text(json.dumps(config))

    def blank_metrics_token(self):
        token = env_values(self.secret)["METRICS_AUTH_TOKEN"]
        self.secret.write_text(self.original.replace(f"METRICS_AUTH_TOKEN={token}", "METRICS_AUTH_TOKEN="))

    def test_pinned_generation_uses_the_known_good_2_3_0(self):
        config = json.loads((self.root / "demo.config.json").read_text())
        self.assertEqual(config["component_versions"]["ai_studio"], "v2.3.0")
        self.assertEqual(generator.KNOWN_GOOD_VERSIONS["ai_studio"], "v2.3.0")
        self.assertEqual(validator.validate_ai_studio_secrets(self.root), [])

    def test_a_missing_metrics_token_is_rejected(self):
        self.blank_metrics_token()
        errors = validator.validate_ai_studio_secrets(self.root)
        self.assertTrue(any("METRICS_AUTH_TOKEN is empty" in error for error in errors), errors)

    def test_the_requirement_is_version_aware(self):
        self.blank_metrics_token()
        config = json.loads((self.root / "demo.config.json").read_text())
        config["component_versions"]["ai_studio"] = "v2.1.0"
        (self.root / "demo.config.json").write_text(json.dumps(config))
        self.assertEqual(validator.validate_ai_studio_secrets(self.root), [])
        config["component_versions"]["ai_studio"] = "v2.3.1"
        (self.root / "demo.config.json").write_text(json.dumps(config))
        self.assertTrue(validator.validate_ai_studio_secrets(self.root))


@unittest.skipUnless(GENERATOR_OK, SKIP_REASON)
class AdminEnvValidatorTests(unittest.TestCase):
    """validate_ai_studio_secrets requires both private files (LOW-15)."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "bundle"
        result = run_generator(cls.root, "--versions", "pinned")
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.companion = cls.root / "companion" / "ai-studio"
        cls.originals = {name: (cls.companion / name).read_text() for name in ("secret.env", "admin.env")}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def tearDown(self):
        for name, text in self.originals.items():
            (self.companion / name).write_text(text)
            (self.companion / name).chmod(0o600)

    def errors(self) -> list[str]:
        return validator.validate_ai_studio_secrets(self.root)

    def test_generated_files_validate(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(validator.validate_gitignore(self.root), [])

    def test_missing_admin_env_is_rejected(self):
        (self.companion / "admin.env").unlink()
        self.assertTrue(any("admin.env: missing" in error for error in self.errors()), self.errors())

    def test_loose_admin_env_permissions_are_rejected(self):
        (self.companion / "admin.env").chmod(0o644)
        self.assertTrue(any("admin.env: permissions" in error for error in self.errors()), self.errors())

    def test_empty_admin_password_is_rejected(self):
        password = env_values(self.companion / "admin.env")["AISTUDIO_ADMIN_PASSWORD"]
        (self.companion / "admin.env").write_text(self.originals["admin.env"].replace(password, ""))
        self.assertTrue(any("AISTUDIO_ADMIN_PASSWORD is empty" in error for error in self.errors()), self.errors())

    def test_admin_password_in_secret_env_is_rejected(self):
        (self.companion / "secret.env").write_text(self.originals["secret.env"] + "AISTUDIO_ADMIN_PASSWORD=Aa1!leaked\n")
        self.assertTrue(any("belongs in admin.env" in error for error in self.errors()), self.errors())

    def test_the_gitignore_entry_is_required(self):
        gitignore = self.root / ".gitignore"
        original = gitignore.read_text()
        try:
            gitignore.write_text(original.replace("companion/*/admin.env\n", ""))
            self.assertTrue(any("companion/*/admin.env" in error for error in validator.validate_gitignore(self.root)))
        finally:
            gitignore.write_text(original)


@unittest.skipUnless(GENERATOR_OK, SKIP_REASON)
class VersionGateTests(unittest.TestCase):
    def test_boundary(self):
        # A pre-release of 2.3.0 is accepted (until 2.3.0 is published it is the only 2.3
        # image on Docker Hub); anything from the 2.2 line or older is refused.
        for accepted in ("v2.3.0", "v2.3.0-rc4", "v2.3.0-beta.2", "v2.3.1", "v2.4.0-beta.1", "v2.10.0", "v3.0.0"):
            generator.check_module_version_support({"ai-studio"}, Args(accepted))
        for refused in ("v2.2.2", "v2.2.0", "v2.2.0-beta.1", "v2.1.0", "v1.9.9"):
            with self.assertRaises(SystemExit) as caught:
                generator.check_module_version_support({"ai-studio"}, Args(refused))
            message = str(caught.exception)
            self.assertIn("needs Tyk AI Studio 2.3.0 or later", message)
            self.assertIn(f"got {refused} (--ai-studio-version)", message)
            self.assertIn("written and verified for AI Studio 2.3 only", message)
            self.assertIn("Regenerate with --ai-studio-version v2.3.0", message)
            self.assertNotRegex(message, r"(?i)release candidate|\brc\d")

    def test_the_message_only_blames_the_flag_when_the_user_passed_it(self):
        cases = {
            "user": "(--ai-studio-version)",
            "pinned": "(the builder's known-good pinned version)",
            "known-good fallback": "(the builder's known-good fallback; Docker Hub could not be read)",
            "latest (Docker Hub, 2026-10-01)": "(the newest stable tag on Docker Hub)",
        }
        for source, origin in cases.items():
            args = Args("v2.2.2")
            args.ai_studio_version_source = source
            with self.assertRaises(SystemExit) as caught:
                generator.check_module_version_support({"ai-studio"}, args)
            message = str(caught.exception)
            self.assertIn(f"got v2.2.2 {origin}", message, source)
            if source != "user":
                self.assertNotIn("(--ai-studio-version)", message, source)

    def test_the_cli_accepts_a_2_3_0_pre_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "pre"
            result = run_generator(target, "--ai-studio-version", "v2.3.0-rc4")
            self.assertEqual(result.returncode, 0, result.stderr)
            compose = (target / "companion" / "ai-studio" / "compose.yaml").read_text()
            self.assertIn("image: tykio/tyk-ai-studio-ent:v2.3.0-rc4\n", compose)
            self.assertIn("image: tykio/tyk-microgateway-ent:v2.3.0-rc4\n", compose)

    def test_the_gate_only_applies_to_the_module(self):
        generator.check_module_version_support({"rest"}, Args("v2.0.0"))

    def test_known_good_satisfies_its_own_gate(self):
        generator.check_module_version_support({"ai-studio"}, Args(generator.KNOWN_GOOD_VERSIONS["ai_studio"]))
        generator.check_module_version_support({"ai-studio"}, Args(None))

    def test_the_cli_refuses_an_explicit_2_2_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "old"
            result = run_generator(target, "--ai-studio-version", "v2.2.2")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("needs Tyk AI Studio 2.3.0 or later", result.stderr)
            self.assertFalse(target.exists())
            result = run_generator(target, "--ai-studio-version", "v2.3.0", "--versions", "pinned")
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(GENERATOR_OK, SKIP_REASON)
class TagFixtureTests(unittest.TestCase):
    """Latest-tag resolution for AI Studio, against the main suite's fixture plus 2.3.0."""

    @staticmethod
    def tags_with_2_3_0():
        tags = {repository: list(values) for repository, values in base_suite.DOCKER_HUB_TAGS.items()}
        for repository in ("tykio/tyk-ai-studio-ent", generator.AI_STUDIO_MICROGATEWAY_REPOSITORY):
            if "v2.3.0" not in tags[repository]:
                tags[repository].insert(0, "v2.3.0")
        return tags

    def test_a_2_3_0_release_on_both_images_resolves_and_passes_the_gate(self):
        from unittest import mock
        tags = self.tags_with_2_3_0()
        components = generator.selected_components({"ai-studio"})
        with mock.patch.object(generator, "fetch_tags", side_effect=lambda repository, notes=None, start_line=None: list(tags[repository])):
            versions, sources, warnings = generator.resolve_component_versions({}, "latest", today="2026-11-19", components=components)
        self.assertEqual(versions["ai_studio"], "v2.3.0")
        self.assertEqual(warnings, [])
        generator.check_module_version_support({"ai-studio"}, Args(versions["ai_studio"]))


    # Docker Hub on 2026-11-19: no stable 2.3 tag yet, four numbered pre-releases and a run
    # of unstable builds on both images (the resolver only reads the 2.3 line onwards).
    TODAY_2_3_TAGS = ["v2.3.0-rc1", "v2.3.0-rc2", "v2.3.0-rc3", "v2.3.0-rc4",
                      "v2.3.0-unstable.1", "v2.3.0-unstable.8", "v2.3.0-unstable.13", "v2.3.0-unstable.21"]

    @staticmethod
    def resolve_with(tags: dict[str, list[str]]):
        from unittest import mock
        with mock.patch.object(generator, "fetch_tags", side_effect=lambda repository, notes=None, start_line=None: list(tags[repository])):
            return generator.resolve_component_versions({}, "latest", today="2026-11-19", components=["ai_studio"])

    def both(self, ai_studio: list[str], microgateway: list[str] | None = None) -> dict[str, list[str]]:
        return {"tykio/tyk-ai-studio-ent": list(ai_studio),
                generator.AI_STUDIO_MICROGATEWAY_REPOSITORY: list(ai_studio if microgateway is None else microgateway)}

    def assert_neutral(self, warnings: list[str]):
        for warning in warnings:
            # The tag itself may carry Docker Hub's suffix; the wording around it may not.
            prose = re.sub(r"v\d+\.\d+\.\d+-\S+", "<tag>", warning)
            self.assertNotRegex(prose, r"(?i)release candidate|\brc\b|\brc\d", warning)

    def test_today_both_images_resolve_to_the_newest_pre_release(self):
        versions, sources, warnings = self.resolve_with(self.both(self.TODAY_2_3_TAGS))
        self.assertEqual((versions["ai_studio"], sources["ai_studio"]), ("v2.3.0-rc4", "latest (Docker Hub, 2026-11-19)"))
        self.assertEqual(len(warnings), 1, warnings)
        self.assertTrue(warnings[0].startswith("NOTE: AI Studio uses v2.3.0-rc4,"), warnings)
        self.assertNotIn("unstable", warnings[0])
        self.assert_neutral(warnings)
        generator.check_module_version_support({"ai-studio"}, Args(versions["ai_studio"]))

    def test_pre_releases_order_numerically_and_never_pick_unstable(self):
        self.assertEqual(generator.newest_prerelease(["v2.3.0-rc4", "v2.3.0-rc10", "v2.3.0-rc9"], (2, 3)), "v2.3.0-rc10")
        self.assertIsNone(generator.newest_prerelease(["v2.3.0-unstable.21", "v2.3.0-unstable.3"], (2, 3)))
        self.assertIsNone(generator.newest_prerelease(["v2.2.0-rc10.0", "v2.3.0"], (2, 3)))

    def test_a_published_2_3_0_wins_over_every_pre_release(self):
        versions, _, warnings = self.resolve_with(self.both(["v2.3.0"] + self.TODAY_2_3_TAGS))
        self.assertEqual(versions["ai_studio"], "v2.3.0")
        self.assertEqual(warnings, [])

    def test_a_lagging_microgateway_keeps_the_newest_common_pre_release(self):
        versions, _, warnings = self.resolve_with(self.both(self.TODAY_2_3_TAGS, ["v2.3.0-rc1", "v2.3.0-rc2", "v2.3.0-rc3", "v2.3.0-unstable.21"]))
        self.assertEqual(versions["ai_studio"], "v2.3.0-rc3")
        self.assertTrue(any(warning.startswith("NOTE: AI Studio uses v2.3.0-rc3,") for warning in warnings), warnings)
        self.assert_neutral(warnings)
        # A stable 2.3.0 on AI Studio alone does not beat a pre-release both images carry.
        versions, _, _ = self.resolve_with(self.both(["v2.3.0"] + self.TODAY_2_3_TAGS, self.TODAY_2_3_TAGS))
        self.assertEqual(versions["ai_studio"], "v2.3.0-rc4")

    def test_a_2_2_only_list_is_refused_as_before(self):
        versions, sources, warnings = self.resolve_with(self.both(["v2.2.2", "v2.2.1", "v2.2.0-rc10.0"]))
        self.assertEqual((versions["ai_studio"], sources["ai_studio"]), ("v2.2.2", "latest (Docker Hub, 2026-11-19)"))
        self.assertEqual(warnings, [])
        args = Args(versions["ai_studio"])
        args.ai_studio_version_source = sources["ai_studio"]
        with self.assertRaises(SystemExit) as caught:
            generator.check_module_version_support({"ai-studio"}, args)
        self.assertIn("got v2.2.2 (the newest stable tag on Docker Hub)", str(caught.exception))
        # No tag at all, or no readable Docker Hub, still falls back to the known-good value.
        versions, sources, _ = self.resolve_with(self.both(["v2.3.0-unstable.21"]))
        self.assertEqual((versions["ai_studio"], sources["ai_studio"]), ("v2.3.0", "known-good fallback"))

    def test_pinned_readme_says_what_to_do_when_the_tag_is_not_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "pinned"
            result = run_generator(target, "--versions", "pinned")
            self.assertEqual(result.returncode, 0, result.stderr)
            readme = (target / "README.md").read_text()
            section = readme[readme.index("## Companion stack: Tyk AI Studio"):]
            self.assertIn("If `v2.3.0` is not on Docker Hub yet for both images", section)
            self.assertIn("`--ai-studio-version`", section)
            self.assertIn("`--versions latest`", section)
            self.assertNotRegex(section, r"(?i)release candidate|\brc\b|\brc\d")

class StubBackendTests(unittest.TestCase):
    """mock-llm/backend.py must read chunked bodies and answer stream:true with SSE.

    AI Studio drives the upstream with stream: true even for a non-streaming
    caller, and the stub must also read a chunked request body.
    """

    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        backend = SCRIPTS.parent / "assets" / "starter" / "companion" / "ai-studio" / "mock-llm" / "backend.py"
        cls.process = subprocess.Popen(
            [sys.executable, str(backend)], env=dict(os.environ, BACKEND_NAME="alpha", PORT=str(cls.port)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)

    def post(self, payload: dict, chunked: bool) -> tuple[int, dict, bytes]:
        body = json.dumps(payload).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            if chunked:
                connection.putrequest("POST", "/v1/chat/completions")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Transfer-Encoding", "chunked")
                connection.endheaders()
                for start in range(0, len(body), 7):  # several chunks, not one
                    piece = body[start:start + 7]
                    connection.send(b"%x\r\n%s\r\n" % (len(piece), piece))
                connection.send(b"0\r\n\r\n")
            else:
                connection.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_chunked_stream_request_is_answered_with_sse(self):
        status, headers, raw = self.post({"model": "alpha-fast", "stream": True, "messages": [{"role": "user", "content": "hi"}]}, chunked=True)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/event-stream"))
        self.assertEqual(headers["x-backend-name"], "alpha")
        events = [line[len("data: "):] for line in raw.decode().splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1], "[DONE]")
        chunks = [json.loads(event) for event in events[:-1]]
        text = "".join(choice["delta"].get("content", "") for chunk in chunks for choice in chunk.get("choices", []))
        self.assertEqual(text, "SERVED-BY=alpha MODEL=alpha-fast")
        self.assertTrue(any(chunk.get("usage") for chunk in chunks))

    def test_content_length_request_is_answered_with_json(self):
        status, headers, raw = self.post({"model": "alpha-fast", "messages": [{"role": "user", "content": "hi"}]}, chunked=False)
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertEqual(payload["choices"][0]["message"]["content"], "SERVED-BY=alpha MODEL=alpha-fast")
        self.assertEqual(payload["model"], "alpha-fast")

    def test_chunked_non_stream_request_keeps_the_connection_usable(self):
        # A chunked body left on the socket used to be parsed as the next request
        # line, so the SECOND request on a keep-alive connection is the one that
        # broke ("400 Bad request syntax ('8a')"). Send two on one connection.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            for model in ("beta-deep", "alpha-fast"):
                body = json.dumps({"model": model, "messages": []}).encode()
                connection.putrequest("POST", "/v1/chat/completions")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Transfer-Encoding", "chunked")
                connection.endheaders()
                for start in range(0, len(body), 5):
                    piece = body[start:start + 5]
                    connection.send(b"%x\r\n%s\r\n" % (len(piece), piece))
                connection.send(b"0\r\n\r\n")
                response = connection.getresponse()
                raw = response.read()
                self.assertEqual(response.status, 200, raw)
                self.assertEqual(json.loads(raw)["model"], model)
                if model == "beta-deep":
                    first_socket = connection.sock
                    self.assertIsNotNone(first_socket, "the server closed the keep-alive connection")
            self.assertIs(connection.sock, first_socket, "the second request did not reuse the connection")
        finally:
            connection.close()

    def test_sigterm_stops_the_stub_at_once(self):
        # It runs as PID 1 in its container; without a handler every stop waits 10 s.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        backend = SCRIPTS.parent / "assets" / "starter" / "companion" / "ai-studio" / "mock-llm" / "backend.py"
        process = subprocess.Popen([sys.executable, str(backend)], env=dict(os.environ, BACKEND_NAME="beta", PORT=str(port)),
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            self.assertIn("listening", process.stdout.readline())
            # An idle keep-alive client must not hold the shutdown up either.
            idle = socket.create_connection(("127.0.0.1", port), timeout=2)
            started = time.monotonic()
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=3), 0)
            self.assertLess(time.monotonic() - started, 3)
            idle.close()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
