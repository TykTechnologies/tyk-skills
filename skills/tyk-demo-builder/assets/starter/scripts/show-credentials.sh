#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
[[ -f "${RUNTIME_ENV}" ]] || { echo "No runtime credentials. Run bootstrap and configure first." >&2; exit 1; }
cat "${RUNTIME_ENV}"
