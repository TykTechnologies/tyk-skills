#!/usr/bin/env python3
"""Generate an isolated, neutral Tyk demo bundle from bundled assets."""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import sys
import urllib.request


SKILL_DIR = Path(__file__).resolve().parents[1]
STARTER = SKILL_DIR / "assets" / "starter"

SUPPORTED = {
    "rest", "api-key", "basic-auth", "jwt", "oauth", "mtls", "chained-auth",
    "routing", "transforms", "validation", "error-overrides", "rate-limit",
    "quota", "cache", "circuit-breaker", "websocket", "graphql", "streams",
    "portal", "dcr", "portal-branding", "otel", "go-plugin", "grpc-plugin",
    "operator", "tyk-sync", "cicd", "governance", "mcp", "ai-studio",
    "gateway-sharding", "js-plugin", "key-import", "custom-domain", "portal-catalogues",
    "versioning", "vault", "upstream-mtls",
}

# ai-studio deliberately has no dependencies. Tyk AI Studio is a separate product
# from the Gateway, with its own licence, images, compose project and ports, so it
# neither needs nor implies any Gateway module. It is emitted as a self-contained
# companion/ai-studio/ directory rather than as services on the bundle's compose.
DEPENDENCIES = {
    "api-key": {"rest"}, "basic-auth": {"rest"}, "jwt": {"rest"},
    "oauth": {"rest"}, "mtls": {"rest"},
    "chained-auth": {"rest", "api-key", "basic-auth"},
    "routing": {"rest"}, "transforms": {"rest"}, "validation": {"rest"},
    "error-overrides": {"rest"}, "rate-limit": {"rest"},
    "quota": {"rest", "api-key"}, "cache": {"rest"},
    "circuit-breaker": {"rest"}, "websocket": {"api-key"},
    # portal and portal-branding imply each other: whenever a demo shows the Developer Portal,
    # it shows it in your organisation's branding (portal/brand/), never in the stock Tyk theme.
    "graphql": {"rest"}, "streams": {"rest"}, "portal": {"api-key", "portal-branding"},
    "dcr": {"portal", "jwt"}, "portal-branding": {"portal"},
    "otel": {"rest"}, "go-plugin": {"rest"}, "grpc-plugin": {"rest", "api-key"},
    "operator": {"rest"}, "tyk-sync": {"rest"}, "cicd": {"tyk-sync"},
    "governance": {"rest"},
    "mcp": {"api-key"},
    "gateway-sharding": {"rest", "api-key"}, "js-plugin": {"api-key"},
    "key-import": {"api-key"}, "custom-domain": {"rest"},
    "portal-catalogues": {"portal"},
    "versioning": {"rest"}, "vault": {"rest"}, "upstream-mtls": {"rest"},
}

# Modules that cannot be demonstrated on a plain-HTTP listener, so selecting one
# switches the generated gateway to TLS. Derived from the module set rather than
# a flag: TLS is a consequence of what is being proved, not a taste.
#
# Only mtls qualifies today, and the gateway source is explicit about why: both
# client-certificate paths give up before looking at a certificate when the
# request did not arrive over TLS. internal/crypto/helpers.go ValidateRequestCerts
# opens with `if r.TLS == nil { return errors.New("TLS not enabled") }` — that is
# the static allowlist path (server.clientCertificates) the mtls asset uses — and
# the per-client path in gateway/mw_auth_key.go reads r.TLS.PeerCertificates for
# the session key. Nothing else in the supported set touches r.TLS.
#
# Everything else is deliberately plain HTTP. A self-signed gateway certificate
# costs `-k` on every command, a browser and MCP-client trust warning mid-demo,
# and a demo CA mounted into any in-network caller via SSL_CERT_FILE — none of
# which demonstrates anything. Worse, a handshake failure surfaces as a
# misleading application-level error (`initialize: Bad Request` from an MCP
# client, for instance) that reads as a Tyk bug in front of an audience.
TLS_MODULES = {"mtls"}

MODULE_PROOF = {
    "rest": ("REST proxying", "apis/*-core.oas.json", "REST-001"),
    "api-key": ("API key authentication", "apis/*-api-key.oas.json", "AUTH-KEY-001"),
    "basic-auth": ("Basic authentication", "apis/*-basic-auth.oas.json", "AUTH-BASIC-001"),
    "jwt": ("JWT validation and scope mapping", "apis/*-jwt.oas.json", "AUTH-JWT-001"),
    "oauth": ("OAuth2 client credentials", "apis/*-oauth.oas.json", "AUTH-OAUTH-001"),
    "mtls": ("Client mTLS", "apis/mtls.oas.template.json", "AUTH-MTLS-001"),
    "chained-auth": ("Native compliant multi-auth", "apis/*-chained-auth.oas.json", "AUTH-CHAIN-001"),
    "routing": ("Nested request-body routing", "apis/*-core.oas.json", "ROUTE-001"),
    "transforms": ("Request body transformation", "apis/*-core.oas.json", "TRANSFORM-001"),
    "validation": ("OAS request validation", "apis/*-core.oas.json", "VALIDATE-001"),
    "error-overrides": ("Standard error overrides", "apis/*-core.oas.json", "ERROR-001"),
    "rate-limit": ("Operation rate limiting", "apis/*-core.oas.json", "LIMIT-001"),
    "quota": ("Policy quota", "policies/*-api-key-policy.json", "QUOTA-001"),
    "cache": ("Response caching", "apis/*-core.oas.json", "CACHE-001"),
    "circuit-breaker": ("Circuit breaker", "apis/*-core.oas.json", "CB-001"),
    "websocket": ("Authenticated WebSocket proxy", "apis/*-websocket.oas.json", "WS-001"),
    "graphql": ("GraphQL proxy", "apis/*-graphql.oas.json", "GQL-001"),
    "streams": ("Kafka-backed Tyk Streams", "apis/*-streams-*.oas.json", "STREAM-001"),
    "portal": ("Portal provider, catalogue, products, plans, user, and documentation", "portal/configure-portal.sh", "PORTAL-001"),
    "dcr": ("Dynamic client registration", "upstreams/identity_service.py", "DCR-001"),
    "portal-branding": ("Portal in your organisation's branding: logo, favicon, colours and page copy", "portal/brand/brand.json", "PORTAL-UI-001"),
    "otel": ("OpenTelemetry traces and metrics", "otel/collector.yaml", "OTEL-001"),
    "go-plugin": ("Version-matched Go Post plugin", "plugins/go", "PLUGIN-GO-001"),
    "grpc-plugin": ("gRPC Post-hook termination", "plugins/grpc", "PLUGIN-GRPC-001"),
    "operator": ("Tyk Operator reconciliation", "operator", "OPERATOR-001"),
    "tyk-sync": ("Tyk Sync promotion", "ci/tyk-sync.sh", "SYNC-001"),
    "cicd": ("CI template checks and local promotion", "ci", "CICD-001"),
    "governance": ("API governance rulesets and continuous compliance evaluation", "governance", "GOV-001"),
    "mcp": ("MCP proxy with tool blocking, per-tool rate limits, and REST-as-MCP", "mcp/*.mcp.oas.json", "MCP-DISCOVERY-001"),
    "ai-studio": ("Tyk AI Studio Enterprise Model Router (separate product, companion stack)", "companion/ai-studio/compose.yaml", "AISTUDIO-ROUTER-001"),
    "gateway-sharding": ("Gateway sharding: a segmented gateway loads only tagged APIs", "apis/*-external-services.oas.json", "SHARD-001"),
    "js-plugin": ("Pre-authentication JavaScript plugin credential mapping", "plugins/js/*-credential-mapper.js", "PLUGIN-JS-001"),
    "key-import": ("Consumer keys imported with their existing values", "migration/imported-keys.example.json", "KEY-IMPORT-001"),
    "custom-domain": ("Custom domain and legacy listen path kept during migration", "apis/*-legacy-path.oas.json", "DOMAIN-001"),
    "portal-catalogues": ("Audience-restricted Portal catalogues, partner and documentation-only products", "portal/configure-catalogues.sh", "PORTAL-CAT-001"),
    "versioning": ("Tyk OAS API versioning: a base API and a header-selected child version", "apis/*-versioned*.oas.json", "VERSION-001"),
    "vault": ("Upstream credential injected per request from HashiCorp Vault", "scripts/configure-vault.sh", "SECRETS-VAULT-001"),
    "upstream-mtls": ("Upstream mutual TLS with certificate pinning", "apis/upstream-mtls.oas.template.json", "UPSTREAM-MTLS-001"),
}

# Proof that every bundle gets, whatever its modules: component health and the gateway admin
# API refusing a caller without the node secret. Its requirement row sits under `rest`.
PLATFORM_PROOF = ("Platform health endpoints and admin API protection", "scripts/verify-demo.sh", "HEALTH-001")

# Evidence test IDs that belong in the requirement row but are not the module's primary
# proof id. demo.config.json records exactly one id per module, while a mapping row may list
# several: update-requirements.py requires every id in the cell to be present and passing
# before the row is marked `live`, so listing GOV-002 here makes the CI-gate proof a
# precondition of the governance claim rather than a detail buried in results.json.
ADDITIONAL_PROOF = {
    "governance": ("GOV-002",),
    # DCR-001 exercises the identity service alone; PORTAL-DCR-AUD-001 proves that the
    # Portal's DCR events leave the JWT API's audience, issuer and JWKS cache intact.
    "dcr": ("PORTAL-DCR-AUD-001",),
}

# Additional proof ids that only apply when a companion module is also selected. GOV-003 is
# the Operator admission-webhook hard block, which needs both the governance and the operator
# planes; listing it on a governance-only bundle would leave the row permanently unproven.
COMBINED_PROOF = {
    ("governance", "operator"): ("GOV-003",),
}

# Default component set: the releases the modules are written for (see
# references/standalone-runtime.md). `--versions pinned` uses it
# verbatim with no network access, and `--versions latest` falls back to it per component when
# Docker Hub cannot answer. Refresh it only after a rehearsal on the new set, because a newer
# release can change module behaviour. The starter .env.example carries the same values.
KNOWN_GOOD_VERSIONS = {
    "dashboard": "v5.16.0",
    "gateway": "v5.16.0",
    "pump": "v1.18.0",
    "portal": "v1.20.1",
    "operator": "v1.6.0",
    "tyk_sync": "v2.3.0",
    "ai_studio": "v2.3.0",
}

# component -> (argparse dest, CLI option, Docker Hub repository, .env variable). Dashboard is
# listed before Gateway because the Gateway follows the Dashboard's resolved version.
COMPONENTS = {
    "dashboard": ("dashboard_version", "--dashboard-version", "tykio/tyk-dashboard", "DASHBOARD_VERSION"),
    "gateway": ("gateway_version", "--gateway-version", "tykio/tyk-gateway-ee", "GATEWAY_VERSION"),
    "pump": ("pump_version", "--pump-version", "tykio/tyk-pump-docker-pub", "PUMP_VERSION"),
    "portal": ("portal_version", "--portal-version", "tykio/portal", "PORTAL_VERSION"),
    "operator": ("operator_version", "--operator-version", "tykio/tyk-operator", "OPERATOR_VERSION"),
    "tyk_sync": ("sync_version", "--sync-version", "tykio/tyk-sync", "TYK_SYNC_VERSION"),
    # No .env variable: the AI Studio companion is its own compose project, so its tag is
    # written straight into companion/ai-studio/compose.yaml for both of its images.
    "ai_studio": ("ai_studio_version", "--ai-studio-version", "tykio/tyk-ai-studio-ent", None),
}

# The AI Studio companion runs the Microgateway at the same tag as AI Studio itself, so the
# Microgateway follows the resolved AI Studio version the way the Gateway follows the Dashboard.
AI_STUDIO_MICROGATEWAY_REPOSITORY = "tykio/tyk-microgateway-ent"

# Components that belong to one module and are resolved only when it is selected, so a bundle
# never makes a Docker Hub call for a product it does not ship.
MODULE_COMPONENTS = {"ai_studio": "ai-studio"}

# Key order for demo.config.json.
CONFIG_COMPONENT_ORDER = ("gateway", "dashboard", "pump", "portal", "operator", "tyk_sync", "ai_studio")


def selected_components(modules: set[str]) -> list[str]:
    """Return the components to resolve for a module set, in resolution order."""
    return [component for component in COMPONENTS if MODULE_COMPONENTS.get(component, "") in modules or component not in MODULE_COMPONENTS]

# The mock MCP server is built from source because it publishes no image, and the repository
# has no tags, so the build context is pinned to a commit rather than `#main`. To bump it:
#   gh api repos/TykTechnologies/tyk-mock-mcp-server/commits/main --jq .sha
# then paste the full 40-character SHA here, regenerate, and rehearse the mcp module. The image
# tag carries the short SHA so a bump rebuilds instead of reusing a cached image.
MOCK_MCP_SERVER_COMMIT = "7a69c3dbf4d030fba125f00cbef18c9e6d3f078e"

# Docker Hub's anonymous tag listing stops after 1,000 tags, and tykio/tyk-gateway-ee carries
# more than that, so a listing ordered by last update can miss a release. Tags are therefore
# read one release line at a time with the `name=vX.Y.` filter, starting from the known-good
# line (the Gateway starts from the Dashboard's line) and moving forward while each line holds a
# strict release; after the first line without one, the next major's `.0` line is tried once.
DOCKER_HUB_TAGS_URL = "https://hub.docker.com/v2/repositories/{repository}/tags?page_size=100&name=v{major}.{minor}."
DOCKER_HUB_TIMEOUT_SECONDS = 10
# Pages read within one release line (100 tags each), and release lines probed per repository.
DOCKER_HUB_MAX_PAGES = 10
DOCKER_HUB_MAX_LINES = 12
# Points at a JSON object of {"tykio/<image>": ["tag", ...]} used instead of Docker Hub, so
# tests stay offline and deterministic. An image absent from the fixture behaves as a failed
# lookup.
DOCKER_HUB_FIXTURE_ENV = "TYK_DEMO_DOCKER_HUB_FIXTURE"
STABLE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
# Any usable Tyk image tag, pre-releases included; mirrored in validate_bundle.py.
VERSION_TAG = re.compile(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--modules", required=True)
    parser.add_argument("--port-base", type=int)
    parser.add_argument("--identity-mode", choices=("local", "external"), default="local")
    parser.add_argument(
        "--versions", choices=("latest", "pinned"), default="latest",
        help="latest (default): resolve each component's newest stable tag from Docker Hub at "
             "generation time; pinned: use the builder's known-good set with no network access. "
             "An explicit --<component>-version always wins.",
    )
    for component, (dest, option, repository, _) in COMPONENTS.items():
        note = f" (also used for {AI_STUDIO_MICROGATEWAY_REPOSITORY}; resolved only when ai-studio is selected)" if component == "ai_studio" else ""
        parser.add_argument(option, dest=dest, help=f"exact {repository} tag{note}")
    parser.add_argument(
        "--brand-dir", type=Path,
        help="your organisation's Portal brand kit (brand.json, the SVG/PNG assets it names, and an "
             "optional content.json), copied to portal/brand/ instead of the neutral placeholder",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def stable_key(tag: str) -> tuple[int, int, int] | None:
    """Return a sortable key for a strict `vX.Y.Z` tag, or None for anything else.

    rc, alpha, beta, -fips, -arm64/-amd64, floating `v5.15`, and `latest` all return None, so
    they can never be selected as a generation-time default.
    """
    match = STABLE_TAG.fullmatch(tag or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def semver(tag: str) -> tuple[int, int, int]:
    """stable_key() for sorting: non-stable tags sort below every release."""
    key = stable_key(tag)
    return key if key is not None else (-1, -1, -1)


def highest_stable(tags: list[str]) -> str | None:
    stable = [tag for tag in tags if stable_key(tag)]
    return max(stable, key=semver) if stable else None


def release_line(tag: str) -> tuple[int, int] | None:
    """Return (major, minor) of a `vX.Y[.Z...]` tag, or None."""
    match = re.match(r"v?(\d+)\.(\d+)", tag or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


# AI Studio only: a `vX.Y.Z-<suffix>` build the latest-mode resolution may use before the
# line's first stable tag is published. Unstable, nightly and platform-variant builds are
# never candidates.
PRERELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)-([0-9A-Za-z.]+)")
NOT_A_CANDIDATE_SUFFIX = re.compile(r"(?i)unstable|nightly|snapshot|dev|arm64|amd64|fips")


def prerelease_key(tag: str) -> tuple | None:
    """Sortable key for a candidate pre-release tag, or None.

    Orders by version, then by the suffix's words and numbers compared naturally, so a
    `-x10` suffix sorts after `-x4` and `-x4` after `-x1`.
    """
    match = PRERELEASE_TAG.fullmatch(tag or "")
    if not match or NOT_A_CANDIDATE_SUFFIX.search(match.group(4)):
        return None
    parts = tuple((0, int(part), "") if part.isdigit() else (1, 0, part.lower())
                  for part in re.findall(r"\d+|[A-Za-z]+", match.group(4)))
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))), parts


def newest_prerelease(tags: list[str], line: tuple[int, int]) -> str | None:
    """The newest candidate pre-release of one release line, or None."""
    candidates = [tag for tag in tags if prerelease_key(tag) and release_line(tag) == line]
    return max(candidates, key=lambda tag: prerelease_key(tag) or ()) if candidates else None


def start_line_for(repository: str) -> tuple[int, int]:
    """The release line a repository's walk starts from: its known-good tag's line."""
    component = next((name for name, spec in COMPONENTS.items() if spec[2] == repository), None)
    if component is None and repository == AI_STUDIO_MICROGATEWAY_REPOSITORY:
        component = "ai_studio"
    line = release_line(KNOWN_GOOD_VERSIONS.get(component or "", ""))
    if line is None:
        raise LookupError(f"no known-good release line for {repository}")
    return line


