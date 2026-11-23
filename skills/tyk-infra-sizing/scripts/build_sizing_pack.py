#!/usr/bin/env python3
"""Build the full sizing pack from one brief.

Outputs, all under --out (default: the project's workspace folder,
${TYK_WORKSPACE:-~/tyk-workspace}/<project>/sizing, with <project> named from the brief's
"organisation" field):

    sizing-result.json         engine result (every number, for review and reruns)
    sizing-summary.md          working Markdown summary with questions to confirm
    <env-stem>.excalidraw      editable topology diagram per environment
    <env-stem>.png             preview render of each diagram (needs Pillow)
    sizing-content.json        structured report content (headings, tables, notes)
    sizing-report.md           the full sizing report in Markdown
    <name>.docx                optional: rendered and validated only when a
                               tyk-docx-theme skill is installed (needs python-docx)

The calculation, the Markdown report and the diagrams need only the standard library and
Pillow (requirements-diagrams.txt at the repository root). When a tyk-docx-theme skill is
installed the pack also renders a DOCX; otherwise that step is skipped with one line on
stderr and the build still succeeds. Sibling skills are located through the source
repository layout or the installed skills directories.

Usage:
    python3 build_sizing_pack.py brief.json [--out <dir>] [--name file-stem]
        [--environments all|production] [--no-diagram] [--preview]
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sizing_docx_content as C  # noqa: E402
import sizing_engine as E  # noqa: E402
from sizing_markdown import render_markdown  # noqa: E402
from size_estate import load_brief, markdown  # noqa: E402


def resolve_sibling(name: str) -> Path | None:
    local = HERE.parent.parent / name
    if (local / "SKILL.md").is_file():
        return local.resolve()
    resolver = HERE.parent.parent / "tyk-demo-builder" / "scripts" / "resolve_skill_dir.py"
    if resolver.is_file():
        spec = importlib.util.spec_from_file_location("resolve_skill_dir", resolver)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            found = module.resolve(name)
            if found:
                return Path(found)
    return None


def trim_whitespace(png: Path, margin: int = 24) -> None:
    """Crop the render to its content. The rasteriser sizes the canvas from element
    bounding boxes, and leftward arrows inflate them, which would shrink the diagram in the DOCX."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return
    with Image.open(png) as image:
        rgb = image.convert("RGB")
        box = ImageChops.difference(rgb, Image.new("RGB", rgb.size, (255, 255, 255))).getbbox()
        if not box:
            return
        left, top, right, bottom = box
        cropped = rgb.crop((max(0, left - margin), max(0, top - margin), min(rgb.width, right + margin), min(rgb.height, bottom + margin)))
    cropped.save(png)


SKILL_DIR = HERE.parent


def _plugin_root() -> Path | None:
    """The plugin or repository folder that holds this skill, when it can be recognised.

    In the repository layout that is <root>/skills/tyk-infra-sizing with a plugin.json at
    <root>. An installed copy elsewhere has no such parent, and only the skill's own folder
    is protected.
    """
    root = SKILL_DIR.parent.parent
    if SKILL_DIR.parent.name == "skills" and ((root / "plugin.json").is_file() or (root / ".claude-plugin").is_dir()):
        return root
    return None


REPO_ROOT = _plugin_root() or SKILL_DIR


def workspace_sizing_dir(organisation: str) -> Path:
    """The project's sizing folder: ${TYK_WORKSPACE:-~/tyk-workspace}/<project>/sizing."""
    folder = re.sub(r"[^a-z0-9]+", "-", organisation.lower()).strip("-")
    if not folder:
        raise SystemExit("The brief has no usable organisation name; pass --out or set \"organisation\" in the brief.")
    root = Path(os.environ.get("TYK_WORKSPACE") or Path.home() / "tyk-workspace").expanduser()
    return root / folder / "sizing"


def refuse_inside_repository(out: Path) -> None:
    resolved = out.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit("Sizing output must be outside the plugin's own folder; pass --out with a workspace path.")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "environment"


def allocate_stems(names: list[str]) -> dict[str, str]:
    """Map each environment name to a unique, deterministic file stem.

    slug() folds case and punctuation, so "EU West" and "EU-West" would share a file. The first
    name keeps the plain slug; later ones get -2, -3 and so on, skipping any stem already taken.
    """
    stems: dict[str, str] = {}
    taken: set[str] = set()
    for name in names:
        base = slug(name)
        stem, n = base, 1
        while stem in taken:
            n += 1
            stem = f"{base}-{n}"
        taken.add(stem)
        stems[name] = stem
    return stems


