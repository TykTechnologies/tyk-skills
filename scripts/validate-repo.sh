#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
BUILDER_SKILL="$ROOT/skills/tyk-demo-builder"
DIAGRAMS_SKILL="$ROOT/skills/tyk-excalidraw-diagrams"
SIZING_SKILL="$ROOT/skills/tyk-infra-sizing"
SKIP_COMPOSE=false

# A missing optional dependency is a convenience locally and a hole in CI: the
# workflow installs every requirements file, so an import failure there means the
# check never ran and the green tick is misleading. Skip loudly on a workstation,
# fail wherever CI is set.
require_dependency() {
  local what=$1 requirements=$2 check=$3
  if [[ -n "${CI:-}${GITHUB_ACTIONS:-}" ]]; then
    printf '%s is unavailable but CI installs it; %s cannot be skipped here.\n' \
      "$what" "$check" >&2
    exit 1
  fi
  printf 'WARNING: %s unavailable, so the %s did not run.\n' "$what" "$check" >&2
  printf '         Install it with: python3 -m pip install -r %s   (or: uv pip install -r %s)\n' \
    "$requirements" "$requirements" >&2
}

if [[ ${1:-} == --skip-compose ]]; then
  SKIP_COMPOSE=true
elif [[ $# -gt 0 ]]; then
  printf 'Usage: %s [--skip-compose]\n' "$0" >&2
  exit 2
fi

# Nested git checkouts, such as agent worktrees under .claude/worktrees/, are other branches
# rather than part of this checkout; repository_files() in validate_repository.py skips them too.
repo_find() {
  find "$ROOT" -mindepth 1 \( -name .git \
    -o \( -type d -exec test -e '{}/.git' ';' \) \) -prune -o "$@"
}

printf '%s\n' '[1/9] Repository contracts'
python3 "$SCRIPT_DIR/validate_repository.py"

printf '%s\n' '[2/9] Python compilation and unit tests'
TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/tyk-skills-validation.XXXXXX")
trap 'rm -rf "$TEMP_ROOT"' EXIT
export PYTHONPYCACHEPREFIX="$TEMP_ROOT/pycache"
python3 -m compileall -q "$SCRIPT_DIR"
# Every skill that ships scripts is compiled, and every one that ships tests has them run.
for skill_scripts in "$ROOT"/skills/*/scripts; do
  [[ -d "$skill_scripts" ]] || continue
  python3 -m compileall -q "$skill_scripts"
  if compgen -G "$skill_scripts/test_*.py" >/dev/null; then
    printf 'Unit tests: %s\n' "${skill_scripts#"$ROOT"/}"
    python3 -m unittest discover -s "$skill_scripts" -p 'test_*.py'
  fi
done
python3 -m unittest discover -s "$SCRIPT_DIR" -p 'test_*.py'

printf '%s\n' '[3/9] Shell syntax and lint'
while IFS= read -r -d '' script; do bash -n "$script"; done < <(repo_find -type f -name '*.sh' -print0)
if command -v shellcheck >/dev/null 2>&1; then
  while IFS= read -r -d '' script; do shellcheck "$script"; done < <(find "$ROOT/scripts" -type f -name '*.sh' -print0)
fi

printf '%s\n' '[4/9] Clean-room generation'
# --versions pinned keeps this step offline and deterministic; the unit tests cover the
# Docker Hub latest-stable resolution against a fixture.
generate_and_check() {
  local name=$1 modules=$2
  python3 "$BUILDER_SKILL/scripts/init_demo_bundle.py" \
    --output "$TEMP_ROOT/$name" \
    --slug "validation-$name" \
    --name "Validation $name" \
    --modules "$modules" \
    --versions pinned
  if [[ "$SKIP_COMPOSE" == true ]]; then
    python3 "$BUILDER_SKILL/scripts/validate_bundle.py" "$TEMP_ROOT/$name" --skip-compose
  else
    python3 "$BUILDER_SKILL/scripts/validate_bundle.py" "$TEMP_ROOT/$name"
  fi
  python3 "$BUILDER_SKILL/scripts/sanitize_bundle.py" "$TEMP_ROOT/$name"
}
# Gateway policy modules with OpenTelemetry.
generate_and_check base rest,jwt,routing,transforms,validation,error-overrides,otel
# MCP with the TLS module: their compose files and comments are what the PyYAML-free
# fallback parser has to get right.
generate_and_check mcp-mtls rest,api-key,mcp,mtls
# The Portal with its placeholder brand kit, and Tyk Sync delivery with the CI templates.
generate_and_check portal-sync rest,api-key,key-import,custom-domain,portal,tyk-sync,cicd

printf '%s\n' '[5/9] Skill package validation'
QUICK_VALIDATE=
for candidate in \
  "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" \
  "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/.system/skill-creator/scripts/quick_validate.py"; do
  if [[ -f "$candidate" ]]; then QUICK_VALIDATE=$candidate; break; fi
done
if [[ -n "$QUICK_VALIDATE" ]] && python3 -c 'import yaml' >/dev/null 2>&1; then
  for skill in "$ROOT"/skills/*/; do
    python3 "$QUICK_VALIDATE" "${skill%/}"
  done
else
  printf '%s\n' 'quick_validate.py or PyYAML unavailable; repository metadata checks already passed.'
fi

printf '%s\n' '[6/9] Sizing pack smoke test'
# The DOCX is optional (it needs a separately installed document theme), so the pack must
# build from the standard library alone; the diagram needs Pillow.
sizing_diagram_flag=()
python3 -c 'import PIL' >/dev/null 2>&1 || sizing_diagram_flag=(--no-diagram)
python3 "$SIZING_SKILL/scripts/build_sizing_pack.py" "$SIZING_SKILL/examples/multi-region-hybrid.json" \
  --out "$TEMP_ROOT/sizing" --name sizing ${sizing_diagram_flag[@]+"${sizing_diagram_flag[@]}"} >/dev/null
printf '%s\n' 'Sizing pack generated.'

printf '%s\n' '[7/9] Diagram smoke test'
python3 "$DIAGRAMS_SKILL/scripts/example_hybrid_dataplane.py" "$TEMP_ROOT/diagram.excalidraw"
python3 - "$TEMP_ROOT/diagram.excalidraw" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1]))
elements = document["elements"]
# Components are composed native Excalidraw glyphs: the repository ships no raster
# assets, so a generated document must embed no files at all.
assert document.get("files") == {}, "diagram embeds files; components must be native glyphs"
assert len(elements) >= 80, f"diagram has only {len(elements)} elements"
glyphs = [element for element in elements if (element.get("customData") or {}).get("tykComponent")]
assert glyphs, "no component glyphs were drawn"
# A glyph is a small drawing, not one tagged shape: its group holds several elements.
for glyph in glyphs:
    group = glyph["groupIds"][0]
    parts = [element for element in elements if group in element.get("groupIds", [])]
    assert len(parts) >= 3, f"glyph {glyph['customData']['tykComponent']} is not composed"
