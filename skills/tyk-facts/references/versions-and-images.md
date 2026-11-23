# Versions and images

Last verified: 2026-11-19 against Docker Hub tags, GitHub tags and releases, the Tyk Helm chart repository and the Tyk release notes

Never write a version as "the latest" from memory. Resolve it with the commands below, then
say which source you used and when. The tags in the "Example" column were the newest strict
`vX.Y.Z` tags in each release line on 2026-11-19 and are examples only.

## Components

| Component | Docker Hub image | Licence | Example as of 2026-11-19 | Release notes |
| --- | --- | --- | --- | --- |
| Tyk Gateway (open source build) | `tykio/tyk-gateway` | MPL 2.0 | `v5.16.0` | https://tyk.io/docs/developer-support/release-notes/gateway |
| Tyk Gateway Enterprise Edition build | `tykio/tyk-gateway-ee` | Licensed (`ee` features) | `v5.16.0` | https://tyk.io/docs/developer-support/release-notes/gateway |
| Tyk Dashboard | `tykio/tyk-dashboard` | Licensed | `v5.16.0` | https://tyk.io/docs/developer-support/release-notes/dashboard |
| Tyk Pump | `tykio/tyk-pump-docker-pub` | MPL 2.0 | `v1.18.0` | https://tyk.io/docs/developer-support/release-notes/pump |
| Tyk Developer Portal | `tykio/portal` | Licensed | `v1.20.1` (the release notes list 1.20.0 as the newest) | https://tyk.io/docs/developer-support/release-notes/portal |
| MDCB | `tykio/tyk-mdcb-docker` | Licensed | `v2.14.0` | https://tyk.io/docs/developer-support/release-notes/mdcb |
| Tyk Operator | `tykio/tyk-operator` | Licensed | `v1.6.0` | https://tyk.io/docs/developer-support/release-notes/operator |
| Tyk Sync | `tykio/tyk-sync` | Licensed Dashboard required | `v2.3.0` | https://tyk.io/docs/developer-support/release-notes/sync |
| AI Studio Community Edition | `tykio/tyk-ai-studio` (Edge Gateway: `tykio/tyk-microgateway`) | AGPL-3.0 | 2.3 line: resolve live ("AI Studio 2.3" below) | https://tyk.io/docs/developer-support/release-notes/ai-studio |
| AI Studio Enterprise Edition | `tykio/tyk-ai-studio-ent` (Edge Gateway: `tykio/tyk-microgateway-ent`) | Licensed | 2.3 line: resolve live ("AI Studio 2.3" below) | https://tyk.io/docs/developer-support/release-notes/ai-studio |
| Helm charts | `tyk-helm` repository, https://helm.tyk.io/public/helm/charts/ | MPL-2.0 (https://github.com/TykTechnologies/tyk-charts) | chart `5.5.0` | https://tyk.io/docs/developer-support/release-notes/helm-chart |

