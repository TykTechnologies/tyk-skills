#!/usr/bin/env python3
"""Validate repository-level contracts without third-party Python packages."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "tyk-demo-builder",
    "tyk-excalidraw-diagrams",
    "tyk-infra-sizing",
    "tyk-facts",
    "tyk-poc-runbook",
    "tyk-migration-planner",
    "tyk-developer-portal-branding",
    "tyk-rate-limits-keys-policies",
    "tyk-gitops-sync-operator",
    "tyk-api-definitions-routing",
    "tyk-mcp-gateway",
    "tyk-ai-gateway",
    "tyk-authentication",
    "tyk-developer-portal-setup",
    "tyk-observability",
    "tyk-troubleshooting",
    "tyk-custom-plugins",
    "tyk-websockets-streams-webhooks",
    "tyk-api-governance",
    "tyk-upgrades",
    "tyk-deployment-planner",
    "tyk-deployment-runner",
    "tyk-production-readiness",
)
SKILLS = {name: ROOT / "skills" / name for name in SKILL_NAMES}
BUILDER = SKILLS["tyk-demo-builder"]
SIZING = SKILLS["tyk-infra-sizing"]

# Skill names a SKILL.md may mention that do not ship in this repository: the optional
# document theme the sizing pack uses when it is installed. The tyk-install repository is a
# deployment repository the deployment skills clone, not a skill.
EXTERNAL_SKILLS = {"tyk-" + "docx-theme"}

SKIPPED_DIRECTORIES = {".git", ".runtime", "__pycache__"}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def repository_files(root: Path) -> list[Path]:
    """Every file in this checkout, leaving out nested git checkouts and caches.

    Agent worktrees (for example under .claude/worktrees/) are complete checkouts of another
    branch, carrying their own copy of this validator and any work in progress. They are not
    part of this checkout, so scanning them reports someone else's branch as this one's residue.
    A nested checkout is any directory below the root holding a .git file or directory.
    """
    files = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in SKIPPED_DIRECTORIES and not os.path.lexists(os.path.join(directory, name, ".git"))
        )
        # A linked git worktree has a .git file (a pointer to its git directory), not a directory.
        files.extend(Path(directory, name) for name in sorted(names) if name != ".git")
    return files


# Residue that must never reach the public tree. Every term is assembled from parts so this
# file does not itself carry the names it forbids, and each pattern is narrow enough that
# ordinary prose ("internal network", "assurance", "API promotion between environments",
# "slack capacity") does not trip it.
def _term(*parts: str, word: bool = False, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    text = re.escape("".join(parts))
    return re.compile(rf"\b{text}\b" if word else text, flags)


RESIDUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Internal workspace and team vocabulary.
    ("internal workspace", _term("tyk-", "commercial")),
    ("internal workspace", _term("TYK_", "COMMERCIAL_HOME")),
    ("internal workspace", _term("pre", "sales")),
    ("internal workspace", _term("pre-", "sales")),
    ("internal workspace", _term("post-", "sales")),
    ("internal workspace", re.compile("account" + r"\s+" + "team", re.IGNORECASE)),
    ("internal workspace", _term("clarifications-", "required")),
    ("internal workspace", re.compile("clarification" + r"\s+" + "questions", re.IGNORECASE)),
    # Private repositories and internal names.
    ("private repository", _term("tyk-", "analytics")),
    ("private repository", _term("tyk-", "sink", word=True)),
    ("private repository", re.compile(r"\btyk-[a-z0-9-]+-" + "internal" + r"\b", re.IGNORECASE)),
    # Not an email local part such as portal-admin@example.invalid.
    ("private repository", re.compile(re.escape("portal-" + "admin") + "(?!@)", re.IGNORECASE)),
    ("private repository", _term("ai-studio-", "enterprise")),
    ("internal name", _term("mid", "sommar")),
    ("internal name", _term("tyk-", "planner")),
    ("internal name", _term("plan_", "deployment")),
    ("internal name", _term("planner_", "golden")),
    ("internal name", _term("tyk-", "ww", word=True)),
    ("internal name", _term("PLANNER_", "PARITY")),
    ("internal name", _term("tyk-docs-", "hugo")),
    # Internal skills and workflows.
    ("internal skill", _term("tyk-capability-", "response-doc")),
    ("internal workflow", _term("connectors", ".md")),
    ("internal workflow", _term("learning-and-", "promotion")),
    ("internal workflow", _term("controlled-", "learning")),
    ("internal workflow", re.compile(r"\bpromot\w*\b[^.\n]{0,40}\brecipes?\b|\brecipes?\b[^.\n]{0,40}\bpromot\w*", re.IGNORECASE)),
    ("internal integration", _term("Gran", "ola", word=True)),
    ("internal integration", _term("Hub", "Spot", word=True)),
    ("internal integration", _term("Conf", "luence", word=True)),
    ("internal integration", _term("Ji", "ra", word=True)),
    ("internal integration", _term("Sl", "ack", word=True, flags=0)),
    ("internal integration", re.compile("Google" + r"\s+" + "(?:Drive|Calendar)", re.IGNORECASE)),
    ("internal integration", _term("Gm", "ail", word=True)),
    # People, tickets, attribution and machine paths.
    ("person or sample organisation", _term("Bank", "ABC")),
    ("person", _term("moham", "med")),
    ("ticket id", re.compile(r"\b" + "TT" + r"-\d+\b")),
    ("commit attribution", _term("Co-", "Authored-By")),
    ("absolute user path", re.compile("/" + "Users/")),
    ("file URL", re.compile("file" + ":///")),
    # Secrets.
    ("JWT-like token", re.compile(r"eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}")),
    ("private key", re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?" + "PRIVATE KEY")),
    (
        "non-empty Tyk licence",
        re.compile(r"(?m)^(?:export[ \t]+)?(?:TYK_LICENSE_KEY|TYK_DB_LICENSEKEY|PORTAL_LICENSEKEY)[ \t]*=[ \t]*(?!\$\{|__)[^\s#]+$"),
    ),
    # The document theme is an optional sibling of the sizing skill and nowhere else.
    ("document theme outside the sizing skill", _term("tyk-", "docx-theme")),
]

# label -> repository-relative prefixes where that label is allowed.
RESIDUE_ALLOWED = {"document theme outside the sizing skill": ("skills/tyk-infra-sizing/",)}

# Real organisation names are never written into this public repository, not even split
# into parts. Keep them in a file outside the checkout, one per line, and point this at it.
EXTRA_TERMS_VARIABLE = "TYK_SKILLS_RESIDUE_TERMS"


def extra_residue_patterns() -> list[tuple[str, re.Pattern[str]]]:
    location = os.environ.get(EXTRA_TERMS_VARIABLE)
    if not location:
        return []
    patterns = []
    for line in Path(location).expanduser().read_text().splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            patterns.append(("private term", re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)))
    return patterns


def find_residue(
    text: str, relative_path: str, patterns: list[tuple[str, re.Pattern[str]]] | None = None
) -> list[tuple[int, str, str]]:
    """Return (line number, label, matched text) for every residue hit in text."""
    hits = []
    for label, pattern in RESIDUE_PATTERNS if patterns is None else patterns:
        if relative_path.startswith(RESIDUE_ALLOWED.get(label, ())):
            continue
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append((line, label, match.group(0)))
    return sorted(hits)


def read_text_file(path: Path) -> str | None:
    """The file as text, or None when it is binary."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_residue(errors: list[str]) -> None:
    patterns = RESIDUE_PATTERNS + extra_residue_patterns()
    for path in repository_files(ROOT):
        if not path.is_file() or path.is_symlink():
            continue
        text = read_text_file(path)
        if text is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for line, label, matched in find_residue(text, relative, patterns):
            errors.append(f"{label} in {relative}:{line}: {matched!r}")


