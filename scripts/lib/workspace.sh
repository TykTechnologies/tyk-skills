# shellcheck shell=bash
# The project workspace: everything the skills generate lives under one root, one folder per
# project, so material from different evaluations never mixes and never lands in this
# repository or in an unrelated codebase.
#
#   ${TYK_WORKSPACE:-~/tyk-workspace}/<project>/
#       demos/<slug>/       demo and proof of concept bundles (their private .env holds licences)
#       sizing/             sizing briefs and packs
#       docs/               diagrams, runbooks, migration plans and other documents

workspace_root() {
  printf '%s\n' "${TYK_WORKSPACE:-$HOME/tyk-workspace}"
}

# Lowercase, hyphenated folder name for a project: "Payments Gateway POC" -> payments-gateway-poc.
workspace_project() {
  python3 - "$1" <<'PY'
import re
import sys
print(re.sub(r"[^a-z0-9]+", "-", sys.argv[1].lower()).strip("-"))
PY
}

# workspace_path <kind> <project> [<slug>] prints the default output folder, or fails when
# the project name has no usable characters.
workspace_path() {
  local kind=$1 project
  project=$(workspace_project "$2")
  [[ -n "$project" ]] || return 1
  if [[ -n "${3:-}" ]]; then
    printf '%s/%s/%s/%s\n' "$(workspace_root)" "$project" "$kind" "$3"
  else
    printf '%s/%s/%s\n' "$(workspace_root)" "$project" "$kind"
  fi
}