def fetch_line(repository: str, line: tuple[int, int], notes: list[str] | None = None) -> list[str]:
    """Return every tag of one release line (`name=vX.Y.`), following pages."""
    url: object = DOCKER_HUB_TAGS_URL.format(repository=repository, major=line[0], minor=line[1])
    prefix = f"v{line[0]}.{line[1]}."
    names: list[str] = []
    for _page in range(DOCKER_HUB_MAX_PAGES):
        request = urllib.request.Request(str(url), headers={"Accept": "application/json", "User-Agent": "tyk-demo-builder"})
        with urllib.request.urlopen(request, timeout=DOCKER_HUB_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
        # The filter is a substring match; keep only tags that really start with the line.
        names.extend(name for name in (str(item.get("name", "")) for item in payload.get("results") or [] if isinstance(item, dict))
                     if name.startswith(prefix))
        url = payload.get("next")
        if not isinstance(url, str) or not url.startswith("https://hub.docker.com/"):
            break
    else:
        if notes is not None:
            notes.append(f"WARNING: stopped reading the {repository} v{line[0]}.{line[1]} line after {DOCKER_HUB_MAX_PAGES} pages; a newer release in that line may exist beyond them. Pass the component's version flag to pin one explicitly.")
    return names


def fetch_tags(repository: str, notes: list[str] | None = None, start_line: tuple[int, int] | None = None) -> list[str]:
    """Return the tags of a Docker Hub repository from one release line onwards.

    Raises on any failure; the caller turns that into a per-component fallback. Starts at
    `start_line` (default: the repository's known-good line) and reads each line in turn while
    it holds a strict `vX.Y.Z` release; after the first line without one it tries the next
    major's `.0` line once. Earlier lines are never read, so the result is the starting line and
    everything newer.
    """
    fixture = os.environ.get(DOCKER_HUB_FIXTURE_ENV)
    if fixture:
        tags = json.loads(Path(fixture).read_text()).get(repository)
        if not isinstance(tags, list):
            raise LookupError(f"{repository} is not in the {DOCKER_HUB_FIXTURE_ENV} fixture")
        return [str(tag) for tag in tags]
    print(f"Resolving the latest {repository} release from Docker Hub...", file=sys.stderr)
    major, minor = start_line or start_line_for(repository)
    names: list[str] = []
    tried_next_major = False
    for _line in range(DOCKER_HUB_MAX_LINES):
        line_names = fetch_line(repository, (major, minor), notes)
        names.extend(line_names)
        if highest_stable(line_names):
            minor += 1
            continue
        if tried_next_major:
            break
        tried_next_major = True
        major, minor = major + 1, 0
    else:
        if notes is not None:
            notes.append(f"WARNING: stopped reading {repository} after {DOCKER_HUB_MAX_LINES} release lines; a newer release may exist beyond them. Pass the component's version flag to pin one explicitly.")
    return names


def resolve_component_versions(
    requested: dict[str, str | None], mode: str, today: str | None = None,
    components: list[str] | None = None,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Resolve every component to a concrete tag and record where it came from.

    Precedence: an explicit flag (`user`), then `--versions pinned` (`pinned`), then the
    newest strict stable tag on Docker Hub (`latest (Docker Hub, <UTC date>)`), then the
    known-good value (`known-good fallback`) with a warning. The Gateway follows the
    Dashboard's resolved version when tyk-gateway-ee carries that exact tag, else the highest
    tag in the same major.minor, else the newest tag; when it follows a user-supplied or
    fallback Dashboard version its source is `matches dashboard`.

    `components` limits resolution to the components a bundle ships (default: every component
    not tied to a module, see MODULE_COMPONENTS). AI Studio is resolved to a tag that
    tykio/tyk-microgateway-ent also carries, because the companion runs both at one tag.
    """
    wanted = set(components) if components is not None else {c for c in COMPONENTS if c not in MODULE_COMPONENTS}
    date = today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    latest_source = f"latest (Docker Hub, {date})"
    versions: dict[str, str] = {}
    sources: dict[str, str] = {}
    warnings: list[str] = []

    def lookup(component: str, consequence: str, start_line: tuple[int, int] | None = None) -> list[str] | None:
        repository = COMPONENTS[component][2]
        try:
            if start_line is None:
                return fetch_tags(repository, notes=warnings)
            return fetch_tags(repository, notes=warnings, start_line=start_line)
        except Exception as exc:  # offline, DNS, HTTP, JSON, or fixture errors all fall back
            warnings.append(f"WARNING: could not read {repository} tags from Docker Hub ({type(exc).__name__}: {exc}); {component} {consequence}.")
            return None

    def fallback(component: str) -> None:
        versions[component] = KNOWN_GOOD_VERSIONS[component]
        sources[component] = "known-good fallback"

    def resolve_gateway() -> None:
        target = versions["dashboard"]
        dashboard_is_latest = sources["dashboard"].startswith("latest")
        repository = COMPONENTS["gateway"][2]
        if mode == "pinned":
            if sources["dashboard"] == "user":
                versions["gateway"], sources["gateway"] = target, "matches dashboard"
            else:
                versions["gateway"], sources["gateway"] = KNOWN_GOOD_VERSIONS["gateway"], "pinned"
            return
        if sources["dashboard"] == "user":
            consequence = f"follows the Dashboard version {target}"
        else:
            consequence = f"uses the known-good {KNOWN_GOOD_VERSIONS['gateway']}"
        # Read the Gateway from the Dashboard's release line onwards, so an older Dashboard
        # chosen with --dashboard-version still finds its matching Gateway tag.
        tags = lookup("gateway", consequence, release_line(target))
        if tags is None:
            if sources["dashboard"] == "user":
                versions["gateway"], sources["gateway"] = target, "matches dashboard"
            else:
                fallback("gateway")
                if versions["gateway"] != target:
                    warnings.append(f"WARNING: the Gateway's known-good {versions['gateway']} differs from the Dashboard {target}. Pass --gateway-version {target} to match them.")
            return
        if target in tags:
            versions["gateway"] = target
            sources["gateway"] = latest_source if dashboard_is_latest else "matches dashboard"
            return
        line = re.match(r"v?(\d+)\.(\d+)", target)
        same_line = [tag for tag in tags if line and (stable_key(tag) or ())[:2] == (int(line.group(1)), int(line.group(2)))]
        if same_line:
            chosen = highest_stable(same_line)
            assert line is not None and chosen is not None  # same_line is non-empty only when line matched
            versions["gateway"] = chosen
            sources["gateway"] = latest_source
            warnings.append(f"WARNING: {repository} has no {target} tag; the Gateway uses {chosen}, the highest tag in the Dashboard's v{line.group(1)}.{line.group(2)} line.")
            return
        chosen = highest_stable(tags)
        if not chosen:
            warnings.append(f"WARNING: {repository} has no stable vX.Y.Z tag; the Gateway uses the known-good {KNOWN_GOOD_VERSIONS['gateway']}.")
            fallback("gateway")
            return
        versions["gateway"] = chosen
        sources["gateway"] = latest_source
        warnings.append(f"WARNING: {repository} has no tag in the Dashboard {target} release line; the Gateway uses the newest stable {chosen}, which differs from the Dashboard. Pass --gateway-version to choose explicitly.")

    def resolve_ai_studio() -> None:
        """Resolve AI Studio to one tag both companion images carry.

        In order: the newest stable tag of the known-good line or later that both images
        carry; else the newest pre-release of the known-good line that both carry (never an
        unstable build), with a note naming it, so a bundle can start before that line's first
        stable tag is published and moves to it automatically once it is; else the newest
        stable AI Studio tag matched to the Microgateway where possible (the version gate then
        judges it), or the known-good fallback.
        """
        repository = COMPONENTS["ai_studio"][2]
        tags = lookup("ai_studio", f"uses the known-good {KNOWN_GOOD_VERSIONS['ai_studio']}")
        if tags is None:
            fallback("ai_studio")
            return
        unchecked = ""
        try:
            gateway_tags: set[str] | None = set(fetch_tags(AI_STUDIO_MICROGATEWAY_REPOSITORY, notes=warnings))
        except Exception as exc:  # the AI Studio tag stands; say the pairing is unchecked
            gateway_tags = None
            unchecked = f"WARNING: could not read {AI_STUDIO_MICROGATEWAY_REPOSITORY} tags from Docker Hub ({type(exc).__name__}: {exc}); the companion assumes it carries {{tag}}."
        common = [tag for tag in tags if gateway_tags is None or tag in gateway_tags]
        floor = release_line(KNOWN_GOOD_VERSIONS["ai_studio"]) or (0, 0)
        newest = highest_stable(tags)
        chosen = highest_stable([tag for tag in common if (release_line(tag) or (0, 0)) >= floor])
        if chosen is None:
            early = newest_prerelease(common, floor)
            if early:
                versions["ai_studio"], sources["ai_studio"] = early, latest_source
                line = f"{floor[0]}.{floor[1]}"
                pair = repository if gateway_tags is None else f"{repository} and {AI_STUDIO_MICROGATEWAY_REPOSITORY}"
                warnings.append(f"NOTE: AI Studio uses {early}, the newest {line} build on Docker Hub for {pair}; no stable {line} tag is published there yet. Regenerate in latest mode once it is to move to it.")
                if unchecked:
                    warnings.append(unchecked.format(tag=early))
                return
            chosen = newest
        if chosen is None:
            warnings.append(f"WARNING: {repository} has no stable vX.Y.Z tag from the known-good {floor[0]}.{floor[1]} line onwards; ai_studio uses the known-good {KNOWN_GOOD_VERSIONS['ai_studio']}.")
            fallback("ai_studio")
            return
        versions["ai_studio"], sources["ai_studio"] = chosen, latest_source
        if gateway_tags is None:
            warnings.append(unchecked.format(tag=chosen))
            return
        if newest and newest != chosen and newest not in gateway_tags and chosen in gateway_tags:
            warnings.append(f"WARNING: {AI_STUDIO_MICROGATEWAY_REPOSITORY} has no {newest} tag; AI Studio uses {chosen}, the newest tag both images carry.")
            return
        if chosen in gateway_tags:
            return
        shared = highest_stable(common)
        if shared:
            versions["ai_studio"] = shared
            warnings.append(f"WARNING: {AI_STUDIO_MICROGATEWAY_REPOSITORY} has no {chosen} tag; AI Studio uses {shared}, the newest tag both images carry.")
        else:
            warnings.append(f"WARNING: {AI_STUDIO_MICROGATEWAY_REPOSITORY} has no {chosen} tag and no stable tag in common with {repository}; pass --ai-studio-version explicitly.")

    for component in COMPONENTS:
        if component not in wanted:
            continue
        if requested.get(component):
            versions[component] = str(requested[component])
            sources[component] = "user"
            continue
        if component == "gateway":
            resolve_gateway()
            continue
        if mode == "pinned":
            versions[component] = KNOWN_GOOD_VERSIONS[component]
            sources[component] = "pinned"
            continue
        if component == "ai_studio":
            resolve_ai_studio()
            continue
        tags = lookup(component, f"uses the known-good {KNOWN_GOOD_VERSIONS[component]}")
        best = highest_stable(tags) if tags is not None else None
        if best:
            versions[component] = best
            sources[component] = latest_source
        else:
            if tags is not None:
                warnings.append(f"WARNING: {COMPONENTS[component][2]} has no stable vX.Y.Z tag from the known-good {'.'.join(map(str, release_line(KNOWN_GOOD_VERSIONS[component]) or ()))} line onwards; {component} uses the known-good {KNOWN_GOOD_VERSIONS[component]}.")
            fallback(component)
    return versions, sources, warnings


def validate_slug(slug: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,46}[a-z0-9]", slug):
        raise SystemExit("--slug must be 3-48 lowercase letters, digits, or hyphens")


def validate_display_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", name):
        raise SystemExit("--name must be 1-80 safe display characters: letters, digits, spaces, dots, underscores, or hyphens")


def validate_version(value: str, option: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise SystemExit(f"{option} must be a safe container tag")


def supports_jwks_uris(gateway_version: str) -> bool:
    """True when the target Gateway resolves JWT signing keys from jwksURIs.

    jwksURIs arrived in Gateway 5.9.0 and is honoured only for OAS APIs. Below that the
    field is ignored entirely, which would leave a generated JWT API with no signing-key
    source and no error explaining why, so the gate is mandatory rather than cosmetic.
    """
    match = re.match(r"v?(\d+)\.(\d+)", gateway_version or "")
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (5, 9)


def supports_governance(dashboard_version: str) -> bool:
    """True when the target Dashboard ships API Governance.

    Governance is embedded in the Dashboard and reached GA in 5.15.0. Below that release
    every `/api/rulesets` and `/api/governance` path is absent, so a generated governance
    bundle would configure nothing, verify nothing, and fail with a 404 that says nothing
    about the real cause. The gate is therefore mandatory rather than cosmetic.
    """
    match = re.match(r"v?(\d+)\.(\d+)", dashboard_version or "")
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (5, 15)


def supports_governance_operator(operator_version: str) -> bool:
    """True when the target Tyk Operator ships the TykRuleset CRD and its admission webhook.

    The Operator release notes list both as added in v1.5.0. A pre-release sorts below its
    release, so v1.5.0-rcN is refused while v1.6.0-rcN passes. The module works with
    v1.6.0, the generator's default.
    """
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?", operator_version or "")
    if not match:
        return False
    release = tuple(int(match.group(index)) for index in (1, 2, 3))
    if release != (1, 5, 0):
        return release > (1, 5, 0)
    return match.group(4) is None

def version_tuple(value: str) -> tuple[int, int, int]:
    """Parse a vMAJOR.MINOR[.PATCH] image tag for comparison. (0,0,0) if unparseable."""
    match = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", value or "")
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def javascript_plugin_driver(gateway_version: str, dashboard_version: str) -> str:
    """Return the pluginConfig.driver for a JavaScript plugin on the target Gateway and Dashboard.

    Gateway 5.15.0 added the goja engine, selected per API with driver "javascript"
    (`JavaScriptDriver` in apidef/api_definitions.go; the goja VM is initialised only for
    that driver). The API reaches the Gateway through the Dashboard, though, and the Tyk OAS
    schema lists "javascript" in the driver enum only from 5.15.0
    (apidef/oas/schema/x-tyk-api-gateway.json), so a 5.14 Dashboard paired with a 5.15
    Gateway cannot be trusted to store it. "javascript" is therefore chosen only when BOTH
    are 5.15.0 or later; any older one gets "otto". goja has no `console` and no `_`
    helper, so the bundled plugin uses log() alone and runs unchanged on either engine.
    """
    newest = min(version_tuple(gateway_version), version_tuple(dashboard_version))
    return "javascript" if newest >= (5, 15, 0) else "otto"


def goja_blockers(versions: dict[str, str]) -> list[str]:
    """Name the components below 5.15.0 that keep the js-plugin API on otto, e.g. ["Dashboard v5.14.0"]."""
    return [f"{label} {versions.get(key, '')}".strip() for label, key in (("Gateway", "gateway"), ("Dashboard", "dashboard"))
            if version_tuple(versions.get(key, "")) < (5, 15, 0)]


# Linux caps a hostname at 64 bytes and DNS a label at 63, so a long slug is shortened.
HOSTNAME_LIMIT = 63


def gateway_hostname(slug: str, role: str = "") -> str:
    """Return the readable compose hostname for a gateway (`<slug>-gateway[-<role>]`).

    The Dashboard node list shows gateways by hostname only; without one it shows the
    container id. The slug is shortened when needed so the name stays a valid label.
    """
    suffix = "-gateway" + (f"-{role}" if role else "")
    return slug[: HOSTNAME_LIMIT - len(suffix)].rstrip("-") + suffix


def secure_upstream_host(slug: str) -> str:
    """Return the in-network hostname of the upstream-mtls module's HTTPS upstream.

    It is visible (the API's upstream URL, the certificate mappings), so it derives
    from the slug rather than a generic service name, and stays a valid DNS label.
    """
    suffix = "-secure-upstream"
    return slug[: HOSTNAME_LIMIT - len(suffix)].rstrip("-") + suffix


# Where the vault module keeps the upstream credential: the Vault dev server mounts KV v2 at
# `secret/`, and the gateway adds the `data/` segment itself, so the reference names the
# mount and the path only. The path and field must not contain a dot: the gateway splits
# `$secret_vault.<path>.<field>` at the first dot.
VAULT_SECRET_FIELD = "api_key"


def vault_secret_path(slug: str) -> str:
    return f"secret/{slug}/upstream"


def vault_data_path(slug: str) -> str:
    """The KV v2 HTTP API path of the same secret: <mount>/data/<path>."""
    return f"secret/data/{slug}/upstream"


def js_identifier(slug: str) -> str:
    """Return the JavaScript global name of the bundle's credential-mapper plugin."""
    parts = [part for part in slug.split("-") if part]
    name = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:]) + "CredentialMapper"
    return name if name[0].isalpha() else f"plugin{name[0].upper()}{name[1:]}"


# The value imported-keys.example.json carries in place of each key; import-keys.sh replaces it.
IMPORTED_KEY_PLACEHOLDER = "<minted by scripts/import-keys.sh>"


def imported_key_value() -> str:
    """Return a freshly minted, caller-chosen key value for a key-import sample record.

    Stands in for a credential a previous gateway issued; the point of the module is that
    the consumer keeps this exact value. Minted at generation rather than derived from the
    slug: the imported keys are live, no-quota credentials on the running stack, and a value
    anyone could compute from the bundle name would be a published key. Well above the
    gateway's default min_token_length.
    """
    return "migrated-" + secrets.token_hex(16)


def check_module_version_support(modules: set[str], args) -> None:
    """Refuse combinations whose failure mode is silent.

    Every check here exists because the product does not error - it quietly does
    nothing, so the bundle generates, the stack comes up, and the chapter is simply
    absent. That is far more expensive to diagnose than a refusal at generate time.
    Runs on resolved versions, so it also guards the Docker Hub defaults.
    """
    problems: list[str] = []

    if "mcp" in modules:
        for label, option, value in (
            ("Gateway", "--gateway-version", args.gateway_version),
            ("Dashboard", "--dashboard-version", args.dashboard_version),
        ):
            if version_tuple(value) < (5, 15, 0):
                problems.append(
                    f"--modules mcp needs {label} 5.15.0 or later; got {value} ({option}). "
                    "MCP proxies and REST-as-MCP derivation are a 5.15 feature: they are a "
                    "Dashboard resource on /api/mcps served by a 5.15 Gateway, and neither "
                    "exists on an older release."
                )

    if "mcp" in modules and "portal" in modules:
        if version_tuple(args.portal_version) < (1, 19, 0):
            problems.append(
                f"--modules mcp,portal needs Portal 1.19.0 or later; got {args.portal_version}. "
                "Earlier Portal releases do not publish MCP proxies as products. The failure "
                "is silent: the plan syncs and the product is simply not there."
            )

    if {"versioning", "tyk-sync"} <= modules:
        problems.append(
            "--modules versioning,tyk-sync cannot be combined: the link between a base API and "
            "its child version is created by scripts/import-apis.sh (x-demo-version-of), which "
            "tyk-sync does not read, and promoting versioned APIs through tyk-sync has not been "
            "proved, so SYNC-001 could not be trusted for them. Put the versioning module and "
            "the tyk-sync and cicd modules in separate bundles."
        )

    if "ai-studio" in modules:
        explicit = getattr(args, "ai_studio_version", None)
        ai_studio_version = explicit or KNOWN_GOOD_VERSIONS["ai_studio"]
        # main() records where the version came from, so the message only blames
        # --ai-studio-version when the user passed it; a direct call that hands in a
        # version stands for the flag.
        source = getattr(args, "ai_studio_version_source", None) or ("user" if explicit else "pinned")
        origin = {
            "user": "--ai-studio-version",
            "pinned": "the builder's known-good pinned version",
            "known-good fallback": "the builder's known-good fallback; Docker Hub could not be read",
        }.get(source) or ("the newest stable tag on Docker Hub" if source.startswith("latest") else source)
        # version_tuple() drops a pre-release suffix, so a 2.3.0 pre-release reads as 2.3.0
        # and passes: until 2.3.0 is published on Docker Hub, its pre-release builds are the
        # only 2.3 images there. Anything from the 2.2 line or older is refused.
        if version_tuple(ai_studio_version) < (2, 3, 0):
            problems.append(
                f"--modules ai-studio needs Tyk AI Studio 2.3.0 or later; got {ai_studio_version} "
                f"({origin}). The companion's configuration, scripts and proofs are written "
                "and verified for AI Studio 2.3 only, and an earlier release does not run them. "
                "Regenerate with --ai-studio-version v2.3.0 (or newer)."
            )

    if problems:
        raise SystemExit("\n".join(problems))


def gateway_tls_required(modules: set[str]) -> bool:
    """True when a selected module cannot be proved without a TLS listener."""
    return bool(modules & TLS_MODULES)


def tls_tokens(tls: bool) -> dict[str, str]:
    """Return the generation-time tokens that carry the listener decision.

    Every seam that has to agree with the scheme is resolved here, so a bundle
    cannot end up half-TLS: the URL scheme, the one place `-k` may appear, the
    Dashboard's display-scheme flag, the certificate volume, and whether
    certificates are minted at all.
    """
    if tls:
        return {
            "GATEWAY_SCHEME": "https",
            "GENERATE_HTTPS": "true",
            "GATEWAY_CURL_CMD": "curl -k",
            "GATEWAY_CURL_FN": (
                "# The gateway presents the demo CA's self-signed certificate, so gateway\n"
                "# calls skip verification. This is the only -k in the bundle.\n"
                'gw_curl() { curl -k "$@"; }'
            ),
            "MCP_JSON_FN": (
                "  mcp_json() {\n"
                '    python3 "${ROOT_DIR}/scripts/mcp-check.py" "$@" --insecure 2>/dev/null || true\n'
                "  }"
            ),
            "CERT_GENERATION": (
                "\n"
                "# A selected module needs a TLS listener, so mint the demo CA, the server\n"
                "# certificate and the client certificates before the gateway starts.\n"
                '"${ROOT_DIR}/scripts/generate-demo-certs.sh"'
            ),
            "GATEWAY_TLS_VOLUME": "      - ./generated/certs/server.pem:/opt/tyk-gateway/certs/server.pem:ro",
        }
    return {
        "GATEWAY_SCHEME": "http",
        "GENERATE_HTTPS": "false",
        "GATEWAY_CURL_CMD": "curl",
        "GATEWAY_CURL_FN": (
            "# The gateway listens on plain HTTP, so there is no certificate to skip.\n"
            'gw_curl() { curl "$@"; }'
        ),
        "MCP_JSON_FN": (
            "  mcp_json() {\n"
            '    python3 "${ROOT_DIR}/scripts/mcp-check.py" "$@" 2>/dev/null || true\n'
            "  }"
        ),
        "CERT_GENERATION": "",
        "GATEWAY_TLS_VOLUME": "",
    }


def expand_modules(raw: str, identity_mode: str) -> list[str]:
    selected = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = selected - SUPPORTED
    if unknown:
        raise SystemExit(f"Unsupported modules: {', '.join(sorted(unknown))}")
    if not selected:
        raise SystemExit("Select at least one module")
    changed = True
    while changed:
        changed = False
        for module in tuple(selected):
            for dependency in DEPENDENCIES.get(module, set()):
                if dependency not in selected:
                    selected.add(dependency)
                    changed = True
    if identity_mode == "external" and "dcr" in selected:
        # The DCR assets remain available, but runtime proof waits for provider settings.
        pass
    return sorted(selected)


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_ports(base: int | None) -> dict[str, int]:
    # Appended, never reordered: existing names keep their offset so a port a
    # someone has already written down does not move when a module is added.
    # The AI Studio ports belong to a separate compose project but are allocated
    # from the same checked-free block, which is what stops the two stacks
    # colliding on a workstation. gateway_external is the segmented gateway of the
    # gateway-sharding module, vault the Vault dev server of the vault module and
    # upstream_mtls the client-certificate upstream of the upstream-mtls module.
    names = ["dashboard", "portal", "gateway", "upstream", "identity", "websocket", "graphql", "kafka", "otel_grpc", "otel_http", "prometheus", "tempo", "mcp_mock", "mcp_inspector", "mcp_inspector_proxy", "aistudio_admin", "aistudio_proxy", "aistudio_router", "gateway_external", "vault", "upstream_mtls"]
    candidates = [base] if base else list(range(23000, 49000, 20))
    for candidate in candidates:
        if candidate is None or candidate < 1024 or candidate + len(names) > 65535:
            continue
        if all(port_free(candidate + offset) for offset in range(len(names))):
            return {name: candidate + offset for offset, name in enumerate(names)}
    raise SystemExit("Unable to allocate a free port block")


def prepare_target(target: Path, force: bool) -> None:
    marker = target / ".tyk-standalone-demo"
    if target.exists() and any(target.iterdir()):
        if not force:
            raise SystemExit(f"Target is not empty: {target}")
        if not marker.exists():
            raise SystemExit("--force only replaces a previously generated bundle")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    marker.write_text("generated by tyk-demo-builder\n")


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_directory(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def replace_tokens(path: Path, tokens: dict[str, str]) -> None:
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        return
    for key, value in tokens.items():
        text = text.replace(f"__{key}__", value)
    path.write_text(text)


def copy_assets(target: Path, modules: set[str], identity_mode: str, slug: str = "demo") -> None:
    always = [".env.example", ".gitignore", "config", "scripts", "upstreams/http_api.py"]
    optional = {
        "identity": {"jwt", "dcr"},
        "upstreams/websocket_server.py": {"websocket"},
        "upstreams/requirements-websocket.txt": {"websocket"},
        "upstreams/Dockerfile.websocket": {"websocket"},
        "upstreams/graphql_server.py": {"graphql"},
        "upstreams/requirements-graphql.txt": {"graphql"},
        "upstreams/Dockerfile.graphql": {"graphql"},
        "upstreams/mtls_upstream.py": {"upstream-mtls"},
        "otel": {"otel"}, "portal": {"portal"}, "operator": {"operator"},
        "ci": {"tyk-sync", "cicd"}, "plugins/go": {"go-plugin"},
        "plugins/grpc": {"grpc-plugin"}, "governance": {"governance"},
        "mcp": {"mcp"},
        # A whole self-contained directory, not services bolted onto the
        # bundle's compose.yaml: Tyk AI Studio is a separate product with its
        # own licence, images, compose project and lifecycle.
        "companion/ai-studio": {"ai-studio"},
    }
    for relative in always:
        source = STARTER / relative
        if source.is_dir():
            copy_directory(source, target / relative)
        else:
            copy_file(source, target / relative)
    for relative, required in optional.items():
        if not modules.intersection(required):
            continue
        source = STARTER / relative
        if source.is_dir():
            copy_directory(source, target / relative)
        elif source.exists():
            copy_file(source, target / relative)
    # scripts/ is copied wholesale, so prune the module-specific ones a bundle
    # cannot use. Leaving them is harmless at runtime but invites the question
    # "why is there an MCP importer in a demo with no MCP?".
    module_scripts = {
        "scripts/import-mcps.sh": {"mcp"},
        "scripts/mcp-check.py": {"mcp"},
        "scripts/websocket-check.py": {"websocket"},
        # An HTTP bundle has nothing to sign, and a certificate generator left
        # lying in one is the kind of dead scaffolding that gets run "just in
        # case" and then puzzled over when nothing consumes its output.
        "scripts/generate-demo-certs.sh": TLS_MODULES,
        "scripts/import-keys.sh": {"key-import"},
        "scripts/record-screenshot.sh": {"portal-branding"},
        "scripts/configure-vault.sh": {"vault"},
        "scripts/generate-upstream-certs.sh": {"upstream-mtls"},
        "scripts/configure-upstream-mtls.sh": {"upstream-mtls"},
        "portal/configure-catalogues.sh": {"portal-catalogues"},
        "portal/configure-dcr.sh": {"dcr"},
    }
    for relative, required in module_scripts.items():
        if not modules.intersection(required):
            (target / relative).unlink(missing_ok=True)
    if identity_mode != "local":
        # configure-dcr.sh wires Portal DCR to the bundled identity service, which an
        # external-identity bundle does not run; configure-demo.sh never calls it there.
        (target / "portal" / "configure-dcr.sh").unlink(missing_ok=True)
    # compose.yaml mounts ./plugins into every gateway, so the directory exists in every
    # bundle; left to Docker it would be created empty (and root-owned on Linux) at start.
    copy_file(STARTER / "plugins" / "README.md", target / "plugins" / "README.md")
    if "js-plugin" in modules:
        # Named after the bundle so the file anyone sees in the plugin path is the bundle's own.
        copy_file(STARTER / "plugins" / "js" / "credential-mapper.js", target / "plugins" / "js" / f"{slug}-credential-mapper.js")
    if identity_mode == "local" and modules.intersection({"jwt", "dcr"}):
        copy_file(STARTER / "upstreams" / "identity_service.py", target / "upstreams" / "identity_service.py")
        copy_file(STARTER / "upstreams" / "Dockerfile.identity", target / "upstreams" / "Dockerfile.identity")
    if {"operator", "governance"}.issubset(modules):
        # The admission-webhook ruleset and its proof need both planes, so they live outside
        # both module asset directories and are only ever placed when the two modules are
        # selected together. They stay an explicit step: apply-operator.sh renders them with
        # the namespace substituted but never applies them; verify-governance.sh does.
        for name in ("tyk-ruleset.yaml", "governed-api-compliant.yaml", "governed-api-noncompliant.yaml", "verify-governance.sh"):
            copy_file(STARTER / "governance-operator" / name, target / "operator" / name)


def service_blocks(modules: set[str], identity_mode: str, tls: bool = False, slug: str = "demo") -> tuple[str, str, str, str, list[str]]:
    blocks: list[str] = []
    profiles: list[str] = []
    gateway_env: list[str] = []
    gateway_dependencies: list[str] = []
    gateway_volumes: list[str] = []

    if tls:
        gateway_env.extend([
            '      TYK_GW_HTTPSERVEROPTIONS_USESSL: "true"',
            '      TYK_GW_HTTPSERVEROPTIONS_SSLCERTIFICATES: /opt/tyk-gateway/certs/server.pem',
        ])

    if identity_mode == "local" and modules.intersection({"jwt", "dcr"}):
        profiles.append("identity")
        blocks.append('''
  identity:
    profiles: [identity]
    build:
      context: ./upstreams
      dockerfile: Dockerfile.identity
    restart: unless-stopped
    environment:
      ISSUER: http://identity:8080
      # No PUBLIC_BASE_URL: discovery URLs follow the Host each caller used, so host tools
      # get localhost:${IDENTITY_PORT} and the Portal (DCR) gets identity:8080. Set it only
      # to force one address for every caller.
    # A test fixture: POST /token signs any claims it is sent. Keep it on loopback
    # (BIND_ADDRESS); Compose services reach it as identity:8080 without the published port.
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${IDENTITY_PORT}:8080"]
    volumes: [identity-data:/data]
''')
    if "portal" in modules:
        profiles.append("portal")
        blocks.append('''
  tyk-portal:
    profiles: [portal]
    image: tykio/portal:${PORTAL_VERSION}
    restart: unless-stopped
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${PORTAL_PORT}:3001"]
    env_file: [./config/portal.env]
    environment:
      PORTAL_LICENSEKEY: ${TYK_PORTAL_LICENSE}
      PORTAL_DATABASE_CONNECTIONSTRING: host=tyk-postgres port=5432 dbname=portal user=tykuser password=${POSTGRES_PASSWORD} sslmode=disable
    depends_on:
      tyk-postgres: {condition: service_healthy}
''')
    if "websocket" in modules:
        profiles.append("websocket")
        blocks.append('''
  websocket-upstream:
    profiles: [websocket]
    build:
      context: ./upstreams
      dockerfile: Dockerfile.websocket
    restart: unless-stopped
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${WEBSOCKET_PORT}:8080"]
''')
    if "graphql" in modules:
        profiles.append("graphql")
        blocks.append('''
  graphql-upstream:
    profiles: [graphql]
    build:
      context: ./upstreams
      dockerfile: Dockerfile.graphql
    restart: unless-stopped
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${GRAPHQL_PORT}:8080"]
''')
    if "streams" in modules:
        profiles.append("streams")
        blocks.append('''
  kafka:
    profiles: [streams]
    image: apache/kafka:3.8.0
    restart: unless-stopped
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${KAFKA_PORT}:9094"]
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093,EXTERNAL://:9094
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092,EXTERNAL://localhost:${KAFKA_PORT}
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,EXTERNAL:PLAINTEXT
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: 0
''')
    if "otel" in modules:
        profiles.append("otel")
        gateway_env.extend([
            '      TYK_GW_OPENTELEMETRY_TRACES_ENABLED: "true"',
            '      TYK_GW_OPENTELEMETRY_TRACES_EXPORTER: grpc',
            '      TYK_GW_OPENTELEMETRY_TRACES_ENDPOINT: otel-collector:4317',
        ])
        blocks.append('''
  otel-collector:
    profiles: [otel]
    image: otel/opentelemetry-collector-contrib:0.100.0
    command: [--config=/etc/otelcol/config.yaml]
    volumes:
      - ./otel/collector.yaml:/etc/otelcol/config.yaml:ro
      - demo-logs:/var/log/demo:ro
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${OTEL_GRPC_PORT}:4317", "${BIND_ADDRESS:-127.0.0.1}:${OTEL_HTTP_PORT}:4318"]
    depends_on: [tempo]

  tempo:
    profiles: [otel]
    image: grafana/tempo:2.5.0
    command: [-config.file=/etc/tempo.yaml]
    volumes: [./otel/tempo.yaml:/etc/tempo.yaml:ro]
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${TEMPO_PORT}:3200"]

  prometheus:
    profiles: [otel]
    image: prom/prometheus:v2.53.0
    command: [--config.file=/etc/prometheus/prometheus.yaml]
    volumes: [./otel/prometheus.yaml:/etc/prometheus/prometheus.yaml:ro]
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${PROMETHEUS_PORT}:9090"]
''')
    if modules.intersection({"go-plugin", "grpc-plugin"}):
        gateway_env.append('      TYK_GW_COPROCESSOPTIONS_ENABLECOPROCESS: "true"')
    if "grpc-plugin" in modules:
        profiles.append("grpc-plugin")
        gateway_env.extend([
            '      TYK_GW_COPROCESSOPTIONS_COPROCESSGRPCSERVER: tcp://grpc-plugin:5555',
        ])
        gateway_dependencies.append('      grpc-plugin: {condition: service_healthy}')
        blocks.append('''
  grpc-plugin:
    profiles: [grpc-plugin]
    build: ./plugins/grpc
    restart: unless-stopped
''')
    if "mcp" in modules:
        profiles.append("mcp")
        # The Inspector needs the demo CA only when the gateway terminates TLS.
        # On a plain-HTTP bundle the mount would point at a certificate that was
        # never generated and Compose would silently create a directory there.
        inspector_ca = '''
      # The Inspector's proxy is Node and the gateway presents the demo's
      # self-signed certificate. Without the demo CA in Node's trust store the
      # connection fails as an opaque "fetch failed".
      NODE_EXTRA_CA_CERTS: /certs/ca.crt
    volumes:
      - ./generated/certs/ca.crt:/certs/ca.crt:ro''' if tls else ""
        blocks.append('''
  mcp-mock-server:
    profiles: [mcp]
    image: tyk-mock-mcp-server:''' + MOCK_MCP_SERVER_COMMIT[:12] + '''
    build:
      context: https://github.com/TykTechnologies/tyk-mock-mcp-server.git#''' + MOCK_MCP_SERVER_COMMIT + '''
    restart: unless-stopped
    environment:
      PORT: 7878
    ports: ["${BIND_ADDRESS:-127.0.0.1}:${MCP_MOCK_PORT}:7878"]
    healthcheck:
      test: [CMD, wget, --no-verbose, --tries=1, --spider, "http://localhost:7878/health"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s

  mcp-inspector:
    profiles: [mcp]
    image: ghcr.io/modelcontextprotocol/inspector:2.3.0
    restart: unless-stopped
    # Host loopback only: auth is disabled below, so this binding is the sole
    # guard on a backend that can spawn local processes.
    ports:
      - "127.0.0.1:${MCP_INSPECTOR_PORT}:6274"
      - "127.0.0.1:${MCP_INSPECTOR_PROXY_PORT}:6277"
    environment:
      DANGEROUSLY_OMIT_AUTH: "true"
      ALLOWED_ORIGINS: "http://localhost:${MCP_INSPECTOR_PORT},http://127.0.0.1:${MCP_INSPECTOR_PORT}"''' + inspector_ca + '''
    depends_on:
      mcp-mock-server: {condition: service_healthy}
''')
    if "vault" in modules:
        profiles.append("vault")
        # The gateway registers a KV store named "vault" from these settings; API definitions
        # then reference `$secret_vault.<mount>/<path>.<field>`, resolved on every request.
        gateway_env.extend([
            '      TYK_GW_KV_VAULT_ADDRESS: http://vault:8200',
            '      TYK_GW_KV_VAULT_TOKEN: ${VAULT_TOKEN}',
            '      TYK_GW_KV_VAULT_KVVERSION: "2"',
        ])
        gateway_dependencies.append('      vault: {condition: service_healthy}')
        blocks.append('''
  vault:
    profiles: [vault]
    image: hashicorp/vault:1.17.6
    restart: unless-stopped
    # Dev mode: unsealed, KV v2 mounted at secret/, data held in memory only, so
    # scripts/configure-vault.sh re-seeds after a restart without overwriting a value.
    # Host loopback only: the root token below is the only credential.
    cap_add: [IPC_LOCK]
    environment:
      VAULT_DEV_ROOT_TOKEN_ID: ${VAULT_TOKEN}
      VAULT_DEV_LISTEN_ADDRESS: 0.0.0.0:8200
    ports: ["127.0.0.1:${VAULT_PORT}:8200"]
    healthcheck:
      test: [CMD, vault, status, -address=http://127.0.0.1:8200]
      interval: 5s
      timeout: 3s
      retries: 20
''')
    if "upstream-mtls" in modules:
        profiles.append("upstream-mtls")
        # Go reads SSL_CERT_DIR as a colon-separated list, so the gateway keeps the system
        # roots and also trusts the upstream CA from a hashed directory (<hash>.0).
        gateway_env.append('      SSL_CERT_DIR: /etc/ssl/certs:/opt/tyk-upstream-ca')
        gateway_volumes.append('      - ./generated/certs/upstream/ca-dir:/opt/tyk-upstream-ca:ro')
        blocks.append(f'''
  secure-upstream:
    profiles: [upstream-mtls]
    image: python:3.12-alpine
    restart: unless-stopped
    # HTTPS that refuses a connection without a client certificate from the upstream CA.
    command: [python, /app/server.py]
    ports: ["${{BIND_ADDRESS:-127.0.0.1}}:${{UPSTREAM_MTLS_PORT}}:8443"]
    volumes:
      - ./upstreams/mtls_upstream.py:/app/server.py:ro
      - ./generated/certs/upstream:/certs:ro
    networks:
      default:
        aliases: [{secure_upstream_host(slug)}]
''')
    if "gateway-sharding" in modules:
        # Built last so it carries every environment line, volume and dependency the modules
        # above gave the primary gateway (TLS, OTel, coprocess, Vault, upstream CA); only
        # segmentation differs.
        blocks.append(external_gateway_block(slug, gateway_env, gateway_dependencies, tls, gateway_volumes))
    return "".join(blocks).rstrip(), "\n".join(gateway_env), "\n".join(gateway_dependencies), "\n".join(gateway_volumes), sorted(set(profiles))


def external_gateway_block(slug: str, gateway_env: list[str], gateway_dependencies: list[str], tls: bool,
                           gateway_volumes: list[str] | None = None) -> str:
    """Return the compose service for the segmented gateway of the gateway-sharding module.

    Same image, env file, module environment and plugin mount as tyk-gateway; it adds only
    DBAppConfOptions.NodeIsSegmented and Tags (Go field names, config/config.go at v5.15.0),
    so it loads just the APIs whose server.gatewayTags carry "external". The primary gateway
    stays unsegmented and loads every API. Sharding places configuration; it does not
    isolate: both gateways share Redis, so keys, sessions, quotas and rate-limit counters
    are common to both.
    """
    lines = [
        "",
        "  tyk-gateway-external:",
        "    image: tykio/tyk-gateway-ee:${GATEWAY_VERSION}",
        f"    hostname: {gateway_hostname(slug, 'external')}",
        "    restart: unless-stopped",
        '    ports: ["${BIND_ADDRESS:-127.0.0.1}:${GATEWAY_EXTERNAL_PORT}:8080"]',
        "    env_file: [./config/gateway.env]",
        "    environment:",
        "      TYK_GW_SECRET: ${NODE_SECRET}",
        "      TYK_GW_NODESECRET: ${NODE_SECRET}",
        '      TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED: "true"',
        "      TYK_GW_DBAPPCONFOPTIONS_TAGS: external",
        *gateway_env,
        "    volumes:",
    ]
    if tls:
        lines.append(tls_tokens(True)["GATEWAY_TLS_VOLUME"])
    lines.extend(gateway_volumes or [])
    lines.extend([
        "      - ./plugins:/opt/tyk-gateway/plugins:ro",
        "    depends_on:",
        "      tyk-dashboard: {condition: service_started}",
        "      tyk-redis: {condition: service_healthy}",
        *gateway_dependencies,
    ])
    return "\n".join(lines) + "\n"


def common_oas(slug: str, name: str, api_id: str, listen: str, upstream: str, description: str = "") -> dict:
    # `name` and `description` are visible (API list, Portal, screenshots), so they
    # derive from the display name and never say demo, test, mock, sample, lab or evidence.
    # Ownership is recorded in x-demo-owner, which no page shows.
    return {
        "openapi": "3.0.3",
        "info": {"title": name, "version": "1.0.0", "description": description or f"{name}, served through the Tyk Gateway."},
        "servers": [{"url": upstream}],
        "paths": {},
        "components": {"securitySchemes": {}},
        "x-demo-owner": slug,
        "x-tyk-api-gateway": {
            "info": {"id": api_id, "name": name, "state": {"active": True, "internal": False}},
            "upstream": {"url": upstream},
            # Detailed activity logs per API, so the log browser shows request and response bodies,
            # while the gateway-wide setting stays off and streaming APIs are never buffered.
            "server": {"listenPath": {"value": listen, "strip": True}, "authentication": {"enabled": False},
                       "detailedActivityLogs": {"enabled": True}},
            "middleware": {"global": {"trafficLogs": {"enabled": True}, "contextVariables": {"enabled": True}}, "operations": {}},
        },
    }


def operation(method: str = "get", body: bool = False) -> dict:
    value = {"operationId": "", "responses": {"200": {"description": "Successful response"}}}
    if body:
        value["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}
    return {method: value}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def ai_studio_admin_password() -> str:
    """Return a random first-run admin password that satisfies AI Studio's rule.

    Registration refuses a password without an uppercase letter, a lowercase
    letter, a number and a special character (400 "password must contain at least
    one uppercase letter, one lowercase letter, one number, and one special
    character"). A plain token_urlsafe() does not guarantee those, so the four
    classes are prefixed explicitly.
    """
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "Aa1!" + "".join(secrets.choice(alphabet) for _ in range(20))


def write_ai_studio_secrets(target: Path) -> None:
    """Fill the companion stack's private secret.env and admin.env from their examples.

    Mirrors the bundle's own .env.example -> .env step, split in two files:
    secret.env is an env_file of both containers and holds only what they need;
    admin.env holds the host-only admin login (AISTUDIO_ADMIN_EMAIL /
    AISTUDIO_ADMIN_PASSWORD), which no container reads, so the password stays out
    of the containers' environments and `docker inspect`. Both are gitignored and
    written mode 0600. Several things make this mandatory rather than cosmetic:

    * Fixed values copied from an example configuration would be the same in
      every deployment, so a generated bundle mints its own.
    * GRPC_AUTH_TOKEN/EDGE_AUTH_TOKEN and MICROGATEWAY_ENCRYPTION_KEY/
      ENCRYPTION_KEY are the control-side and edge-side names for one value each,
      so each pair is minted once and written twice.
    * METRICS_AUTH_TOKEN protects `/metrics` on both planes, so one scrape token
      is minted for both.

    The licence is deliberately left empty: it is the one value a human must add,
    and start.sh refuses to run until they have.
    """
    companion = target / "companion" / "ai-studio"
    example = companion / "secret.env.example"
    if not example.is_file():
        return
    edge_token = secrets.token_hex(24)
    encryption_key = secrets.token_hex(16)  # ENCRYPTION_KEY must be exactly 32 chars
    container_values = {
        "GRPC_AUTH_TOKEN": edge_token,
        "EDGE_AUTH_TOKEN": edge_token,
        "MICROGATEWAY_ENCRYPTION_KEY": encryption_key,
        "ENCRYPTION_KEY": encryption_key,
        "JWT_SECRET": secrets.token_hex(32),
        "TYK_AI_SECRET_KEY": secrets.token_urlsafe(32),
        "METRICS_AUTH_TOKEN": secrets.token_hex(24),  # 48 hex characters
    }
    host_values = {"AISTUDIO_ADMIN_PASSWORD": ai_studio_admin_password()}
    for source, values, private in (
        (example, container_values, companion / "secret.env"),
        (companion / "admin.env.example", host_values, companion / "admin.env"),
    ):
        text = source.read_text()
        for key, value in values.items():
            text = text.replace(f"\n{key}=\n", f"\n{key}={value}\n")
        private.touch(mode=0o600, exist_ok=True)
        private.chmod(0o600)  # before the secrets are written, whatever the umask
        private.write_text(text)


def stable_policy_id(slug: str, policy_name: str) -> str:
    """Return a Mongo-compatible, deterministic policy identifier."""
    return hashlib.sha256(f"{slug}:{policy_name}".encode()).hexdigest()[:24]


def core_api(target: Path, slug: str, name: str, modules: set[str], gateway_base: str) -> None:
    api_id = f"{slug}-core"
    api = common_oas(slug, f"{name} Core API", api_id, f"/demo/{slug}/core/", "http://demo-upstream:8080")
    api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/core"}]
    paths = api["paths"]
    operations = api["x-tyk-api-gateway"]["middleware"]["operations"]
    if "otel" in modules:
        api["x-tyk-api-gateway"]["server"]["detailedTracing"] = {"enabled": True}

    paths["/v1/echo"] = operation("get")
    paths["/v1/echo"]["get"]["operationId"] = "echo"

    if "routing" in modules:
        paths["/v1/route"] = operation("post", True)
        paths["/v1/route"]["post"]["operationId"] = "routeByInboundCountry"
        operations["routeByInboundCountry"] = {
            "urlRewrite": {"enabled": True, "pattern": "/v1/route", "rewriteTo": "/eu/route", "triggers": [
                {"condition": "any", "rewriteTo": "/uk/route", "rules": [{"in": "requestBody", "pattern": '"InboundData"\\s*:\\s*\\{[^}]*"country_code"\\s*:\\s*"(GB|UK)"', "negate": False}]},
                {"condition": "any", "rewriteTo": "/us/route", "rules": [{"in": "requestBody", "pattern": '"InboundData"\\s*:\\s*\\{[^}]*"country_code"\\s*:\\s*"(US|CA)"', "negate": False}]},
            ]}
        }
    if "transforms" in modules:
        paths["/v1/transform"] = operation("post", True)
        paths["/v1/transform"]["post"]["operationId"] = "transformPayload"
        template = '{"weight_kg":{{ round (mulf .weight_lb 0.453592) 2 }},"country_code":"{{ if eq .country \"United Kingdom\" }}GB{{ else }}{{ .country }}{{ end }}","source":"tyk"}'
        operations["transformPayload"] = {"transformRequestBody": {"enabled": True, "format": "json", "body": base64.b64encode(template.encode()).decode()}}
        paths["/v1/transform-nested"] = operation("post", True)
        paths["/v1/transform-nested"]["post"]["operationId"] = "transformNestedPayload"
        nested_template = '{"parcel":{"weight_in_kilograms":{{ round (mulf .parcel.weight_lb 0.453592) 2 }},"items":[{{ range $index, $item := .parcel.items }}{{ if $index }},{{ end }}{"weight_in_kilograms":{{ round (mulf $item.weight_lb 0.453592) 2 }}}{{ end }}]},"inbound_country_code":"{{ if eq .InboundData.country \"United Kingdom\" }}GB{{ else if eq .InboundData.country \"Australia\" }}AU{{ else }}{{ .InboundData.country }}{{ end }}"}'
        operations["transformNestedPayload"] = {"transformRequestBody": {"enabled": True, "format": "json", "body": base64.b64encode(nested_template.encode()).decode()}}
    if "validation" in modules:
        paths["/v1/validated"] = operation("post", True)
        paths["/v1/validated"]["post"].update({
            "operationId": "validatePayload",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["customer_id"], "properties": {"customer_id": {"type": "string"}}}}}},
        })
        operations["validatePayload"] = {"validateRequest": {"enabled": True, "errorResponseCode": 422}}
    if "error-overrides" in modules:
        paths["/v1/failure"] = operation("get")
        paths["/v1/failure"]["get"]["operationId"] = "controlledFailure"
        operations["controlledFailure"] = {"urlRewrite": {"enabled": True, "pattern": "/v1/failure", "rewriteTo": "/global/failure"}}
        paths["/v1/errors/{status}"] = {
            "get": {
                "operationId": "controlledStatusError",
                "parameters": [{"name": "status", "in": "path", "required": True, "schema": {"type": "integer", "enum": [401, 422, 429, 503]}}],
                "responses": {str(code): {"description": "Controlled error"} for code in (401, 422, 429, 503)},
            }
        }
        operations["controlledStatusError"] = {"urlRewrite": {"enabled": True, "pattern": "/v1/errors/(.*)", "rewriteTo": "/status/$1"}}
        api["x-tyk-api-gateway"]["errorOverrides"] = {"enabled": True, "value": {
            "401": [{"response": {"statusCode": 401, "headers": {"Content-Type": "application/json"}, "body": '{"error":{"code":"AUTHENTICATION_ERROR","message":"Authentication credentials are missing or invalid.","retryable":false}}'}}],
            "422": [{"response": {"statusCode": 422, "headers": {"Content-Type": "application/json"}, "body": '{"error":{"code":"VALIDATION_ERROR","message":"Request validation failed.","retryable":false}}'}}],
            "429": [{"response": {"statusCode": 429, "headers": {"Content-Type": "application/json", "Retry-After": "10"}, "body": '{"error":{"code":"RATE_LIMITED","message":"Too many requests.","retryable":true}}'}}],
            "503": [{"response": {"statusCode": 503, "headers": {"Content-Type": "application/json"}, "body": '{"error":{"code":"UPSTREAM_UNAVAILABLE","message":"The upstream service is unavailable.","retryable":true}}'}}],
        }}
    if "rate-limit" in modules:
        paths["/v1/limited"] = operation("get")
        paths["/v1/limited"]["get"]["operationId"] = "limitedOperation"
        operations["limitedOperation"] = {"rateLimit": {"enabled": True, "rate": 3, "per": "10s"}}
    if "cache" in modules:
        paths["/v1/cached"] = operation("get")
        paths["/v1/cached"]["get"]["operationId"] = "cachedOperation"
        operations["cachedOperation"] = {"cache": {"enabled": True, "timeout": 30, "cacheResponseCodes": [200]}}
        api["x-tyk-api-gateway"]["middleware"]["global"]["cache"] = {"enabled": True, "timeout": 30, "cacheResponseCodes": [200], "cacheAllSafeRequests": False}
    if "circuit-breaker" in modules:
        paths["/v1/breaker"] = operation("get")
        paths["/v1/breaker"]["get"]["operationId"] = "breakerOperation"
        operations["breakerOperation"] = {"circuitBreaker": {"enabled": True, "threshold": 0.5, "sampleSize": 3, "coolDownPeriod": 10, "halfOpenStateEnabled": True}}
    write_json(target / "apis" / f"{api_id}.oas.json", api)