# Installed skills never need Tyk source. Whoever installs the plugin verifies on their own
# running stack and against the public documentation, so no skill may tell its reader to clone,
# fetch or read a Tyk repository, depend on a local source registry, or identify private code.
# A link to a public Tyk GitHub file at a release tag stays allowed as a citation: these patterns
# match instructions and the retired machinery, not URLs.
SOURCE_INSTRUCTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "source registry or helper",
        re.compile(
            r"tyk_" + r"sources|setup-" + r"sources|tyk-" + r"sources|TYK_" + r"SOURCES_\w+"
            r"|source-" + r"verification\.md|source\s+" + r"preflight",
            re.IGNORECASE,
        ),
    ),
    ("retired evidence label", re.compile(r"docs-source-" + r"verified", re.IGNORECASE)),
    (
        "instruction to clone a Tyk repository",
        re.compile(
            # The two plugins are installed from a clone; Tyk product source never is.
            r"\bclon(?:e|es|ing)\b[^\n]{0,80}?TykTechnologies/(?!(?:tyk-install|tyk-skills)\b)[\w.-]+"
            r"|\bclon(?:e|es|ing)\b[^.\n]{0,40}?\b(?:Tyk|tyk-docs|tyk-pump|ai-studio)\b[\w-]*\s+"
            r"(?:repo|repos|repository|repositories|source|codebase)",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction to read Tyk source",
        re.compile(
            r"\b(?:read|grep|search|browse|inspect|consult|fetch)\w*\b[^.\n]{0,40}?"
            r"\b(?:Tyk|Gateway|Pump|component|public|the|its)\s+source(?:\s+code)?\b"
            r"(?!\s+(?:of\s+truth|director|REST|API|file|path|spec|OAS|brand|requirement))",
            re.IGNORECASE,
        ),
    ),
    (
        "private code identified",
        re.compile(
            r"\b(?:Dashboard|Developer\s+Portal|Portal|MDCB|Operator|Tyk\s+Sync|AI\s+Studio\s+Enterprise)"
            r"\s+(?:source|code)(?:\s+code)?\s+(?:at|in|for)\b",
            re.IGNORECASE,
        ),
    ),
]

