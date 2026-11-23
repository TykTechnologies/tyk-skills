#!/usr/bin/env python3
"""Tests for the Streams cold-start retry, per-API detailed recording and visible text.

Found on Gateway 5.15.1 and AI Studio 2.2.2: the first Streams publish can answer 500 while the
stream starts, global detailed recording buffers every response body, and generated
descriptions and App names must not read like an exercise. Every bundle here uses
`--versions pinned`, so the suite needs no network.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent

# Words that must not show in a name, description or App name (see the skill's
# Visible Naming section).
FORBIDDEN = re.compile(r"\b(demo|rfi|proof|proves|proven|test|tested|mock|lab|sample|evidence)\b", re.I)


def generate(root: Path, slug: str, name: str, modules: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "init_demo_bundle.py"), "--output", str(root), "--slug", slug,
         "--name", name, "--modules", modules, "--versions", "pinned"],
        capture_output=True, text=True, check=True)


def visible_text(bundle: Path) -> list[tuple[str, str]]:
    """Names and descriptions a Dashboard, Portal or AI Studio page can show."""
    found = []
    for path in sorted(list((bundle / "apis").glob("*.json")) + list((bundle / "mcp").glob("*.json"))):
        doc = json.loads(path.read_text())
        info = doc.get("info", {})
        x_info = doc.get("x-tyk-api-gateway", {}).get("info", {})
        for value in (info.get("title"), info.get("description"), x_info.get("name")):
            if value:
                found.append((path.name, value))
    configure = bundle / "companion" / "ai-studio" / "configure-ai-studio.sh"
    if configure.is_file():
        text = configure.read_text()
        for m in re.finditer(r'^(?:APP_NAME|ZERO_APP_NAME|ROUTER_NAME)="\$\{\w+:-([^}]*)\}"', text, re.M):
            found.append((configure.name, m.group(1)))
        for m in re.finditer(r'ensure_app "\$\{\w+\}" "([^"]*)"', text):
            found.append((configure.name, m.group(1)))
    return found


class StreamsPublishRetryTests(unittest.TestCase):
    """The generated STREAM-001 publish is retried a bounded number of times."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.tmp.name) / "bundle"
        generate(cls.bundle, "acme-events", "Acme Events", "rest,streams")
        verify = (cls.bundle / "scripts" / "verify-demo.sh").read_text()
        start = verify.index("  publish_code=000")
        end = verify.index("Kafka event was not observed")
        end = verify.index("\n  fi\n", end) + len("\n  fi\n")
        cls.block = verify[start:end]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_block(self, codes: list[str], consumed: str = "evt-1") -> str:
        """Run the publish block with stubbed gateway, Kafka consumer, record and sleep."""
        script = f"""
set -u
CODES=({' '.join(codes)})
COUNTER="$(mktemp)"; echo 0 >"$COUNTER"
GATEWAY_URL=http://gw
event_id=evt-1
# gw_curl runs in a command substitution (a subshell), so the call count lives in a file
gw_curl() {{ local n; n="$(cat "$COUNTER")"; echo $((n + 1)) >"$COUNTER"
  local last=$(( ${{#CODES[@]}} - 1 )); local i=$(( n < last ? n : last )); printf '%s' "${{CODES[$i]}}"; }}
compose() {{ printf '%s' "{consumed}"; }}
record() {{ printf 'RECORD %s %s | %s\\n' "$1" "$2" "$3"; }}
sleep() {{ :; }}
{self.block}
printf 'CALLS %s\\n' "$(cat "$COUNTER")"; rm -f "$COUNTER"
"""
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout

    def test_cold_stream_500_then_202_passes_on_retry(self):
        out = self.run_block(["500", "202"])
        self.assertIn("RECORD STREAM-001 passed", out)
        self.assertIn("publish attempt 2", out)
        self.assertIn("CALLS 2", out)

    def test_first_publish_success_needs_no_retry(self):
        out = self.run_block(["202"])
        self.assertIn("RECORD STREAM-001 passed", out)
        self.assertIn("CALLS 1", out)

    def test_stream_that_never_starts_fails_after_bounded_attempts(self):
        out = self.run_block(["500"])
        self.assertIn("RECORD STREAM-001 failed", out)
        self.assertIn("HTTP 500 after 5 attempts", out)
        self.assertIn("CALLS 5", out)

    def test_published_but_unconsumed_event_still_fails(self):
        out = self.run_block(["202"], consumed="something-else")
        self.assertIn("RECORD STREAM-001 failed", out)
        self.assertIn("Kafka event was not observed", out)

    def test_single_unretried_publish_is_gone(self):
        verify = (self.bundle / "scripts" / "verify-demo.sh").read_text()
        self.assertNotIn('gw_curl -fsS "${GATEWAY_URL}/demo/acme-events/streams/events"', verify)


class DetailedRecordingTests(unittest.TestCase):
    """Detail is recorded per API; the gateway-wide switch stays off so responses stream."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.tmp.name) / "bundle"
        generate(cls.bundle, "acme-detail", "Acme Detail", "rest,api-key,streams")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_gateway_wide_detailed_recording_is_off(self):
        env = (self.bundle / "config" / "gateway.env").read_text()
        self.assertIn("TYK_GW_ANALYTICSCONFIG_ENABLEDETAILEDRECORDING=false", env)
        self.assertNotIn("TYK_GW_ANALYTICSCONFIG_ENABLEDETAILEDRECORDING=true", env)

    def test_every_generated_api_records_detail_itself(self):
        apis = sorted((self.bundle / "apis").glob("*.oas.json"))
        self.assertTrue(apis)
        for path in apis:
            server = json.loads(path.read_text())["x-tyk-api-gateway"]["server"]
            self.assertEqual(server.get("detailedActivityLogs"), {"enabled": True}, path.name)


class VisibleTextTests(unittest.TestCase):
    """Generated names and descriptions never read like an exercise."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.tmp.name) / "bundle"
        generate(cls.bundle, "acme-text", "Acme Retail", "rest,api-key,streams,mcp,ai-studio")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_scanner_catches_a_forbidden_word(self):
        # negative control: the scanner itself must flag the wording this suite exists to stop
        self.assertTrue(FORBIDDEN.search("The same asset proves brokering an external MCP server"))
        self.assertTrue(FORBIDDEN.search("Acme Retail Demo App"))
        self.assertFalse(FORBIDDEN.search("Acme Retail Assistant App"))

    def test_api_mcp_and_app_text_is_clean(self):
        found = visible_text(self.bundle)
        self.assertTrue(any(source.endswith(".mcp.oas.json") for source, _ in found), "MCP descriptions scanned")
        self.assertTrue(any(source == "configure-ai-studio.sh" for source, _ in found), "AI Studio names scanned")
        offenders = [(s, v) for s, v in found if FORBIDDEN.search(v.replace("Acme Retail", ""))]
        self.assertEqual(offenders, [])

    def test_ai_studio_app_is_renamed_in_place_from_its_former_name(self):
        configure = (self.bundle / "companion" / "ai-studio" / "configure-ai-studio.sh").read_text()
        self.assertIn('APP_NAME="${APP_NAME:-Acme Retail Assistant App}"', configure)
        self.assertIn('LEGACY_APP_NAME="Acme Retail Demo App"', configure)
        body = configure[configure.index("ensure_app() {"):configure.index("app_secret() {")]
        adopt, create = body.index('"$6")'), body.index("api POST /api/v1/apps")
        self.assertLess(adopt, create, "a former-name App must be adopted before a new one is created")


if __name__ == "__main__":
    unittest.main()
