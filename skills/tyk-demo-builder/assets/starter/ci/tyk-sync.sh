#!/usr/bin/env bash
set -euo pipefail
# Dumps hold every owned API and policy, so everything this script writes under .runtime/ is
# private: 0700 directories and 0600 files, as validate_bundle.py requires.
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/.runtime/tyk-sync"
TYK_SYNC_BIN="${TYK_SYNC_BIN:-tyk-sync}"

usage() {
  echo "Usage: $0 validate <definitions-dir> | promote <definitions-dir> | rollback <dump-dir>" >&2
  exit 2
}

validate_definitions() {
  local definitions_dir="$1" found=0 file
  [[ -d "${definitions_dir}" ]] || { echo "Definitions directory not found: ${definitions_dir}" >&2; exit 1; }
  while IFS= read -r file; do
    found=1
    jq -e . "${file}" >/dev/null
  done < <(find "${definitions_dir}" -type f -name '*.json' -print | sort)
  [[ "${found}" -eq 1 ]] || { echo "No JSON definitions found in ${definitions_dir}." >&2; exit 1; }
}

require_runtime() {
  : "${TYK_SYNC_DASHBOARD_URL:?Set TYK_SYNC_DASHBOARD_URL}"
  : "${TYK_SYNC_SECRET:?Set TYK_SYNC_SECRET}"
  : "${TYK_SYNC_ORG_ID:?Set TYK_SYNC_ORG_ID}"
  command -v "${TYK_SYNC_BIN}" >/dev/null || { echo "Tyk Sync not found: ${TYK_SYNC_BIN} (set TYK_SYNC_BIN, or use ci/tyk-sync-docker.sh)" >&2; exit 1; }
  # tyk-sync reads the Dashboard secret from TYKGIT_DB_SECRET when --secret is absent, so the
  # secret never reaches a command line that any local user can read from the process list.
  export TYKGIT_DB_SECRET="${TYK_SYNC_SECRET}"
  mkdir -p "${RUNTIME_DIR}/rollback" "${RUNTIME_DIR}/evidence" "${RUNTIME_DIR}/snapshots"
  chmod 700 "${ROOT_DIR}/.runtime" "${RUNTIME_DIR}"
}

# tyk-sync writes with its own umask (in a container, the image's), so its output is made
# private after the fact.
make_private() {
  chmod -R go-rwx "$1"
}

dump_dashboard() {
  local target="$1"
  mkdir -p "${target}"
  "${TYK_SYNC_BIN}" dump --dashboard "${TYK_SYNC_DASHBOARD_URL}" --target "${target}"
  make_private "${target}"
}