# Visible label and description tail per authentication module, so an API reads
# "Northwind API Key Services" rather than "Northwind api-key API".
AUTH_API_LABELS = {
    "api-key": ("API Key Services", "services protected by an API key in the Authorization header."),
    "basic-auth": ("Basic Auth Services", "services protected by HTTP Basic Authentication."),
    "jwt": ("JWT Services", "services protected by JWT validation: issuer, audience, JWKS signing keys and scope-to-policy mapping."),
    "oauth": ("OAuth Services", "services protected by OAuth 2.0 client credentials issued by Tyk."),
    "mtls": ("mTLS Services", "services protected by client certificate (mutual TLS) authentication."),
}


def auth_api(target: Path, slug: str, name: str, module: str, gateway_base: str, identity_mode: str, gateway_version: str = "") -> None:
    api_id = f"{slug}-{module}"
    label, description = AUTH_API_LABELS[module]
    api = common_oas(slug, f"{name} {label}", api_id, f"/demo/{slug}/{module}/", "http://demo-upstream:8080", f"{name} {description}")
    api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/{module}"}]
    api["paths"]["/v1/echo"] = operation("get")
    api["paths"]["/v1/echo"]["get"]["operationId"] = f"{module.replace('-', '').title()}Echo"
    auth = api["x-tyk-api-gateway"]["server"]["authentication"]
    auth["enabled"] = True

    # baseIdentityProvider is deliberately omitted on these single-scheme APIs. Each auth
    # middleware matches its own type OR the unset value, so on one scheme the field changes
    # nothing. It becomes load-bearing only with two or more schemes in legacy processing
    # mode, where it decides which middleware owns the session and therefore which ACL, rate
    # limit and quota apply. The chained-auth recipe uses compliant mode instead.
    if module == "api-key":
        api["security"] = [{"authToken": []}]
        api["components"]["securitySchemes"]["authToken"] = {"type": "apiKey", "in": "header", "name": "Authorization"}
        auth.update({"stripAuthorizationData": True, "securitySchemes": {"authToken": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}}}})
    elif module == "basic-auth":
        api["security"] = [{"basicAuth": []}]
        api["components"]["securitySchemes"]["basicAuth"] = {"type": "http", "scheme": "basic"}
        auth.update({"securitySchemes": {"basicAuth": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}}}})
    elif module == "jwt":
        api["security"] = [{"jwtAuth": []}]
        api["components"]["securitySchemes"]["jwtAuth"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        issuer = "http://identity:8080" if identity_mode == "local" else "https://idp.example.invalid"
        audience = "tyk-demo" if identity_mode == "local" else "replace-with-external-audience"
        jwks_url = f"{issuer}/.well-known/jwks.json"
        jwt_scheme = {"enabled": True, "header": {"enabled": True, "name": "Authorization"}, "identityBaseField": "sub", "signingMethod": "rsa", "allowedAudiences": [audience], "allowedIssuers": [issuer], "scopes": {"claimName": "scope", "claims": ["scope"], "scopeToPolicyMapping": [{"scope": "demo.read", "policyId": stable_policy_id(slug, "jwt-read")}]}}
        if supports_jwks_uris(gateway_version):
            # Gateway 5.9.0+ resolves jwksURIs before source for OAS APIs. Plain text rather
            # than base64, supports several endpoints, and exposes a per-endpoint cache
            # timeout so key-rotation propagation is an explicit number.
            jwt_scheme["jwksURIs"] = [{"url": jwks_url, "cacheTimeout": "5m"}]
        else:
            # Below 5.9.0 jwksURIs is ignored, so fall back to the base64 source field.
            jwt_scheme["source"] = base64.b64encode(jwks_url.encode()).decode()
        auth.update({"stripAuthorizationData": True, "securitySchemes": {"jwtAuth": jwt_scheme}})
    elif module == "oauth":
        api["security"] = [{"oauth": []}]
        api["components"]["securitySchemes"]["oauth"] = {"type": "oauth2", "flows": {"clientCredentials": {"tokenUrl": "/oauth/token", "scopes": {}}}}
        auth.update({"stripAuthorizationData": True, "securitySchemes": {"oauth": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}}}})
    write_json(target / "apis" / f"{api_id}.oas.json", api)


