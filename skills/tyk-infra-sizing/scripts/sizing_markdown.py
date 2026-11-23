#!/usr/bin/env python3
"""Render the sizing content JSON as a Markdown report.

The content JSON is what sizing_docx_content.build_content() produces: a title block, an
executive summary and sections of typed blocks (heading, paragraph, bullets, numbered,
table, callout, image, references). This module walks it and writes the same report as
Markdown, so the pack is complete without any DOCX tooling. It has no third-party
dependencies.

Usage:
    python3 sizing_markdown.py sizing-content.json sizing-report.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _image_path(path: str, base_dir: Path | None) -> str:
    """Link the image relative to the report when it sits beside or below it."""
    if base_dir is None:
        return path
    try:
        return Path(os.path.relpath(Path(path).resolve(), base_dir.resolve())).as_posix()
    except ValueError:  # another drive on Windows
        return path


def render_block(block: dict, base_dir: Path | None = None) -> list[str]:
    kind = block.get("type")
    if kind == "heading":
        level = min(6, int(block.get("level", 2)) + 1)  # section titles are level 2
        return ["#" * level + " " + block["text"]]
    if kind == "paragraph":
        return [block["text"]]
    if kind == "bullets":
        return [f"- {item}" for item in block.get("items", []) if item]
    if kind == "numbered":
        return [f"{index}. {item}" for index, item in enumerate((i for i in block.get("items", []) if i), 1)]
    if kind == "table":
        headers = [_cell(h) for h in block.get("headers", [])]
        lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in block.get("rows", [])]
        return lines
    if kind == "callout":
        title = block.get("title") or str(block.get("kind", "note")).capitalize()
        return [f"> **{title}:** {block.get('text', '')}"]
    if kind == "image":
        alt = block.get("alt_text") or block.get("caption") or "figure"
        lines = [f"![{alt}]({_image_path(block['path'], base_dir)})"]
        if block.get("caption"):
            lines += ["", f"*{block['caption']}*"]
        return lines
    if kind == "references":
        return [f"- [{item['label']}]({item['url']})" for item in block.get("items", [])]
    if kind == "code":
        return ["```" + block.get("language", ""), block.get("text", ""), "```"]
    if kind == "section":  # page orientation only matters in a paginated document
        return []
    if block.get("text"):
        return [block["text"]]
    if block.get("items"):
        return [f"- {item}" for item in block["items"] if item]
    return []


def _blocks(blocks: list[dict], base_dir: Path | None) -> list[str]:
    out: list[str] = []
    for block in blocks:
        lines = render_block(block, base_dir)
        if lines:
            out += lines + [""]
    return out


def _anchors(titles: list[str]) -> list[str]:
    """GitHub-style heading anchors: lower case, punctuation dropped, spaces to hyphens, and a
    -1, -2 suffix for repeated titles. The executive summary heading comes first."""
    seen: dict[str, int] = {"executive-summary": 1, "contents": 1}
    anchors = []
    for title in titles:
        base = re.sub(r"[^\w\- ]", "", title.lower()).strip().replace(" ", "-")
        n = seen.get(base, 0)
        seen[base] = n + 1
        anchors.append(base if n == 0 else f"{base}-{n}")
    return anchors


def render_markdown(content: dict, base_dir: Path | None = None) -> str:
    """Return the whole report. `base_dir` is the report's folder, for relative image links."""
    out = [f"# {content.get('title') or 'Infrastructure Sizing'}", ""]
    meta = [content.get("client"), content.get("document_type"), content.get("date"),
            f"version {content['version']}" if content.get("version") else "", content.get("classification")]
    meta = [m for m in meta if m]
    if meta:
        out += [" | ".join(meta), ""]
    if content.get("description"):
        out += [f"*{content['description']}*", ""]
    sections = content.get("sections", [])
    if content.get("toc") and sections:
        out += ["## Contents", ""]
        anchors = _anchors([section["title"] for section in sections])
        out += [f"{index}. [{section['title']}](#{anchor})" for index, (section, anchor) in enumerate(zip(sections, anchors), 1)]
        out.append("")
    if content.get("executive_summary"):
        out += ["## Executive summary", ""] + _blocks(content["executive_summary"], base_dir)
    for section in sections:
        out += [f"## {section['title']}", ""] + _blocks(section.get("blocks", []), base_dir)
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("content", help="content JSON from sizing_docx_content.py")
    parser.add_argument("report", help="Markdown file to write")
    args = parser.parse_args()
    report = Path(args.report)
    report.write_text(render_markdown(json.loads(Path(args.content).read_text()), report.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
