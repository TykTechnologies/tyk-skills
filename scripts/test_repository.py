"""Unit tests for the repository validator, the workspace helper and the demo runner.

Residue samples are assembled from parts so this file never carries the terms the
validator forbids.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import validate_repository as V  # noqa: E402


def labels(text: str, path: str = "docs/example.md") -> list[str]:
    return [label for _, label, _ in V.find_residue(text, path)]


class ResidueTests(unittest.TestCase):
    def assert_flagged(self, text: str, label: str, path: str = "docs/example.md") -> None:
        self.assertIn(label, labels(text, path), f"{text!r} should be flagged as {label}")

    def assert_clean(self, text: str, path: str = "docs/example.md") -> None:
        self.assertEqual(V.find_residue(text, path), [], f"{text!r} should not be flagged")

    def test_internal_workspace_terms(self):
        for text in (
            "~/tyk-" + "commercial/acme",
            "export TYK_" + "COMMERCIAL_HOME=x",
            "the " + "Pre" + "sales team",
            "pre-" + "sales engineer",
            "post-" + "sales hand-off",
            "ask the account" + "  team",
            "clarifications-" + "required.md",
            "send the clarification" + " questions",
        ):
            self.assert_flagged(text, "internal workspace")

    def test_internal_repository_suffix_only(self):
        self.assert_flagged("clone tyk-operator-" + "internal", "private repository")
        self.assert_flagged("TYK-SYNC-" + "INTERNAL", "private repository")
        self.assert_flagged("tyk-" + "analytics-ui", "private repository")
        self.assert_flagged("tyk-" + "sink", "private repository")
        self.assert_flagged("the portal-" + "admin service", "private repository")
        self.assert_clean("PORTAL_ADMIN_EMAIL=portal-" + "admin@demo.invalid")
        self.assert_clean("the gateway sits on an internal network")
        self.assert_clean("internal-only listener")
        self.assert_clean("tyk-sinkhole")

    def test_internal_names(self):
        for text in ("mid" + "sommar", "tyk-" + "planner", "plan_" + "deployment()", "tyk-" + "ww repo", "tyk-docs-" + "hugo"):
            self.assert_flagged(text, "internal name")
        self.assert_clean("visit tyk-www today")
        self.assert_clean("planner output")

    def test_internal_workflow_and_integrations(self):
        self.assert_flagged("see " + "connectors" + ".md", "internal workflow")
        self.assert_flagged("pro" + "mote the new " + "rec" + "ipe", "internal workflow")
        self.assert_flagged("rec" + "ipe " + "pro" + "motion", "internal workflow")
        self.assert_flagged("Sl" + "ack channel", "internal integration")
        self.assert_flagged("a JI" + "RA ticket", "internal integration")
        self.assert_flagged("Google" + " Drive folder", "internal integration")
        self.assert_clean("API promotion between environments with Tyk Sync")
        self.assert_clean("keep 20% slack in the cluster")
        self.assert_clean("a Kafka connector for Streams")

    def test_people_tickets_and_paths(self):
        self.assert_flagged("Bank" + "ABC sizing", "person or sample organisation")
        self.assert_flagged("Moham" + "med wrote", "person")
        self.assert_flagged("fixed in " + "TT" + "-12345", "ticket id")
        self.assert_flagged("Co-" + "authored-by: someone", "commit attribution")
        self.assert_flagged("/" + "Users/someone/repo", "absolute user path")
        self.assert_clean("STT-12 is not a ticket")
        self.assert_clean("TTL-30 seconds")
        self.assert_clean("users/ folder")

    def test_secrets(self):
        self.assert_flagged("-----BEGIN " + "PRIVATE KEY-----", "private key")
        self.assert_flagged("TYK_LICENSE_KEY=" + "abc123", "non-empty Tyk licence")
        self.assert_clean("TYK_LICENSE_KEY=${TYK_LICENSE_KEY}")
        self.assert_clean("TYK_LICENSE_KEY=__REPLACE_ME__")
        self.assert_clean("TYK_LICENSE_KEY=\nTYK_PORTAL_LICENSE=")
        self.assert_flagged("export TYK_DB_LICENSEKEY=" + "abc", "non-empty Tyk licence")

    def test_document_theme_only_in_sizing_skill(self):
        theme = "tyk-" + "docx-theme"
        self.assert_clean(f"when a {theme} skill is installed", "skills/tyk-infra-sizing/SKILL.md")
        self.assert_flagged(f"use {theme}", "document theme outside the sizing skill", "skills/tyk-demo-builder/SKILL.md")
        self.assert_flagged(f"use {theme}", "document theme outside the sizing skill", "README.md")

    def test_reports_line_numbers(self):
        hits = V.find_residue("clean\nclean\nsee " + "tyk-" + "sink here\n", "x.md")
        self.assertEqual([(line, label) for line, label, _ in hits], [(3, "private repository")])

    def test_extra_terms_file(self):
        with tempfile.TemporaryDirectory() as temp:
            terms = Path(temp, "terms.txt")
            terms.write_text("# comment\nExample Org\n\n")
            previous = os.environ.get(V.EXTRA_TERMS_VARIABLE)
            os.environ[V.EXTRA_TERMS_VARIABLE] = str(terms)
            try:
                patterns = V.extra_residue_patterns()
            finally:
                if previous is None:
                    os.environ.pop(V.EXTRA_TERMS_VARIABLE)
                else:
                    os.environ[V.EXTRA_TERMS_VARIABLE] = previous
        self.assertEqual(len(patterns), 1)
        self.assertTrue(V.find_residue("demo for example org", "x.md", patterns))
        self.assertFalse(V.find_residue("demo for example organisation", "x.md", patterns))

    def test_validator_and_tests_are_clean(self):
        for path in (SCRIPTS / "validate_repository.py", Path(__file__).resolve()):
            self.assertEqual(V.find_residue(path.read_text(), path.relative_to(ROOT).as_posix()), [], path.name)


class RepositoryWalkTests(unittest.TestCase):
    def test_skips_nested_checkouts_and_caches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "kept").mkdir()
            (root / "kept" / "a.md").write_text("x")
            (root / "worktree" / "deep").mkdir(parents=True)
            (root / "worktree" / ".git").write_text("gitdir: elsewhere")
            (root / "worktree" / "deep" / "b.md").write_text("x")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "c.pyc").write_text("x")
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_text("x")
            found = {path.relative_to(root).as_posix() for path in V.repository_files(root)}
        self.assertEqual(found, {"kept/a.md"})

    def test_sibling_references(self):
        text = (
            "Run `python3 <dir>/scripts/resolve_skill_dir.py tyk-infra-sizing`.\n"
            "Use the `tyk-facts` skill, then the tyk-poc-runbook skill's checklist.\n"
            "Hand off to the tyk-install plugin. Read tyk-docs.\n"
            "Locate siblings with resolve_skill_dir.py in the builder.\n"
        )
        self.assertEqual(V.sibling_references(text), {"tyk-infra-sizing", "tyk-facts", "tyk-poc-runbook"})


class WorkspaceTests(unittest.TestCase):
    def run_workspace(self, command: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = {key: value for key, value in os.environ.items() if key != "TYK_WORKSPACE"}
        merged.update(env or {})
        return subprocess.run(
            ["bash", "-c", f'. "{SCRIPTS}/lib/workspace.sh"; {command}'],
            env=merged,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_project_names_are_lowercase_and_hyphenated(self):
        for raw, expected in (
            ("Example Organisation", "example-organisation"),
            ("  Payments, POC v2! ", "payments-poc-v2"),
            ("already-fine", "already-fine"),
        ):
            result = self.run_workspace(f'workspace_project "{raw}"')
            self.assertEqual(result.stdout.strip(), expected)

    def test_default_root_and_paths(self):
        result = self.run_workspace('workspace_path demos "My Project" first-demo', {"HOME": "/home/tester"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/home/tester/tyk-workspace/my-project/demos/first-demo")
        result = self.run_workspace('workspace_path sizing "My Project"', {"HOME": "/home/tester"})
        self.assertEqual(result.stdout.strip(), "/home/tester/tyk-workspace/my-project/sizing")

    def test_workspace_override(self):
        result = self.run_workspace('workspace_path docs Acme', {"TYK_WORKSPACE": "/srv/work"})
        self.assertEqual(result.stdout.strip(), "/srv/work/acme/docs")

    def test_unusable_project_name_fails(self):
        result = self.run_workspace('workspace_path demos "!!!" slug')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class BuildDemoTests(unittest.TestCase):
    def run_build(self, temp: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        bin_dir = temp / "bin"
        bin_dir.mkdir(exist_ok=True)
        stub = bin_dir / "claude"
        stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >"$AGENT_ARGS_LOG"\ncat >"$AGENT_PROMPT_LOG"\n')
        stub.chmod(0o755)
        merged = {key: value for key, value in os.environ.items() if key != "TYK_DEMO_ENGINE"}
        merged.update(
            {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "AGENT_ARGS_LOG": str(temp / "args.log"),
                "AGENT_PROMPT_LOG": str(temp / "prompt.log"),
                "TYK_WORKSPACE": str(temp / "workspace"),
            }
        )
        merged.update(env or {})
        return subprocess.run(
            [str(SCRIPTS / "build-demo.sh"), *args], cwd=temp, env=merged, capture_output=True, text=True, check=False
        )

    def test_prompt_targets_the_project_workspace_and_builder(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            requirements = temp / "requirements.md"
            requirements.write_text("JWT auth and rate limiting\n")
            result = self.run_build(
                temp,
                "--requirements", str(requirements),
                "--project", "Payments POC",
                "--slug", "jwt-demo",
                "--name", "JWT Demo",
                "--engine", "claude",
                "--non-interactive",
                "--no-install",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = (temp / "prompt.log").read_text()
            expected_output = (temp / "workspace" / "payments-poc" / "demos" / "jwt-demo").resolve()
        self.assertIn("Use /tyk-demo-builder", prompt)
        self.assertIn("evaluating Tyk", prompt)
        self.assertIn(f"Output directory: {expected_output}", prompt)
        lowered = prompt.lower()
        for word in ("customer", "promot", "connector", "capability", "docx"):
            self.assertNotIn(word, lowered)

    def test_refuses_output_inside_repository(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            requirements = temp / "requirements.md"
            requirements.write_text("x\n")
            result = self.run_build(
                temp,
                "--requirements", str(requirements),
                "--output", str(ROOT / "generated" / "demo"),
                "--slug", "inside-demo",
                "--name", "Inside",
                "--engine", "claude",
                "--no-install",
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside this plugin repository", result.stderr)
        self.assertFalse((ROOT / "generated").exists())

    def test_removed_options_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            for option in ("--client", "--capability-doc", "--doc-pdf", "--doc-title"):
                result = self.run_build(Path(raw), option, "x")
                self.assertEqual(result.returncode, 2, option)
                self.assertIn("Unknown option", result.stderr)

    def test_needs_output_or_project(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            requirements = temp / "requirements.md"
            requirements.write_text("x\n")
            result = self.run_build(temp, "--requirements", str(requirements), "--slug", "a-demo", "--name", "A")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--project", result.stderr)


class InstallerTests(unittest.TestCase):
    def test_help_lists_options_and_plugin_route(self):
        result = subprocess.run([str(SCRIPTS / "install-skill.sh"), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--engine", "--copy", "--replace", "plugin"):
            self.assertIn(option, result.stdout)

    def test_installer_offers_no_source_setup(self):
        text = (SCRIPTS / "install-skill.sh").read_text()
        self.assertEqual(V.find_source_instructions(text), [])
        self.assertNotIn("sources", text.lower().replace("bash_source", ""))


def source_labels(text: str) -> list[str]:
    return [label for _, label, _ in V.find_source_instructions(text)]


class SourceInstructionTests(unittest.TestCase):
    """Installed skills never send their reader to Tyk source; citations stay allowed."""

    def test_retired_machinery_is_flagged(self):
        for text in (
            "python3 scripts/tyk_" + "sources.py status",
            "run ./scripts/setup-" + "sources.sh first",
            "clones live in ~/tyk-" + "sources",
            "export TYK_" + "SOURCES_DIR=x",
            "see references/source-" + "verification.md",
            "run the Tyk source " + "preflight",
        ):
            self.assertIn("source registry or helper", source_labels(text), text)
        self.assertIn("retired evidence label", source_labels("(docs-source-" + "verified)"))

    def test_clone_instructions_are_flagged(self):
        for text in (
            "git clone https://github.com/TykTechnologies/tyk",
            "gh repo clone TykTechnologies/tyk-docs",
            "clone the Tyk repositories first",
            "cloning the tyk-docs repository takes minutes",
        ):
            self.assertIn("instruction to clone a Tyk repository", source_labels(text), text)
        # The plugins themselves are installed from a clone; that is not Tyk product source.
        for text in (
            "git clone https://github.com/TykTechnologies/tyk-install",
            "gh repo clone TykTechnologies/tyk-skills && cd tyk-skills",
            "github.com/huandu/go-clone v1.6.0",
        ):
            self.assertEqual(source_labels(text), [], text)

    def test_read_source_instructions_are_flagged(self):
        for text in (
            "read the source for the pinned version",
            "grep the Gateway source at v5.16.0",
            "Search the public source code at the matching tag",
            "inspect its source at the release tag",
        ):
            self.assertIn("instruction to read Tyk source", source_labels(text), text)
        for text in (
            "Treat the supplied requirements as the source of truth.",
            "Read the source REST API's definition first.",
            "Check a Tyk Sync source directory before it is promoted.",
            "(`gateway/api.go` at `v5.16.0`)",
            "https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/mw_jwt.go",
        ):
            self.assertEqual(source_labels(text), [], text)

    def test_private_code_identified(self):
        for text in ("verified against the Portal source at v1.19.0", "the Dashboard code in its handler"):
            self.assertIn("private code identified", source_labels(text), text)
        self.assertEqual(source_labels("Portal sources each product from a policy"), [])

    def test_shipped_builder_is_clean(self):
        builder = ROOT / "skills" / "tyk-demo-builder"
        for path in [builder / "SKILL.md", *sorted((builder / "references").glob("*.md"))]:
            self.assertEqual(V.find_source_instructions(path.read_text()), [], path.name)
        for removed in ("references/source-" + "verification.md", "scripts/tyk_" + "sources.py"):
            self.assertFalse((builder / removed).exists(), removed)
        self.assertFalse((SCRIPTS / ("setup-" + "sources.sh")).exists())


def narration_labels(text: str) -> list[str]:
    return [label for _, label, _ in V.find_evidence_narration(text)]


class EvidenceNarrationTests(unittest.TestCase):
    def test_evidence_labels_are_flagged(self):
        for text in (
            "a JWT API answers 403 (`live-verified`, Gateway 5.16.0, 2026-11-19)",
            "See the rate limit page (`docs-verified`).",
            "(`source-" + "verified`, `user/session.go` at `v5.16.0`)",
            "Live-Verified on 5.16.0",
        ):
            self.assertIn("evidence label", narration_labels(text), text)

    def test_live_run_captions_are_flagged(self):
        for text in (
            "the log shows the panic (live, 2026-11-19)",
            "## What arrived (live)",
            "| Request | Answer (live) |",
            "exit 0, then 2 (Live: a second run)",
        ):
            self.assertIn("live-run caption", narration_labels(text), text)
        self.assertIn("live-run narration", narration_labels("both proved live on 1.20.1"))
        self.assertIn("live-run narration", narration_labels("Proved  live with two Gateways"))

    def test_plain_facts_pass(self):
        for text in (
            "Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`)",
            'On Gateway 5.16.0, a JWT API whose policy has a quota answers `403 {"error": "Quota exceeded"}` while Redis is down.',
            "See https://tyk.io/docs/api-management/rate-limit",
            "Sign in through the Live Portal (the published site).",
            "Generated ids and secrets live in `.runtime/runtime.env`.",
            "Re-run the script; it converges.",
            "A requirement is marked `live` only with evidence from the deployed stack.",
            "(delivered live from the broker)",
        ):
            self.assertEqual(narration_labels(text), [], text)

    def test_check_reports_a_planted_label(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "skills" / "tyk-example" / "references" / "facts.md"
            reference.parent.mkdir(parents=True)
            reference.write_text("Quota answers 403 (`live-" + "verified`, 2026-11-19).\n")
            (root / "skills" / "Archive.zip").write_bytes(b"(live, binary)")
            original = V.ROOT
            V.ROOT = root
            try:
                errors: list[str] = []
                V.check_evidence_narration(errors)
            finally:
                V.ROOT = original
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("skills/tyk-example/references/facts.md:1", errors[0])

    def test_shipped_skills_are_clean(self):
        errors: list[str] = []
        V.check_evidence_narration(errors)
        self.assertEqual(errors, [])


class ManifestTests(unittest.TestCase):
    def copy_manifests(self, temp: Path) -> None:
        for relative in ("plugin.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json"):
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((ROOT / relative).read_text())

    def test_repository_manifests_agree(self):
        import sync_manifests

        self.assertEqual(sync_manifests.differences(), [])

    def test_drift_is_reported_and_synced(self):
        import json
        import sync_manifests

        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            self.copy_manifests(temp)
            source = json.loads((temp / "plugin.json").read_text())
            source["version"] = "9.9.9"
            source["description"] = "A changed description."
            (temp / "plugin.json").write_text(json.dumps(source))
            drift = sync_manifests.differences(temp)
            self.assertTrue(any("marketplace.json plugins.0.version" in line for line in drift), drift)
            self.assertTrue(any(".codex-plugin/plugin.json description" in line for line in drift), drift)
            sync_manifests.sync(temp)
            self.assertEqual(sync_manifests.differences(temp), [])
            codex = json.loads((temp / ".codex-plugin" / "plugin.json").read_text())
            self.assertEqual(codex["version"], "9.9.9")
            # Fields the sync does not own are left alone.
            self.assertIn("defaultPrompt", codex["interface"])

    def test_changelog_has_the_manifest_version(self):
        errors: list[str] = []
        V.check_changelog(errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