def mtls_api(target: Path, slug: str, name: str, gateway_base: str) -> None:
    module = "mtls"
    api_id = f"{slug}-{module}"
    label, description = AUTH_API_LABELS[module]
    api = common_oas(slug, f"{name} {label}", api_id, f"/demo/{slug}/{module}/", "http://demo-upstream:8080", f"{name} {description}")
    api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/{module}"}]
    api["paths"]["/v1/echo"] = operation("get")
    api["paths"]["/v1/echo"]["get"]["operationId"] = "MtlsEcho"
    server = api["x-tyk-api-gateway"]["server"]
    server["clientCertificates"] = {"enabled": True, "allowlist": []}
    write_json(target / "apis" / f"{module}.oas.template.json", api)


def chained_auth_api(target: Path, slug: str, name: str, gateway_base: str) -> None:
    api_id = f"{slug}-chained-auth"
    api = common_oas(slug, f"{name} Native Multi-Auth API", api_id, f"/demo/{slug}/chained-auth/", "http://demo-upstream:8080")
    api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/chained-auth"}]
    api["security"] = [{"authToken": []}, {"basicAuth": []}]
    api["components"]["securitySchemes"] = {
        "authToken": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "basicAuth": {"type": "http", "scheme": "basic"},
    }
    api["paths"]["/v1/echo"] = operation("get")
    api["paths"]["/v1/echo"]["get"]["operationId"] = "NativeMultiAuthEcho"
    api["x-tyk-api-gateway"]["server"]["authentication"] = {
        "enabled": True,
        "securityProcessingMode": "compliant",
        "securitySchemes": {
            "authToken": {"enabled": True, "header": {"enabled": True, "name": "X-API-Key"}},
            "basicAuth": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}},
        },
    }
    write_json(target / "apis" / f"{api_id}.oas.json", api)


