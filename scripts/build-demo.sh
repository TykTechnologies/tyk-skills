#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck source=lib/engine.sh disable=SC1091
. "$SCRIPT_DIR/lib/engine.sh"
# shellcheck source=lib/workspace.sh disable=SC1091
. "$SCRIPT_DIR/lib/workspace.sh"
REQUIREMENTS=()
OUTPUT=
PROJECT=
SLUG=
NAME=
MODULES=
PORT_BASE=
IDENTITY_MODE=local
NON_INTERACTIVE=false
NO_INSTALL=false
LICENSE_ENV=
ENGINE=
BYPASS_PERMISSIONS=false

usage() {
  cat <<'EOF'
Usage:
  ./scripts/build-demo.sh \
    --requirements <file> [--requirements <file> ...] \
    [--output <directory> | --project <project-name>] \
    --slug <demo-slug> \
    --name <display-name> \
    [--modules <csv>] [--port-base <port>] \
    [--identity-mode local|external] [--license-env <file>] \
    [--engine codex|claude] [--bypass-permissions] \
    [--non-interactive] [--no-install]

Builds and verifies a standalone Tyk demo or proof of concept from your requirements
with the tyk-demo-builder skill.

Without --output the bundle goes to the project's workspace folder,
${TYK_WORKSPACE:-~/tyk-workspace}/<project>/demos/<slug>, named from --project.

--engine selects the agent CLI. It defaults to TYK_DEMO_ENGINE, otherwise the installed
CLI, preferring codex when both are present.
--bypass-permissions disables Claude permission checks for an unattended run.
--non-interactive runs the agent without a conversation; it may propose changes to the
reusable builder but never makes them.
EOF
}

