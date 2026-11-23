#!/usr/bin/env python3
"""Measure which skill Claude Code routes each prompt to, with and without descriptions.

Every prompt in prompts.jsonl runs once through `claude -p` with this repository loaded as a
plugin from its local path. The runner reads the stream-json output, records the first Skill
tool call (if any) and writes a results JSON plus a Markdown summary table.

Modes:
  descriptions  the normal skill listing: every skill name with its description
  names-only    SLASH_COMMAND_TOOL_CHAR_BUDGET=1 shrinks the listing budget so Claude Code keeps
                every skill name but drops the descriptions, which is what a newly installed,
                rarely used skill gets when the listing is over its budget

`--rename old=new` evaluates candidate names without touching the working tree: the plugin
manifests and every SKILL.md are copied to a temporary folder, the named skills are renamed
there, and the run uses that copy. Results stay keyed by the current names.

Standard library only. Requires the `claude` CLI on PATH and a logged-in account.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN_NAME = "tyk-skills"

ROUTER_PROMPT = (
    "You are taking part in a skill-routing test. Treat the user's message as a real request. "
    "If one of your available skills is the right one for it, call the Skill tool once to load "
    "that skill and then stop: do not carry out the request, call no other tool and write at "
    "most one short sentence. If no available skill fits, call no tool and reply with the single "
    "word NONE."
)

# Variables a parent Claude Code session exports that would tie the child run to that session.
PARENT_SESSION_VARS = (
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_REPORT_FINDINGS",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("descriptions", "names-only"), required=True)
    parser.add_argument("--prompts", type=Path, default=HERE / "prompts.jsonl")
    parser.add_argument("--plugin-dir", type=Path, default=REPO, help="plugin root (default: this repository)")
    parser.add_argument("--out-dir", type=Path, default=HERE)
    parser.add_argument("--label", default="", help="suffix for the output file names, e.g. renamed")
    parser.add_argument("--skills", default="", help="comma-separated skills whose prompts to run (default: all)")
    parser.add_argument("--ids", default="", help="comma-separated prompt ids to run (default: all)")
    parser.add_argument("--rename", action="append", default=[], metavar="OLD=NEW",
                        help="run against a temporary copy with this skill renamed (repeatable)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180, help="seconds per prompt")
    parser.add_argument("--model", default="", help="model override (default: the CLI default)")
    parser.add_argument("--claude", default="claude", help="path to the claude binary")
    args = parser.parse_args()
    if args.concurrency < 1 or args.concurrency > 4:
        parser.error("--concurrency must be between 1 and 4")
    return args


def load_prompts(path: Path, skills: set[str], ids: set[str]) -> list[dict]:
    prompts = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        for key in ("id", "skill", "kind", "expected", "prompt"):
            if key not in record:
                raise SystemExit(f"{path}:{number}: missing {key}")
        if skills and record["skill"] not in skills:
            continue
        if ids and record["id"] not in ids:
            continue
        prompts.append(record)
    return prompts


def frontmatter_name(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    match = re.search(r"^name:\s*(\S+)\s*$", text.split("\n---", 1)[0], re.MULTILINE)
    return match.group(1) if match else skill_md.parent.name


def renamed_copy(plugin_dir: Path, renames: dict[str, str], work: Path) -> Path:
    """Copy the plugin manifests and SKILL.md files, then apply the renames to the copy only."""
    target = work / "plugin"
    for name in (".claude-plugin", ".codex-plugin"):
        if (plugin_dir / name).is_dir():
            shutil.copytree(plugin_dir / name, target / name)
    if (plugin_dir / "plugin.json").is_file():
        shutil.copy2(plugin_dir / "plugin.json", target / "plugin.json")
    pattern = re.compile(r"\b(" + "|".join(re.escape(old) for old in sorted(renames, key=len, reverse=True)) + r")\b") if renames else None
    for skill_md in sorted((plugin_dir / "skills").glob("*/SKILL.md")):
        old = skill_md.parent.name
        new = renames.get(old, old)
        text = skill_md.read_text(encoding="utf-8")
        if pattern:
            text = pattern.sub(lambda m: renames[m.group(1)], text)
        destination = target / "skills" / new / "SKILL.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    missing = [old for old in renames if not (plugin_dir / "skills" / old / "SKILL.md").is_file()]
    if missing:
        raise SystemExit(f"--rename names unknown skills: {', '.join(missing)}")
    return target


def child_env(mode: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in PARENT_SESSION_VARS}
    if mode == "names-only":
        env["SLASH_COMMAND_TOOL_CHAR_BUDGET"] = "1"
    else:
        env.pop("SLASH_COMMAND_TOOL_CHAR_BUDGET", None)
    return env


def command(args: argparse.Namespace, plugin_dir: Path) -> list[str]:
    cmd = [
        args.claude, "-p",
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--plugin-dir", str(plugin_dir),
        "--no-session-persistence",
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "dontAsk",
        "--append-system-prompt", ROUTER_PROMPT,
        "--tools", "Skill",
    ]
    if args.model:
        cmd += ["--model", args.model]
    return cmd


def strip_plugin(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip().lstrip("/").split()[0]
    return name.split(":", 1)[1] if name.startswith(PLUGIN_NAME + ":") else name


def run_one(prompt: dict, cmd: list[str], env: dict[str, str], cwd: Path, timeout: int) -> dict:
    started = time.monotonic()
    record = {"chosen": None, "chosen_raw": None, "skills_called": [], "text": "", "cost_usd": None,
              "duration_s": None, "error": None, "model": None, "plugin_skills_listed": None, "usage": {}}
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        proc.stdin.write(prompt["prompt"])
        proc.stdin.close()
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                record["model"] = event.get("model")
                record["plugin_skills_listed"] = sum(1 for s in event.get("skills", []) if s.startswith(PLUGIN_NAME + ":"))
            elif kind == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use" and block.get("name") == "Skill":
                        raw = (block.get("input") or {}).get("skill") or (block.get("input") or {}).get("command")
                        record["skills_called"].append(raw)
                        if record["chosen_raw"] is None:
                            record["chosen_raw"] = raw
                            record["chosen"] = strip_plugin(raw)
            elif kind == "result":
                record["text"] = (event.get("result") or "")[:400]
                record["cost_usd"] = event.get("total_cost_usd")
                usage = event.get("usage") or {}
                record["usage"] = {k: usage.get(k) for k in ("input_tokens", "output_tokens",
                                                             "cache_creation_input_tokens", "cache_read_input_tokens")}
        proc.wait()
        if proc.returncode not in (0, None) and record["cost_usd"] is None:
            stderr = proc.stderr.read()[-400:]
            record["error"] = "timeout" if not timer.is_alive() else f"exit {proc.returncode}: {stderr.strip()}"
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    record["duration_s"] = round(time.monotonic() - started, 1)
    return record


def summarise(records: list[dict], skills: list[str]) -> dict:
    per_skill = {}
    for skill in skills:
        own = [r for r in records if r["skill"] == skill]
        pos = [r for r in own if r["kind"] == "positive"]
        near = [r for r in own if r["kind"] == "near_miss"]
        per_skill[skill] = {
            "positives": len(pos),
            "positives_correct": sum(1 for r in pos if r["correct"]),
            "near_misses": len(near),
            "near_misses_correct": sum(1 for r in near if r["correct"]),
            "near_misses_pulled_in": sum(1 for r in near if r["chosen_canonical"] == skill),
            "wrongly_chosen_elsewhere": sum(1 for r in records if r["skill"] != skill
                                            and r["chosen_canonical"] == skill and r["expected"] != skill),
        }
    total = len(records)
    return {
        "prompts": total,
        "correct": sum(1 for r in records if r["correct"]),
        "positives_correct": sum(1 for r in records if r["kind"] == "positive" and r["correct"]),
        "positives": sum(1 for r in records if r["kind"] == "positive"),
        "near_misses_correct": sum(1 for r in records if r["kind"] == "near_miss" and r["correct"]),
        "near_misses": sum(1 for r in records if r["kind"] == "near_miss"),
        "errors": sum(1 for r in records if r["error"]),
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in records), 4),
        "per_skill": per_skill,
    }


def markdown(meta: dict, summary: dict, records: list[dict], renames: dict[str, str]) -> str:
    lines = [
        f"# Routing results: {meta['mode']}{' (' + meta['label'] + ')' if meta['label'] else ''}",
        "",
        f"- Date: {meta['date']}; Claude Code {meta['claude_version']}; model {meta['model']}",
        f"- Prompts: {summary['prompts']}; correct {summary['correct']} "
        f"(positives {summary['positives_correct']}/{summary['positives']}, "
        f"near-misses {summary['near_misses_correct']}/{summary['near_misses']}); errors {summary['errors']}",
        f"- Cost ${summary['cost_usd']:.2f}; wall time {meta['wall_time_s']} s",
    ]
    if renames:
        lines.append("- Renames: " + ", ".join(f"`{o}` -> `{n}`" for o, n in renames.items()))
    lines += [
        "",
        "| Skill | Own prompts | Near-misses routed right | Near-misses pulled in | Chosen wrongly elsewhere |",
        "| --- | --- | --- | --- | --- |",
    ]
    for skill, row in summary["per_skill"].items():
        shown = f"{skill} (as {renames[skill]})" if skill in renames else skill
        lines.append(f"| {shown} | {row['positives_correct']}/{row['positives']} | "
                     f"{row['near_misses_correct']}/{row['near_misses']} | {row['near_misses_pulled_in']} | "
                     f"{row['wrongly_chosen_elsewhere']} |")
    misses = [r for r in records if not r["correct"]]
    if misses:
        lines += ["", "## Misrouted prompts", "", "| Id | Expected | Chosen |", "| --- | --- | --- |"]
        for r in misses:
            lines.append(f"| {r['id']} | {r['expected'] or 'none'} | {r['chosen_canonical'] or ('error' if r['error'] else 'none')} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    renames = dict(item.split("=", 1) for item in args.rename)
    reverse = {new: old for old, new in renames.items()}
    skills_filter = {s for s in args.skills.split(",") if s}
    ids_filter = {s for s in args.ids.split(",") if s}
    prompts = load_prompts(args.prompts, skills_filter, ids_filter)
    if not prompts:
        raise SystemExit("no prompts selected")

    plugin_dir = args.plugin_dir.resolve()
    all_skills = sorted(frontmatter_name(p) for p in (plugin_dir / "skills").glob("*/SKILL.md"))
    plugin_skills = set(all_skills)
    version = subprocess.run([args.claude, "--version"], capture_output=True, text=True).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="tyk-routing-") as tmp:
        work = Path(tmp)
        run_plugin = renamed_copy(plugin_dir, renames, work) if renames else plugin_dir
        cwd = work / "cwd"
        cwd.mkdir()
        cmd = command(args, run_plugin)
        # Keep machine paths out of the results: they are committed next to this script.
        display_dir = "temporary renamed copy" if renames else (
            "." if plugin_dir == REPO else plugin_dir.name)
        shown_cmd = ["<plugin-dir>" if part == str(run_plugin) else part for part in cmd]
        env = child_env(args.mode)
        started = time.monotonic()
        results: dict[str, dict] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(run_one, p, cmd, env, cwd, args.timeout): p for p in prompts}
            for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                prompt = futures[future]
                outcome = future.result()
                results[prompt["id"]] = outcome
                print(f"[{done}/{len(prompts)}] {prompt['id']}: expected {prompt['expected'] or 'none'}, "
                      f"chose {outcome['chosen'] or outcome['error'] or 'none'}", file=sys.stderr, flush=True)
        wall = round(time.monotonic() - started, 1)

    records = []
    for prompt in prompts:
        outcome = results[prompt["id"]]
        chosen = outcome["chosen"]
        canonical = reverse.get(chosen, chosen) if chosen else None
        if canonical not in plugin_skills:
            # A skill from outside this plugin (a built-in one) counts as "no plugin skill".
            canonical = None
        records.append({**prompt, **outcome, "chosen_canonical": canonical,
                        "correct": outcome["error"] is None and canonical == prompt["expected"]})

    summary_skills = [s for s in all_skills if not skills_filter or s in skills_filter]
    summary_skills = [reverse.get(s, s) for s in summary_skills]
    summary = summarise(records, summary_skills)
    models = sorted({r["model"] for r in records if r["model"]})
    date = dt.date.today().isoformat()
    meta = {"mode": args.mode, "label": args.label, "date": date, "claude_version": version,
            "model": ", ".join(models) or "unknown", "plugin_dir": display_dir, "renames": renames,
            "concurrency": args.concurrency, "timeout_s": args.timeout, "wall_time_s": wall,
            "router_prompt": ROUTER_PROMPT, "command": shown_cmd}
    stem = f"results-{args.mode}{'-' + args.label if args.label else ''}-{date}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{stem}.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "records": records}, indent=2) + "\n", encoding="utf-8")
    table = markdown(meta, summary, records, renames)
    (args.out_dir / f"{stem}.md").write_text(table, encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