def protocol_apis(target: Path, slug: str, name: str, modules: set[str], ports: dict[str, int], gateway_base: str) -> None:
    # "https://host" -> "wss://host", "http://host" -> "ws://host": one
    # substitution keeps the WebSocket scheme from drifting from the listener.
    websocket_base = gateway_base.replace("http", "ws", 1)
    if "websocket" in modules:
        api = common_oas(slug, f"{name} WebSocket API", f"{slug}-websocket", f"/demo/{slug}/ws/", "ws://websocket-upstream:8080")
        api["servers"] = [{"url": f"{websocket_base}/demo/{slug}/ws"}]
        api["security"] = [{"authToken": []}]
        api["components"]["securitySchemes"]["authToken"] = {"type": "apiKey", "in": "header", "name": "Authorization"}
        api["x-tyk-api-gateway"]["server"]["authentication"] = {"enabled": True, "stripAuthorizationData": True, "securitySchemes": {"authToken": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}}}}
        api["paths"]["/"] = {"get": {"operationId": "websocketConnect", "responses": {"101": {"description": "Switching protocols"}}}}
        write_json(target / "apis" / f"{slug}-websocket.oas.json", api)
    if "graphql" in modules:
        api = common_oas(slug, f"{name} GraphQL API", f"{slug}-graphql", f"/demo/{slug}/graphql/", "http://graphql-upstream:8080")
        api["paths"]["/"] = operation("post", True)
        api["paths"]["/"]["post"]["operationId"] = "graphqlQuery"
        write_json(target / "apis" / f"{slug}-graphql.oas.json", api)
    if "streams" in modules:
        producer = common_oas(slug, f"{name} Streams Producer", f"{slug}-streams-producer", f"/demo/{slug}/streams/", "http://demo-upstream:8080")
        producer["servers"] = [{"url": f"{gateway_base}/demo/{slug}/streams"}]
        producer["x-tyk-api-gateway"]["upstream"] = {"url": "http://demo-upstream:8080", "proxy": {"enabled": False}}
        producer["paths"]["/events"] = {"post": {"operationId": "publishEvent", "responses": {"202": {"description": "Accepted"}}}}
        producer["x-tyk-streaming"] = {
            "streams": {
                "producer": {
                    "input": {
                        "http_server": {
                            "path": "/events",
                            "allowed_verbs": ["POST"],
                            "timeout": "20s",
                            "sync_response": {"status": "202"},
                        }
                    },
                    "output": {
                        "broker": {
                            "pattern": "fan_out",
                            "outputs": [
                                {"kafka": {"addresses": ["kafka:9092"], "target_version": "3.8.0", "topic": f"{slug}-events"}},
                                {"sync_response": {}},
                            ],
                        }
                    },
                }
            }
        }
        write_json(target / "apis" / f"{slug}-streams-producer.oas.json", producer)
        asyncapi = f'''asyncapi: 3.0.0
info:
  title: {json.dumps(name + " Events")}
  version: 1.0.0
  description: {json.dumps(f"Neutral Kafka event contract owned by {slug}.")}
servers:
  localKafka:
    host: localhost:__KAFKA_PORT__
    protocol: kafka
channels:
  events:
    address: {slug}-events
    messages:
      demoEvent:
        $ref: '#/components/messages/DemoEvent'
operations:
  receiveDemoEvent:
    action: receive
    channel:
      $ref: '#/channels/events'
components:
  messages:
    DemoEvent:
      payload:
        type: object
        required: [event_id]
        properties:
          event_id:
            type: string
'''
        (target / "asyncapi").mkdir(parents=True, exist_ok=True)
        (target / "asyncapi" / f"{slug}-events.asyncapi.yaml").write_text(asyncapi.replace("__KAFKA_PORT__", str(ports["kafka"])))


def plugin_apis(target: Path, slug: str, name: str, modules: set[str], gateway_base: str) -> None:
    if "go-plugin" in modules:
        api = common_oas(slug, f"{name} Go Plugin API", f"{slug}-go-plugin", f"/demo/{slug}/go-plugin/", "http://demo-upstream:8080")
        api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/go-plugin"}]
        api["paths"]["/v1/echo"] = operation("get")
        api["paths"]["/v1/echo"]["get"]["operationId"] = "goPluginEcho"
        global_middleware = api["x-tyk-api-gateway"]["middleware"]["global"]
        global_middleware["pluginConfig"] = {"driver": "goplugin"}
        global_middleware["postPlugins"] = [{"enabled": True, "functionName": "PostEnrich", "path": "/opt/tyk-gateway/plugins/go/build/post_enrich.so"}]
        write_json(target / "apis" / f"{slug}-go-plugin.oas.json", api)
    if "grpc-plugin" in modules:
        api = common_oas(slug, f"{name} gRPC Post Hook API", f"{slug}-grpc-plugin", f"/demo/{slug}/grpc-plugin/", "http://demo-upstream:8080")
        api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/grpc-plugin"}]
        api["security"] = [{"authToken": []}]
        api["components"]["securitySchemes"]["authToken"] = {"type": "apiKey", "in": "header", "name": "Authorization"}
        api["paths"]["/v1/echo"] = operation("get")
        api["paths"]["/v1/echo"]["get"]["operationId"] = "grpcPluginEcho"
        api["x-tyk-api-gateway"]["server"]["authentication"] = {"enabled": True, "stripAuthorizationData": True, "securitySchemes": {"authToken": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}}}}
        global_middleware = api["x-tyk-api-gateway"]["middleware"]["global"]
        global_middleware["pluginConfig"] = {"driver": "grpc"}
        global_middleware["postPlugins"] = [{"enabled": True, "functionName": "RequestEnrichmentHook", "requireSession": False}]
        write_json(target / "apis" / f"{slug}-grpc-plugin.oas.json", api)


def key_protected_oas(slug: str, name: str, api_id: str, listen: str, server_url: str, description: str, operation_id: str) -> dict:
    """A Tyk OAS API with the Authorization-header key scheme and one GET /v1/echo."""
    api = common_oas(slug, name, api_id, listen, "http://demo-upstream:8080", description)
    api["servers"] = [{"url": server_url}]
    api["security"] = [{"authToken": []}]
    api["components"]["securitySchemes"]["authToken"] = {"type": "apiKey", "in": "header", "name": "Authorization"}
    api["x-tyk-api-gateway"]["server"]["authentication"] = {"enabled": True, "stripAuthorizationData": True, "securitySchemes": {"authToken": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}}}}
    api["paths"]["/v1/echo"] = operation("get")
    api["paths"]["/v1/echo"]["get"]["operationId"] = operation_id
    return api


def sharding_apis(target: Path, slug: str, name: str, gateway_base: str, external_base: str) -> None:
    """Write the two tagged APIs of the gateway-sharding module.

    server.gatewayTags decides where an API loads: the segmented external gateway loads only
    APIs tagged "external", and the unsegmented primary gateway loads every API, tagged or
    not. An untagged API therefore never reaches a segmented gateway.
    """
    for role, base, description in (
        ("external", external_base, f"{name} services published to the external gateway group (gateway tag external); every unsegmented gateway loads them too."),
        ("internal", gateway_base, f"{name} services tagged internal: the unsegmented gateway loads them and the segmented external gateway never does."),
    ):
        api_id = f"{slug}-{role}-services"
        api = key_protected_oas(slug, f"{name} {role.title()} Services", api_id, f"/demo/{slug}/{role}-services/",
                                f"{base}/demo/{slug}/{role}-services", description, f"{role}ServicesEcho")
        api["x-tyk-api-gateway"]["server"]["gatewayTags"] = {"enabled": True, "tags": [role]}
        write_json(target / "apis" / f"{api_id}.oas.json", api)


def compat_api(target: Path, slug: str, name: str, gateway_base: str, gateway_version: str, dashboard_version: str) -> None:
    """Write the js-plugin API: a key-protected API with a pre-auth JavaScript plugin."""
    api_id = f"{slug}-compat"
    api = key_protected_oas(
        slug, f"{name} Compatibility Services", api_id, f"/demo/{slug}/compat/", f"{gateway_base}/demo/{slug}/compat",
        f"{name} services that also accept a credential the way a previous gateway took it, as a user_key query "
        "parameter or an app_key header, mapped into Authorization by a pre-authentication JavaScript plugin so "
        "migrated clients keep working unchanged.",
        "compatibilityEcho",
    )
    global_middleware = api["x-tyk-api-gateway"]["middleware"]["global"]
    global_middleware["pluginConfig"] = {"driver": javascript_plugin_driver(gateway_version, dashboard_version)}
    # Loaded by file path from the read-only ./plugins mount; no bundle server is needed.
    global_middleware["prePlugins"] = [{"enabled": True, "functionName": js_identifier(slug), "path": f"/opt/tyk-gateway/plugins/js/{slug}-credential-mapper.js"}]
    write_json(target / "apis" / f"{api_id}.oas.json", api)


def legacy_path_api(target: Path, slug: str, name: str, scheme: str) -> None:
    """Write the custom-domain API: the hostname and path consumers use today, kept as is."""
    api_id = f"{slug}-legacy-path"
    host = f"api.legacy.{slug}.invalid"
    api = common_oas(slug, f"{name} Legacy Path", api_id, "/api/v1/records/", "http://demo-upstream:8080",
                     f"{name} records on the hostname and path consumers already call ({host}/api/v1/records/), kept unchanged during migration.")
    api["servers"] = [{"url": f"{scheme}://{host}/api/v1/records"}]
    api["paths"]["/{recordId}"] = {"get": {
        "operationId": "legacyGetRecord",
        "parameters": [{"name": "recordId", "in": "path", "required": True, "schema": {"type": "string"}}],
        "responses": {"200": {"description": "Successful response"}},
    }}
    # With enable_custom_domains on the gateway, an API bound to a domain answers only for
    # that Host; APIs without one keep answering on every host.
    api["x-tyk-api-gateway"]["server"]["customDomain"] = {"enabled": True, "name": host}
    write_json(target / "apis" / f"{api_id}.oas.json", api)


def partner_api(target: Path, slug: str, name: str, gateway_base: str) -> None:
    """Write the API behind the portal-catalogues partner product."""
    api_id = f"{slug}-partner-services"
    api = key_protected_oas(slug, f"{name} Partner Services", api_id, f"/demo/{slug}/partner-services/",
                            f"{gateway_base}/demo/{slug}/partner-services",
                            f"{name} services for partner organisations, published only in the partner catalogue.", "partnerServicesEcho")
    write_json(target / "apis" / f"{api_id}.oas.json", api)


# Top-level extension that marks a Tyk OAS file as a child version of another API. The keys
# are the query parameters of the Dashboard call that links a version to its base
# (POST /api/apis/oas?base_api_id=...&base_api_version_name=...&new_version_name=...&set_default=...),
# which is the only way the Dashboard records the link: a base file that hand-writes
# info.versioning pointing at a separately imported API is not linked. scripts/import-apis.sh
# reads it, imports every base first, and never prunes a file that carries it.
VERSION_OF_EXTENSION = "x-demo-version-of"


def add_request_header(api: dict, name: str, value: str) -> None:
    """Add one header to every request through the API's global request header transform."""
    middleware = api["x-tyk-api-gateway"]["middleware"]["global"]
    middleware["transformRequestHeaders"] = {"enabled": True, "add": [{"name": name, "value": value}], "remove": []}


def versioning_apis(target: Path, slug: str, name: str, gateway_base: str) -> None:
    """Write the versioning module's base API (v1) and its child version (v2).

    Each version stamps X-Service-Version on the request so the echo upstream reports which
    one served it. The child is linked to the base by import-apis.sh, never by the base file.
    """
    base_id = f"{slug}-versioned"
    for version, api_id, title, listen in (
        ("v1", base_id, f"{name} Versioned Services", f"/demo/{slug}/versioned/"),
        ("v2", f"{slug}-versioned-v2", f"{name} Versioned Services v2", f"/demo/{slug}/versioned-v2/"),
    ):
        api = common_oas(slug, title, api_id, listen, "http://demo-upstream:8080",
                         f"{name} services published as version {version}; clients select a version by request header and get v1 by default.")
        api["info"]["version"] = f"{version[1:]}.0.0"
        api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/versioned"}]
        api["paths"]["/v1/echo"] = operation("get")
        api["paths"]["/v1/echo"]["get"]["operationId"] = f"versioned{version.upper()}Echo"
        add_request_header(api, "X-Service-Version", version)
        if api_id != base_id:
            api[VERSION_OF_EXTENSION] = {"base_api_id": base_id, "base_api_version_name": "v1", "new_version_name": version, "set_default": False}
        write_json(target / "apis" / f"{api_id}.oas.json", api)


def vault_api(target: Path, slug: str, name: str, gateway_base: str) -> None:
    """Write the vault module's API: the upstream credential comes from Vault on every request.

    The header value is a `$secret_vault.<mount>/<path>.<field>` reference. The gateway reads
    it per request with no cache, so a rotation in Vault applies to the very next request
    with no API change or reload. An unresolvable reference becomes an empty header value
    (the request is still proxied), which is why the proof checks the value upstream.
    """
    api_id = f"{slug}-vault-credentials"
    api = common_oas(slug, f"{name} Vault Credentials", api_id, f"/demo/{slug}/vault-credentials/", "http://demo-upstream:8080",
                     f"{name} services whose upstream credential is read from HashiCorp Vault on every request and never stored in the API definition or held by the client.")
    api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/vault-credentials"}]
    api["paths"]["/v1/echo"] = operation("get")
    api["paths"]["/v1/echo"]["get"]["operationId"] = "vaultCredentialEcho"
    add_request_header(api, "X-Upstream-Credential", f"$secret_vault.{vault_secret_path(slug)}.{VAULT_SECRET_FIELD}")
    write_json(target / "apis" / f"{api_id}.oas.json", api)