absolute_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --requirements)
      [[ $# -ge 2 ]] || { printf '%s\n' '--requirements needs a value' >&2; exit 2; }
      REQUIREMENTS+=("$2"); shift 2 ;;
    --output)
      [[ $# -ge 2 ]] || { printf '%s\n' '--output needs a value' >&2; exit 2; }
      OUTPUT=$2; shift 2 ;;
    --project)
      [[ $# -ge 2 ]] || { printf '%s\n' '--project needs a value' >&2; exit 2; }
      PROJECT=$2; shift 2 ;;
    --slug)
      [[ $# -ge 2 ]] || { printf '%s\n' '--slug needs a value' >&2; exit 2; }
      SLUG=$2; shift 2 ;;
    --name)
      [[ $# -ge 2 ]] || { printf '%s\n' '--name needs a value' >&2; exit 2; }
      NAME=$2; shift 2 ;;
    --modules)
      [[ $# -ge 2 ]] || { printf '%s\n' '--modules needs a value' >&2; exit 2; }
      MODULES=$2; shift 2 ;;
    --port-base)
      [[ $# -ge 2 ]] || { printf '%s\n' '--port-base needs a value' >&2; exit 2; }
      PORT_BASE=$2; shift 2 ;;
    --identity-mode)
      [[ $# -ge 2 ]] || { printf '%s\n' '--identity-mode needs a value' >&2; exit 2; }
      IDENTITY_MODE=$2; shift 2 ;;
    --license-env)
      [[ $# -ge 2 ]] || { printf '%s\n' '--license-env needs a value' >&2; exit 2; }
      LICENSE_ENV=$2; shift 2 ;;
    --engine)
      [[ $# -ge 2 ]] || { printf '%s\n' '--engine needs a value' >&2; exit 2; }
      ENGINE=$2; shift 2 ;;
    --bypass-permissions) BYPASS_PERMISSIONS=true; shift ;;
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    --no-install) NO_INSTALL=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ ${#REQUIREMENTS[@]} -gt 0 ]] || { printf '%s\n' 'At least one --requirements file is required.' >&2; exit 2; }
[[ -n "$SLUG" && -n "$NAME" ]] || { usage >&2; exit 2; }
[[ "$SLUG" =~ ^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$ ]] || { printf '%s\n' 'Invalid slug.' >&2; exit 2; }
if [[ -z "$OUTPUT" ]]; then
  [[ -n "$PROJECT" ]] || { printf '%s\n' 'Give --output, or --project to use the project workspace folder.' >&2; exit 2; }
  OUTPUT=$(workspace_path demos "$PROJECT" "$SLUG") || { printf '%s\n' '--project needs letters or digits to name a workspace folder.' >&2; exit 2; }
fi
[[ "$IDENTITY_MODE" == local || "$IDENTITY_MODE" == external ]] || { printf '%s\n' 'identity mode must be local or external.' >&2; exit 2; }
if [[ -n "$PORT_BASE" && ! "$PORT_BASE" =~ ^[0-9]+$ ]]; then
  printf '%s\n' 'port base must be numeric.' >&2
  exit 2
fi

engine_validate_requested "$ENGINE" || exit $?

command -v python3 >/dev/null || { printf '%s\n' 'Python 3 is required.' >&2; exit 1; }

ABS_REQUIREMENTS=()
for source in "${REQUIREMENTS[@]}"; do
  path=$(absolute_path "$source")
  [[ -f "$path" ]] || { printf 'Requirement source not found: %s\n' "$path" >&2; exit 1; }
  ABS_REQUIREMENTS+=("$path")
done

ABS_OUTPUT=$(absolute_path "$OUTPUT")
OUTPUT_PARENT=$(dirname -- "$ABS_OUTPUT")
case "$ABS_OUTPUT/" in
  "$REPO_ROOT/"*)
    printf '%s\n' 'The generated bundle must be outside this plugin repository.' >&2
    exit 1
    ;;
esac
if [[ -d "$ABS_OUTPUT" ]] && [[ -n "$(find "$ABS_OUTPUT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  printf 'Output directory is not empty: %s\n' "$ABS_OUTPUT" >&2
  exit 1
fi
mkdir -p "$OUTPUT_PARENT"

ABS_LICENSE_ENV=
if [[ -n "$LICENSE_ENV" ]]; then
  ABS_LICENSE_ENV=$(absolute_path "$LICENSE_ENV")
  [[ -f "$ABS_LICENSE_ENV" ]] || { printf 'License environment file not found: %s\n' "$ABS_LICENSE_ENV" >&2; exit 1; }
fi

RESOLVED_ENGINE=$(engine_resolve "$ENGINE") || exit $?
if [[ "$BYPASS_PERMISSIONS" == true && "$RESOLVED_ENGINE" != claude ]]; then
  printf '%s\n' '--bypass-permissions applies only to --engine claude.' >&2
  exit 2
fi

CLAUDE_PERMISSION=$ENGINE_DEFAULT_CLAUDE_PERMISSION
if [[ "$BYPASS_PERMISSIONS" == true ]]; then
  CLAUDE_PERMISSION=bypassPermissions
  printf '%s\n' 'Warning: --bypass-permissions disables all Claude permission checks for this run.' >&2
fi

engine_require_bin "$RESOLVED_ENGINE" || exit $?

if [[ "$NO_INSTALL" != true ]]; then
  "$SCRIPT_DIR/install-skill.sh" --engine "$RESOLVED_ENGINE"
fi

BUILDER_REF=$(engine_skill_ref "$RESOLVED_ENGINE" tyk-demo-builder)

PROMPT=$(mktemp "${TMPDIR:-/tmp}/tyk-demo-builder-prompt.XXXXXX")
chmod 600 "$PROMPT"
trap 'rm -f "$PROMPT"' EXIT

{
  printf 'Use %s to build and fully verify a standalone Tyk demo.\n' "$BUILDER_REF"
  printf '%s\n' 'The user is evaluating Tyk and wants each requirement below proven on a running stack.'
  printf 'Output directory: %s\n' "$ABS_OUTPUT"
  printf 'Demo slug: %s\n' "$SLUG"
  printf 'Display name: %s\n' "$NAME"
  printf 'Identity provider mode: %s\n' "$IDENTITY_MODE"
  if [[ -n "$MODULES" ]]; then printf 'Module hint: %s\n' "$MODULES"; fi
  if [[ -n "$PORT_BASE" ]]; then printf 'Requested port base: %s\n' "$PORT_BASE"; fi
  if [[ -n "$ABS_LICENSE_ENV" ]]; then
    printf 'License environment source: %s\n' "$ABS_LICENSE_ENV"
    printf '%s\n' 'Use it to populate the generated ignored .env without printing any value.'
  fi
  printf '%s\n' 'Requirement sources:'
  for source in "${ABS_REQUIREMENTS[@]}"; do printf -- '- %s\n' "$source"; done
  # The skill owns the working rules - module selection, idempotency, positive and
  # negative verification, evidence and status definitions. Restating them here would
  # give them two homes and let them drift, and anyone installing this as a plugin never
  # runs this script at all. Pass run parameters only, and let the skill carry the method.
  cat <<'EOF'

Treat the supplied requirements as the source of truth, and follow the skill's own
workflow, required validation and evidence rules in full.
EOF
  if [[ "$NON_INTERACTIVE" == true ]]; then
    printf '%s\n' 'This is a non-interactive run: do not edit the reusable builder; propose changes only.'
  fi
} >"$PROMPT"

printf 'Launching Tyk demo workflow for %s\n' "$NAME"
printf 'Agent engine: %s\n' "$RESOLVED_ENGINE"
printf 'Generated bundle target: %s\n' "$ABS_OUTPUT"

engine_launch "$RESOLVED_ENGINE" "$NON_INTERACTIVE" "$PROMPT" "$OUTPUT_PARENT" "$CLAUDE_PERMISSION"