def build(brief_path: str, out: Path, name: str | None, environments: str, diagram: bool, preview: bool) -> dict:
    result = E.size_estate(load_brief(brief_path))  # validate before creating anything
    out.mkdir(parents=True, exist_ok=True)
    (out / "sizing-result.json").write_text(json.dumps(result, indent=2, default=str))
    (out / "sizing-summary.md").write_text(markdown(result))
    produced = {"result": str(out / "sizing-result.json"), "summary": str(out / "sizing-summary.md"), "diagrams": []}

    diagram_png: dict[str, str] = {}  # environment name -> its own preview PNG
    if diagram:
        diagrams_skill = resolve_sibling("tyk-excalidraw-diagrams")
        if diagrams_skill is None:
            raise SystemExit("tyk-excalidraw-diagrams skill not found; install the bundled skills or pass --no-diagram")
        import sizing_diagram  # noqa: PLC0415 - optional dependency path
        envs = result["environments"] if environments == "all" else C.prod_envs(result)
        renderer = diagrams_skill / "scripts" / "render_excalidraw.py"
        stems = allocate_stems([e["name"] for e in envs])
        for env in envs:
            stem = out / stems[env["name"]]
            with contextlib.redirect_stdout(sys.stderr):  # the diagram library reports saves on stdout
                sizing_diagram.build_diagram(result, str(stem.with_suffix(".excalidraw")), env["name"])
            entry = {"environment": env["name"], "excalidraw": str(stem.with_suffix(".excalidraw"))}
            rendered = subprocess.run([sys.executable, str(renderer), str(stem.with_suffix(".excalidraw")), str(stem.with_suffix(".png")), "--scale", "0.35"],
                                      capture_output=True, text=True)
            if rendered.returncode == 0:
                trim_whitespace(stem.with_suffix(".png"))
                entry["png"] = str(stem.with_suffix(".png"))
                diagram_png[env["name"]] = entry["png"]
            else:
                raise SystemExit("diagram render failed (install Pillow from requirements-diagrams.txt, or pass --no-diagram):\n"
                                 + (rendered.stderr or rendered.stdout).strip()[-400:])
            produced["diagrams"].append(entry)

    content = C.build_content(result, diagram_png.get(C.prod_envs(result)[0]["name"]))
    (out / "sizing-content.json").write_text(json.dumps(content, indent=2))
    produced["content"] = str(out / "sizing-content.json")
    (out / "sizing-report.md").write_text(render_markdown(content, out))
    produced["report"] = str(out / "sizing-report.md")

    # The DOCX is optional: it needs a tyk-docx-theme skill and python-docx.
    theme = resolve_sibling("tyk-docx-theme")
    skipped = None
    if theme is None:
        skipped = "DOCX skipped: the optional tyk-docx-theme skill is not installed."
    elif importlib.util.find_spec("docx") is None:
        skipped = f"DOCX skipped: {sys.executable} cannot import python-docx, which the tyk-docx-theme renderer needs."
    if skipped:
        print(skipped, file=sys.stderr)
        produced["docx"] = None
        produced["docx_skipped"] = skipped
        return produced
    stem = name or slug(f"{result.get('organisation') or 'tyk'} infrastructure sizing")
    docx = out / f"{stem}.docx"
    subprocess.run([sys.executable, str(theme / "scripts" / "render_tyk_docx.py"), str(out / "sizing-content.json"), str(docx)], check=True, capture_output=True, text=True)
    validate = [sys.executable, str(theme / "scripts" / "validate_tyk_docx.py"), str(docx)]
    if preview:
        validate += ["--render-dir", str(out / "preview")]
    checked = subprocess.run(validate, capture_output=True, text=True)
    produced["docx"] = str(docx)
    produced["docx_validation"] = "passed" if checked.returncode == 0 else (checked.stdout + checked.stderr).strip()[-800:]
    if checked.returncode != 0:
        print(json.dumps(produced, indent=2))
        raise SystemExit("DOCX validation failed; see docx_validation above")
    return produced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("brief")
    parser.add_argument("--out", help="output folder (default: ${TYK_WORKSPACE:-~/tyk-workspace}/<project>/sizing)")
    parser.add_argument("--name", help="DOCX file stem, when the DOCX is rendered (default: <organisation>-infrastructure-sizing)")
    parser.add_argument("--environments", choices=("all", "production"), default="all", help="which environments get a diagram")
    parser.add_argument("--no-diagram", action="store_true")
    parser.add_argument("--preview", action="store_true", help="with the DOCX, also render PDF/PNG pages for visual QA (needs soffice and pdftoppm)")
    args = parser.parse_args()
    try:
        if args.out:
            out = Path(args.out)
        else:
            loaded = load_brief(args.brief)
            E.size_estate(loaded)  # a brief error outranks a missing organisation name
            out = workspace_sizing_dir(str((loaded.get("organisation") if isinstance(loaded, dict) else "") or ""))
        refuse_inside_repository(out)
        produced = build(args.brief, out, args.name, args.environments, not args.no_diagram, args.preview)
    except E.BriefError as exc:
        print(f"brief error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"DOCX rendering failed: {(exc.stderr or exc.stdout or str(exc)).strip()[-400:]}", file=sys.stderr)
        return 1
    print(json.dumps(produced, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