def upstream_mtls_api(target: Path, slug: str, name: str, gateway_base: str) -> None:
    """Write the upstream-mtls API template; configure-upstream-mtls.sh fills in the certificate IDs.

    On 5.15.1 the mutual TLS mapping matches the upstream as host:port, while the
    pinning mapping matches the bare host (with host:port a wrong pin is silently ignored).
    Both take certificate-store IDs, and a pin ID that is not in the store is an empty
    allowlist, which means no pinning at all.
    """
    host = secure_upstream_host(slug)
    api_id = f"{slug}-upstream-mtls"
    api = common_oas(slug, f"{name} Secure Upstream", api_id, f"/demo/{slug}/upstream-mtls/", f"https://{host}:8443",
                     f"{name} services behind an upstream that requires the gateway's client certificate and whose public key the gateway pins.")
    api["servers"] = [{"url": f"{gateway_base}/demo/{slug}/upstream-mtls"}]
    api["paths"]["/v1/echo"] = operation("get")
    api["paths"]["/v1/echo"]["get"]["operationId"] = "secureUpstreamEcho"
    upstream = api["x-tyk-api-gateway"]["upstream"]
    upstream["mutualTLS"] = {"enabled": True, "domainToCertificateMapping": [{"domain": f"{host}:8443", "certificate": ""}]}
    upstream["certificatePinning"] = {"enabled": True, "domainToPublicKeysMapping": [{"domain": host, "publicKeys": []}]}
    write_json(target / "apis" / "upstream-mtls.oas.template.json", api)


def imported_keys_file(target: Path, slug: str, name: str) -> None:
    """Write migration/imported-keys.json: two neutral records exported from a previous gateway.

    The key values are minted per bundle, so they are this bundle's live consumer
    credentials once import-keys.sh has run; the README says so. The file is git-ignored and
    a strict sanitizer run refuses it, so it never travels with a shared bundle. The shareable
    imported-keys.example.json holds the same records with a placeholder instead of each value;
    import-keys.sh mints fresh values from it when imported-keys.json is missing.
    """
    def export(key) -> dict:
        records = [
            {"key": key(), "alias": f"{name} Migrated Application {1000 + index}",
             "meta_data": {"source": "previous-gateway", "application_id": f"app-{1000 + index}"}}
            for index in (1, 2)
        ]
        return {
            "description": "Consumer credentials exported from the previous gateway. scripts/import-keys.sh imports each with its existing value, so migrated consumers keep the key they already have.",
            "policy_stable_name": f"{slug}-migrated-consumers-policy",
            "keys": records,
        }

    write_json(target / "migration" / "imported-keys.json", export(imported_key_value))
    (target / "migration" / "imported-keys.json").chmod(0o600)  # live key values, like .env
    write_json(target / "migration" / "imported-keys.example.json", export(lambda: IMPORTED_KEY_PLACEHOLDER))


def rate_limit_headers() -> dict:
    """Bounded, formatted rate-limit headers.

    The OWASP template wants a rate-limit header on responses, and its integer rules reject
    an integer schema without `format` and without both bounds, so all four fields are
    load-bearing rather than decorative.
    """
    return {
        "X-RateLimit-Limit": {
            "description": "Requests permitted in the current window.",
            "schema": {"type": "integer", "format": "int32", "minimum": 0, "maximum": 10000},
        }
    }


def governance_apis(target: Path, slug: str, name: str, gateway_port: int) -> None:
    """Write the deliberately non-compliant and the compliant governed APIs.

    Both carry the `{slug}-payments` category, but the category is NOT expressed as a
    `#tag` in the API name: a name tag is honoured by evaluation and by the compliance view
    yet silently bypasses the pre-save Deployment Warning, which reads
    `/api/apis/oas/{id}/categories` and sees an empty list. `configure-governance.sh`
    assigns the category through that endpoint instead.
    """
    upstream = "http://demo-upstream:8080"

    legacy_id = f"{slug}-governed-legacy"
    legacy = common_oas(slug, f"{name} Governed Legacy", legacy_id, f"/demo/{slug}/governed-legacy/", upstream)
    # Every omission below is a finding the demo shows and then fixes: a plain http server
    # entry, no security, no operationId, no description, and an unbounded integer path
    # parameter that trips the OWASP numeric-id, integer-format and integer-limit rules.
    legacy["info"].pop("description", None)
    legacy.pop("components", None)
    legacy["servers"] = [{"url": f"http://localhost:{gateway_port}/demo/{slug}/governed-legacy"}]
    legacy["x-tyk-api-gateway"]["server"].pop("authentication", None)
    legacy["paths"]["/v1/payments"] = {
        "get": {"responses": {"200": {"description": "Successful response"}}},
        "post": {
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"reference": {"type": "string"}}}}}},
            "responses": {"200": {"description": "Successful response"}},
        },
    }
    legacy["paths"]["/v1/payments/{id}"] = {
        "get": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "Successful response"}},
        }
    }
    write_json(target / "apis" / f"{legacy_id}.oas.json", legacy)

    standard_id = f"{slug}-governed-standard"
    standard = common_oas(slug, f"{name} Governed Standard", standard_id, f"/demo/{slug}/governed-standard/", upstream)
    standard["info"]["description"] = "Governed reference API meeting the OWASP baseline and the gateway standards ruleset."
    standard["servers"] = [{"url": f"https://localhost:{gateway_port}/demo/{slug}/governed-standard"}]
    standard["security"] = [{"authToken": []}]
    standard["components"]["securitySchemes"]["authToken"] = {"type": "apiKey", "in": "header", "name": "Authorization"}
    standard["x-tyk-api-gateway"]["server"]["authentication"] = {
        "enabled": True,
        "stripAuthorizationData": True,
        "securitySchemes": {"authToken": {"enabled": True, "header": {"enabled": True, "name": "Authorization"}}},
    }
    # The custom gateway-standards ruleset asserts this block exists, so it is the fix the
    # demo applies rather than incidental configuration.
    standard["x-tyk-api-gateway"]["upstream"] = {"url": upstream, "rateLimit": {"enabled": True, "rate": 100, "per": "60s"}}
    headers = rate_limit_headers()
    responses = {
        "200": {"description": "Successful response", "headers": dict(headers)},
        "400": {"description": "Request validation failed", "headers": dict(headers)},
        "401": {"description": "Authentication credentials are missing or invalid", "headers": dict(headers)},
        "429": {
            "description": "Too many requests",
            "headers": {
                **headers,
                "Retry-After": {
                    "description": "Seconds to wait before retrying.",
                    "schema": {"type": "integer", "format": "int32", "minimum": 0, "maximum": 10000},
                },
            },
        },
        "500": {"description": "Unexpected upstream error", "headers": dict(headers)},
    }
    standard["paths"]["/v1/payments"] = {"get": {"operationId": "listGovernedPayments", "responses": responses}}
    write_json(target / "apis" / f"{standard_id}.oas.json", standard)


def policy_files(target: Path, slug: str, modules: set[str], name: str = "") -> None:
    # Policy names are visible: the Dashboard policy list, and for an ACL-partitioned
    # policy the Portal product name. They derive from the display name; stable_name in
    # meta_data is what scripts match on.
    name = name or slug

    def right(api_id: str, label: str) -> dict:
        return {api_id: {"api_id": api_id, "api_name": f"{name} {label}", "versions": ["Default"]}}

    policies: list[dict] = []
    if "api-key" in modules:
        access_rights = right(f"{slug}-api-key", "API Key Services")
        if "websocket" in modules:
            access_rights.update(right(f"{slug}-websocket", "WebSocket API"))
        if "grpc-plugin" in modules:
            access_rights.update(right(f"{slug}-grpc-plugin", "gRPC Post Hook API"))
        if "chained-auth" in modules:
            access_rights.update(right(f"{slug}-chained-auth", "Native Multi-Auth API"))
        policies.append({"id": stable_policy_id(slug, "api-key"), "name": f"{name} API Key Consumers", "active": True, "org_id": "", "rate": 60, "per": 60, "quota_max": 10 if "quota" in modules else -1, "quota_renewal_rate": 60 if "quota" in modules else -1, "access_rights": access_rights, "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-api-key-policy"}})
    if "jwt" in modules:
        policies.append({"id": stable_policy_id(slug, "jwt-read"), "name": f"{name} JWT Read Scope", "active": True, "org_id": "", "rate": 60, "per": 60, "quota_max": -1, "access_rights": right(f"{slug}-jwt", "JWT Services"), "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-jwt-read-policy", "scope": "demo.read"}})
    if "mcp" in modules:
        # Access rights name the MCP proxies, which are separate Dashboard
        # resources from the OAS APIs but are referenced the same way here.
        policies.append({
            "id": stable_policy_id(slug, "mcp"),
            "name": f"{name} MCP Agents",
            "active": True,
            "org_id": "",
            "rate": 200,
            "per": 60,
            "quota_max": -1,
            "access_rights": {**right(f"{slug}-mcp-broker", "MCP Broker"), **right(f"{slug}-rest-as-mcp", "REST as MCP")},
            "tags": [slug],
            "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-mcp-policy"},
        })
    if "oauth" in modules:
        policies.append({"id": stable_policy_id(slug, "oauth"), "name": f"{name} OAuth Clients", "active": True, "org_id": "", "rate": 60, "per": 60, "quota_max": -1, "access_rights": right(f"{slug}-oauth", "OAuth Services"), "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-oauth-policy"}})
    if modules.intersection({"gateway-sharding", "js-plugin"}):
        # The key the sharding and JavaScript-plugin proofs use. No quota, so those proofs
        # never spend the API-key policy's quota and fail unrelated checks with 403.
        platform_rights: dict = {}
        if "gateway-sharding" in modules:
            platform_rights.update(right(f"{slug}-external-services", "External Services"))
            platform_rights.update(right(f"{slug}-internal-services", "Internal Services"))
        if "js-plugin" in modules:
            platform_rights.update(right(f"{slug}-compat", "Compatibility Services"))
        policies.append({"id": stable_policy_id(slug, "platform"), "name": f"{name} Platform Consumers", "active": True, "org_id": "", "rate": 1000, "per": 60, "quota_max": -1, "quota_renewal_rate": -1, "access_rights": platform_rights, "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-platform-policy"}})
    if "key-import" in modules:
        # Imported keys are attached here, never to the API-key policy, for the same reason.
        migrated_rights = right(f"{slug}-api-key", "API Key Services")
        if "js-plugin" in modules:
            migrated_rights.update(right(f"{slug}-compat", "Compatibility Services"))
        policies.append({"id": stable_policy_id(slug, "migrated-consumers"), "name": f"{name} Migrated Consumers", "active": True, "org_id": "", "rate": 1000, "per": 60, "quota_max": -1, "quota_renewal_rate": -1, "access_rights": migrated_rights, "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-migrated-consumers-policy"}})
    if "portal" in modules:
        # The ACL-partitioned policies become Portal products (their name is the product name)
        # and the rate/quota-partitioned one becomes the plan.
        acl_only = {"quota": False, "rate_limit": False, "complexity": False, "acl": True, "per_api": False}
        policies.extend([
            {
                "id": stable_policy_id(slug, "portal-product"),
                "name": f"{name} Shared Services",
                "active": True,
                "org_id": "",
                "rate": 60,
                "per": 60,
                "quota_max": -1,
                "access_rights": right(f"{slug}-api-key", "API Key Services"),
                "partitions": dict(acl_only),
                "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-portal-product-policy"},
            },
            {
                "id": stable_policy_id(slug, "portal-plan"),
                "name": f"{name} Standard Plan",
                "active": True,
                "org_id": "",
                "rate": 60,
                "per": 60,
                "quota_max": 1000,
                "quota_renewal_rate": 2592000,
                "access_rights": {},
                "partitions": {"quota": True, "rate_limit": True, "complexity": False, "acl": False, "per_api": False},
                "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-portal-plan-policy"},
            },
        ])
        if "portal-catalogues" in modules:
            policies.append({"id": stable_policy_id(slug, "partner-product"), "name": f"{name} Partner Services", "active": True, "org_id": "", "rate": 60, "per": 60, "quota_max": -1, "access_rights": right(f"{slug}-partner-services", "Partner Services"), "partitions": dict(acl_only), "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-partner-product-policy"}})
        if "dcr" in modules:
            # The product Portal DCR is enabled on; portal/configure-dcr.sh finds it by the
            # Dashboard _id of this policy, which the Portal records as its ReferenceID.
            policies.append({"id": stable_policy_id(slug, "jwt-product"), "name": f"{name} Self-Service", "active": True, "org_id": "", "rate": 60, "per": 60, "quota_max": -1, "access_rights": right(f"{slug}-jwt", "JWT Services"), "partitions": dict(acl_only), "meta_data": {"demo_owner": slug, "stable_name": f"{slug}-jwt-product-policy"}})
    for policy in policies:
        # Throttling is opt-out, not opt-in: a policy that omits these fields
        # stores them as 0, which the Dashboard reads as "throttling enabled,
        # zero interval" and then refuses to save from the UI. -1 is the
        # disabled marker. Set here rather than on each policy so a new policy
        # cannot be added without them.
        policy.setdefault("throttle_interval", -1)
        policy.setdefault("throttle_retry_limit", -1)
        write_json(target / "policies" / f"{policy['id']}.json", policy)


# The neutral placeholder brand kit. A slate palette that is nobody's brand, a text-only
# wordmark set from the display name (never a drawn logo), and an abstract hero. It only keeps
# stock Tyk branding off the Portal; brand.json says so with "placeholder": true, and
# PORTAL-UI-001 then reports the neutral brand rather than an organisation's.
PLACEHOLDER_BRAND_COLORS = {
    "primary": "#3a5a78", "primary_dark": "#2c4660", "primary_darker": "#1f3346",
    "primary_light": "#e6edf3", "on_primary": "#ffffff", "secondary": "#5b6b7b",
    "nav_bg": "#1f2933", "nav_link": "#ffffff", "link": "#2c4660", "text": "#1f2933",
    "hero_bg": "#1f2933",
}
PLACEHOLDER_FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def wordmark_svg(name: str, fill: str) -> str:
    # The width is an estimate for a 24px semibold system font; the Portal scales the image by
    # height, so a generous width only adds transparent space.
    width = 13 * len(name) + 8
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="40" viewBox="0 0 {width} 40" '
        f'role="img" aria-label="{xml_text(name)}">\n'
        f'  <text x="0" y="29" font-family="{PLACEHOLDER_FONT.replace(chr(39), "")}" font-size="24" '
        f'font-weight="600" fill="{fill}">{xml_text(name)}</text>\n</svg>\n'
    )


def favicon_svg(name: str) -> str:
    initial = next((character for character in name if character.isalnum()), "A").upper()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">\n'
        f'  <rect width="64" height="64" rx="14" fill="{PLACEHOLDER_BRAND_COLORS["primary"]}"/>\n'
        f'  <text x="32" y="44" text-anchor="middle" font-family="{PLACEHOLDER_FONT.replace(chr(39), "")}" '
        f'font-size="36" font-weight="700" fill="#ffffff">{xml_text(initial)}</text>\n</svg>\n'
    )


def hero_svg() -> str:
    colors = PLACEHOLDER_BRAND_COLORS
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="560" height="420" viewBox="0 0 560 420">\n'
        f'  <circle cx="300" cy="210" r="170" fill="{colors["primary_light"]}"/>\n'
        f'  <rect x="150" y="120" width="220" height="150" rx="18" fill="{colors["primary"]}"/>\n'
        f'  <rect x="250" y="180" width="200" height="140" rx="18" fill="{colors["primary_dark"]}" opacity="0.85"/>\n'
        f'  <circle cx="420" cy="120" r="34" fill="{colors["secondary"]}" opacity="0.7"/>\n'
        f'  <circle cx="140" cy="310" r="22" fill="{colors["secondary"]}" opacity="0.5"/>\n</svg>\n'
    )


def placeholder_content(name: str) -> dict:
    """Neutral CMS copy for the stock Home and About pages, which otherwise describe Tyk."""
    return {"pages": {
        # The Home page title is also its hero heading.
        "/": {"title": f"Build with the {name} APIs", "blocks": {
            "HeaderDescription": f"Welcome to the {name} developer portal. Browse the API catalogue, request access and manage your application credentials in one place.",
            "FlipFlopLeftImageTitle1": f"About {name}",
            "FlipFlopLeftImageDescription1": f"{name} publishes its APIs here so that teams and partners can find them, read the documentation and start building.",
        }},
        "/about-us": {"title": f"About {name}", "blocks": {
            "HeaderDescription": f"{name} offers its APIs to internal teams and partners through this portal.",
            "NoImageSectionTitle": "What you can do here",
            "NoImageSectionDescription": "<p>Explore the API catalogue, read each product's documentation, and request access for your applications.</p>",
            "FlipFlopRightImageTitle": "How access works",
            "FlipFlopRightImageDescription": "<p>Choose a product and a plan, register an application, and receive credentials once the request is approved.</p>",
            "FlipFlopLeftImageTitle": "Support",
            "FlipFlopLeftImageDescription": f"<p>Questions about an API? Contact the {name} API team.</p>",
        }},
    }}


def write_placeholder_brand_kit(target: Path, name: str) -> None:
    brand_dir = target / "portal" / "brand"
    brand_dir.mkdir(parents=True, exist_ok=True)
    (brand_dir / "wordmark.svg").write_text(wordmark_svg(name, PLACEHOLDER_BRAND_COLORS["text"]))
    (brand_dir / "wordmark-on-dark.svg").write_text(wordmark_svg(name, "#ffffff"))
    (brand_dir / "favicon.svg").write_text(favicon_svg(name))
    (brand_dir / "hero.svg").write_text(hero_svg())
    write_json(brand_dir / "brand.json", {
        "placeholder": True,
        "name": name,
        "logo": "wordmark.svg",
        "logo_on_dark": "wordmark-on-dark.svg",
        "logo_alt": name,
        "nav_suffix": "Developer Portal",
        "favicon": "favicon.svg",
        "hero": "hero.svg",
        "footer_text": f"© {name}",
        "colors": PLACEHOLDER_BRAND_COLORS,
        "font_family": PLACEHOLDER_FONT,
        "sources": {
            "note": "Neutral placeholder generated from the display name. Replace it with your organisation's brand kit (logo, logo for dark backgrounds, favicon, colours, typeface) and record where each came from.",
        },
    })
    write_json(brand_dir / "content.json", placeholder_content(name))


