#!/usr/bin/env sh
set -eu

gateway_version=${TYK_GATEWAY_VERSION:?Set TYK_GATEWAY_VERSION to the exact Gateway tag, for example v5.13.1}
gateway_arch=${TYK_GATEWAY_ARCH:?Set TYK_GATEWAY_ARCH to the Gateway architecture, for example amd64}

printf '%s\n' "$gateway_version" |
	grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' || {
		echo "TYK_GATEWAY_VERSION must be an exact stable vMAJOR.MINOR.PATCH tag" >&2
		exit 2
	}

case "$gateway_version" in
	*-*|*+*)
		echo "Prerelease and metadata tags are not accepted; use an exact stable Gateway release" >&2
		exit 2
		;;
esac

case "$gateway_arch" in
	x86_64) gateway_arch=amd64 ;;
	aarch64) gateway_arch=arm64 ;;
esac

case "$gateway_arch" in
	amd64|arm64) ;;
	*)
		echo "Unsupported TYK_GATEWAY_ARCH: $gateway_arch" >&2
		exit 2
		;;
esac

command -v docker >/dev/null 2>&1 || {
	echo "docker is required to run the Tyk plugin compiler" >&2
	exit 127
}

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
artifact="post_enrich_${gateway_version}_linux_${gateway_arch}.so"
compat_artifact="post_enrich_${gateway_version#v}_linux_${gateway_arch}.so"
output_dir="$script_dir/build"
mkdir -p "$output_dir"
stage_dir=$(mktemp -d "$output_dir/.compiler-source.XXXXXX")
trap 'rm -rf "$stage_dir"' EXIT HUP INT TERM
cp "$script_dir/plugin.go" "$stage_dir/"

docker run --rm \
	--platform linux/amd64 \
	--volume "$stage_dir:/plugin-source" \
	"tykio/tyk-plugin-compiler:$gateway_version" \
	post_enrich.so "" linux "$gateway_arch"

set -- "$stage_dir"/*_linux_"$gateway_arch".so
if [ "$#" -ne 1 ] || [ ! -s "$1" ]; then
	echo "Compiler did not produce exactly one *_linux_${gateway_arch}.so artifact" >&2
	exit 1
fi

cp "$1" "$output_dir/$artifact"
cp "$1" "$output_dir/$compat_artifact"
cp "$1" "$output_dir/post_enrich.so"

cat >"$output_dir/build-metadata.json" <<EOF
{
  "gateway_version": "$gateway_version",
  "target_os": "linux",
  "target_arch": "$gateway_arch",
  "compiler_image": "tykio/tyk-plugin-compiler:$gateway_version",
  "stable_artifact": "post_enrich.so",
  "versioned_artifact": "$artifact",
  "compatibility_artifact": "$compat_artifact"
}
EOF

printf 'Built stable and versioned plugins for Tyk Gateway %s on linux/%s\n' \
    "$gateway_version" "$gateway_arch"
