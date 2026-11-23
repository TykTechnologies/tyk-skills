#!/usr/bin/env python3
"""Check a Tyk OAS API definition for mistakes the Gateway accepts or half-accepts.

Tyk Gateway 5.16.0 validates the `x-tyk-api-gateway` extension with a schema that allows
unknown fields, so a misspelt or invented field is stored nowhere and does nothing. Several
other mistakes load cleanly and only show up as wrong behaviour at request time. This script
reports them before the definition is sent.

    python3 check_tyk_oas.py orders.oas.json            # a full Tyk OAS definition
    python3 check_tyk_oas.py --plain openapi.json       # a plain OpenAPI document for import
    python3 check_tyk_oas.py --json orders.oas.json     # findings as JSON

Findings are ERROR (the definition is rejected, or a setting does nothing), WARN (it loads but
probably not as meant) and NOTE (worth knowing, never fails the check).
Exit status: 0 no errors, 1 at least one error (or a warning with --strict), 2 unreadable input.
Python standard library only. JSON input only: convert YAML to JSON first.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EXTENSION = "x-tyk-api-gateway"
FIELDS_FILE = Path(__file__).with_name("extension_fields.json")
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
MCP_FIELDS = ("mcpTools", "mcpResources", "mcpPrompts")
CLASSIC_MARKERS = ("api_id", "proxy", "version_data", "use_keyless", "auth_configs")
OPENAPI_3_0 = re.compile(r"^3\.0\.\d(-.+)?$")
OPENAPI_3_1 = re.compile(r"^3\.1(\.\d+)?(-.+)?$")
PATH_PARAM = re.compile(r"{([^}]+)}")
SCALARS = {
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
}


class Findings:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, level: str, where: str, message: str) -> None:
        self.items.append({"level": level, "where": where, "message": message})

    def error(self, where: str, message: str) -> None:
        self.add("ERROR", where, message)

    def warn(self, where: str, message: str) -> None:
        self.add("WARN", where, message)

    def note(self, where: str, message: str) -> None:
        self.add("NOTE", where, message)

    def count(self, level: str) -> int:
        return sum(1 for item in self.items if item["level"] == level)


def load_fields(path: Path = FIELDS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def walk_extension(value, node: dict, defs: dict, where: str, out: Findings) -> None:
    """Compare a value in the extension with the field map; report unknown and missing fields."""
    while "ref" in node:
        node = defs.get(node["ref"], {"any": 1})
    if "any" in node or value is None:
        return
    if "f" in node:
        if not isinstance(value, dict):
            out.error(where, "must be an object")
            return
        for name in node.get("r", []):
            if name not in value:
                out.error(where, f"`{name}` is required; the Gateway rejects the definition without it")
        for name, child in value.items():
            if name in MCP_FIELDS:
                out.note(f"{where}.{name}", "MCP proxy settings are not checked by this script")
                continue
            if name not in node["f"]:
                out.error(
                    f"{where}.{name}",
                    "not a field of the Tyk extension at this place; the Gateway accepts the "
                    "definition and silently drops it",
                )
                continue
            walk_extension(child, node["f"][name], defs, f"{where}.{name}", out)
        return
    if "arr" in node:
        if value is None:
            return
        if not isinstance(value, list):
            out.error(where, "must be an array")
            return
        for index, child in enumerate(value):
            walk_extension(child, node["arr"], defs, f"{where}[{index}]", out)
        return
    if "map" in node:
        if not isinstance(value, dict):
            out.error(where, "must be an object")
            return
        for name, child in value.items():
            walk_extension(child, node["map"], defs, f"{where}.{name}", out)
        return
    kind = node.get("t")
    if kind in SCALARS and value is not None and not SCALARS[kind](value):
        out.error(where, f"must be a {kind}; the Gateway answers `Invalid type`")
    elif "enum" in node and value is not None and value not in node["enum"]:
        allowed = ", ".join(repr(item) for item in node["enum"])
        out.error(where, f"must be one of {allowed}")


def operations(document: dict) -> dict[str, tuple[str, str, dict]]:
    """Map operationId -> (path, method, operation) for every operation that has one."""
    found: dict[str, tuple[str, str, dict]] = {}
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict) and operation.get("operationId"):
                found[operation["operationId"]] = (path, method, operation)
    return found


def resolve_parameter(parameter, document: dict):
    if isinstance(parameter, dict) and isinstance(parameter.get("$ref"), str):
        name = parameter["$ref"].rsplit("/", 1)[-1]
        return ((document.get("components") or {}).get("parameters") or {}).get(name) or {}
    return parameter if isinstance(parameter, dict) else {}


def refs(value, where: str = "$"):
    """Yield (where, target) for every $ref in the document."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                yield where, child
            else:
                yield from refs(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from refs(child, f"{where}[{index}]")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def enabled(block) -> bool:
    return isinstance(block, dict) and block.get("enabled") is True


def check_openapi(document: dict, out: Findings) -> None:
    """Checks on the OpenAPI part; they apply with or without the Tyk extension."""
    if "swagger" in document:
        out.error("swagger", "Swagger / OpenAPI 2.0 is rejected; convert the document to OpenAPI 3.0 or 3.1")
    version = document.get("openapi")
    if not isinstance(version, str) or not (OPENAPI_3_0.match(version) or OPENAPI_3_1.match(version)):
        out.error("openapi", f"`openapi` is {version!r}; the Gateway loads 3.0.x and 3.1.x only")
    info = document.get("info")
    if not isinstance(info, dict):
        out.error("info", "`info` must be an object")
    elif not info.get("version"):
        out.error("info.version", "`info.version` must be a non-empty string")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        out.error("paths", "`paths` must be an object (use {} for an API with no declared operations)")
        paths = {}

    seen_ids: dict[str, str] = {}
    for path, item in paths.items():
        if not path.startswith("/"):
            out.error(f"paths.{path}", "a path must start with `/`")
        if not isinstance(item, dict):
            continue
        template = [name.split(":", 1)[0] for name in PATH_PARAM.findall(path)]
        if any(":" in name for name in PATH_PARAM.findall(path)):
            out.warn(
                f"paths.{path}",
                "a regular expression inside `{name:regex}` is ignored when the Gateway matches "
                "operations: the segment matches any value. Constrain it with the parameter schema "
                "and `validateRequest`",
            )
        shared = [resolve_parameter(p, document) for p in item.get("parameters") or []]
        for method in HTTP_METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            where = f"paths.{path}.{method}"
            if not isinstance(operation.get("responses"), dict):
                out.error(where, "an operation needs a `responses` object")
            operation_id = operation.get("operationId")
            if not operation_id:
                out.warn(
                    where,
                    "no `operationId`: middleware cannot be bound to this operation in a hand-written "
                    "definition (import generates one from the path and method)",
                )
            elif operation_id in seen_ids:
                out.error(where, f"`operationId` {operation_id!r} is also used by {seen_ids[operation_id]}; ids must be unique")
            else:
                seen_ids[operation_id] = where
            declared = {
                p.get("name")
                for p in shared + [resolve_parameter(p, document) for p in operation.get("parameters") or []]
                if p.get("in") == "path"
            }
            missing = [name for name in template if name not in declared]
            if missing:
                out.error(where, f"path parameter(s) {missing} are in the path but not declared in `parameters`")

    components = document.get("components") or {}
    if isinstance(components, dict):
        if "pathItems" in components:
            out.error("components.pathItems", "`components.pathItems` is rejected (`extra sibling fields: [pathItems]`)")
        for name, scheme in (components.get("securitySchemes") or {}).items():
            if isinstance(scheme, dict) and scheme.get("type") == "mutualTLS":
                out.error(
                    f"components.securitySchemes.{name}",
                    "a `mutualTLS` security scheme is rejected; client certificates are configured in the "
                    "Tyk extension, not as an OpenAPI security scheme",
                )
    for where, target in refs(document):
        if not target.startswith("#"):
            out.error(where, f"`$ref` to {target!r}: only references inside the document (`#/...`) are resolved")
        elif where.startswith("$.webhooks"):
            out.error(where, "a `$ref` inside `webhooks` is not resolved and the definition is rejected; inline it")


def check_extension(document: dict, fields: dict, out: Findings) -> None:
    """Checks that need the Tyk extension."""
    ext = document[EXTENSION]
    if not isinstance(ext, dict):
        out.error(EXTENSION, "must be an object")
        return
    walk_extension(ext, fields["root"], fields["defs"], EXTENSION, out)

    info = ext.get("info") if isinstance(ext.get("info"), dict) else {}
    server = ext.get("server") if isinstance(ext.get("server"), dict) else {}
    upstream = ext.get("upstream") if isinstance(ext.get("upstream"), dict) else {}
    middleware = ext.get("middleware") if isinstance(ext.get("middleware"), dict) else {}
    global_mw = middleware.get("global") if isinstance(middleware.get("global"), dict) else {}
    op_mw = middleware.get("operations") if isinstance(middleware.get("operations"), dict) else {}

    api_id = info.get("id")
    if isinstance(api_id, str) and api_id and not re.fullmatch(r"[A-Za-z0-9._~-]+", api_id):
        out.error(f"{EXTENSION}.info.id", "allowed characters are a-z, A-Z, 0-9, `.`, `_`, `-`, `~`")
    state = info.get("state") if isinstance(info.get("state"), dict) else {}
    if state.get("active") is False:
        out.warn(
            f"{EXTENSION}.info.state.active",
            "`false`: a Dashboard-managed Gateway does not load the API (404), but a Gateway that reads "
            "files still serves it",
        )

    listen = (server.get("listenPath") or {}).get("value") if isinstance(server.get("listenPath"), dict) else None
    if isinstance(listen, str):
        where = f"{EXTENSION}.server.listenPath.value"
        if not listen.startswith("/"):
            out.error(where, "must start with `/` (the Dashboard answers `Listen path must start with a leading slash /`)")
        if " " in listen:
            out.error(where, "contains a space; the Gateway skips the API at load (`Listen path contains spaces, is invalid`)")

    url = upstream.get("url")
    balancing = upstream.get("loadBalancing")
    has_targets = enabled(balancing) and bool(balancing.get("targets"))
    streaming = isinstance(document.get("x-tyk-streaming"), dict)
    if streaming and not has_targets and not url:
        out.note(f"{EXTENSION}.upstream.url", "empty on a Tyk Streams API, which does not use the upstream")
    elif not has_targets:
        if not isinstance(url, str) or ("://" not in url and "$" not in url):
            out.error(
                f"{EXTENSION}.upstream.url",
                f"{url!r} is not an absolute URL; it is accepted, then every request fails with 500 "
                "`There was a problem proxying the request`",
            )
    if enabled(balancing) and not balancing.get("targets"):
        out.error(f"{EXTENSION}.upstream.loadBalancing.targets", "load balancing is enabled with no targets")

    known = operations(document)
    for operation_id in op_mw:
        if operation_id not in known:
            out.error(
                f"{EXTENSION}.middleware.operations.{operation_id}",
                "no operation in `paths` has this `operationId`; the block is kept but never runs",
            )
    allowing = [name for name, block in op_mw.items() if enabled((block or {}).get("allow"))]
    if allowing:
        without = sorted(name for name in known if name not in allowing)
        if without:
            out.warn(
                f"{EXTENSION}.middleware.operations",
                "an allow list is on for some operations only; every other path and method returns 403 "
                f"`Requested endpoint is forbidden`, including these declared operations: {', '.join(without)}",
            )
    if not enabled(global_mw.get("cache")):
        for name, block in op_mw.items():
            if enabled((block or {}).get("cache")):
                out.error(
                    f"{EXTENSION}.middleware.operations.{name}.cache",
                    "an operation cache does nothing unless `middleware.global.cache.enabled` is true",
                )
    if any("$tyk_context." in text for text in strings(ext)) and not enabled(global_mw.get("contextVariables")):
        out.warn(
            f"{EXTENSION}.middleware.global.contextVariables",
            "`$tyk_context.` is used but context variables are not enabled; the values resolve to empty strings",
        )
    upstream_path = ""
    if isinstance(url, str) and "://" in url:
        upstream_path = "/" + url.split("://", 1)[1].partition("/")[2]
    if upstream_path.strip("/"):
        for name, block in op_mw.items():
            if enabled((block or {}).get("transformResponseBody")):
                out.warn(
                    f"{EXTENSION}.middleware.operations.{name}.transformResponseBody",
                    "the upstream URL carries a path, and with one the response body transform is not applied; "
                    "keep the upstream URL to scheme, host and port",
                )
    if isinstance(listen, str) and listen.strip("/"):
        prefix = "/" + listen.strip("/")
        for name, block in op_mw.items():
            rewrite = (block or {}).get("urlRewrite")
            pattern = rewrite.get("pattern") if isinstance(rewrite, dict) else None
            if isinstance(pattern, str) and pattern.startswith("^/") and not pattern.startswith("^" + prefix):
                out.warn(
                    f"{EXTENSION}.middleware.operations.{name}.urlRewrite.pattern",
                    f"the pattern is matched against the whole request path, listen path included; anchored at `^/` "
                    f"it must start with `^{prefix}/`",
                )
        for path in (document.get("paths") or {}):
            if path == prefix or path.startswith(prefix + "/"):
                out.warn(
                    f"paths.{path}",
                    "the path repeats the listen path; operation paths are relative to the listen path",
                )

    check_authentication(document, server, out)
    check_upstream_security(upstream, out)


def check_authentication(document: dict, server: dict, out: Findings) -> None:
    auth = server.get("authentication")
    where = f"{EXTENSION}.server.authentication"
    declared = ((document.get("components") or {}).get("securitySchemes") or {}) if isinstance(document.get("components"), dict) else {}
    if not enabled(auth):
        if document.get("security"):
            out.warn(
                where,
                "the OpenAPI document declares `security`, but authentication is not enabled in the Tyk "
                "extension, so the API is open to everyone",
            )
        return
    schemes = auth.get("securitySchemes") if isinstance(auth.get("securitySchemes"), dict) else {}
    legacy = any(auth.get(name) for name in ("hmac", "oidc", "custom"))
    if not schemes and not legacy:
        out.error(where, "authentication is enabled with no `securitySchemes`; every request is refused with 401")
    for name in schemes:
        if name not in declared:
            out.warn(f"{where}.securitySchemes.{name}", "no security scheme with this name in `components.securitySchemes`")
    if auth.get("securityProcessingMode") == "compliant" and auth.get("baseIdentityProvider"):
        out.warn(
            f"{where}.baseIdentityProvider",
            "set together with `securityProcessingMode: compliant`; requests authenticated by any other "
            "method then fail with `Session state is missing or unset`",
        )


def check_upstream_security(upstream: dict, out: Findings) -> None:
    where = f"{EXTENSION}.upstream"
    pinning = upstream.get("certificatePinning")
    if enabled(pinning):
        for index, entry in enumerate(pinning.get("domainToPublicKeysMapping") or []):
            domain = entry.get("domain") if isinstance(entry, dict) else None
            if isinstance(domain, str) and re.search(r":\d+$", domain):
                out.error(
                    f"{where}.certificatePinning.domainToPublicKeysMapping[{index}].domain",
                    "a domain with a port never matches, so the pin is not applied; use the bare host name",
                )
    auth = upstream.get("authentication")
    if enabled(auth):
        if enabled(auth.get("basicAuth")) and enabled(auth.get("oauth")):
            out.error(f"{where}.authentication", "basic auth and OAuth are both enabled (`multiple upstream authentication modes not allowed`)")
        out.note(
            f"{where}.authentication",
            "upstream authentication runs only on the Enterprise Edition Gateway; the open source Gateway "
            "accepts the block and sends no credential",
        )


def check(document, plain: bool = False, fields: dict | None = None) -> Findings:
    out = Findings()
    if not isinstance(document, dict):
        out.error("$", "the definition must be a JSON object")
        return out
    if "openapi" not in document and "swagger" not in document and any(key in document for key in CLASSIC_MARKERS):
        out.error("$", "this is a Tyk classic API definition, not Tyk OAS; classic definitions use other endpoints")
        return out
    check_openapi(document, out)
    if plain:
        if EXTENSION in document:
            out.error(EXTENSION, "a document for import must not contain the Tyk extension (`the import payload should not contain x-tyk-api-gateway`)")
        return out
    if EXTENSION not in document:
        out.error(EXTENSION, "missing (`the payload should contain x-tyk-api-gateway`); to load a plain OpenAPI document use the import endpoint and --plain")
        return out
    check_extension(document, fields or load_fields(), out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a Tyk OAS API definition before loading it.")
    parser.add_argument("file", help="the definition, as JSON")
    parser.add_argument("--plain", action="store_true", help="the file is a plain OpenAPI document meant for import")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)
    try:
        document = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"cannot read {args.file}: {error}", file=sys.stderr)
        return 2
    findings = check(document, plain=args.plain)
    errors, warnings = findings.count("ERROR"), findings.count("WARN")
    if args.json:
        print(json.dumps({"errors": errors, "warnings": warnings, "findings": findings.items}, indent=2))
    else:
        for item in findings.items:
            print(f"{item['level']:5} {item['where']}: {item['message']}")
        print(f"{errors} error(s), {warnings} warning(s), {findings.count('NOTE')} note(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