def brand_kit_check():
    """validate_bundle.brand_kit_errors, so the generator and the validator hold one rule."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from validate_bundle import brand_kit_errors
    finally:
        sys.path.pop(0)
    return brand_kit_errors


def check_brand_dir(brand_dir: Path) -> None:
    """Refuse a supplied brand kit before anything is written."""
    if not brand_dir.is_dir():
        raise SystemExit(f"--brand-dir is not a directory: {brand_dir}")
    errors = brand_kit_check()(brand_dir, "--brand-dir")
    if errors:
        raise SystemExit("--brand-dir is not a usable brand kit:\n  " + "\n  ".join(errors))


def copy_brand_kit(source: Path, target: Path) -> None:
    """Copy brand.json, the assets it names and content.json, and nothing else from the kit."""
    brand = json.loads((source / "brand.json").read_text())
    brand_dir = target / "portal" / "brand"
    brand_dir.mkdir(parents=True, exist_ok=True)
    names = {"brand.json", "content.json"} | {brand[key] for key in ("logo", "logo_on_dark", "favicon", "hero") if brand.get(key)}
    for name in sorted(names):
        if (source / name).is_file():
            shutil.copyfile(source / name, brand_dir / name)


def requirement_mapping(target: Path, modules: list[str]) -> None:
    lines = ["# Requirements Mapping", "", "Replace the neutral requirement text with your own requirement IDs before marking any item live. When the IDs use your own prefix, set `requirement_id_prefix` in `demo.config.json` so `scripts/update-requirements.py` keeps updating the rows.", "", "| Requirement | Tyk capability | Asset | Test ID | Status | Evidence |", "| --- | --- | --- | --- | --- | --- |"]
    # (label, capability, asset, test ids, status) per row. HEALTH-001 runs for every bundle;
    # its row sits directly under `rest`, or first when a bundle has no REST module.
    rows: list[tuple[str, str, str, list[str], str]] = []
    health_row = ("platform health", PLATFORM_PROOF[0], PLATFORM_PROOF[1], [PLATFORM_PROOF[2]], "blocked")
    for module in modules:
        capability, asset, test_id = MODULE_PROOF[module]
        status = "talk-track" if module == "operator" else "blocked"
        extra = list(ADDITIONAL_PROOF.get(module, ()))
        for (owner, companion), ids in COMBINED_PROOF.items():
            if owner == module and companion in modules:
                extra.extend(ids)
        rows.append((module, capability, asset, [test_id, *extra], status))
        if module == "rest":
            rows.append(health_row)
    if "rest" not in modules:
        rows.insert(0, health_row)
    for index, (label, capability, asset, ids, status) in enumerate(rows, 1):
        test_ids = ", ".join(f"`{item}`" for item in ids)
        lines.append(f"| REQ-{index:03d}: Demonstrate {label} | {capability} | `{asset}` | {test_ids} | `{status}` | Pending verification |")
    (target / "notes").mkdir(exist_ok=True)
    (target / "notes" / "requirements-mapping.md").write_text("\n".join(lines) + "\n")


def write_readme(
    target: Path, name: str, slug: str, modules: list[str], ports: dict[str, int],
    versions: dict[str, str], version_sources: dict[str, str], tls: bool = False,
    identity_mode: str = "local",
) -> None:
    # TLS changes how every example command in a run is written, so it is stated
    # rather than left for the reader to infer from the URL scheme.
    transport = ""
    if tls:
        transport = f"""
## The gateway terminates TLS

`{', '.join(sorted(TLS_MODULES & set(modules)))}` needs a TLS listener, so this bundle is the exception:
the gateway presents a self-signed certificate minted by
`scripts/generate-demo-certs.sh` into `generated/certs/`. Every gateway command
therefore needs `curl -k` (bundle scripts use the `gw_curl` wrapper), browsers
and MCP clients will warn on the certificate, and any in-network service calling
the gateway needs `generated/certs/ca.crt` mounted and trusted, usually via
`SSL_CERT_FILE`. Bundles without a TLS module listen on plain HTTP and need none
of this.
"""
    companion = ""
    if "ai-studio" in modules:
        tyk_mcp = (
            "`AISTUDIO-TYKMCP-001` imports this bundle's MCP proxies from its Dashboard over a\n"
            "read-only connection, so bring the stack above up (start, bootstrap, configure)\n"
            "before verifying the companion."
            if "mcp" in modules else
            "`AISTUDIO-TYKMCP-001` records `blocked`: it imports MCP proxies from this bundle's\n"
            "Dashboard and needs the `mcp` module, which this bundle does not select."
        )
        ai_studio_tag = versions.get('ai_studio', '')
        # A tag read from Docker Hub at generation time exists there; a pinned, fallback or
        # flag value may name a release that is not published yet.
        unpublished = "" if version_sources.get("ai_studio", "").startswith("latest") else f"""
If `{ai_studio_tag}` is not on Docker Hub yet for both images, regenerate with
`--ai-studio-version` set to a tag both images carry, or with `--versions latest`,
which picks the newest 2.3 tag both images carry.
"""
        companion = f"""
## Companion stack: Tyk AI Studio {ai_studio_tag} (separate product)

`companion/ai-studio/` is its own compose project (`{slug}-aistudio`) with its own
Enterprise licence, images, network and lifecycle. It is not part of the stack
above and `scripts/verify-demo.sh` does not cover it. It is written for AI Studio
2.3; the generator refuses an `--ai-studio-version` older than 2.3.
{unpublished}
```bash
cd companion/ai-studio
$EDITOR secret.env          # add TYK_AI_LICENSE (AI Studio 2.3 Enterprise)
./start.sh
./verify-ai-studio.sh       # nine AISTUDIO-* proofs -> evidence/validation.md
```

Admin UI: `http://localhost:{ports['aistudio_admin']}/admin`
Unified OpenAI-compatible endpoint (edge): `http://localhost:{ports['aistudio_router']}/v1/chat/completions`
with `"model": "{{llm-slug}}/{{model}}"` or `"{slug}-router/{{model}}"`, and `GET /v1/models`.
Model Router alias: `http://localhost:{ports['aistudio_router']}/router/{slug}-router/v1/chat/completions`

The proofs cover the unified ingress, the Model Router, LLM failover, a zero
budget, a guardrail with its compliance event, the audit trail, token-protected
`/metrics` and the Tyk Dashboard MCP import. The Model Router, guardrail, budget,
audit trail and Tyk MCP import are Enterprise features. The two LLMs point at
in-network stub backends; `LLM_A_VENDOR` / `LLM_A_ENDPOINT` / `LLM_A_API_KEY` /
`LLM_A_METADATA_JSON` (and `LLM_B_*`) point them at real providers with no file
edits. {tyk_mcp}

See `companion/ai-studio/README.md`.
"""
    text = f"""# {name}

Portable Tyk demo bundle generated by `tyk-demo-builder`.

Selected modules: {', '.join(modules)}

## Run

```bash
# The generator created a private .env. Add TYK_LICENSE_KEY and,
# when Portal is selected, TYK_PORTAL_LICENSE.
./scripts/start-demo-stack.sh
./scripts/bootstrap-demo-stack.sh
./scripts/configure-demo.sh
./scripts/verify-demo.sh
```

Dashboard: `http://localhost:{ports['dashboard']}`
Gateway: `{'https' if tls else 'http'}://localhost:{ports['gateway']}`

Use `./scripts/show-credentials.sh` to display generated credentials explicitly.

## Network exposure

Every published port binds to `BIND_ADDRESS` in `.env`, `127.0.0.1` by default, so only this
machine reaches the demo. The local identity service (`upstreams/identity_service.py`, when
the bundle has one) is a deliberately unrestricted test fixture: its `POST /token` signs
whatever claims the caller sends, so anyone who can reach it can mint tokens this demo's JWT
APIs accept. Keep it local. To present from another machine, set `BIND_ADDRESS=0.0.0.0` (or
one interface address) only on a trusted network and only for the session, then restart the
stack; that publishes the token issuer, the Dashboard, the Gateway and every upstream to
every peer that can reach this host.

## Component versions

| Component | Image | Tag | Source | Change with |
| --- | --- | --- | --- | --- |
"""
    for component, (_, option, repository, variable) in COMPONENTS.items():
        if component not in versions:
            continue
        if variable is None:
            # The AI Studio companion is its own compose project; its tag is written into
            # companion/ai-studio/compose.yaml for both images rather than into .env.
            text += f"| {component} | `{repository}`, `{AI_STUDIO_MICROGATEWAY_REPOSITORY}` | `{versions[component]}` | {version_sources[component]} | `{option}` (regenerate), or both `image:` tags in `companion/ai-studio/compose.yaml` |\n"
            continue
        text += f"| {component} | `{repository}` | `{versions[component]}` | {version_sources[component]} | `{variable}` in `.env`, or `{option}` |\n"
    text += """
These tags are written to `.env` and `demo.config.json`, so every run of this bundle asks for
the same Tyk releases. A tag is not an image digest, and the supporting images (`redis`,
`postgres`, `python` and the observability images) use floating tags, so two machines can
still pull different builds; `docker compose images` shows what actually ran. To change a
version, edit its variable in `.env`, or regenerate with its flag. `--versions pinned`
regenerates on the builder's own default set, the versions its modules were last rehearsed
on, which is not a compatibility guarantee for every module combination. A newer release can
change module behaviour, so run `./scripts/rehearse-demo.sh` after any version change.
"""
    if "governance" in modules:
        text += f"""
## API Governance

Requires Dashboard and Gateway `v5.15.0` or later, and a Dashboard licence whose scope
carries the `governance` claim. The Tyk documentation describes the claim as disabled by
default and enabled per organisation on request, not self-service. The generator refuses
an older Dashboard or Gateway, so a `.env` edited below `v5.15.0` breaks every governance
call.

Without the claim every governance endpoint answers `403`;
`governance/configure-governance.sh` then records `GOVERNANCE_LICENSED=false` and
`GOV-001` is reported `blocked` rather than failed.

Demo flow:

1. **Rulesets** — the `{name} OWASP Baseline` (Deployment Warning on) and
   `{name} Gateway Standards` rulesets, both scoped to the `{slug}-payments` category.
2. **Governance tab** — `apis/{slug}-governed-legacy.oas.json` is non-compliant with
   how-to-fix guidance; the API-list compliance column, filter, and portfolio stats agree.
3. **Fix** — `apis/{slug}-governed-standard.oas.json` is compliant. Break or fix a spec,
   save, and the status flips within about two seconds.
4. **CI check** — `./governance/governance-check.sh` exits `0` compliant, `1` warnings only,
   `2` non-compliant, `3` could not evaluate. This is the enforceable gate; treat `3` as a
   failure, because a spec that could not be evaluated was never shown to be compliant.

Guardrails: an unknown category returns `200` with zero rulesets, so the check fails when
nothing matched; only `error` severity makes an API non-compliant; categories are stored
outside the OAS document and set through the categories endpoint; the Dashboard adds its
own `http://` server URLs on every save, so
`owasp-security-hosts-https-oas3` is overridden to `warn`; the Deployment Warning is
UI-only and a REST save of a non-compliant API returns a plain `200`.

Read `governance/README.md` for the full detail.
"""
        if "operator" in modules:
            text += """
`operator/tyk-ruleset.yaml` adds the only hard block that exists: a `TykRuleset` with
`action: block`, enforced by the Operator admission webhook on `TykOasApiDefinition`
create and update. It needs `tyk-operator` v1.5.0 or later and
is bypassed by edits to the referenced OAS ConfigMap. After `./operator/apply-operator.sh`,
run `./operator/verify-governance.sh`: it applies the ruleset, proves a compliant API is
admitted and a non-compliant one is denied, and records `GOV-003`.
"""
    text += gateway_module_readme(name, slug, set(modules), ports, versions, tls, identity_mode)
    text += transport + companion
    (target / "README.md").write_text(text)


def gateway_module_readme(
    name: str, slug: str, modules: set[str], ports: dict[str, int], versions: dict[str, str], tls: bool,
    identity_mode: str = "local",
) -> str:
    """README sections for the portal-branding, tyk-sync, gateway-sharding, custom-domain,
    js-plugin, key-import, portal-catalogues, versioning, vault and upstream-mtls modules, and
    the Portal DCR audience check."""
    scheme = "https" if tls else "http"
    curl = "curl -k" if tls else "curl"
    text = ""
    if "portal-branding" in modules:
        text += """
## Developer Portal branding

