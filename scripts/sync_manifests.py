#!/usr/bin/env python3
"""Keep every plugin manifest in step with plugin.json.

plugin.json (the Agent Plugins manifest at the repository root) is the one place the
plugin's version and description are edited. This script copies them, and the shared
listing links below, into the native manifests that `claude plugin install` and
`codex plugin add` read:

    python3 scripts/sync_manifests.py           # write the native manifests
    python3 scripts/sync_manifests.py --check   # exit 1 and list every difference

validate_plugins.py runs the same comparison, so a release cannot ship with manifests
that disagree. Standard library only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin.json"

# Listing links. The Agent Plugins schema is closed and has no field for them, so they
# live here and are written to the manifests whose schema accepts them.
SUPPORT_URL = "https://github.com/TykTechnologies/tyk-skills/issues"
PRIVACY_URL = "https://tyk.io/privacy-policy/"
TERMS_URL = "https://tyk.io/terms-conditions/"
DOCUMENTATION_URL = "https://github.com/TykTechnologies/tyk-skills#readme"


def expected_fields(source: dict) -> dict[str, dict[tuple[str, ...], object]]:
    """manifest path -> {json path: value} that the manifest must carry."""
    version, description = source["version"], source["description"]
    homepage, repository = source["homepage"], source["repository"]
    return {
        ".claude-plugin/plugin.json": {
            ("version",): version,
            ("description",): description,
            ("homepage",): homepage,
            ("repository",): repository,
            ("license",): source["license"],
            ("supportUrl",): SUPPORT_URL,
            ("privacyPolicyUrl",): PRIVACY_URL,
            ("termsOfServiceUrl",): TERMS_URL,
            ("documentationUrl",): DOCUMENTATION_URL,
        },
        ".claude-plugin/marketplace.json": {
            ("metadata", "version"): version,
            ("metadata", "description"): description,
            ("plugins", 0, "version"): version,
            ("plugins", 0, "description"): description,
            ("plugins", 0, "homepage"): homepage,
            ("plugins", 0, "repository"): repository,
        },
        ".codex-plugin/plugin.json": {
            ("version",): version,
            ("description",): description,
            ("homepage",): homepage,
            ("repository",): repository,
            ("license",): source["license"],
            ("interface", "websiteURL"): homepage,
            ("interface", "supportURL"): SUPPORT_URL,
            ("interface", "privacyPolicyURL"): PRIVACY_URL,
            ("interface", "termsOfServiceURL"): TERMS_URL,
        },
    }


def read(document: dict, path: tuple) -> object:
    node: object = document
    for key in path:
        try:
            node = node[key]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def write(document: dict, path: tuple, value: object) -> None:
    node: object = document
    for key in path[:-1]:
        node = node[key]  # type: ignore[index]
    node[path[-1]] = value  # type: ignore[index]


def differences(root: Path = ROOT) -> list[str]:
    source = json.loads((root / "plugin.json").read_text())
    found = []
    for relative, fields in expected_fields(source).items():
        document = json.loads((root / relative).read_text())
        for path, value in fields.items():
            actual = read(document, path)
            if actual != value:
                where = ".".join(str(part) for part in path)
                found.append(f"{relative} {where} is {actual!r}; plugin.json gives {value!r}")
    return found


def sync(root: Path = ROOT) -> list[str]:
    source = json.loads((root / "plugin.json").read_text())
    changed = []
    for relative, fields in expected_fields(source).items():
        manifest = root / relative
        document = json.loads(manifest.read_text())
        before = json.dumps(document, sort_keys=True)
        for path, value in fields.items():
            write(document, path, value)
        if json.dumps(document, sort_keys=True) != before:
            manifest.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
            changed.append(relative)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report differences and change nothing")
    args = parser.parse_args()
    if args.check:
        found = differences()
        for line in found:
            print(f"DRIFT: {line}", file=sys.stderr)
        if found:
            print("Run python3 scripts/sync_manifests.py to copy plugin.json into the other manifests.", file=sys.stderr)
            return 1
        print("Manifests agree with plugin.json")
        return 0
    changed = sync()
    print("Updated: " + ", ".join(changed) if changed else "Manifests already agree with plugin.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
