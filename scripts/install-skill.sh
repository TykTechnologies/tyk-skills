#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck source=lib/engine.sh disable=SC1091
. "$SCRIPT_DIR/lib/engine.sh"
MODE=symlink
REPLACE=false
ENGINE=
SKILLS=(
  tyk-demo-builder
  tyk-excalidraw-diagrams
  tyk-infra-sizing
  tyk-facts
  tyk-poc-runbook
  tyk-migration-planner
  tyk-developer-portal-branding
  tyk-rate-limits-keys-policies
  tyk-gitops-sync-operator
  tyk-api-definitions-routing
  tyk-mcp-gateway
  tyk-ai-gateway
  tyk-authentication
  tyk-developer-portal-setup
  tyk-observability
  tyk-troubleshooting
  tyk-custom-plugins
  tyk-websockets-streams-webhooks
  tyk-api-governance
  tyk-upgrades
  tyk-deployment
)
# Skills that earlier versions of this repository installed. A link to one of them is removed
# only when it is a symlink into this clone.
REMOVED_SKILLS=(
  tyk-deployment-planner
  tyk-deployment-runner
  tyk-production-readiness
)

usage() {
  cat <<'EOF'
Usage: ./scripts/install-skill.sh [--engine codex|claude|both] [--copy] [--replace]

Installs all bundled Tyk skills into the agent skills directory.

  --engine    Target agent: codex (CODEX_HOME), claude (CLAUDE_CONFIG_DIR), or both.
              Defaults to TYK_DEMO_ENGINE, otherwise the installed CLI (codex wins).
  --copy      Copy each skill instead of creating symlinks.
  --replace   Preserve each different installation as a timestamped backup.

The repository can instead be installed as a plugin; see the note printed at the end.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine)
      [[ $# -ge 2 ]] || { printf '%s\n' '--engine needs a value' >&2; exit 2; }
      ENGINE=$2; shift 2 ;;
    --copy) MODE=copy; shift ;;
    --replace) REPLACE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

ENGINES=()
if [[ "$ENGINE" == both ]]; then
  ENGINES=(codex claude)
else
  resolved=$(engine_resolve "$ENGINE") || exit $?
  ENGINES=("$resolved")
fi

resolve_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
}

remove_stale_links() {
  local root=$1 name target current repo
  repo=$(resolve_path "$REPO_ROOT")
  for name in "${REMOVED_SKILLS[@]}"; do
    target="$root/$name"
    [[ -L "$target" ]] || continue
    current=$(resolve_path "$target")
    if [[ "$current" == "$repo/"* ]]; then
      rm -- "$target"
      printf 'Removed link to a skill this repository no longer ships: %s\n' "$target"
    fi
  done
}

install_one() {
  local root=$1 name=$2
  local source="$REPO_ROOT/skills/$name"
  local target="$root/$name"
  local current backup

  [[ -f "$source/SKILL.md" ]] || { printf 'Bundled skill is incomplete: %s\n' "$source" >&2; return 1; }

  if [[ -L "$target" ]]; then
    current=$(resolve_path "$target")
    if [[ "$current" == "$source" && "$MODE" == symlink ]]; then
      printf 'Skill already linked: %s\n' "$target"
      return 0
    fi
  fi

  if [[ -e "$target" || -L "$target" ]]; then
    if [[ -d "$target" && ! -L "$target" ]] && diff -qr "$source" "$target" >/dev/null 2>&1; then
      printf 'Identical skill already installed: %s\n' "$target"
      return 0
    fi
    if [[ "$REPLACE" != true ]]; then
      printf 'A different skill installation exists at %s\n' "$target" >&2
      printf 'Re-run with --replace to preserve it as a backup before installing this version.\n' >&2
      return 1
    fi
    backup="${target}.backup.$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$target" "$backup"
    printf 'Preserved previous installation: %s\n' "$backup"
  fi

  if [[ "$MODE" == copy ]]; then
    cp -R "$source" "$target"
    printf 'Copied skill to %s\n' "$target"
  else
    ln -s "$source" "$target"
    printf 'Linked skill to %s\n' "$target"
  fi
}

for engine in "${ENGINES[@]}"; do
  root=$(engine_skills_root "$engine")
  printf 'Installing skills for %s: %s\n' "$engine" "$root"
  mkdir -p "$root"
  remove_stale_links "$root"
  for skill in "${SKILLS[@]}"; do
    install_one "$root" "$skill"
  done
done

cat <<EOF

This repository can instead be installed as a plugin (use one method, not both):
  Claude Code: claude plugin marketplace add $REPO_ROOT
               claude plugin install tyk-skills@tyk-skills
  Codex:       codex plugin marketplace add $REPO_ROOT
               codex plugin add tyk-skills@tyk-skills
Either marketplace command also accepts the GitHub repository in place of the local path.
EOF
