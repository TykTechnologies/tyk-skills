#!/usr/bin/env bash
# Runs the pinned tykio/tyk-sync image as a drop-in for a local tyk-sync binary:
#
#   TYK_SYNC_BIN=./ci/tyk-sync-docker.sh ./ci/tyk-sync.sh promote apis
#
# - The bundle is mounted at its own absolute path, so the absolute paths ci/tyk-sync.sh passes
#   (.runtime/tyk-sync/...) mean the same inside the container.
# - The image's entrypoint is ./tyk-sync relative to its working directory /opt/tyk-sync, so
#   running from another working directory needs the absolute entrypoint.
# - The container runs as the caller's uid and gid, so the dumps it writes belong to them.
# - host.docker.internal reaches a Dashboard published on this host. Use
#   http://host.docker.internal:<port> rather than localhost, which inside the container is
#   the container itself. Docker Desktop and OrbStack forward the name to the host's loopback,
#   where the bundle publishes its ports (BIND_ADDRESS, 127.0.0.1 by default). A Linux Docker
#   Engine would map it to the bridge gateway, which a loopback bind does not answer, so there
#   the container runs on the host network with the name pointing at 127.0.0.1.
# - The Dashboard secret is passed by name (-e TYKGIT_DB_SECRET), never as a value on the
#   command line.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${TYK_SYNC_VERSION:-}"
if [[ -z "${version}" ]]; then
  for env_file in "${ROOT_DIR}/.env" "${ROOT_DIR}/.env.example"; do
    [[ -f "${env_file}" ]] || continue
    version="$(sed -n 's/^TYK_SYNC_VERSION=//p' "${env_file}" | tail -1)"
    [[ -z "${version}" ]] || break
  done
fi
[[ -n "${version}" ]] || { echo "Set TYK_SYNC_VERSION (the bundle's .env carries it)." >&2; exit 1; }

network_args=(--add-host host.docker.internal:host-gateway)
docker_os="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || true)"
if [[ "$(uname -s)" == Linux && "${docker_os}" != *"Docker Desktop"* ]]; then
  network_args=(--network host --add-host host.docker.internal:127.0.0.1)
fi

exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  "${network_args[@]}" \
  -e TYKGIT_DB_SECRET \
  -v "${ROOT_DIR}:${ROOT_DIR}" -w "${ROOT_DIR}" \
  --entrypoint /opt/tyk-sync/tyk-sync \
  "tykio/tyk-sync:${version}" "$@"
