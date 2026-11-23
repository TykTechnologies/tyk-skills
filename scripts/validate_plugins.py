#!/usr/bin/env python3
"""
Validate every plugin in this repository against Agent Plugins 1.0.0.

Checks the portable contract only - the things a conformant client will reject or
skip a plugin for - and, for a plugin at the repository root, that the native
Claude Code and Codex manifests describe the same plugin. Run it before committing
a new or changed plugin:

    python3 scripts/validate_plugins.py            # all plugins
    python3 scripts/validate_plugins.py <plugin-dir>   # one plugin in a multi-plugin repo

Spec: https://agent-plugins.org  |  Skills: https://agentskills.io/specification
Deliberately dependency-free so it runs anywhere, including CI.
"""
import json
import os
import re
import sys

SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MANIFEST_FIELDS = {"$schema", "name", "version", "description", "author",
                   "homepage", "repository", "license", "keywords", "extensions"}
AUTHOR_FIELDS = {"name", "email", "url"}
PLUGIN_NAME = re.compile(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
SKILL_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
SKILL_DIRS = {"SKILL.md", "scripts", "references", "assets"}

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What `claude plugin install` and `codex plugin add` actually read, beside the portable
# plugin.json. Each is optional for a plugin in a multi-plugin repository, and required
# for a plugin at the repository root, which is how this repository ships.
NATIVE = {
    "Claude Code plugin": ".claude-plugin/plugin.json",
    "Claude Code marketplace": ".claude-plugin/marketplace.json",
    "Codex plugin": ".codex-plugin/plugin.json",
    "Codex marketplace": ".agents/plugins/marketplace.json",
}


class Report:
    def __init__(self, label):
        self.label = label
        self.rows = []

    def check(self, ok, rule, detail=""):
        self.rows.append(("ok" if ok else "FAIL", rule, detail))
        return bool(ok)

    def note(self, rule):
        """Informational only - never affects the exit status."""
        self.rows.append(("note", rule, ""))

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    @property
    def checks(self):
        return [r for r in self.rows if r[0] != "note"]

    def print(self):
        print(f"\n{self.label}")
        for status, rule, detail in self.rows:
            mark = {"ok": "  ok  ", "FAIL": "  FAIL", "note": "  --  "}[status]
            print(f"{mark} {rule}" + (f"   [{detail}]" if detail and status == "FAIL" else ""))


def frontmatter(path):
    m = re.match(r"^---\n(.*?)\n---\n", open(path, encoding="utf-8").read(), re.S)
    if not m:
        return None
    body = m.group(1)
    out = {}
    for key in ("name", "description", "license", "compatibility", "allowed-tools"):
        f = re.search(rf"^{key}:\s*(.+?)(?=\n[a-z-]+:|\Z)", body, re.M | re.S)
        if f:
            out[key] = f.group(1).strip()
    return out


def validate_plugin(path):
    r = Report(f"plugin: {os.path.relpath(path, REPO)}")

    manifest = os.path.join(path, "plugin.json")
    if not r.check(os.path.isfile(manifest), "§4.1 manifest exists at plugin.json"):
        return r
    try:
        m = json.load(open(manifest, encoding="utf-8"))
    except json.JSONDecodeError as e:
        r.check(False, "§5.2 manifest is valid JSON", str(e))
        return r

    r.check(isinstance(m, dict), "§5.2 manifest is a top-level object")
    unknown = set(m) - MANIFEST_FIELDS
    r.check(not unknown, "§5.2 manifest schema is closed", f"unknown: {sorted(unknown)}")
    r.check(m.get("$schema") == SCHEMA_ID, "§5.2 $schema is the canonical 1.0.0 id",
            repr(m.get("$schema")))
    name = m.get("name")
    r.check(isinstance(name, str) and 1 <= len(name) <= 64,
            "§5.3 name present and 1-64 chars", repr(name))
    r.check(isinstance(name, str) and bool(PLUGIN_NAME.fullmatch(name)),
            "§5.5 name matches the required pattern", repr(name))
    if "author" in m:
        r.check(isinstance(m["author"], dict) and set(m["author"]) <= AUTHOR_FIELDS,
                "§5.4 author has only name/email/url")
    if "keywords" in m:
        r.check(isinstance(m["keywords"], list)
                and all(isinstance(k, str) for k in m["keywords"]),
                "§5.4 keywords is an array of strings")
    if "extensions" in m:
        r.check(isinstance(m["extensions"], dict)
                and all(isinstance(v, dict) for v in m["extensions"].values()),
                "§5.6 extensions maps namespaces to objects")

    # §4.1.3 - nothing in the package may resolve outside the plugin root
    root = os.path.realpath(path)
    escaped = [os.path.relpath(p, path)
               for p in (os.path.join(dp, f)
                         for dp, _, fs in os.walk(path) for f in fs)
               if not os.path.realpath(p).startswith(root + os.sep)]
    r.check(not escaped, "§4.1 all package paths stay within the plugin root",
            str(escaped[:3]))

    skills_dir = os.path.join(path, "skills")
    mcp = os.path.join(path, "mcp.json")
    if os.path.exists(skills_dir):
        r.check(os.path.isdir(skills_dir), "§6.2 skills/ is a directory")
    if os.path.exists(mcp):
        r.check(os.path.isfile(mcp), "§6.2 mcp.json is a regular file")
        try:
            json.load(open(mcp, encoding="utf-8"))
            r.check(True, "§7.2 mcp.json is valid JSON")
        except json.JSONDecodeError as e:
            r.check(False, "§7.2 mcp.json is valid JSON", str(e))

    if os.path.isdir(skills_dir):
        # §7.1 - immediate children only, no recursive search
        skills = sorted(d for d in os.listdir(skills_dir)
                        if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md")))
        r.check(skills, "§7.1 skills/ has at least one skill with SKILL.md")
        for s in skills:
            sp = os.path.join(skills_dir, s)
            fm = frontmatter(os.path.join(sp, "SKILL.md"))
            if fm is None:
                r.check(False, f"[{s}] SKILL.md has YAML frontmatter")
                continue
            r.check(True, f"[{s}] SKILL.md has YAML frontmatter")
            r.check(fm.get("name") == s, f"[{s}] name matches its directory",
                    f"{fm.get('name')!r} vs {s!r}")
            r.check(bool(SKILL_NAME.fullmatch(fm.get("name", ""))) and len(fm["name"]) <= 64,
                    f"[{s}] name is lowercase/hyphenated, <=64 chars")
            desc = fm.get("description", "")
            r.check(0 < len(desc) <= 1024, f"[{s}] description is 1-1024 chars",
                    f"{len(desc)} chars")
            if "compatibility" in fm:
                r.check(len(fm["compatibility"]) <= 500,
                        f"[{s}] compatibility is <=500 chars")
            # A skill directory MAY contain any files beyond SKILL.md - scripts/,
            # references/ and assets/ are conventions, not requirements. Report
            # anything else as information so it is visible, never as a failure.
            extra = sorted(set(os.listdir(sp)) - SKILL_DIRS)
            if extra:
                r.note(f"[{s}] extra directories (allowed, not auto-loaded): "
                       f"{', '.join(extra)}")
    if os.path.realpath(path) == os.path.realpath(REPO):
        validate_native_packaging(r, path, m)
    return r


def validate_native_packaging(r, path, portable):
    """The neutral manifest is the contract; the native manifests are what
    `claude plugin install` and `codex plugin add` actually read. They must
    describe the same plugin and load the same skills."""
    name, version = portable.get("name"), portable.get("version")
    loaded = {}
    for label, rel in NATIVE.items():
        full = os.path.join(path, rel)
        if not r.check(os.path.isfile(full), f"{label} manifest exists at {rel}"):
            continue
        try:
            with open(full, encoding="utf-8") as f:
                loaded[label] = json.load(f)
        except ValueError as e:
            r.check(False, f"{label} manifest is valid JSON", str(e))
            continue
        r.check(isinstance(loaded[label], dict), f"{label} manifest is a top-level object")
    loaded = {k: v for k, v in loaded.items() if isinstance(v, dict)}
    for label in ("Claude Code plugin", "Codex plugin"):
        m = loaded.get(label)
        if m is None:
            continue
        r.check(m.get("name") == name, f"{label} name matches plugin.json",
                f"{m.get('name')!r} != {name!r}")
        r.check(m.get("version") == version, f"{label} version matches plugin.json",
                f"{m.get('version')!r} != {version!r}")
        r.check(m.get("skills") == "./skills/", f"{label} loads skills from ./skills/",
                repr(m.get("skills")))
    for label in ("Claude Code marketplace", "Codex marketplace"):
        m = loaded.get(label)
        if m is None:
            continue
        entries = m.get("plugins") or []
        r.check(len(entries) == 1 and entries[0].get("name") == name,
                f"{label} lists exactly this plugin", repr([e.get("name") for e in entries]))
        src = entries[0].get("source") if entries else None
        root = src == "./" or (isinstance(src, dict) and src.get("path") == "./")
        r.check(root, f"{label} entry points at the repository root", repr(src))
    # plugin.json is the single source of the version, the description and the listing
    # links; scripts/sync_manifests.py copies them into the native manifests.
    if len(loaded) == len(NATIVE):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import sync_manifests

        try:
            drift = sync_manifests.differences()
        except (KeyError, ValueError, OSError) as e:
            drift = [f"cannot compare manifests: {e}"]
        r.check(not drift, "every manifest agrees with plugin.json (version, description, links)",
                "; ".join(drift[:3]) + ("; run python3 scripts/sync_manifests.py" if drift else ""))
    claude_mp = loaded.get("Claude Code marketplace")
    if claude_mp and claude_mp.get("plugins"):
        entry_version = claude_mp["plugins"][0].get("version")
        r.check(entry_version == version, "Claude Code marketplace entry version matches plugin.json",
                f"{entry_version!r} != {version!r}")
        metadata_version = (claude_mp.get("metadata") or {}).get("version", version)
        r.check(metadata_version == version, "Claude Code marketplace metadata version matches plugin.json",
                f"{metadata_version!r} != {version!r}")


def discover(wanted):
    """A repository either *is* one plugin, or *contains* several.

    When plugin.json sits at the repository root the root is the plugin root, and
    scanning subdirectories would find nothing. Check the root first, then fall
    back to immediate children for a multi-plugin repository.
    """
    if os.path.isfile(os.path.join(REPO, "plugin.json")):
        return [REPO]
    return sorted(os.path.join(REPO, d) for d in os.listdir(REPO)
                  if os.path.isfile(os.path.join(REPO, d, "plugin.json"))
                  and (not wanted or d in wanted))


def main():
    wanted = sys.argv[1:]
    plugins = discover(wanted)
    if not plugins:
        print("no plugins found" + (f" matching {wanted}" if wanted else ""))
        return 1

    reports = [validate_plugin(p) for p in plugins]
    for rep in reports:
        rep.print()
    total = sum(len(r.checks) for r in reports)
    bad = sum(len(r.failed) for r in reports)
    print(f"\n{total - bad}/{total} checks passed across {len(reports)} plugin(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