The Developer Portal is never shown in the stock Tyk theme. The brand kit lives in `portal/brand/`: `brand.json` names the logo (`logo`, and
`logo_on_dark` for the dark navigation bar, footer and sign-in pages), the favicon, the hero
image, the footer text, the colours and the typeface, and records in `sources` where each came
from; `content.json` replaces the stock Home and About page copy. `configure-demo.sh` applies
both on every run: `portal/apply-neutral-theme.sh` builds the theme from the running Portal's
default (colours go into the theme's `--tdp-*` CSS variables) and `portal/customise-content.sh`
rewrites the page copy.

Unless the bundle was generated with `--brand-dir`, the kit is a neutral placeholder
(`"placeholder": true`): a text wordmark of the display name, a slate palette and an abstract
hero image. It is a valid technical proof that no stock Tyk branding remains. For a branded
presentation, put your logo files, favicon, colours and typeface in `portal/brand/`, record
their sources, remove `"placeholder"`, then run:

```bash
./portal/apply-neutral-theme.sh
./portal/customise-content.sh
```

Never invent a logo. `PORTAL-UI-001` fails while any stock Tyk image or copy is still served on
the home, about, catalogue or login page. It passes once the four browser screenshots
`evidence/screenshots/portal-{theme,catalogue,openapi-viewer,login}.png` are captured from this
stack and each is registered with `scripts/record-screenshot.sh <name> <portal-path> <marker>`,
which records the file's digest, the page, this stack's organisation and the brand kit in
`evidence/screenshots/manifest.json`. A screenshot whose file, Portal, organisation or brand kit
has changed since is not counted.
"""
    if "tyk-sync" in modules:
        text += """
## Tyk Sync promotion

```bash
./ci/tyk-sync-local.sh promote apis
./ci/tyk-sync-local.sh rollback .runtime/tyk-sync/rollback/<promotion-id>
```

`ci/tyk-sync-local.sh` runs `ci/tyk-sync.sh` against this stack through the pinned
`tykio/tyk-sync` image (`ci/tyk-sync-docker.sh`, `TYK_SYNC_VERSION` in `.env`), so no local
binary is needed; inside that container the Dashboard is `http://host.docker.internal:<port>`.
APIs the Dashboard already holds go through `tyk-sync update` and new ones through
`tyk-sync publish`, so a promotion is repeatable. `SYNC-001` reads the latest promotion's
Dashboard read-back from `.runtime/tyk-sync/`, and `scripts/rehearse-demo.sh` runs a promotion.
See `ci/README.md` for CI use and the `TYK_SYNC_*` variables.
"""
    if "gateway-sharding" in modules:
        text += f"""
## Gateway sharding

Two gateways share one Dashboard and one Redis. `tyk-gateway`
(`{scheme}://localhost:{ports['gateway']}`, hostname `{gateway_hostname(slug, 'internal')}`) is not
segmented and loads every API. `tyk-gateway-external`
(`{scheme}://localhost:{ports['gateway_external']}`, hostname `{gateway_hostname(slug, 'external')}`) is
segmented on the tag `external` and loads only APIs whose `server.gatewayTags` carry it:
`{name} External Services` does, `{name} Internal Services` (tag `internal`) never does,
and an untagged API never reaches a segmented gateway. The Dashboard's Nodes & licenses
page lists both gateways by hostname. `SHARD-001` proves it.

The Dashboard licence must allow two gateway nodes. Over the allowance the segmented gateway
cannot register, loads no APIs and is missing from the node list, so `SHARD-001` fails;
check the remaining node count on the Nodes & licenses page before the demo.

Sharding places configuration; it does not isolate. Both gateways read the same Redis, so
keys, sessions, quotas and rate-limit counters are shared. Separate Redis (or a separate
data plane) is what isolation takes.
"""
    if "custom-domain" in modules:
        text += f"""
## Keep existing URLs during migration

`{name} Legacy Path` answers only for the host `api.legacy.{slug}.invalid` on the path
`/api/v1/records/`, which is stripped before the upstream (custom domains are enabled on
every gateway). Consumers keep calling the URL they use today:

```bash
{curl} -H 'Host: api.legacy.{slug}.invalid' {scheme}://localhost:{ports['gateway']}/api/v1/records/42
```

`DOMAIN-001` proves the stripped path upstream and a 404 for the same path without that host.
"""
    if "js-plugin" in modules:
        driver = javascript_plugin_driver(versions.get("gateway", ""), versions.get("dashboard", ""))
        engine = (
            "The API selects the goja engine (Gateway and Dashboard 5.15.0 and later) with "
            "`pluginConfig.driver: \"javascript\"`; the gateway tags the plugin's log lines with "
            "`prefix=jsvm-goja`, which `PLUGIN-JS-001` asserts."
            if driver == "javascript" else
            f"This bundle targets {' and '.join(goja_blockers(versions))}, older than the goja engine "
            "(5.15.0 on both the Gateway and the Dashboard, whose Tyk OAS schema gained the "
            "`javascript` driver in that release), so the API uses `pluginConfig.driver: \"otto\"` and "
            "`PLUGIN-JS-001` does not assert the `jsvm-goja` log line. The plugin uses only `log()`, so "
            "it runs unchanged on goja after upgrading both: switch the driver to `\"javascript\"`."
        )
        text += f"""
## JavaScript plugin

`{name} Compatibility Services` runs `plugins/js/{slug}-credential-mapper.js` before
authentication: a credential sent as `?user_key=` or in an `app_key` header, the way a
previous gateway took it, is copied into `Authorization` and removed before the upstream.
{engine} goja has no `console` and no `_` helper, so plugins must use `log()` only.
"""
    if "key-import" in modules:
        text += f"""
## Key import

`scripts/import-keys.sh` imports the records in `migration/imported-keys.json` with their
existing values through the Dashboard custom-key API (`POST /api/keys/{{keyId}}`, `PUT` when the
key exists) and attaches them to the no-quota `{name} Migrated Consumers` policy. A consumer
keeps the key the previous gateway issued. The gateway refuses a custom key shorter than
its `min_token_length` (default 3); with key hashing on, the Dashboard reads `key_hash` back
as null, so assert on the key id and alias. `KEY-IMPORT-001` proves it.

The two key values in `migration/imported-keys.json` were minted at random when this bundle
was generated. Once imported they are live, no-quota consumer credentials on this stack,
so treat the file like any other credential: do not paste the values into slides or a
write-up. The file is git-ignored and `sanitize_bundle.py --strict-runtime` refuses it, so
delete it before handing the bundle to anyone else. `migration/imported-keys.example.json`
holds the same records without values; when `imported-keys.json` is missing,
`import-keys.sh` mints fresh values from it.
"""
    if "portal-catalogues" in modules:
        text += f"""
## Portal catalogues

`portal/configure-catalogues.sh` creates the organisations `{name} Internal Teams` and
`{name} Partners`, one developer each, and two private catalogues with audiences. The
`{name} Partner Services` product and a documentation-only specification product are
published to the partner catalogue only. `PORTAL-CAT-001` logs in as each developer and
compares what they see. A private catalogue without an audience is visible to every
developer, and a user update through the Portal API clears the user's team membership.
"""
    if "dcr" in modules and identity_mode == "local":
        text += f"""
## Portal DCR and the JWT API

`portal/configure-dcr.sh` connects Portal DCR to the bundled identity service, enables DCR on
the `{name} Self-Service` product, approves one access request, and snapshots the JWT API
after each event. `PORTAL-DCR-AUD-001` fails if an event dropped the audience, the issuer or
the JWKS cache: Portal 1.17 and 1.18 did, Portal 1.19.0 with Dashboard 5.15.0 does not.
"""
    elif "dcr" in modules:
        text += """
## Portal DCR

This bundle uses an external identity provider, so it has no bundled identity service to
register clients with, and the Portal DCR wiring is not generated. `DCR-001` and
`PORTAL-DCR-AUD-001` record `blocked` until the provider's discovery URL, registration
endpoint and client settings are configured in the Portal and proved against it.
"""
    if "versioning" in modules:
        text += f"""
## API versioning

`{name} Versioned Services` is the base API (version `v1`) and `{name} Versioned Services v2`
is its child version. `apis/{slug}-versioned-v2.oas.json` names its base in the top-level
`{VERSION_OF_EXTENSION}` block, and `scripts/import-apis.sh` creates it through the Dashboard's
versioning call (`POST /api/apis/oas?base_api_id=...&base_api_version_name=v1&new_version_name=v2`),
which is the only way the Dashboard links a version to its base. A request without a version
header is served by v1; the header named in the base's `info.versioning.key` selects v2:

```bash
{curl} {scheme}://localhost:{ports['gateway']}/demo/{slug}/versioned/v1/echo
{curl} -H 'x-api-version: v2' {scheme}://localhost:{ports['gateway']}/demo/{slug}/versioned/v1/echo
```

(The Dashboard sets the header name; read it from the base readback if it differs.) An update
of a versioned base must carry `info.dbId`, `info.orgId` and `info.versioning` from its
readback, which the importer merges in: without `dbId` the Dashboard inserts a second row with
an empty id, and without `orgId` it refuses the update. The importer never prunes a child
version. `VERSION-001` proves the routing, the link and one row per API id after re-import.
"""
    if "vault" in modules:
        text += f"""
## Upstream credentials from Vault

A Vault dev server (`http://localhost:{ports['vault']}`, root token `VAULT_TOKEN` in `.env`) holds
the upstream credential at `{vault_secret_path(slug)}`, field `{VAULT_SECRET_FIELD}`.
`{name} Vault Credentials` adds it to every upstream request as `X-Upstream-Credential` from
the reference `$secret_vault.{vault_secret_path(slug)}.{VAULT_SECRET_FIELD}`; the gateway reads
it per request, with no cache, so a rotation in Vault applies to the next request with no API
change or reload. Dev mode keeps data in memory: `scripts/configure-vault.sh` (run by
`configure-demo.sh`) seeds the value only when it is absent, so run configuration again after
restarting Vault. An unresolvable reference becomes an empty header, not an error.
`SECRETS-VAULT-001` compares a hash of the value the upstream received with Vault's, rotates it,
and checks that a client-supplied header is overwritten and the value never reaches the client.
"""
    if "upstream-mtls" in modules:
        text += f"""
## Upstream mutual TLS and certificate pinning

`{name} Secure Upstream` proxies to `https://{secure_upstream_host(slug)}:8443`, which refuses any
connection without a client certificate from the bundle's upstream CA. `start-demo-stack.sh` mints
the certificates into `generated/certs/upstream/`, and `scripts/configure-upstream-mtls.sh` uploads
the gateway's client certificate, the upstream's public key and an unrelated public key to the
Dashboard certificate store, then renders `apis/{slug}-upstream-mtls.oas.json` from the template.
The mutual TLS mapping matches `{secure_upstream_host(slug)}:8443` (host and port); the pinning
mapping matches `{secure_upstream_host(slug)}` (bare host). The gateway trusts the upstream CA
through `SSL_CERT_DIR`. `UPSTREAM-MTLS-001` proves a 200 with the gateway's client certificate
name reported upstream, a 500 when the pin is swapped for the unrelated key, recovery, and the
upstream refusing a caller without a client certificate.
"""
    return text


def main() -> int:
    args = parse_args()
    validate_slug(args.slug)
    validate_display_name(args.name)
    for dest, option, _, _ in COMPONENTS.values():
        if getattr(args, dest) is not None:
            validate_version(getattr(args, dest), option)
    modules = expand_modules(args.modules, args.identity_mode)
    module_set = set(modules)
    versions, version_sources, version_warnings = resolve_component_versions(
        {component: getattr(args, dest) for component, (dest, _, _, _) in COMPONENTS.items()},
        args.versions,
        components=selected_components(module_set),
    )
    for warning in version_warnings:
        print(warning, file=sys.stderr)
    for component, (_, option, _, _) in COMPONENTS.items():
        if component not in versions:
            continue
        # Docker Hub tag names are constrained too, but a resolved tag lands in .env and
        # compose, so it passes the same gate as a user-supplied one. validate_bundle.py
        # requires the same strict vX.Y.Z[-suffix] shape, so refuse anything else here
        # rather than generate a bundle that fails its own validation.
        validate_version(versions[component], option)
        if not VERSION_TAG.fullmatch(versions[component]):
            raise SystemExit(f"{option} must be a vX.Y.Z tag with an optional -suffix (for example v5.15.0 or v1.5.0-rc2); got {versions[component]}")
    for component, (dest, _, _, _) in COMPONENTS.items():
        if component in versions:
            setattr(args, dest, versions[component])
    if "governance" in module_set:
        if not supports_governance(args.dashboard_version):
            raise SystemExit(
                "The governance module requires Dashboard v5.15.0 or later, but "
                f"--dashboard-version is {args.dashboard_version}. Regenerate with "
                "--dashboard-version v5.15.0 --gateway-version v5.15.0 (or newer), or drop "
                "both flags to use the latest stable release."
            )
        if not supports_governance(args.gateway_version):
            raise SystemExit(
                "The governance module requires Gateway v5.15.0 or later, but "
                f"--gateway-version is {args.gateway_version}. Regenerate with "
                "--dashboard-version v5.15.0 --gateway-version v5.15.0 (or newer), or drop "
                "both flags to use the latest stable release."
            )
        if "operator" in module_set and not supports_governance_operator(args.operator_version):
            raise SystemExit(
                "Governance with the operator module requires Tyk Operator v1.5.0 or later "
                "(the release that added TykRuleset), but --operator-version is "
                f"{args.operator_version}. Drop --operator-version to use the latest release, "
                "or regenerate with --operator-version v1.6.0."
            )
    # The ai-studio gate names where a refused version came from (flag, pin or Docker Hub).
    args.ai_studio_version_source = version_sources.get("ai_studio")
    check_module_version_support(module_set, args)
    if args.brand_dir is not None:
        if "portal" not in module_set:
            raise SystemExit("--brand-dir brands the Developer Portal; select the portal module or drop --brand-dir")
        args.brand_dir = args.brand_dir.expanduser().resolve()
        check_brand_dir(args.brand_dir)
    tls = gateway_tls_required(module_set)
    ports = choose_ports(args.port_base)
    gateway_base = f"{'https' if tls else 'http'}://localhost:{ports['gateway']}"
    target = args.output.expanduser().resolve()
    prepare_target(target, args.force)
    copy_assets(target, module_set, args.identity_mode, args.slug)

    optional_services, gateway_env, gateway_dependencies, gateway_volumes, profiles = service_blocks(module_set, args.identity_mode, tls, args.slug)
    compose = (STARTER / "compose.yaml.tmpl").read_text().replace("__SLUG__", args.slug)
    for token, value in (
        ("__OPTIONAL_SERVICES__", optional_services),
        ("__GATEWAY_MODULE_ENV__", gateway_env),
        ("__GATEWAY_MODULE_DEPENDS__", gateway_dependencies),
        ("__GATEWAY_TLS_VOLUME__", tls_tokens(tls)["GATEWAY_TLS_VOLUME"]),
        ("__GATEWAY_MODULE_VOLUMES__", gateway_volumes),
    ):
        # Whole-line tokens: drop the line when the value is empty, rather than
        # leaving a blank line inside a mapping or a sequence.
        compose = compose.replace(token, value) if value else compose.replace(f"{token}\n", "")
    (target / "compose.yaml").write_text(compose)

    tokens = {
        "SLUG": args.slug,
        "DISPLAY_NAME": args.name,
        "COMPOSE_PROFILES": ",".join(profiles),
        "ENABLE_WEBSOCKETS": str("websocket" in module_set).lower(),
        "ENABLE_STREAMS": str("streams" in module_set).lower(),
        "AISTUDIO_VERSION": versions.get("ai_studio", ""),
        "ENABLE_JSVM": str("js-plugin" in module_set).lower(),
        "ENABLE_CUSTOM_DOMAINS": str("custom-domain" in module_set).lower(),
        "JS_PLUGIN_NAME": js_identifier(args.slug),
        # Readable hostnames for the Dashboard node list. With sharding the primary gateway is
        # the unsegmented "internal" one of a pair.
        "GATEWAY_HOSTNAME": gateway_hostname(args.slug, "internal" if "gateway-sharding" in module_set else ""),
        "GATEWAY_EXTERNAL_HOSTNAME": gateway_hostname(args.slug, "external"),
        "SECURE_UPSTREAM_HOST": secure_upstream_host(args.slug),
        "VAULT_SECRET_PATH": vault_secret_path(args.slug),
        "VAULT_SECRET_FIELD": VAULT_SECRET_FIELD,
        "VAULT_DATA_PATH": vault_data_path(args.slug),
    }
    tokens.update(tls_tokens(tls))
    tokens.update({f"{name.upper()}_PORT": str(value) for name, value in ports.items()})
    for path in target.rglob("*"):
        if path.is_file():
            replace_tokens(path, tokens)
    # After token replacement, so a supplied SVG is copied byte for byte.
    if "portal-branding" in module_set:
        if args.brand_dir is not None:
            copy_brand_kit(args.brand_dir, target)
        else:
            write_placeholder_brand_kit(target, args.name)

    env_example = target / ".env.example"
    env_text = env_example.read_text()
    for component, (_, _, _, variable) in COMPONENTS.items():
        if variable is None or component not in versions:
            continue
        # Replace the whole line so the starter's known-good value never leaks through.
        env_text, count = re.subn(rf"(?m)^{variable}=.*$", f"{variable}={versions[component]}", env_text)
        if count != 1:
            raise SystemExit(f"starter .env.example must define {variable} exactly once")
    env_example.write_text(env_text)
    private_env = env_text
    private_env = private_env.replace("ADMIN_SECRET=\n", f"ADMIN_SECRET={secrets.token_urlsafe(32)}\n")
    private_env = private_env.replace("NODE_SECRET=\n", f"NODE_SECRET={secrets.token_urlsafe(32)}\n")
    private_env = private_env.replace("POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n")
    if "vault" in module_set:
        # Hex, so the token never needs quoting in .env, compose or a header.
        private_env = private_env.replace("VAULT_TOKEN=\n", f"VAULT_TOKEN={secrets.token_hex(24)}\n")
    env_file = target / ".env"
    env_file.write_text(private_env)
    env_file.chmod(0o600)

    config = {
        "schema_version": 1,
        "slug": args.slug,
        "display_name": args.name,
        "modules": modules,
        "identity_provider_mode": args.identity_mode,
        "compose_profiles": profiles,
        "component_versions": {component: versions[component] for component in CONFIG_COMPONENT_ORDER if component in versions},
        "component_version_sources": {component: version_sources[component] for component in CONFIG_COMPONENT_ORDER if component in versions},
        "ports": ports,
        "proof": {module: MODULE_PROOF[module][2] for module in modules},
        # Derived from the module set, never asked for. Recorded so the bundle
        # is self-describing: a reader, or a later script, can tell which
        # listener the assets were written for without inferring it from a URL.
        "gateway_tls": tls,
        # The ID prefix scripts/update-requirements.py matches in notes/requirements-mapping.md.
        # Change it when the rows are renumbered with your own requirement IDs.
        "requirement_id_prefix": "REQ",
    }
    write_json(target / "demo.config.json", config)

    if "rest" in module_set:
        core_api(target, args.slug, args.name, module_set, gateway_base)
    for module in ("api-key", "basic-auth", "jwt", "oauth"):
        if module in module_set:
            auth_api(target, args.slug, args.name, module, gateway_base, args.identity_mode, args.gateway_version)
    if "mtls" in module_set:
        mtls_api(target, args.slug, args.name, gateway_base)
    if "chained-auth" in module_set:
        chained_auth_api(target, args.slug, args.name, gateway_base)
    protocol_apis(target, args.slug, args.name, module_set, ports, gateway_base)
    plugin_apis(target, args.slug, args.name, module_set, gateway_base)
    if "governance" in module_set:
        governance_apis(target, args.slug, args.name, ports["gateway"])
    if "gateway-sharding" in module_set:
        sharding_apis(target, args.slug, args.name, gateway_base, f"{'https' if tls else 'http'}://localhost:{ports['gateway_external']}")
    if "js-plugin" in module_set:
        compat_api(target, args.slug, args.name, gateway_base, args.gateway_version, args.dashboard_version)
    if "custom-domain" in module_set:
        legacy_path_api(target, args.slug, args.name, "https" if tls else "http")
    if "portal-catalogues" in module_set:
        partner_api(target, args.slug, args.name, gateway_base)
    if "key-import" in module_set:
        imported_keys_file(target, args.slug, args.name)
    if "versioning" in module_set:
        versioning_apis(target, args.slug, args.name, gateway_base)
    if "vault" in module_set:
        vault_api(target, args.slug, args.name, gateway_base)
    if "upstream-mtls" in module_set:
        upstream_mtls_api(target, args.slug, args.name, gateway_base)
    policy_files(target, args.slug, module_set, args.name)
    requirement_mapping(target, modules)
    write_readme(target, args.name, args.slug, modules, ports, versions, version_sources, tls, args.identity_mode)
    if "ai-studio" in module_set:
        write_ai_studio_secrets(target)

    for path in target.rglob("*.sh"):
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"Generated {target}")
    print(f"Modules: {', '.join(modules)}")
    print("Component versions: " + ", ".join(f"{component} {versions[component]} ({version_sources[component]})" for component in COMPONENTS if component in versions))
    print("Next: set license values in the private .env, then run scripts/start-demo-stack.sh")
    if "portal-branding" in module_set and args.brand_dir is None:
        print("NOTE: portal/brand/ holds a neutral placeholder brand kit. It keeps stock Tyk branding off the Portal; for a branded presentation, supply your organisation's brand kit (logo, favicon, colours, typeface) in portal/brand/.")
    if "js-plugin" in module_set and javascript_plugin_driver(args.gateway_version, args.dashboard_version) == "otto":
        print(f"NOTE: the goja JavaScript engine needs Gateway and Dashboard 5.15.0; this bundle has {' and '.join(goja_blockers(versions))}, so the js-plugin API uses driver \"otto\"; PLUGIN-JS-001 does not assert the jsvm-goja log line.")
    if "ai-studio" in module_set:
        # Loud on purpose. Without an ENT licence this module produces nothing
        # demonstrable: the Model Router returns ErrEnterpriseFeature for every
        # call in Community Edition, and there is no partial version of the demo.
        print()
        print("=" * 74)
        print(" TYK AI STUDIO COMPANION STACK: ENTERPRISE LICENCE REQUIRED")
        print("=" * 74)
        print(" The ai-studio module is a SEPARATE PRODUCT from the Tyk Gateway. It was")
        print(f" written to companion/ai-studio/ with its own compose project ({args.slug}-aistudio),")
        print(" its own images and its own ports. It does not appear in compose.yaml and")
        print(" scripts/verify-demo.sh does not cover it.")
        print()
        print(" The Model Router is Enterprise-only and has no Community fallback, so")
        print(" without a Tyk AI Studio ENTERPRISE licence there is nothing to show.")
        print()
        print(f"   1. add TYK_AI_LICENSE to {target}/companion/ai-studio/secret.env")
        print("   2. cd companion/ai-studio && ./start.sh")
        print("   3. ./verify-ai-studio.sh      -> nine AISTUDIO-* proofs (ENT, ROUTER, UNIFIED, ...)")
        print()
        print(" secret.env already holds freshly minted edge secrets and admin.env the")
        print(" admin login (host scripts only); both are gitignored and mode 0600.")
        print(" Do not commit either.")
        print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
