#!/usr/bin/env python3
"""Update generated requirement rows from machine-readable verification results.

Rules:
- A row may list several test IDs, comma separated. A row is `live` only when every listed
  test has a passing result in the current results; otherwise the weakest observed status
  wins. A test proving a related capability is not evidence for a broader claim.
- A listed test with no entry in the current results is unproven, not absent. A row with
  any missing test is never `live`: it keeps a curated non-live status (`talk-track`,
  `blocked`) and is otherwise set to `blocked`, and the evidence cell names the tests that
  did not run. This also applies when none of the row's tests ran: an earlier `live`, or an
  earlier machine segment, is never carried over from a previous run. Downgrades from real
  failures still apply, because a recorded failure is evidence and understating it would be
  the same defect in reverse.
- The current results are `evidence/results.json` from `verify-demo.sh`. A test ID it does
  not hold is looked up in a companion stack's own `companion/<name>/evidence/results.json`
  (the AI Studio companion writes `AISTUDIO-*` there with `verify-ai-studio.sh`), so a row
  proved by a separate runner is judged by that runner's last run, not downgraded.
- The machine segment names its results file and the run it came from: the `run` field
  `verify-demo.sh` stamps on every result (UTC time the results were written), or the
  file's modification time when a result carries none.
- Curated evidence text is preserved. The machine-generated detail is kept in a separate
  trailing segment after MACHINE_MARKER so repeated runs are idempotent rather than
  appending to the cell each time.
- Rows without test IDs, and non-live rows whose tests did not run and that carry no
  earlier machine segment, are left exactly as written.
- Requirement rows are recognised by their ID prefix. Generated rows use `REQ-`; a bundle
  that renumbers them with your own IDs sets `requirement_id_prefix` in
  demo.config.json (for example `"ACME"` for `ACME-014`). Both prefixes are processed.
- Curated evidence is never dropped. Only an earlier machine segment is replaced, and the
  generator's own `Pending verification` placeholder is not curated text.
- The mapping is read and written in UTF-8, through a temporary file and an atomic replace.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile

MACHINE_MARKER = " · verified: "
# A cell that is nothing but an earlier machine segment (a row without curated text, or one
# written before MACHINE_MARKER existed). Anything more than that is curated text and is kept. The detail may itself hold
# balanced parentheses, two levels deep, such as "(REST-001 passed (retried))"; text after
# the closing parenthesis means the cell is curated.
# One machine group is "`<results file>` (<detail>)"; a cell may hold several, "; " joined.
MACHINE_GROUP = r"`(?:companion/[^`/]+/)?evidence/results\.json` \((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)"
LEGACY_MACHINE_CELL = re.compile(rf"^{MACHINE_GROUP}(?:; {MACHINE_GROUP})*$")
PLACEHOLDER = "Pending verification"
# Weakest first: a failure outranks a block, which outranks a pass.
STATUS_PRECEDENCE = ("failed", "blocked", "passed")
# Statuses an author may curate that are not a claim of proof; anything else (including a
# stale `live`) becomes `blocked` when a listed test did not run.
CURATED_NON_LIVE = ("talk-track", "blocked")
MAIN_RESULTS = "evidence/results.json"

root = Path(__file__).resolve().parents[1]


def run_stamp(path: Path, items: list[dict]) -> str:
    """Identify the run a results file belongs to: its `run` field, else the file's mtime."""
    for item in items:
        if isinstance(item, dict) and str(item.get("run") or "").strip():
            return str(item["run"]).strip()
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_results(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in items if isinstance(item, dict) and "test_id" in item]


# test_id -> (result, source file, run stamp). The main results win; a companion stack only
# supplies test IDs the main run does not hold.
by_test: dict[str, tuple[dict, str, str]] = {}
main_items = load_results(root / MAIN_RESULTS)
main_stamp = run_stamp(root / MAIN_RESULTS, main_items)
for item in main_items:
    by_test.setdefault(item["test_id"], (item, MAIN_RESULTS, main_stamp))
for source in sorted((root / "companion").glob("*/evidence/results.json")):
    try:
        items = load_results(source)
    except (OSError, ValueError, TypeError):
        continue  # an unreadable companion file proves nothing; its tests stay "not run"
    stamp = run_stamp(source, items)
    for item in items:
        by_test.setdefault(item["test_id"], (item, source.relative_to(root).as_posix(), stamp))
config_path = root / "demo.config.json"
config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
configured_prefix = str(config.get("requirement_id_prefix") or "REQ").strip().rstrip("-") or "REQ"
ROW_PREFIXES = tuple(dict.fromkeys((f"| {configured_prefix}-", "| REQ-")))
mapping = root / "notes" / "requirements-mapping.md"
updated = []


def parse_test_ids(cell: str) -> list[str]:
    return [part.strip().strip("`").strip() for part in cell.split(",") if part.strip().strip("`").strip()]


def aggregate_status(found: list[dict]) -> str:
    statuses = {item["status"] for item in found}
    for candidate in STATUS_PRECEDENCE:
        if candidate in statuses:
            return "live" if candidate == "passed" else candidate
    return sorted(statuses)[0]


def curated_part(cell: str) -> str:
    """Existing evidence text with any previous machine segment removed."""
    text = cell.split(MACHINE_MARKER)[0].strip()
    if text == PLACEHOLDER or LEGACY_MACHINE_CELL.match(text):
        return ""
    return text


def write_atomic(path: Path, text: str) -> None:
    """Replace a file through a temporary sibling so a failed write never leaves half a table."""
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


for line in mapping.read_text(encoding="utf-8").splitlines():
    if not line.startswith(ROW_PREFIXES):
        updated.append(line)
        continue
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 6:
        updated.append(line)
        continue
    test_ids = parse_test_ids(cells[3])
    if not test_ids:
        updated.append(line)
        continue
    found = [by_test[test_id] for test_id in test_ids if test_id in by_test]
    missing = [test_id for test_id in test_ids if test_id not in by_test]
    current = cells[4].strip().strip("`").strip()
    has_machine = MACHINE_MARKER in cells[5] or bool(LEGACY_MACHINE_CELL.match(cells[5].strip()))
    if not found and current != "live" and not has_machine:
        # Nothing ran and nothing machine-made is on the row: the author's text stands.
        updated.append(line)
        continue
    status = aggregate_status([item for item, _, _ in found]) if found else "live"
    if missing and status == "live":
        # Partial (or no) evidence never marks a row live, whatever an earlier run said. Keep a
        # curated non-live status, otherwise `blocked`, and say which tests did not run.
        status = current if current in CURATED_NON_LIVE else "blocked"
    cells[4] = f"`{status}`"
    groups: dict[tuple[str, str], list[str]] = {}
    for item, source, stamp in found:
        groups.setdefault((source, stamp), []).append(f"{item['test_id']} {item['status']}")
    if missing:
        groups.setdefault((MAIN_RESULTS, main_stamp), []).extend(
            f"{test_id} not run in this run" for test_id in missing
        )
    machine = "; ".join(
        f"`{source}` (run {stamp}: {'; '.join(details)})" for (source, stamp), details in groups.items()
    )
    curated = curated_part(cells[5])
    cells[5] = f"{curated}{MACHINE_MARKER}{machine}" if curated else machine
    line = "| " + " | ".join(cells) + " |"
    updated.append(line)

write_atomic(mapping, "\n".join(updated) + "\n")
