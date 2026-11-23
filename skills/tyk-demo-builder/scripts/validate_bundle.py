#!/usr/bin/env python3
"""Statically validate a generated standalone Tyk demo bundle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Iterable


PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")
# A Tyk image tag: vMAJOR.MINOR.PATCH with an optional pre-release suffix (v1.5.0-rc2,
# v2.2.0-rc10.0). Anything looser, such as "v5.15.0 (rc2)", would reach .env and compose.
VERSION_TAG = re.compile(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
# Extra components a bundle adds by hand only need to be a safe container tag.
SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
EXPECTED_PORTS = {
    "dashboard", "portal", "gateway", "upstream", "identity", "websocket",
    "graphql", "kafka", "otel_grpc", "otel_http", "prometheus", "tempo",
}
# Module-specific ports. The generator allocates them in every bundle (one fixed
# block, so offsets never move), but they are not required: validate_ports only
# checks EXPECTED_PORTS and permits any extra name, so a hand-edited bundle that
# drops these still validates. Informational; validate_ports does not read it.
OPTIONAL_PORTS = {
    "mcp_mock", "mcp_inspector", "mcp_inspector_proxy",
    "aistudio_admin", "aistudio_proxy", "aistudio_router",
    "gateway_external", "vault", "upstream_mtls",
}
REQUIRED_IGNORES = {
    # `**/` because a pattern with a slash before its end is anchored to the bundle root, and
    # a companion stack (companion/ai-studio/evidence/bodies/) keeps response bodies too.
    ".env", ".runtime/", "**/evidence/bodies/", "generated/certs/",
    "generated/*.pem", "generated/*.key", "generated/*.crt",
    "plugins/**/build/", "*.so", "*.zip", "__pycache__/", "*.pyc",
    # The AI Studio companion stack keeps its licence and its minted edge secrets
    # in secret.env, its host-only admin login in admin.env, and its SQLite
    # databases under data/. All three are required ignores rather than
    # conventions: a bundle that tracks any of them has published a credential.
    "secret.env", "companion/*/data/",
    "companion/*/admin.env",
    # key-import: minted consumer keys, live once import-keys.sh runs.
    "migration/imported-keys.json",
}
SUPPORTED_MODULES = {
    "rest", "api-key", "basic-auth", "jwt", "oauth", "mtls", "chained-auth",
    "routing", "transforms", "validation", "error-overrides", "rate-limit",
    "quota", "cache", "circuit-breaker", "websocket", "graphql", "streams",
    "portal", "dcr", "portal-branding", "otel", "go-plugin", "grpc-plugin",
    "operator", "tyk-sync", "cicd", "governance", "mcp", "ai-studio",
    "gateway-sharding", "js-plugin", "key-import", "custom-domain", "portal-catalogues",
    "versioning", "vault", "upstream-mtls",
}
# ai-studio has no dependencies: it is a separate product shipped as a
# self-contained companion/ai-studio/ compose project, not a Gateway module.
# portal and portal-branding imply each other: a Developer Portal in a demo is always shown
# in your organisation's branding, never in the stock Tyk theme.
MODULE_DEPENDENCIES = {
    "api-key": {"rest"}, "basic-auth": {"rest"}, "jwt": {"rest"}, "oauth": {"rest"},
    "mtls": {"rest"}, "chained-auth": {"rest", "api-key", "basic-auth"},
    "routing": {"rest"}, "transforms": {"rest"}, "validation": {"rest"},
    "error-overrides": {"rest"}, "rate-limit": {"rest"}, "quota": {"rest", "api-key"},
    "cache": {"rest"}, "circuit-breaker": {"rest"}, "websocket": {"api-key"},
    "graphql": {"rest"}, "streams": {"rest"}, "portal": {"api-key", "portal-branding"},
    "dcr": {"portal", "jwt"}, "portal-branding": {"portal"}, "otel": {"rest"},
    "go-plugin": {"rest"}, "grpc-plugin": {"rest", "api-key"}, "operator": {"rest"},
    "tyk-sync": {"rest"}, "cicd": {"tyk-sync"}, "governance": {"rest"}, "mcp": {"api-key"},
    "gateway-sharding": {"rest", "api-key"}, "js-plugin": {"api-key"}, "key-import": {"api-key"},
    "custom-domain": {"rest"}, "portal-catalogues": {"portal"},
    "versioning": {"rest"}, "vault": {"rest"}, "upstream-mtls": {"rest"},
}
MODULE_TEST_IDS = {
    "rest": "REST-001", "api-key": "AUTH-KEY-001", "basic-auth": "AUTH-BASIC-001",
    "jwt": "AUTH-JWT-001", "oauth": "AUTH-OAUTH-001", "mtls": "AUTH-MTLS-001",
    "chained-auth": "AUTH-CHAIN-001", "routing": "ROUTE-001", "transforms": "TRANSFORM-001",
    "validation": "VALIDATE-001", "error-overrides": "ERROR-001", "rate-limit": "LIMIT-001",
    "quota": "QUOTA-001", "cache": "CACHE-001", "circuit-breaker": "CB-001",
    "websocket": "WS-001", "graphql": "GQL-001", "streams": "STREAM-001",
    "portal": "PORTAL-001", "dcr": "DCR-001", "portal-branding": "PORTAL-UI-001",
    "otel": "OTEL-001", "go-plugin": "PLUGIN-GO-001", "grpc-plugin": "PLUGIN-GRPC-001",
    "operator": "OPERATOR-001", "tyk-sync": "SYNC-001", "cicd": "CICD-001",
    "governance": "GOV-001",
    "mcp": "MCP-DISCOVERY-001", "ai-studio": "AISTUDIO-ROUTER-001",
    "gateway-sharding": "SHARD-001", "js-plugin": "PLUGIN-JS-001", "key-import": "KEY-IMPORT-001",
    "custom-domain": "DOMAIN-001", "portal-catalogues": "PORTAL-CAT-001",
    "versioning": "VERSION-001", "vault": "SECRETS-VAULT-001", "upstream-mtls": "UPSTREAM-MTLS-001",
}
# The optional requirement_id_prefix in demo.config.json: the ID prefix update-requirements.py
# matches (REQ by default, or your own scheme such as ACME for ACME-014).
REQUIREMENT_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,31}")


def is_template(path: Path) -> bool:
    """Return whether a file is intentionally allowed to retain placeholders."""
    return path.suffix == ".tmpl" or ".template." in path.name or "templates" in path.parts


def bundle_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or path.is_dir():
            continue
        yield path


def safe_relative_path(root: Path, path: Path) -> Path:
    """Resolve a bundle path and reject symlinks that escape the bundle."""
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes bundle: {path.relative_to(root)}") from exc


_BLOCK_SCALAR = re.compile(r"(?:^|:\s+|-\s+)[|>][-+]?[0-9]?[-+]?$")


def check_yaml_structure(text: str) -> None:
    """Dependency-free structural check used when PyYAML is not installed.

    The builder's documented prerequisite is a plain python3, so this path runs on real
    machines and must not reject valid YAML. It is deliberately narrow: it catches tabs in
    indentation, unterminated quoted scalars and unbalanced flow collections, and nothing
    else. To avoid false positives it follows three YAML rules:

    - `#` starts a comment only at the start of a line or after whitespace, outside quotes.
    - A quote opens a quoted scalar only where a scalar can start (start of line, after
      `: `, `- `, `? `, `[`, `{` or `,`), so the apostrophe in `description: it's fine`
      is part of a plain scalar. Inside single quotes `''` is an escaped quote and a
      backslash is literal; inside double quotes a backslash escapes.
    - Lines belonging to a `|` or `>` block scalar are content, never syntax.
    """
    pairs = {"[": "]", "{": "}"}
    stack: list[str] = []
    quote = ""
    block_indent: int | None = None
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if block_indent is not None:
            if not stripped.strip() or indent > block_indent:
                continue
            block_indent = None
        if not quote and "\t" in line[:len(line) - len(line.lstrip(" \t"))]:
            raise ValueError("tabs are not allowed in YAML indentation")
        prev = ""  # last significant character outside quotes on this line
        spaced = False  # whitespace seen since prev
        content_end = len(line)
        index = 0
        while index < len(line):
            char = line[index]
            if quote == "'":
                if char == "'":
                    if index + 1 < len(line) and line[index + 1] == "'":
                        index += 2
                        continue
                    quote, prev, spaced = "", "'", False
                index += 1
                continue
            if quote == '"':
                if char == "\\":
                    index += 2
                    continue
                if char == '"':
                    quote, prev, spaced = "", '"', False
                index += 1
                continue
            if char in " \t":
                spaced = True
                index += 1
                continue
            if char == "#" and (index == 0 or line[index - 1] in " \t"):
                content_end = index
                break
            scalar_start = (
                prev == ""
                or prev in "[{,"
                or (prev in ":-?" and spaced)
                or (prev == ":" and bool(stack))
            )
            if char in "'\"" and scalar_start:
                quote = char
            elif char in pairs and (scalar_start or stack):
                stack.append(pairs[char])
            elif char in "]}" and (stack or scalar_start):
                if not stack or stack.pop() != char:
                    raise ValueError(f"unbalanced YAML flow delimiter: {char}")
            prev, spaced = char, False
            index += 1
        if not quote and not stack and _BLOCK_SCALAR.search(line[:content_end].rstrip()):
            block_indent = indent
    if quote:
        raise ValueError("unterminated YAML quote")
    if stack:
        raise ValueError(f"unterminated YAML flow delimiter: expected {stack[-1]}")


def load_yaml(path: Path) -> None:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        check_yaml_structure(path.read_text(encoding="utf-8"))
        return
    try:
        with path.open(encoding="utf-8") as stream:
            list(yaml.safe_load_all(stream))
    except yaml.YAMLError as exc:
        raise ValueError(str(exc)) from exc


def validate_ports(config: object) -> list[str]:
    if not isinstance(config, dict) or not isinstance(config.get("ports"), dict):
        return ["demo.config.json: ports must be an object"]
    ports = config["ports"]
    errors: list[str] = []
    missing = EXPECTED_PORTS - set(ports)
    if missing:
        errors.append(f"demo.config.json: missing ports: {', '.join(sorted(missing))}")
    # Extra ports are permitted. A bundle may add services the generator does not
    # know about, and a closed set means the validator has to be edited in step
    # with every new component — which is how a bundle ends up failing its own
    # gate and the gate ends up ignored.
    values = list(ports.values())
    if any(type(value) is not int or not 1024 <= value <= 65535 for value in values):
        errors.append("demo.config.json: ports must be integers from 1024 through 65535")
    elif len(values) != len(set(values)):
        errors.append("demo.config.json: ports must be unique")
    # Ports are deliberately not required to form one consecutive block. Choosing
    # product defaults (3000 / 8080 / 3001) is a legitimate decision that makes a
    # demo read like a real deployment, and a bundle should not have to lie in
    # demo.config.json to pass its own validation.
    return errors


def validate_config(config: object) -> list[str]:
    if not isinstance(config, dict):
        return ["demo.config.json: root must be an object"]
    errors = validate_ports(config)
    # Required keys, not an exhaustive set — the same closed-list problem as
    # modules, ports and component_versions. A bundle that records something
    # extra about itself should not fail its own gate for being honest.
    required_keys = {
        "schema_version", "slug", "display_name", "modules", "identity_provider_mode",
        "compose_profiles", "component_versions", "ports", "proof",
    }
    missing_keys = required_keys - set(config)
    if missing_keys:
        errors.append(f"demo.config.json: missing keys: {', '.join(sorted(missing_keys))}")
    if config.get("schema_version") != 1:
        errors.append("demo.config.json: schema_version must be 1")
    if not isinstance(config.get("slug"), str) or not config["slug"]:
        errors.append("demo.config.json: slug must be a non-empty string")
    if not isinstance(config.get("display_name"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", config["display_name"]):
        errors.append("demo.config.json: display_name is missing or unsafe")
    modules = config.get("modules")
    if not isinstance(modules, list) or not modules or any(not isinstance(item, str) or not item for item in modules):
        errors.append("demo.config.json: modules must be a non-empty string array")
        selected: set[str] = set()
        # The proof-map check below reads selected_known unconditionally; without
        # this a malformed modules list raised NameError instead of reporting.
        selected_known: set[str] = set()
    else:
        selected = set(modules)
        if modules != sorted(selected):
            errors.append("demo.config.json: modules must be unique and sorted")
        # A module the generator does not ship must be declared under
        # `custom_modules`, so a bundle can record a component added by hand
        # without a typo ("mpc", "goverance") silently passing as one. Only known
        # modules are held to the dependency, profile and proof expectations below.
        custom = config.get("custom_modules", [])
        if not isinstance(custom, list) or any(not isinstance(item, str) or not item for item in custom):
            errors.append("demo.config.json: custom_modules must be an array of module names")
            custom = []
        unknown = selected - SUPPORTED_MODULES - set(custom)
        if unknown:
            errors.append(
                f"demo.config.json: unknown modules: {', '.join(sorted(unknown))}; "
                "fix the name, or declare a hand-added module under custom_modules"
            )
        selected_known = selected & SUPPORTED_MODULES
        missing_dependencies = {
            dependency for module in selected_known for dependency in MODULE_DEPENDENCIES.get(module, set())
            if dependency not in selected
        }
        if missing_dependencies:
            errors.append(f"demo.config.json: missing module dependencies: {', '.join(sorted(missing_dependencies))}")
    versions = config.get("component_versions")
    # Required keys must be present and carry a strict Tyk tag; extra components a
    # bundle adds (for example an AI or identity product) are recorded, not rejected,
    # but still have to be a safe container tag.
    required_versions = {"gateway", "dashboard", "pump", "portal", "operator", "tyk_sync"}
    if not isinstance(versions, dict) or not required_versions.issubset(versions):
        errors.append("demo.config.json: component_versions must contain all required non-empty version strings")
    else:
        for component, value in sorted(versions.items()):
            pattern = VERSION_TAG if component in required_versions else SAFE_TAG
            if not isinstance(value, str) or not pattern.fullmatch(value):
                expected = "a vX.Y.Z tag with an optional -suffix" if component in required_versions else "a safe container tag"
                errors.append(f"demo.config.json: component_versions.{component} must be {expected}")
    # component_version_sources arrived after schema 1 shipped, so older bundles omit it.
    # When present it must name a source for every required component, and may only name
    # components that component_versions records.
    if "component_version_sources" in config:
        sources = config["component_version_sources"]
        recorded = set(versions) if isinstance(versions, dict) else set()
        if (
            not isinstance(sources, dict)
            or not required_versions.issubset(sources)
            or not set(sources) <= recorded
            or any(not isinstance(value, str) or not value.strip() for value in sources.values())
        ):
            errors.append("demo.config.json: component_version_sources must name a non-empty source for every component version")
    if "requirement_id_prefix" in config:
        prefix = config["requirement_id_prefix"]
        if not isinstance(prefix, str) or not REQUIREMENT_PREFIX.fullmatch(prefix):
            errors.append("demo.config.json: requirement_id_prefix must be 1-32 letters, digits, dots or underscores, starting with a letter (no trailing hyphen)")
    identity_mode = config.get("identity_provider_mode")
    if identity_mode not in {"local", "external"}:
        errors.append("demo.config.json: identity_provider_mode must be local or external")
    expected_profiles = set()
    if identity_mode == "local" and selected.intersection({"jwt", "dcr"}):
        expected_profiles.add("identity")
    for module, profile in (("websocket", "websocket"), ("graphql", "graphql"), ("streams", "streams"), ("portal", "portal"), ("otel", "otel"), ("grpc-plugin", "grpc-plugin"), ("mcp", "mcp"), ("vault", "vault"), ("upstream-mtls", "upstream-mtls")):
        if module in selected:
            expected_profiles.add(profile)
    # Subset checks, not equality: a bundle that adds its own service declares an
    # extra profile, and one that adds a component declares an extra proof entry.
    # Every profile and proof a selected module requires must still be present.
    declared_profiles = config.get("compose_profiles")
    if not isinstance(declared_profiles, list) or declared_profiles != sorted(set(declared_profiles)):
        errors.append("demo.config.json: compose_profiles must be a unique sorted array")
    elif not expected_profiles.issubset(declared_profiles):
        missing_profiles = sorted(expected_profiles - set(declared_profiles))
        errors.append(f"demo.config.json: compose_profiles missing for selected modules: {', '.join(missing_profiles)}")
    expected_proof = {module: MODULE_TEST_IDS[module] for module in sorted(selected_known) if module in MODULE_TEST_IDS}
    declared_proof = config.get("proof")
    if not isinstance(declared_proof, dict):
        errors.append("demo.config.json: proof must be an object")
    else:
        wrong = {k: v for k, v in expected_proof.items() if declared_proof.get(k) != v}
        if wrong:
            errors.append(f"demo.config.json: proof map missing or wrong for: {', '.join(sorted(wrong))}")
    return errors


# Gateway switches a module cannot work without, read from config/gateway.env, which every
# gateway's env_file shares. With the switch off the gateway ignores the API's plugin or
# custom domain without an error, so a file-exists check alone would pass a dead module.
GATEWAY_ENV_SWITCHES = {
    "js-plugin": "TYK_GW_ENABLEJSVM",
    "custom-domain": "TYK_GW_ENABLECUSTOMDOMAINS",
}


# Compose services and gateway settings a module cannot work without. Without them the gateway
# resolves every $secret_vault reference to an empty value, or refuses the upstream's
# certificate, while the API itself still imports cleanly.
GATEWAY_COMPOSE_EXPECTATIONS = {
    "vault": ("vault", ("TYK_GW_KV_VAULT_ADDRESS", "TYK_GW_KV_VAULT_TOKEN", "TYK_GW_KV_VAULT_KVVERSION")),
    "upstream-mtls": ("secure-upstream", ("SSL_CERT_DIR",)),
}


def env_file_values(path: Path) -> dict[str, str]:
    """KEY=VALUE pairs of a dotenv-style file, quotes stripped; comments and blanks skipped."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = value.strip().strip("'\"")
    return values