# Unit tests plant the forbidden phrases on purpose, and binary or vendored files carry no prose.
SOURCE_SCAN_SKIPPED_SUFFIXES = {".sum", ".mod", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf"}


def find_source_instructions(text: str) -> list[tuple[int, str, str]]:
    """Return (line number, label, matched text) for every source instruction in text."""
    hits = []
    for label, pattern in SOURCE_INSTRUCTION_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((text.count("\n", 0, match.start()) + 1, label, match.group(0)))
    return sorted(hits)


def check_source_instructions(errors: list[str]) -> None:
    for path in repository_files(ROOT / "skills"):
        if path.name.startswith("test_") or path.suffix.lower() in SOURCE_SCAN_SKIPPED_SUFFIXES:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        text = read_text_file(path)
        if text is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for line, label, matched in find_source_instructions(text):
            errors.append(f"{label} in {relative}:{line}: {matched!r}; verify live or cite a public page instead")


# Skill text states facts and links the public page that backs them; it never narrates how a
# fact was checked. Maintainers still check every statement before it goes in, but the evidence
# labels and run captions stay out of the skills. "Last verified:" header lines are maintenance
# metadata and do not match these patterns.
EVIDENCE_NARRATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("evidence label", re.compile(r"\b(?:live|docs|source)-verified\b", re.IGNORECASE)),
    ("live-run caption", re.compile(r"\(live\b", re.IGNORECASE)),
    ("live-run narration", re.compile(r"\bproved\s+live\b", re.IGNORECASE)),
]

# Binary or archived files carry no prose.
NARRATION_SCAN_SKIPPED_SUFFIXES = {".zip", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".docx"}


def find_evidence_narration(text: str) -> list[tuple[int, str, str]]:
    """Return (line number, label, matched text) for every evidence label or run caption in text."""
    hits = []
    for label, pattern in EVIDENCE_NARRATION_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((text.count("\n", 0, match.start()) + 1, label, match.group(0)))
    return sorted(hits)


def check_evidence_narration(errors: list[str]) -> None:
    for path in repository_files(ROOT / "skills"):
        if path.suffix.lower() in NARRATION_SCAN_SKIPPED_SUFFIXES:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        text = read_text_file(path)
        if text is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for line, label, matched in find_evidence_narration(text):
            errors.append(
                f"{label} in {relative}:{line}: {matched!r}; state the fact plainly and link the public page, "
                "without saying how it was checked"
            )


# The issue forms CONTRIBUTING.md and the README point people at.
ISSUE_FORMS = ("wrong-fact", "skill-triggering", "script-or-recipe-fails", "new-skill")


def check_changelog(errors: list[str]) -> None:
    """The changelog has an entry for the version every manifest carries."""
    changelog = ROOT / "CHANGELOG.md"
    manifest = ROOT / "plugin.json"
    if not (changelog.is_file() and manifest.is_file()):
        return
    try:
        version = json.loads(manifest.read_text()).get("version")
    except ValueError:
        return  # validate_plugins.py reports the broken manifest
    require(
        re.search(rf"^## \[{re.escape(str(version))}\]", changelog.read_text(), re.MULTILINE) is not None,
        f"CHANGELOG.md has no '## [{version}]' entry for the version in plugin.json",
        errors,
    )


def frontmatter_name(text: str) -> str | None:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return None
    name = re.search(r"^name:\s*(.+?)\s*$", match.group(1), re.MULTILINE)
    return name.group(1).strip("'\"") if name else None


