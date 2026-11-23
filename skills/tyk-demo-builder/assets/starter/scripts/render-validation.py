#!/usr/bin/env python3
"""Refresh the machine-generated results block inside notes/validation.md.

The note is meant to be curated by hand: exact commands, responses, and the source or
documentation behind each behaviour claim. Only the block between the two markers below
is regenerated, so a verification run never erases that text.

- A note with the markers: only the block between them is replaced.
- No note, or a note that is exactly an older fully generated table: the note is written
  fresh with the markers.
- A note with curated text but no markers: the text is kept and the block is appended.
- Any other marker layout (a BEGIN without its END, an END without its BEGIN, two pairs, or
  END before BEGIN) is refused with a non-zero exit and the note is left untouched. Guessing
  would pair the wrong markers on a later run and delete the curated text between them.

The note is written through a temporary file and an atomic replace, in UTF-8.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

BEGIN = "<!-- BEGIN GENERATED RESULTS (scripts/verify-demo.sh) -->"
END = "<!-- END GENERATED RESULTS -->"
# The whole-file layout verify-demo.sh wrote before the markers existed.
LEGACY_GENERATED = re.compile(
    r"\A# Validation\n\nGenerated: [^\n]*\n\n\| Test \| Status \| Detail \|\n\| --- \| --- \| --- \|\n(?:\|[^\n]*\|\n)*\Z"
)


def render_block(results: list[dict]) -> str:
    rows = [
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "| Test | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for item in results:
        detail = str(item.get("detail", "")).replace("|", "\\|").replace("\n", " ")
        rows.append(f"| `{item['test_id']}` | `{item['status']}` | {detail} |")
    return BEGIN + "\n" + "\n".join(rows) + "\n" + END


class MarkerError(ValueError):
    """The note's generated-results markers are not exactly one BEGIN followed by one END."""


def merge(existing: str, block: str) -> str:
    begins, ends = existing.count(BEGIN), existing.count(END)
    if begins == 0 and ends == 0:
        if not existing.strip() or LEGACY_GENERATED.match(existing):
            return "# Validation\n\n## Latest verification run\n\n" + block + "\n"
        return existing.rstrip("\n") + "\n\n## Latest verification run\n\n" + block + "\n"
    if begins != 1 or ends != 1 or existing.index(END) < existing.index(BEGIN):
        order = " (END comes before BEGIN)" if begins == 1 and ends == 1 else ""
        raise MarkerError(
            f"notes/validation.md must hold exactly one {BEGIN} followed by one {END}; "
            f"found {begins} BEGIN and {ends} END marker(s){order}. Fix the markers by hand "
            "(keep one pair around the generated table); the note was left untouched."
        )
    head, rest = existing.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return head + block + tail


def write_atomic(path: Path, text: str) -> None:
    """Replace a file through a temporary sibling so a failed write never leaves half a note."""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    results = json.loads((root / "evidence" / "results.json").read_text(encoding="utf-8"))
    note = root / "notes" / "validation.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    existing = note.read_text(encoding="utf-8") if note.exists() else ""
    try:
        merged = merge(existing, render_block(results))
    except MarkerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    write_atomic(note, merged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