def compose_service_block(text: str, service: str) -> str | None:
    """The text of one two-space-indented compose service, or None when it is absent."""
    match = re.search(rf"(?ms)^  {re.escape(service)}:[ \t]*\n(.*?)(?=^  [A-Za-z0-9_.-]+:[ \t]*\n|^\S|\Z)", text)
    return match.group(1) if match else None


def validate_module_configuration(root: Path, config: dict) -> list[str]:
    """Content checks for modules whose assets can exist while the module is switched off."""
    modules = set(config["modules"])
    errors: list[str] = []
    switches = {module: variable for module, variable in GATEWAY_ENV_SWITCHES.items() if module in modules}
    if switches:
        try:
            values = env_file_values(root / "config" / "gateway.env")
        except (OSError, UnicodeError):
            values = {}
        for module, variable in sorted(switches.items()):
            if values.get(variable, "").lower() != "true":
                errors.append(f"config/gateway.env: module {module} needs {variable}=true for every gateway")
    if "gateway-sharding" in modules:
        try:
            compose = (root / "compose.yaml").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            compose = ""
        block = compose_service_block(compose, "tyk-gateway-external")
        if block is None:
            errors.append("compose.yaml: module gateway-sharding needs a tyk-gateway-external service")
        else:
            if not re.search(r"""(?im)^\s*-?\s*["']?TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED["']?\s*[:=]\s*["']?true\b""", block):
                errors.append("compose.yaml: tyk-gateway-external must set TYK_GW_DBAPPCONFOPTIONS_NODEISSEGMENTED to true (gateway-sharding)")
            if not re.search(r"""(?m)^\s*-?\s*["']?TYK_GW_DBAPPCONFOPTIONS_TAGS["']?\s*[:=]\s*["']?[^\s"'#]""", block):
                errors.append("compose.yaml: tyk-gateway-external must set TYK_GW_DBAPPCONFOPTIONS_TAGS (gateway-sharding)")
        ports = config.get("ports")
        if not isinstance(ports, dict) or "gateway_external" not in ports:
            errors.append("demo.config.json: module gateway-sharding needs ports.gateway_external")
    if modules & set(GATEWAY_COMPOSE_EXPECTATIONS):
        try:
            compose = (root / "compose.yaml").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            compose = ""
        gateway = compose_service_block(compose, "tyk-gateway") or ""
        for module, (service, variables) in sorted(GATEWAY_COMPOSE_EXPECTATIONS.items()):
            if module not in modules:
                continue
            if compose_service_block(compose, service) is None:
                errors.append(f"compose.yaml: module {module} needs a {service} service")
            for variable in variables:
                if not re.search(rf"""(?m)^\s*-?\s*["']?{variable}["']?\s*[:=]\s*\S""", gateway):
                    errors.append(f"compose.yaml: tyk-gateway must set {variable} ({module})")
    return errors