for element in elements:
    assert element["type"] != "image", "diagram contains an image element"
    # Excalidraw curve-smooths multi-point arrows whenever roundness is set, which
    # turns routed elbows into arcs. The library must never emit one.
    if element["type"] == "arrow":
        assert not element["roundness"], "arrow would render as a curve"
print(f"Diagram generated: {len(elements)} elements, {len(glyphs)} component glyphs")
PY
if python3 -c 'import PIL' >/dev/null 2>&1; then
  python3 "$DIAGRAMS_SKILL/scripts/render_excalidraw.py" \
    "$TEMP_ROOT/diagram.excalidraw" "$TEMP_ROOT/diagram.png" --scale 0.2
else
  require_dependency 'pillow' requirements-diagrams.txt 'diagram rasteriser check'
fi

printf '%s\n' '[8/9] Runtime and secret residue scan'
if repo_find -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name '*.crt' -o -name '*.so' \) -print -quit | grep -q .; then
  printf '%s\n' 'Runtime or sensitive artifact found in repository.' >&2
  repo_find -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name '*.crt' -o -name '*.so' \) -print >&2
  exit 1
fi

printf '%s\n' '[9/9] Agent Plugins conformance and native packaging'
python3 "$SCRIPT_DIR/validate_plugins.py"

printf '%s\n' 'All repository validation passed.'
