# shellcheck shell=bash
# Shared agent-engine abstraction for the Tyk demo and document runners.
#
# Supported engines: codex (OpenAI Codex CLI) and claude (Claude Code CLI).
# Callers must set REPO_ROOT before invoking engine_launch.

ENGINE_DEFAULT_CLAUDE_PERMISSION=auto

# engine_resolve [requested]
#
# Resolution order: explicit request, then TYK_DEMO_ENGINE, then autodetection.
# Codex wins when both CLIs are installed so existing invocations keep working.
engine_resolve() {
  local requested=${1:-${TYK_DEMO_ENGINE:-}}

  case "$requested" in
    codex|claude)
      printf '%s\n' "$requested"
      return 0
      ;;
    '') ;;
    *)
      printf 'Unknown engine: %s (expected codex or claude)\n' "$requested" >&2
      return 2
      ;;
  esac

  if command -v codex >/dev/null 2>&1; then
    printf 'codex\n'
  elif command -v claude >/dev/null 2>&1; then
    printf 'claude\n'
  else
    printf '%s\n' 'No supported agent CLI found. Install the Codex CLI or the Claude Code CLI.' >&2
    return 1
  fi
}

# engine_validate_requested <requested>
#
# Rejects a malformed --engine value during argument validation, before any
# filesystem work and without requiring an installed CLI. An empty value defers
# to engine_resolve.
engine_validate_requested() {
  case "${1:-}" in
    codex|claude|'') return 0 ;;
    *) printf 'Unknown engine: %s (expected codex or claude)\n' "$1" >&2; return 2 ;;
  esac
}

# engine_require_bin <engine>
engine_require_bin() {
  local engine=$1

  case "$engine" in
    codex) command -v codex >/dev/null || { printf '%s\n' 'Codex CLI is required for --engine codex.' >&2; return 1; } ;;
    claude) command -v claude >/dev/null || { printf '%s\n' 'Claude Code CLI is required for --engine claude.' >&2; return 1; } ;;
    *) printf 'Unknown engine: %s\n' "$engine" >&2; return 2 ;;
  esac
}

# engine_skill_ref <engine> <skill-name>
#
# Returns the invocation token for a generated runner prompt only. Files shared
# between engines must reference skills by plain name instead.
engine_skill_ref() {
  local engine=$1 name=$2

  case "$engine" in
    codex) printf '$%s\n' "$name" ;;
    claude) printf '/%s\n' "$name" ;;
    *) printf 'Unknown engine: %s\n' "$engine" >&2; return 2 ;;
  esac
}

# engine_skills_root <engine>
engine_skills_root() {
  local engine=$1

  case "$engine" in
    codex) printf '%s\n' "${CODEX_HOME:-$HOME/.codex}/skills" ;;
    claude) printf '%s\n' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" ;;
    *) printf 'Unknown engine: %s\n' "$engine" >&2; return 2 ;;
  esac
}

# engine_launch <engine> <non-interactive> <prompt-file> <output-parent> [claude-permission-mode]
#
# The Claude CLI has no working-directory flag, so it runs inside a subshell that
# changes into REPO_ROOT. Non-interactive Claude runs pass the prompt on stdin,
# which mirrors "codex exec -" and avoids the argv/stdin concatenation stall.
engine_launch() {
  local engine=$1 non_interactive=$2 prompt=$3 output_parent=$4
  local claude_permission=${5:-$ENGINE_DEFAULT_CLAUDE_PERMISSION}
  local args=()

  case "$engine" in
    codex)
      args=(-C "$REPO_ROOT" --add-dir "$output_parent" -s workspace-write)
      if [[ "$non_interactive" == true ]]; then
        codex exec "${args[@]}" - <"$prompt"
      else
        codex "${args[@]}" -a on-request "$(cat "$prompt")"
      fi
      ;;
    claude)
      args=(--add-dir "$output_parent")
      if [[ "$non_interactive" == true ]]; then
        ( cd "$REPO_ROOT" && claude -p --permission-mode "$claude_permission" "${args[@]}" <"$prompt" )
      else
        ( cd "$REPO_ROOT" && claude "${args[@]}" "$(cat "$prompt")" )
      fi
      ;;
    *)
      printf 'Unknown engine: %s\n' "$engine" >&2
      return 2
      ;;
  esac
}