Sources for the licence column: Gateway and Pump open source, everything else licensed:
`licensing-and-packaging.md`. Gateway source tree: the `LICENSE.md` of
https://github.com/TykTechnologies/tyk says the code is MPL 2.0 "except for the 'ee' folder",
which "is subject to a separate commercial license". AI Studio: the `LICENSE.md` of
https://github.com/TykTechnologies/ai-studio is the GNU Affero General Public License v3; the
Community/Enterprise image split is the one used by the `tyk-install` repository
(`docker/ai-studio/.env.example`, "Community Edition by default",
https://github.com/TykTechnologies/tyk-install), and AI Studio and its Edge Gateway run the same
version. The Enterprise images exit at start without a licence that covers AI Studio 2.3
(`editions-and-features.md`). Tyk Sync: "Tyk Sync v2.0 compatible exclusively with licensed Tyk
Dashboard" (https://tyk.io/docs/api-management/automations/sync).

Every paid customer runs the Enterprise Edition gateway build (`licensing-and-packaging.md`). The Tyk Streams docs name the image: "It uses the Enterprise Edition of Tyk Gateway (`tykio/tyk-gateway-ee`)"
(https://tyk.io/docs/api-management/event-driven-apis).

## Resolve the latest stable tag

Docker Hub tells you whether an image tag exists. It is not a release list. Anonymous tag
listing stops after 1,000 tags (`tykio/tyk-gateway` had 2,965 on 2026-11-19), so "the newest
tag on the first page" can miss a release. Resolve in two steps:

1. Take the release line (for example `5.16`) from the component's release notes page in the
   table above.
2. Ask Docker Hub for the newest strict `vX.Y.Z` tag in that line, and confirm the tag exists
   for your architecture. Release candidates, `latest` and architecture tags are ignored.

The functions below work in `sh`, `bash` and `zsh`.

```sh
hub="https://hub.docker.com/v2/namespaces/tykio/repositories"

# Newest vX.Y.Z tag within one release line, for example: newest tyk-gateway 5.16
newest() {
  url="$hub/$1/tags?page_size=100&name=v$2."
  tags=""
  while [ -n "$url" ] && [ "$url" != "null" ]; do
    page="$(curl -fsS --max-time 20 "$url")" || { echo "lookup failed for $1 $2" >&2; return 1; }
    tags="$tags
$(printf '%s' "$page" | jq -r '.results[].name')"
    url="$(printf '%s' "$page" | jq -r '.next')"
  done
  best="$(printf '%s\n' "$tags" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -t. -k1.2,1n -k2,2n -k3,3n | tail -1)"
  [ -n "$best" ] || { echo "no vX.Y.Z tag for $1 in the $2 line" >&2; return 1; }
  printf '%s\n' "$best"
}

# Does this exact tag exist, and for which architectures?
exists() {
  meta="$(curl -fsS --max-time 20 "$hub/$1/tags/$2")" || { echo "$1:$2 not found, or the lookup failed" >&2; return 1; }
  printf '%s' "$meta" | jq -r '"\(.name): " + ([.images[].architecture] | unique | join(", "))'
}

newest tyk-gateway 5.16      # for example v5.16.0
exists tyk-gateway v5.16.0   # for example v5.16.0: amd64, arm64, s390x
```

Without `jq`:

```sh
python3 - tyk-dashboard 5.16 <<'EOF'
import json, re, sys, urllib.request
repo, line = sys.argv[1], sys.argv[2]
url = f"https://hub.docker.com/v2/namespaces/tykio/repositories/{repo}/tags?page_size=100&name=v{line}."
names = []
while url:
    with urllib.request.urlopen(url, timeout=20) as response:
        page = json.load(response)
    names += [tag["name"] for tag in page["results"]]
    url = page.get("next")
tags = [n for n in names if re.fullmatch(r"v\d+\.\d+\.\d+", n)]
if not tags:
    sys.exit(f"no vX.Y.Z tag for {repo} in the {line} line")
print(max(tags, key=lambda s: tuple(map(int, s[1:].split(".")))))
EOF
```

### AI Studio 2.3

Resolve the AI Studio tag with this lookup, not with `newest`. It lists the 2.3 tags of one
image, skips unstable builds and prints the highest by version order (a plain `vX.Y.Z` tag
ranks above any tag with a suffix). Use the exact tag it prints. The control plane and the Edge
Gateway run at the same tag, and on 2026-11-19 all four AI Studio images carried the same 2.3
tag.

```sh
repo=tyk-ai-studio   # or tyk-ai-studio-ent, tyk-microgateway, tyk-microgateway-ent
curl -fsS --max-time 20 "https://hub.docker.com/v2/namespaces/tykio/repositories/$repo/tags?page_size=100&name=v2.3." |
  python3 -c 'import json, re, sys; tags = [t["name"] for t in json.load(sys.stdin)["results"] if "unstable" not in t["name"]]; tags or sys.exit("no 2.3 image tag found"); print(max(tags, key=lambda s: ("-" not in s, [int(n) for n in re.findall(r"\d+", s)])))'
```

A failed lookup is a failure. Say so and stop; never fall back to an example tag without saying
it is one. The newest tag is also not automatically the right one: check it against the
"Compatibility Matrix For Tyk Components" in the Gateway release notes for the release you
deploy, and against the support channel the user needs (latest or long-term support).

Public source repositories: use tags, not the "Latest release" badge. On 2026-11-19 the GitHub
release objects lagged the tags for some repositories (`tyk-pump` showed `v1.16.0` as its
"latest" release while the `v1.18.0` tag existed). Check tags with:

```sh
for r in tyk tyk-pump ai-studio; do
  git ls-remote --tags --refs "https://github.com/TykTechnologies/$r.git" |
    grep -E 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' | sort -t/ -k3 -V | tail -1
done
```

The filter drops `-rc`, `-alpha` and `-beta` tags; use them only when the user asked for a
pre-release.

The public `tyk-operator` and `tyk-sync` GitHub repositories are archived with a default branch
named `deprecated`; the Operator release notes say Operator is closed source from v1.0, and the
Sync release notes say Sync is closed source from v2.0. Resolve those two from Docker Hub and the
release notes only.

Helm charts: resolve from the chart repository, not from GitHub releases (on 2026-11-19 the
GitHub "latest" release was `5.4.0` while the chart repository served `5.5.0`):

```sh
helm repo add tyk-helm https://helm.tyk.io/public/helm/charts/ && helm repo update
helm search repo tyk-helm/tyk-stack
helm show values tyk-helm/tyk-stack | grep -A3 'repository: tykio/tyk-gateway'
```

## Facts to state with every version answer

- **Chart defaults can trail the latest Gateway. Pin the tag.** The Gateway 5.16.0 release notes
  say "Tyk Helm Charts are configured to install the LTS version of Tyk Gateway. You will need to
  modify them to install v5.16.0" (https://tyk.io/docs/developer-support/release-notes/gateway),
  and the chart 5.5.0 notes say it "installs the most recent Tyk LTS release 5.13.5"
  (https://tyk.io/docs/developer-support/release-notes/helm-chart). On 2026-11-19,
  `helm show values tyk-helm/tyk-stack` (chart 5.5.0) defaulted the gateway to
  `tykio/tyk-gateway-ee:v5.13.5`. Read the values of the chart version you install, and set the
  image tags explicitly.
- **5.16.0 and Portal 1.20.0 carry breaking changes; read them before pinning.** The Gateway
  5.16.0 release notes (https://tyk.io/docs/developer-support/release-notes/gateway, "Breaking
  Changes") list four: detailed analytics recording now redacts the `Authorization` header and
  any custom auth header (`analytics_config.allow_unsafe_detailed_logs` restores the old
  behaviour); body-transform and webhook template files must live under the configured
  `template_path` (`allow_unsafe_body_transform_template_paths`,
  `allow_unsafe_webhook_template_paths`); Strip Listen Path no longer rewrites URL Rewrite
  targets that start with the listen path (opt-out `apply_strip_listen_path_to_url_rewrite: true`,
  `TYK_GW_APPLYSTRIPLISTENPATHTOURLREWRITE`, named in the changelog entry; both work on 5.16.0);
  and custom API IDs created through the Gateway or
  Dashboard API must use only letters, digits, `.`, `_`, `-` and `~` (`allow_unsafe_api_ids`),
  which matters when a migration keeps identifiers from another gateway (the Dashboard 5.16.0
  notes carry the same rule for the Dashboard API). The Portal 1.20.0 notes
  (https://tyk.io/docs/developer-support/release-notes/portal) make "the SMTP password and the
  OAuth 2.0 provider registration access token" write-only: "The API no longer returns their
  stored values". Checked 2026-11-19.
- **The `tyk-install` repository pins versions and updates them by automation.** Its public
  history shows recurring `chore: sync component versions` commits by `github-actions[bot]`
  (most recent 2026-10-28, checked 2026-11-19). On 2026-11-19 `docker/self-managed/.env.example`
  pinned Gateway and Dashboard `v5.16.0`, Pump `v1.18.0` and Portal `v1.20.0` (one patch behind
  Docker Hub's `v1.20.1`), and `docker/ai-studio/.env.example` pinned AI Studio `v2.2.2`
  (https://github.com/TykTechnologies/tyk-install). Read the `.env.example` you deploy from and
  set the tags you resolved; do not assume.
- **Gateway and Dashboard versions move together.** Both shipped 5.13.0, 5.14.0, 5.15.0 and
  5.16.0 as matched releases (5.16.1 release candidates of both were on Docker Hub on
  2026-11-19) (compare the release-notes pages above). Running them at the same
  version is the safe default. For an upgrade, follow the upgrade notes and the compatibility
  matrix for the exact source and target versions; this note does not say which mixed versions
  are supported. The Gateway release notes carry a "Compatibility Matrix For Tyk Components" table with
  the recommended MDCB, Operator, Sync, Helm chart and Pump versions; read it for the release you
  deploy.
- **Release cadence (observed, not a documented commitment).** Gateway minor releases on the
  release-notes page: 5.13.0 on 19 May 2026, 5.14.0 on 7 July 2026, 5.15.0 on 1 September 2026,
  5.16.0 on 27 October 2026, that is roughly every seven to eight weeks. No public page found
  states a fixed cadence; do not promise one.
- **Long-term support.** https://tyk.io/docs/developer-support/release-types/long-term-support
  describes LTS for Gateway and Dashboard: 12 months of full support with maintenance releases
  "every eight weeks", then 12 months of maintenance support. On 2026-11-19 it says "Our current
  long term support release is 5.13 LTS". The Gateway release notes add: "Only the most recent
  Tyk Gateway release and the most recent on the Long Term Support (LTS) branch are
  supported". A non-LTS minor "reaches its End-of-Life (EOL) as soon as
  the next minor version is released". The Operator and Sync release notes each say "Only the
  most recent ... release is supported".
- **Docs versions.** "Unversioned URLs ... are the latest release" and `https://tyk.io/docs/5.13/`
  prefixes the LTS docs (https://tyk.io/docs/llms.txt). On 2026-11-19 that index still named
  v5.15 as the latest release while the release notes listed 5.16.0; trust the release notes for
  the version. Prefer the docs for the version in use.

## Resolve live, then quote

- **Tyk Operator 1.6 and the Kubernetes Gateway API.** "Tyk Operator 1.6 adds Kubernetes Gateway
  API support: you can route traffic through your Tyk Gateways with the standard GatewayClass,
  Gateway and HTTPRoute resources (with Tyk Dashboard), alongside Tyk's own custom resources
  (CRDs) for APIs and security policies" (https://tyk.io/docs/developer-support/release-notes/operator
  and https://tyk.io/docs/apim; guide: https://tyk.io/docs/product-stack/tyk-operator/gateway-api/overview).
  The Gateway 5.16.0 compatibility matrix recommends Operator v1.6.0 and Helm chart v5.5.0; on
  2026-11-19 both existed (`tykio/tyk-operator:v1.6.0`, chart `5.5.0`). Resolve the exact
  Operator tag from Docker Hub and the chart version from the Helm repository with the commands
  above before writing either into a manifest.
- **MDCB image.** The MDCB image is `tykio/tyk-mdcb-docker`, as the MDCB release notes name it.
  Its Docker Hub description still read "MDCB Docker Image (internal use)" on 2026-11-19; ignore
  the label and use the image.