VERSION_OF_EXTENSION = "x-demo-version-of"


def validate_api_versions(root: Path) -> list[str]:
    """Check every child-version marker in apis/ names a base the bundle imports first.

    scripts/import-apis.sh links a child to its base through the Dashboard's versioning call,
    which fails when the base does not exist, and it imports bases before children, so a
    child whose base is missing, or is itself a child, would never link.
    """
    apis: dict[str, dict] = {}
    for path in sorted((root / "apis").glob("*.oas.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue  # reported by the per-file JSON check
        if isinstance(document, dict):
            info = (document.get("x-tyk-api-gateway") or {}).get("info") or {}
            apis[str(info.get("id"))] = {"file": path.name, "version_of": document.get(VERSION_OF_EXTENSION)}
    errors: list[str] = []
    for api_id, entry in sorted(apis.items()):
        marker = entry["version_of"]
        if marker is None:
            continue
        where = f"apis/{entry['file']}: {VERSION_OF_EXTENSION}"
        if not isinstance(marker, dict) or not all(
            isinstance(marker.get(key), str) and marker[key] for key in ("base_api_id", "base_api_version_name", "new_version_name")
        ):
            errors.append(f"{where} needs non-empty base_api_id, base_api_version_name and new_version_name strings")
            continue
        if "set_default" in marker and not isinstance(marker["set_default"], bool):
            errors.append(f"{where}.set_default must be true or false")
        base = apis.get(marker["base_api_id"])
        if marker["base_api_id"] == api_id:
            errors.append(f"{where} names the API itself as its base")
        elif base is None:
            errors.append(f"{where} names base {marker['base_api_id']}, which no file in apis/ defines")
        elif base["version_of"] is not None:
            errors.append(f"{where} names base {marker['base_api_id']}, which is itself a child version")
    return errors


def validate_module_assets(root: Path, config: object) -> list[str]:
    if not isinstance(config, dict) or not isinstance(config.get("modules"), list):
        return []
    slug = config.get("slug", "")
    required = {
        "rest": root / "apis" / f"{slug}-core.oas.json",
        "api-key": root / "apis" / f"{slug}-api-key.oas.json",
        "jwt": root / "apis" / f"{slug}-jwt.oas.json",
        "portal": root / "portal" / "configure-portal.sh",
        "otel": root / "otel" / "collector.yaml",
        "go-plugin": root / "plugins" / "go" / "plugin.go",
        "grpc-plugin": root / "plugins" / "grpc" / "main.go",
        "operator": root / "operator" / "tyk-oas-api.yaml",
        "tyk-sync": root / "ci" / "tyk-sync.sh",
        "governance": root / "governance" / "configure-governance.sh",
        "ai-studio": root / "companion" / "ai-studio" / "compose.yaml",
        "gateway-sharding": root / "apis" / f"{slug}-external-services.oas.json",
        "js-plugin": root / "plugins" / "js" / f"{slug}-credential-mapper.js",
        # imported-keys.json holds live key values and is not shared; the example is.
        "key-import": root / "migration" / "imported-keys.example.json",
        "custom-domain": root / "apis" / f"{slug}-legacy-path.oas.json",
        "portal-catalogues": root / "portal" / "configure-catalogues.sh",
        "versioning": root / "apis" / f"{slug}-versioned.oas.json",
        "vault": root / "scripts" / "configure-vault.sh",
        "upstream-mtls": root / "apis" / "upstream-mtls.oas.template.json",
    }
    errors = [f"{path.relative_to(root)}: required asset for module {module} is missing" for module, path in required.items() if module in config["modules"] and not path.is_file()]
    # A second required asset for the modules that ship two halves.
    for module, path in (
        ("gateway-sharding", root / "apis" / f"{slug}-internal-services.oas.json"),
        ("key-import", root / "scripts" / "import-keys.sh"),
        ("versioning", root / "apis" / f"{slug}-versioned-v2.oas.json"),
        ("vault", root / "apis" / f"{slug}-vault-credentials.oas.json"),
        ("upstream-mtls", root / "upstreams" / "mtls_upstream.py"),
        ("upstream-mtls", root / "scripts" / "generate-upstream-certs.sh"),
        ("upstream-mtls", root / "scripts" / "configure-upstream-mtls.sh"),
    ):
        if module in config["modules"] and not path.is_file():
            errors.append(f"{path.relative_to(root)}: required asset for module {module} is missing")
    errors.extend(validate_module_configuration(root, config))
    errors.extend(validate_api_versions(root))
    if "portal-branding" in config["modules"]:
        errors.extend(brand_kit_errors(root / "portal" / "brand"))
    if "ai-studio" in config["modules"]:
        errors.extend(validate_ai_studio_secrets(root))
    return errors


# The Portal brand kit (portal/brand/). Its values reach the theme's CSS and templates, so
# colours and the font stack are held to shapes that cannot close a rule or open a tag, and
# assets are plain file names inside the kit. SVG is markup the browser runs, so a kit asset
# may not carry script, event handlers, javascript: URLs or foreignObject.
BRAND_ASSET_KEYS = ("logo", "logo_on_dark", "favicon", "hero")
BRAND_REQUIRED_KEYS = ("name", "logo", "favicon")
BRAND_COLOR_KEYS = (
    "primary", "primary_dark", "primary_darker", "primary_light", "on_primary", "secondary",
    "nav_bg", "nav_link", "link", "text", "hero_bg",
)
BRAND_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.(?:svg|png)")
BRAND_COLOR = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})")
BRAND_FONT = re.compile(r"[A-Za-z0-9 ,'\"-]{1,200}")
UNSAFE_SVG = re.compile(r"<script|\bon[a-z]+\s*=|javascript:|foreignobject", re.IGNORECASE)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def brand_asset_errors(path: Path, label: str) -> list[str]:
    """Check one brand asset: an SVG without active content, or a real PNG."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"{label}: unreadable: {exc}"]
    if path.suffix.lower() == ".png":
        return [] if data.startswith(PNG_SIGNATURE) else [f"{label}: not a PNG image"]
    text = data.decode("utf-8", errors="replace")
    if "<svg" not in text.lower():
        return [f"{label}: not an SVG image"]
    match = UNSAFE_SVG.search(text)
    return [f"{label}: SVG carries active content ({match.group(0)!r})"] if match else []


def brand_kit_errors(brand_dir: Path, label: str = "portal/brand") -> list[str]:
    """Validate a Portal brand kit: brand.json, the assets it names, and optional content.json."""
    manifest = brand_dir / "brand.json"
    if not manifest.is_file():
        return [f"{label}/brand.json: required brand kit manifest is missing"]
    try:
        brand = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"{label}/brand.json: invalid JSON: {exc}"]
    if not isinstance(brand, dict):
        return [f"{label}/brand.json: root must be an object"]
    errors: list[str] = []
    for key in BRAND_REQUIRED_KEYS:
        if not isinstance(brand.get(key), str) or not brand[key].strip():
            errors.append(f"{label}/brand.json: {key} is required")
    for key in BRAND_ASSET_KEYS:
        name = brand.get(key)
        if name in (None, ""):
            continue
        if not isinstance(name, str) or not BRAND_ASSET_NAME.fullmatch(name):
            errors.append(f"{label}/brand.json: {key} must name an .svg or .png file inside the kit")
            continue
        if not (brand_dir / name).is_file():
            errors.append(f"{label}/{name}: referenced by {key} but missing")
            continue
        errors.extend(brand_asset_errors(brand_dir / name, f"{label}/{name}"))
    colors = brand.get("colors", {})
    if not isinstance(colors, dict):
        errors.append(f"{label}/brand.json: colors must be an object")
        colors = {}
    if not colors.get("primary"):
        errors.append(f"{label}/brand.json: colors.primary is required")
    for key, value in colors.items():
        if key not in BRAND_COLOR_KEYS:
            errors.append(f"{label}/brand.json: colors.{key} is not a known brand colour")
        elif not isinstance(value, str) or not BRAND_COLOR.fullmatch(value):
            errors.append(f"{label}/brand.json: colors.{key} must be a #rgb or #rrggbb colour")
    font = brand.get("font_family")
    if font not in (None, "") and (not isinstance(font, str) or not BRAND_FONT.fullmatch(font)):
        errors.append(f"{label}/brand.json: font_family may hold only letters, digits, spaces, commas, quotes and hyphens")
    if "placeholder" in brand and not isinstance(brand["placeholder"], bool):
        errors.append(f"{label}/brand.json: placeholder must be true or false")
    content = brand_dir / "content.json"
    if content.is_file():
        try:
            pages = json.loads(content.read_text(encoding="utf-8")).get("pages")
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            return errors + [f"{label}/content.json: invalid JSON: {exc}"]
        if not isinstance(pages, dict):
            errors.append(f"{label}/content.json: pages must be an object keyed by Portal page path")
            pages = {}
        for path, page in pages.items():
            where = f"{label}/content.json: page {path}"
            if not path.startswith("/") or not isinstance(page, dict):
                errors.append(f"{where}: must be a path starting with / mapped to an object")
                continue
            if "title" in page and not isinstance(page["title"], str):
                errors.append(f"{where}: title must be a string")
            blocks = page.get("blocks", {})
            if not isinstance(blocks, dict) or any(not isinstance(value, str) for value in blocks.values()):
                errors.append(f"{where}: blocks must map content block names to strings")
    return errors


def validate_ai_studio_secrets(root: Path) -> list[str]:
    """Check the companion stack's private secret.env and admin.env are present, tight and matched.

    Failure modes this catches before anyone sees them: a file left
    unwritten, a file readable by group or other, the two halves of a shared
    control/edge secret drifting apart, and the admin password sitting in
    secret.env. The drift has no useful symptom at runtime - the edge simply never
    registers and the router endpoint 404s. secret.env is an env_file of both
    containers, so an admin password there is in their environments and in
    `docker inspect`; it belongs in the host-only admin.env.

    From AI Studio 2.2 METRICS_AUTH_TOKEN is required too: without it the edge
    does not register /metrics at all and AISTUDIO-METRICS-001 cannot pass. It is
    version-aware, read from demo.config.json, and required when the recorded tag
    is 2.2 or later or cannot be read.

    The licence itself is deliberately NOT checked here. It is a human's job to
    paste it, and start.sh is where that is enforced.
    """
    def private_values(name: str) -> tuple[dict[str, str] | None, list[str]]:
        path = root / "companion" / "ai-studio" / name
        if not path.is_file():
            return None, [f"companion/ai-studio/{name}: missing; the generator must write one with minted values"]
        found: list[str] = []
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            found.append(f"companion/ai-studio/{name}: permissions must not grant group or other access (expected 0600)")
        parsed: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            parsed[key.strip()] = value.strip()
        return parsed, found

    required = ["GRPC_AUTH_TOKEN", "EDGE_AUTH_TOKEN", "MICROGATEWAY_ENCRYPTION_KEY", "ENCRYPTION_KEY", "JWT_SECRET", "TYK_AI_SECRET_KEY"]
    try:
        tag = json.loads((root / "demo.config.json").read_text(encoding="utf-8"))["component_versions"]["ai_studio"]
    except (OSError, ValueError, KeyError, TypeError):
        tag = ""
    release = re.match(r"v?(\d+)\.(\d+)", str(tag))
    if not release or (int(release.group(1)), int(release.group(2))) >= (2, 2):
        required.append("METRICS_AUTH_TOKEN")
    admin, errors = private_values("admin.env")
    if admin is not None and not admin.get("AISTUDIO_ADMIN_PASSWORD"):
        errors.append("companion/ai-studio/admin.env: AISTUDIO_ADMIN_PASSWORD is empty; the generator mints it")
    values, secret_errors = private_values("secret.env")
    errors.extend(secret_errors)
    if values is None:
        return errors
    if values.get("AISTUDIO_ADMIN_PASSWORD"):
        errors.append("companion/ai-studio/secret.env: AISTUDIO_ADMIN_PASSWORD belongs in admin.env; secret.env is an env_file of both containers")
    for key in required:
        if not values.get(key):
            errors.append(f"companion/ai-studio/secret.env: {key} is empty; the generator mints it")
    for left, right in (("GRPC_AUTH_TOKEN", "EDGE_AUTH_TOKEN"), ("MICROGATEWAY_ENCRYPTION_KEY", "ENCRYPTION_KEY")):
        if values.get(left) and values.get(left) != values.get(right):
            errors.append(f"companion/ai-studio/secret.env: {left} and {right} must hold the same value")
    if values.get("ENCRYPTION_KEY") and len(values["ENCRYPTION_KEY"]) != 32:
        errors.append("companion/ai-studio/secret.env: ENCRYPTION_KEY must be exactly 32 characters")
    return errors


def validate_runtime_permissions(root: Path) -> list[str]:
    runtime = root / ".runtime"
    if not runtime.exists():
        return []
    errors: list[str] = []
    if stat.S_IMODE(runtime.stat().st_mode) & 0o077:
        errors.append(".runtime: permissions must not grant group or other access (expected 0700)")
    for path in runtime.rglob("*"):
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            errors.append(f"{path.relative_to(root)}: runtime secret permissions must be 0600 or stricter")
    return errors


def validate_sensitive_permissions(root: Path) -> list[str]:
    """Ensure generated secret-bearing files are private when they exist."""
    candidates = [root / ".env"]
    cert_root = root / "generated" / "certs"
    if cert_root.is_dir():
        candidates.extend(
            path for path in cert_root.rglob("*")
            if path.is_file() and path.suffix in {".key", ".pem"}
        )
    errors: list[str] = []
    for path in candidates:
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            errors.append(f"{path.relative_to(root)}: sensitive file permissions must be 0600 or stricter")
    return errors


def validate_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return [".gitignore: required file is missing"]
    entries = {
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = REQUIRED_IGNORES - entries
    if missing:
        return [f".gitignore: missing required entries: {', '.join(sorted(missing))}"]
    return []


def compose_command() -> list[str] | None:
    if shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, check=False,
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def validate_compose(root: Path) -> list[str]:
    command = compose_command()
    if command is None:
        return ["compose.yaml: Docker Compose is unavailable; use --skip-compose only for an explicit blocked check"]
    env_file = root / ".env" if (root / ".env").is_file() else root / ".env.example"
    args = command + ["--env-file", str(env_file), "-f", str(root / "compose.yaml"), "config", "--quiet"]
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        return [f"compose.yaml: docker compose config failed{suffix}"]
    return []


def validate_bundle(root: Path, *, check_compose: bool = True) -> list[str]:
    root = root.expanduser().resolve()
    errors: list[str] = []
    if not root.is_dir():
        return [f"{root}: bundle directory does not exist"]
    for required in (".tyk-standalone-demo", "demo.config.json", "compose.yaml"):
        if not (root / required).is_file():
            errors.append(f"{required}: required file is missing")

    config: object = None
    config_path = root / "demo.config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"demo.config.json: invalid JSON: {exc}")
        else:
            errors.extend(validate_config(config))
            errors.extend(validate_module_assets(root, config))

    for path in bundle_files(root):
        relative = path.relative_to(root)
        try:
            safe_relative_path(root, path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        try:
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix in {".yaml", ".yml"}:
                load_yaml(path)
            elif path.suffix == ".py":
                compile(path.read_bytes(), str(relative), "exec")
            elif path.suffix == ".sh":
                result = subprocess.run(
                    ["bash", "-n", str(path)], capture_output=True, text=True, check=False,
                )
                if result.returncode:
                    detail = result.stderr.strip().splitlines()
                    suffix = f": {detail[-1]}" if detail else ""
                    errors.append(f"{relative}: bash syntax check failed{suffix}")
        except (OSError, UnicodeError, ValueError, SyntaxError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: invalid {path.suffix.lstrip('.') or 'file'}: {exc}")
            continue

        if is_template(relative) or relative.parts[0] == ".runtime":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        match = PLACEHOLDER.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"{relative}:{line}: unresolved placeholder {match.group(0)}")

    errors.extend(validate_runtime_permissions(root))
    errors.extend(validate_sensitive_permissions(root))
    errors.extend(validate_gitignore(root))
    if check_compose and (root / "compose.yaml").is_file() and (root / ".env.example").is_file():
        errors.extend(validate_compose(root))
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--skip-compose", action="store_true", help="skip docker compose config validation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate_bundle(args.bundle, check_compose=not args.skip_compose)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Validated bundle: {args.bundle.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