expected_api_ids() {
  for file in "${ROOT_DIR}"/apis/*.oas.json; do
    [[ -e "${file}" ]] && jq -r '."x-tyk-api-gateway".info.id' "${file}"
  done | sort -u
}

# A bundle file is a bare Tyk OAS document. tyk-sync 2.x dumps each Tyk OAS API wrapped as
# {"oas": {...}} (plus hook_references, is_site, sort_by, user_owners and user_group_owners)
# next to policy-<id>.json files and a .tyk.json index, so both shapes are read. Policies are
# skipped: their id is not an API id.
definition_id() {
  jq -r 'if type != "object" or has("access_rights") then empty
         elif has("oas") then .oas["x-tyk-api-gateway"].info.id
         else (.api_id // .["x-tyk-api-gateway"].info.id // .id) end // empty' "$1"
}

filter_owned_definitions() {
  local source="$1" target="$2" strict="${3:-false}" file api_id found=0
  mkdir -p "${target}"
  while IFS= read -r file; do
    api_id="$(definition_id "${file}")"
    [[ -n "${api_id}" ]] || continue
    if grep -Fxq "${api_id}" < <(expected_api_ids); then
      cp "${file}" "${target}/${api_id}.json"
      found=1
    elif [[ "${strict}" == true ]]; then
      echo "Refusing unowned Tyk Sync definition: ${api_id}" >&2
      exit 1
    fi
  done < <(find "${source}" -type f -name '*.json' ! -name '.tyk.json' -print | sort)
  [[ "${found}" -eq 1 ]] || { echo "No owned API definitions found in ${source}." >&2; exit 1; }
}

# tyk-sync 2.x publishes only what a .tyk.json index lists, and reads a Tyk OAS API only in its
# {"oas": ...} wrapper. Wrap every bare OAS document in the directory and write the index.
write_sync_index() {
  local dir="$1" file name entries='[]' wrapped
  while IFS= read -r file; do
    name="$(basename "${file}")"
    if jq -e 'has("oas") | not' "${file}" >/dev/null && jq -e 'has("x-tyk-api-gateway")' "${file}" >/dev/null; then
      wrapped="$(jq '{oas: .}' "${file}")"
      printf '%s\n' "${wrapped}" >"${file}"
    fi
    if jq -e 'has("oas")' "${file}" >/dev/null; then
      entries="$(jq -c --arg file "${name}" '. + [{file: $file, oas: {}}]' <<<"${entries}")"
    else
      entries="$(jq -c --arg file "${name}" '. + [{file: $file}]' <<<"${entries}")"
    fi
  done < <(find "${dir}" -maxdepth 1 -type f -name '*.json' ! -name '.tyk.json' -print | sort)
  jq -n --argjson files "${entries}" '{type: "apidef", files: $files}' >"${dir}/.tyk.json"
}

# publish only creates (it stops on an API that already exists) and update only updates (it
# refuses one that does not), so each owned definition goes to the command that fits whether
# the Dashboard already holds it. Neither 2.x command takes --org: the Dashboard assigns the
# organisation of the key. Prints "<created> <updated>" counts.
deliver() {
  local source="$1" existing="$2" work="$3" file api_id created=0 updated=0
  mkdir -p "${work}/create" "${work}/update"
  while IFS= read -r file; do
    api_id="$(definition_id "${file}")"
    [[ -n "${api_id}" ]] || continue
    if [[ -f "${existing}/${api_id}.json" ]]; then
      cp "${file}" "${work}/update/" && updated=$((updated + 1))
    else
      cp "${file}" "${work}/create/" && created=$((created + 1))
    fi
  done < <(find "${source}" -maxdepth 1 -type f -name '*.json' ! -name '.tyk.json' -print | sort)
  if [[ "${updated}" -gt 0 ]]; then
    write_sync_index "${work}/update"
    "${TYK_SYNC_BIN}" update --dashboard "${TYK_SYNC_DASHBOARD_URL}" --path "${work}/update" >&2
  fi
  if [[ "${created}" -gt 0 ]]; then
    write_sync_index "${work}/create"
    "${TYK_SYNC_BIN}" publish --dashboard "${TYK_SYNC_DASHBOARD_URL}" --path "${work}/create" >&2
  fi
  printf '%s %s\n' "${created}" "${updated}"
}

# Every owned API id must be in the read-back, in the organisation the promotion targeted.
verify_expected_ids() {
  local evidence_dir="$1" expected_id file matched
  while IFS= read -r expected_id; do
    matched=0
    while IFS= read -r file; do
      if [[ "$(definition_id "${file}")" == "${expected_id}" ]] \
        && jq -e --arg org "${TYK_SYNC_ORG_ID}" '((.oas // .)["x-tyk-api-gateway"].info.orgId // $org) == $org' "${file}" >/dev/null; then
        matched=1
        break
      fi
    done < <(find "${evidence_dir}" -type f -name '*.json' ! -name '.tyk.json' -print)
    if [[ "${matched}" -ne 1 ]]; then
        echo "Read-back evidence does not contain expected API ID ${expected_id} in organisation ${TYK_SYNC_ORG_ID}" >&2
        exit 1
    fi
  done < <(expected_api_ids)
}

# The fields a promotion must deliver. The Dashboard read-back is never byte-equal to what was
# sent (it adds servers, dbId and orgId and drops defaults such as an empty components block,
# false flags and the default Authorization header location), so each owned API is compared on
# what the demo's behaviour depends on: name and state, listen path, upstream, authentication,
# gateway tags, the operations and the middleware set on them.
DELIVERY_VIEW='(.oas // .) as $d | ($d["x-tyk-api-gateway"] // {}) as $x | {
  title: $d.info.title, version: $d.info.version,
  operations: ([($d.paths // {}) | to_entries[] | .key as $p | .value | to_entries[]
    | select(.key | IN("get","put","post","delete","patch","head","options","trace"))
    | "\(.key | ascii_upcase) \($p) \(.value.operationId // "")"] | sort),
  id: $x.info.id, name: $x.info.name, active: ($x.info.state.active // false),
  listenPath: $x.server.listenPath.value, strip: ($x.server.listenPath.strip // false),
  upstream: $x.upstream.url,
  authentication: ($x.server.authentication.enabled // false),
  securitySchemes: (($x.server.authentication.securitySchemes // {}) | keys),
  gatewayTags: ($x.server.gatewayTags.tags // []),
  operationMiddleware: (($x.middleware.operations // {}) | with_entries(.value |= (keys | sort)))
}'

# sha256 of a definition's canonical JSON (bare OAS, keys sorted), the same for a bundle file
# and its {"oas": ...} wrapper, so verify-demo.sh can tell whether apis/ changed since.
definition_digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    jq -S -c '(.oas // .)' "$1" | sha256sum | cut -d' ' -f1
  else
    jq -S -c '(.oas // .)' "$1" | shasum -a 256 | cut -d' ' -f1
  fi
}

# Every owned definition that was sent must read back with the same delivery view; a stale or
# different configuration under the same API id fails the promotion.
verify_delivered_content() {
  local sent_dir="$1" evidence_dir="$2" file api_id mismatched=()
  while IFS= read -r file; do
    api_id="$(definition_id "${file}")"
    [[ -n "${api_id}" ]] || continue
    if [[ ! -f "${evidence_dir}/${api_id}.json" ]] \
      || ! diff <(jq -S "${DELIVERY_VIEW}" "${file}") <(jq -S "${DELIVERY_VIEW}" "${evidence_dir}/${api_id}.json") >/dev/null; then
      mismatched+=("${api_id}")
    fi
  done < <(find "${sent_dir}" -maxdepth 1 -type f -name '*.json' ! -name '.tyk.json' -print | sort)
  if [[ ${#mismatched[@]} -gt 0 ]]; then
    echo "Read-back content differs from what was delivered for: ${mismatched[*]}" >&2
    exit 1
  fi
}

# {"<api id>": "<digest>"} for the definitions a delivery sent.
delivered_digests() {
  local sent_dir="$1" file api_id entries='{}'
  while IFS= read -r file; do
    api_id="$(definition_id "${file}")"
    [[ -n "${api_id}" ]] || continue
    entries="$(jq -c --arg id "${api_id}" --arg digest "$(definition_digest "${file}")" '. + {($id): $digest}' <<<"${entries}")"
  done < <(find "${sent_dir}" -maxdepth 1 -type f -name '*.json' ! -name '.tyk.json' -print | sort)
  printf '%s\n' "${entries}"
}

# The summary scripts/verify-demo.sh reads for SYNC-001.
record_delivery() {
  local operation="$1" delivery_id="$2" counts="$3" sent_dir="$4"
  jq -n --arg operation "${operation}" --arg id "${delivery_id}" --arg org "${TYK_SYNC_ORG_ID}" \
    --argjson ids "$(expected_api_ids | jq -R . | jq -sc .)" \
    --argjson digests "$(delivered_digests "${sent_dir}")" \
    --argjson created "${counts% *}" --argjson updated "${counts#* }" \
    --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{proof: "SYNC-001", operation: $operation, promotion_id: $id, org_id: $org, api_ids: $ids,
      created: $created, updated: $updated, api_digests: $digests,
      content_check: "delivery view of every sent API equals its read-back",
      evidence: ("evidence/" + $id), recorded_at: $at}' \
    >"${RUNTIME_DIR}/latest-promotion.json"
  printf '%s\n' "${delivery_id}" >"${RUNTIME_DIR}/latest-promotion"
}

operation="${1:-}"
source_path="${2:-}"
[[ -n "${operation}" && -n "${source_path}" ]] || usage

case "${operation}" in
  validate)
    validate_definitions "${source_path}"
    echo "Tyk Sync definitions are valid JSON."
    ;;
  promote)
    validate_definitions "${source_path}"
    require_runtime
    promotion_id="${TYK_SYNC_PROMOTION_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
    snapshot_dir="${RUNTIME_DIR}/snapshots/${promotion_id}"
    rollback_dir="${RUNTIME_DIR}/rollback/${promotion_id}"
    evidence_dir="${RUNTIME_DIR}/evidence/${promotion_id}"
    raw_rollback_dir="${RUNTIME_DIR}/raw/${promotion_id}-before"
    raw_evidence_dir="${RUNTIME_DIR}/raw/${promotion_id}-after"
    [[ ! -e "${snapshot_dir}" && ! -e "${rollback_dir}" && ! -e "${evidence_dir}" ]] || {
      echo "Promotion ID already exists and is immutable: ${promotion_id}" >&2
      exit 1
    }
    mkdir -p "${snapshot_dir}"
    filter_owned_definitions "${source_path}" "${snapshot_dir}" true
    write_sync_index "${snapshot_dir}"
    find "${snapshot_dir}" -type f -exec chmod a-w {} +
    dump_dashboard "${raw_rollback_dir}"
    # A first promotion to an empty Dashboard has nothing to roll back to. A subshell, because
    # the filter exits when it finds no owned definition.
    (filter_owned_definitions "${raw_rollback_dir}" "${rollback_dir}" false 2>/dev/null) || true
    write_sync_index "${rollback_dir}"
    counts="$(deliver "${snapshot_dir}" "${rollback_dir}" "${RUNTIME_DIR}/publish/${promotion_id}")"
    dump_dashboard "${raw_evidence_dir}"
    filter_owned_definitions "${raw_evidence_dir}" "${evidence_dir}" false
    verify_expected_ids "${evidence_dir}"
    verify_delivered_content "${snapshot_dir}" "${evidence_dir}"
    record_delivery promote "${promotion_id}" "${counts}" "${snapshot_dir}"
    echo "Promotion ${promotion_id} verified (created ${counts% *}, updated ${counts#* }); rollback and read-back evidence retained under .runtime/tyk-sync."
    ;;
  rollback)
    require_runtime
    validate_definitions "${source_path}"
    rollback_id="${TYK_SYNC_PROMOTION_ID:-rollback-$(date -u +%Y%m%dT%H%M%SZ)}"
    evidence_dir="${RUNTIME_DIR}/evidence/${rollback_id}"
    publish_dir="${RUNTIME_DIR}/snapshots/${rollback_id}"
    current_dir="${RUNTIME_DIR}/raw/${rollback_id}-current"
    raw_before_dir="${RUNTIME_DIR}/raw/${rollback_id}-before"
    raw_evidence_dir="${RUNTIME_DIR}/raw/${rollback_id}"
    [[ ! -e "${evidence_dir}" ]] || { echo "Evidence ID already exists: ${rollback_id}" >&2; exit 1; }
    filter_owned_definitions "${source_path}" "${publish_dir}" true
    write_sync_index "${publish_dir}"
    dump_dashboard "${raw_before_dir}"
    (filter_owned_definitions "${raw_before_dir}" "${current_dir}" false 2>/dev/null) || true
    counts="$(deliver "${publish_dir}" "${current_dir}" "${RUNTIME_DIR}/publish/${rollback_id}")"
    dump_dashboard "${raw_evidence_dir}"
    filter_owned_definitions "${raw_evidence_dir}" "${evidence_dir}" false
    verify_expected_ids "${evidence_dir}"
    verify_delivered_content "${publish_dir}" "${evidence_dir}"
    record_delivery rollback "${rollback_id}" "${counts}" "${publish_dir}"
    echo "Rollback publish ${rollback_id} verified by Dashboard read-back."
    ;;
  *) usage ;;
esac