def frontmatter_description(text: str) -> str:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return ""
    description = re.search(r"^description:(.*?)(?=^[a-z-]+:|\Z)", match.group(1), re.MULTILINE | re.DOTALL)
    return description.group(1) if description else ""


# Sibling references a SKILL.md can make: the resolver call, and "the <name> skill".
SIBLING_REFERENCES = (
    re.compile(r"resolve_skill_dir\.py`?\s+`?(tyk-[a-z0-9-]*[a-z0-9])"),
    re.compile(r"\b(tyk-[a-z0-9-]*[a-z0-9])`?\s+skill\b"),
    re.compile(r"\bskill\s+`?(tyk-[a-z0-9-]*[a-z0-9])\b"),
)


def sibling_references(text: str) -> set[str]:
    return {match.group(1) for pattern in SIBLING_REFERENCES for match in pattern.finditer(text)}


def main() -> int:
    errors: list[str] = []
    required = [
        ROOT / "AGENTS.md",
        ROOT / ".claude" / "CLAUDE.md",
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / "SECURITY.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CHANGELOG.md",
        ROOT / ".github" / "pull_request_template.md",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
        *(ROOT / ".github" / "ISSUE_TEMPLATE" / f"{form}.yml" for form in ISSUE_FORMS),
        ROOT / ".github" / "workflows" / "validate.yml",
        ROOT / ".github" / "workflows" / "release.yml",
        ROOT / "plugin.json",
        ROOT / ".claude-plugin" / "plugin.json",
        ROOT / ".claude-plugin" / "marketplace.json",
        ROOT / ".codex-plugin" / "plugin.json",
        ROOT / ".agents" / "plugins" / "marketplace.json",
        ROOT / "requirements-diagrams.txt",
        ROOT / "scripts" / "build-demo.sh",
        ROOT / "scripts" / "install-skill.sh",
        ROOT / "scripts" / "sync_manifests.py",
        ROOT / "scripts" / "validate-repo.sh",
        ROOT / "scripts" / "validate_plugins.py",
        ROOT / "scripts" / "lib" / "engine.sh",
        ROOT / "scripts" / "lib" / "workspace.sh",
        BUILDER / "scripts" / "init_demo_bundle.py",
        BUILDER / "scripts" / "validate_bundle.py",
        BUILDER / "scripts" / "sanitize_bundle.py",
        BUILDER / "scripts" / "resolve_skill_dir.py",
        SIZING / "scripts" / "sizing_engine.py",
        SIZING / "scripts" / "build_sizing_pack.py",
        SIZING / "scripts" / "sizing_markdown.py",
        SIZING / "references" / "sizing-method.md",
        SKILLS["tyk-excalidraw-diagrams"] / "scripts" / "tyk_excalidraw.py",
        SKILLS["tyk-facts"] / "references" / "licensing-and-packaging.md",
        SKILLS["tyk-poc-runbook"] / "references" / "acceptance-criteria.md",
        SKILLS["tyk-migration-planner"] / "references" / "kong-to-tyk.md",
    ]
    for skill in SKILLS.values():
        required += [skill / "SKILL.md", skill / "agents" / "openai.yaml"]
    for path in required:
        require(path.is_file(), f"missing required file: {path.relative_to(ROOT)}", errors)

    present = sorted(path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md"))
    for name in present:
        require(name in SKILLS, f"skills/{name} is not a known skill; add it to SKILL_NAMES and install-skill.sh", errors)

    known_skills = set(present) | EXTERNAL_SKILLS
    for name in present:
        skill_md = ROOT / "skills" / name / "SKILL.md"
        text = skill_md.read_text()
        require(frontmatter_name(text) == name, f"skills/{name}/SKILL.md frontmatter name must be {name!r}", errors)
        description = frontmatter_description(text)
        require(
            "Codex" not in description and "OpenAI" not in description,
            f"skills/{name} description must stay vendor-neutral so every agent can trigger it",
            errors,
        )
        for sibling in sorted(sibling_references(text) - known_skills):
            errors.append(f"skills/{name}/SKILL.md names a sibling skill that does not exist: {sibling}")

        # Skill payloads are installed by symlink and read verbatim by every supported
        # agent, so they must not carry an engine-specific invocation sigil. The Codex
        # manifests under agents/ are the one place the $ form remains correct.
        for path in [skill_md, *sorted((skill_md.parent / "references").glob("*.md"))]:
            require(
                "$tyk-" not in path.read_text(),
                f"shared skill payload must not use the Codex-only $ sigil: {path.relative_to(ROOT)}",
                errors,
            )

    check_changelog(errors)

    for name in ("build-demo.sh", "install-skill.sh"):
        script = ROOT / "scripts" / name
        if script.is_file():
            text = script.read_text()
            for engine in ("codex", "claude"):
                require(engine in text, f"{name} must support the {engine} engine", errors)
    installer = ROOT / "scripts" / "install-skill.sh"
    if installer.is_file():
        text = installer.read_text()
        for name in SKILL_NAMES:
            require(f"  {name}\n" in text, f"install-skill.sh does not install {name}", errors)

    engine_lib = ROOT / "scripts" / "lib" / "engine.sh"
    if engine_lib.is_file():
        text = engine_lib.read_text()
        for function in ("engine_resolve", "engine_require_bin", "engine_skill_ref", "engine_skills_root", "engine_launch"):
            require(f"{function}()" in text, f"engine library is missing {function}", errors)
    workspace_lib = ROOT / "scripts" / "lib" / "workspace.sh"
    if workspace_lib.is_file():
        text = workspace_lib.read_text()
        for function in ("workspace_root", "workspace_project", "workspace_path"):
            require(f"{function}()" in text, f"workspace library is missing {function}", errors)

    check_residue(errors)
    check_source_instructions(errors)
    check_evidence_narration(errors)
    check_declared_dependencies(errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Repository contracts valid")
    return 0


# Import name -> the distribution that provides it. A skill runs inside whatever
# environment the agent happens to be in, so an undeclared third-party import is a
# runtime failure on someone else's machine that passes silently on a developer's.
DISTRIBUTION_FOR_IMPORT = {"docx": "python-docx", "lxml": "lxml", "PIL": "pillow", "yaml": "pyyaml"}

# Bundled libraries another skill may import at runtime: module -> the skill that ships it.
CROSS_SKILL_IMPORTS = {"tyk_excalidraw": "tyk-excalidraw-diagrams", "resolve_skill_dir": "tyk-demo-builder"}


def declared_distributions() -> set[str]:
    declared = set()
    for requirements in ROOT.glob("requirements*.txt"):
        for line in requirements.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                declared.add(re.split(r"[=<>!~\[]", line, maxsplit=1)[0].strip().lower())
    return declared


def guarded_imports(tree) -> set[int]:
    """Imports inside a try block that handles ImportError are optional by design.

    validate_bundle.py imports yaml this way and falls back to a pure-Python
    parser, so the package is genuinely not required and must not be declared.
    """
    import ast

    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        handles_import_error = any(
            handler.type is None
            or any(
                name.id in {"ImportError", "ModuleNotFoundError", "Exception"}
                for name in ast.walk(handler.type)
                if isinstance(name, ast.Name)
            )
            for handler in node.handlers
        )
        if not handles_import_error:
            continue
        for statement in node.body:
            for child in ast.walk(statement):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    guarded.add(id(child))
    return guarded


def check_declared_dependencies(errors: list[str]) -> None:
    import ast

    declared = declared_distributions()
    for script in sorted(ROOT.glob("skills/*/scripts/*.py")):
        siblings = {path.stem for path in script.parent.glob("*.py")}
        # A script may import a sibling skill's library only when it names that skill, which
        # is how it locates the library at runtime (resolve_skill_dir or the repo layout).
        source = script.read_text()
        siblings |= {module for module, skill in CROSS_SKILL_IMPORTS.items() if skill in source}
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:  # compileall reports the detail; do not mask it
            errors.append(f"cannot parse {script.relative_to(ROOT)}: {exc}")
            continue
        optional = guarded_imports(tree)
        for node in ast.walk(tree):
            if id(node) in optional:
                continue
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                modules = [node.module.split(".")[0]]
            else:
                continue
            for module in modules:
                if module in sys.stdlib_module_names or module in siblings:
                    continue
                distribution = DISTRIBUTION_FOR_IMPORT.get(module)
                require(
                    distribution is not None,
                    f"{script.relative_to(ROOT)} imports {module!r}, which maps to no "
                    "known distribution; add it to DISTRIBUTION_FOR_IMPORT",
                    errors,
                )
                if distribution is not None:
                    require(
                        distribution in declared,
                        f"{script.relative_to(ROOT)} imports {module!r} but "
                        f"{distribution!r} is in no requirements file",
                        errors,
                    )


if __name__ == "__main__":
    raise SystemExit(main())
