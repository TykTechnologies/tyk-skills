#!/usr/bin/env python3
"""Locate an installed sibling skill directory without assuming an agent runtime.

The Codex CLI expands `$skill-name` to the installed skill directory. The Claude
Code CLI has no equivalent expansion, so shared skill instructions resolve sibling
skills through this helper instead of embedding an engine-specific path root.

Search order:

1. the Claude Code personal skills directory (CLAUDE_CONFIG_DIR or ~/.claude)
2. the Codex skills directory (CODEX_HOME or ~/.codex)
3. a sibling directory in the source repository layout
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def candidate_roots() -> list[Path]:
    claude_home = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    codex_home = os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    return [
        Path(claude_home).expanduser() / "skills",
        Path(codex_home).expanduser() / "skills",
        Path(__file__).resolve().parents[2],
    ]


def resolve(name: str) -> Path | None:
    for root in candidate_roots():
        candidate = root / name
        if (candidate / "SKILL.md").is_file():
            return candidate.resolve()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="skill directory name, for example tyk-facts")
    arguments = parser.parse_args()

    resolved = resolve(arguments.name)
    if resolved is None:
        searched = "\n".join(f"  - {root / arguments.name}" for root in candidate_roots())
        print(
            f"Could not locate the {arguments.name} skill. Searched:\n{searched}\n"
            "Install the bundled skills with ./scripts/install-skill.sh and retry.",
            file=sys.stderr,
        )
        return 1

    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
