#!/usr/bin/env python3
"""Check the custom plugin settings of a Tyk OAS API definition before loading it.

Each finding is a definition that loads on Tyk Gateway 5.16.0 and then fails or behaves other
than its author meant:

    ERROR  requests to the API (or to one operation) fail, usually with 500
    WARN   it works, but slower, unsafely or not as the author probably meant
    NOTE   behaviour worth telling the user about

    check_plugins.py api.oas.json [--map /opt/tyk-gateway/plugins=./plugins ...]

With --map, JavaScript plugin files that the definition names by Gateway path are read from
your local copy and scanned for calls the chosen engine does not have. Standard library only.
Exit status: 0 with no errors, 1 with at least one error, 2 when a file cannot be read.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys

EXT = "x-tyk-api-gateway"
DRIVERS = ("otto", "javascript", "goplugin", "grpc", "python", "lua")
JS_DRIVERS = ("otto", "javascript")
STAGES = ("prePlugins", "postAuthenticationPlugins", "postPlugins", "responsePlugins")
SESSION_STAGES = ("postAuthenticationPlugins", "postPlugins", "responsePlugins")
VERSIONED_SO = re.compile(r"_v?\d+\.\d+\.\d+_[a-z0-9]+_[a-z0-9]+\.so$")

# What each engine lacks, as the Gateway log on 5.16.0 reports it. A syntax error stops the whole
# file from loading; a missing function fails only the requests whose code reaches the call.
JS_ONLY_ON_JAVASCRIPT = [
    ("WARN", re.compile(r"\bTykStorage(Get|Set|SetNX|Del|TTL|Incr)\s*\("),
     "the file calls TykStorage*, which exists only on the javascript driver (otto: ReferenceError, 500 when reached)"),
    ("ERROR", re.compile(r"(^|[;{}\s(])(const|let)\s+\w|=>|`"),
     "const, let, arrow functions or template strings: otto cannot load the file (Unexpected reserved word, 500)"),
]
JS_ONLY_ON_OTTO = [
    ("WARN", re.compile(r"\bconsole\s*\.\s*\w+\s*\("),
     "the file calls console, which does not exist on the javascript driver (ReferenceError, 500 when reached); use log()"),
    ("WARN", re.compile(r"(^|[^\w$.])_\s*\.\s*\w+\s*\("),
     "the file calls the underscore library (_), which does not exist on the javascript driver (ReferenceError, 500 when reached)"),
]


def enabled(block) -> bool:
    return isinstance(block, dict) and bool(block.get("enabled"))


def local_path(path: str, maps: list[tuple[str, str]]) -> str | None:
    for gateway_prefix, local_prefix in maps:
        if path.startswith(gateway_prefix.rstrip("/") + "/"):
            candidate = os.path.join(local_prefix, path[len(gateway_prefix.rstrip("/")) + 1:])
            if os.path.isfile(candidate):
                return candidate
    return None


def scan_js(source: str, driver: str) -> list[tuple[str, str]]:
    rules = JS_ONLY_ON_JAVASCRIPT if driver == "otto" else JS_ONLY_ON_OTTO
    return [(level, text) for level, pattern, text in rules if pattern.search(source)]


def check(document: dict, maps: list[tuple[str, str]] | None = None) -> list[tuple[str, str, str]]:
    maps = maps or []
    out: list[tuple[str, str, str]] = []

    def add(level, where, text):
        out.append((level, where, text))

    ext = document.get(EXT) or {}
    middleware = ext.get("middleware") or {}
    glob = middleware.get("global") or {}
    config = glob.get("pluginConfig") or {}
    driver = config.get("driver", "")
    bundle = config.get("bundle") or {}
    custom = ((ext.get("server") or {}).get("authentication") or {}).get("custom") or {}
    auth_enabled = enabled((ext.get("server") or {}).get("authentication"))
    base = f"{EXT}.middleware.global"

    entries = []
    for stage in STAGES:
        for index, entry in enumerate(glob.get(stage) or []):
            if isinstance(entry, dict) and entry.get("enabled"):
                entries.append((stage, f"{base}.{stage}[{index}]", entry))
    if enabled(custom):
        entries.append(("custom", f"{EXT}.server.authentication.custom.config", custom.get("config") or {}))

    uses_hooks = bool(entries) or enabled(bundle)
    if driver and driver not in DRIVERS:
        add("ERROR", f"{base}.pluginConfig.driver", f"unknown driver {driver!r}; use one of {', '.join(DRIVERS)}")
    if uses_hooks and not driver and not enabled(bundle):
        add("ERROR", f"{base}.pluginConfig.driver",
            "plugins are set but no driver: every request gets 500 and the log says \"Unsupported driver ''\"")
    if driver == "otto":
        add("WARN", f"{base}.pluginConfig.driver",
            "otto is the older JavaScript engine: no inline code, no TykStorage*, ES5 only, and slower; "
            "use \"javascript\" for new plugins")
    if driver == "lua":
        add("WARN", f"{base}.pluginConfig.driver", "lua is not covered by this skill and has no response hook")
    if driver == "python":
        add("NOTE", f"{base}.pluginConfig",
            "Python plugins need Python added to the Gateway image; the stock image answers 500 and logs "
            "\"Driver 'python' isn't loaded\"")
    if driver == "grpc":
        add("NOTE", f"{base}.pluginConfig.driver",
            "gRPC hooks need coprocess_options.enable_coprocess and coprocess_grpc_server in the Gateway "
            "configuration; while the server is down every request to this API gets 500 \"Middleware error\"")

    if enabled(custom):
        cfg = custom.get("config") or {}
        if not enabled(cfg) or not cfg.get("functionName"):
            add("ERROR", f"{EXT}.server.authentication.custom.config",
                "custom authentication is enabled without an enabled config with a functionName")
        add("NOTE", f"{EXT}.server.authentication.custom",
            "sessions a plugin creates share one rate limit and quota unless the session's meta_data sets "
            "rate_limit_pattern; its value is one counter across APIs, so include the API id "
            "(for example \"$tyk_meta.rate_key\" with rate_key = <api id>:<caller>)")
        if driver == "grpc":
            add("NOTE", f"{EXT}.server.authentication.custom",
                "a gRPC auth hook must set metadata[\"token\"] in its reply, or the callers it accepts get 403")
    if enabled(custom) and not auth_enabled:
        add("ERROR", f"{EXT}.server.authentication",
            "custom authentication is set but server.authentication.enabled is not true")

    for stage, where, entry in entries:
        name = entry.get("functionName", "")
        path = entry.get("path", "")
        code = entry.get("code", "")
        if not name:
            add("ERROR", where, "functionName is empty")
        if code and driver != "javascript":
            add("ERROR", where, "inline code runs only on the javascript driver; here every request gets 500")
        if driver in JS_DRIVERS and not path and not code and not enabled(bundle):
            add("ERROR", where, "a JavaScript hook needs a path or (javascript driver) inline code")
        if driver == "goplugin" and path and not enabled(bundle):
            if VERSIONED_SO.search(path):
                add("WARN", where,
                    "the path names a build for one Gateway version; name the plain file (plugin.so) so each "
                    "Gateway loads plugin_<its version>_<os>_<arch>.so and an upgrade needs no definition change")
        if driver in JS_DRIVERS and stage in SESSION_STAGES and not entry.get("requireSession"):
            add("WARN", where, "requireSession is not true: the plugin receives an empty session (no alias, no key data)")
        if driver in JS_DRIVERS:
            source = None
            if code:
                try:
                    source = base64.b64decode(code, validate=True).decode("utf-8", "replace")
                except ValueError:
                    add("ERROR", where, "code is not valid base64")
                    source = None
            elif path and maps:
                found = local_path(path, maps)
                if found:
                    with open(found, encoding="utf-8", errors="replace") as handle:
                        source = handle.read()
            if source is not None:
                for level, text in scan_js(source, driver):
                    add(level, where, text)
                if name and not re.search(r"\b" + re.escape(name) + r"\b", source):
                    add("ERROR", where, f"the file does not mention {name!r}; the hook would fail with ReferenceError")

    for op_id, op in (middleware.get("operations") or {}).items():
        if not isinstance(op, dict):
            continue
        ve = op.get("virtualEndpoint") or {}
        where = f"{EXT}.middleware.operations.{op_id}.virtualEndpoint"
        if enabled(ve):
            name = ve.get("functionName", "")
            if not name:
                add("ERROR", where, "functionName is empty")
            if not ve.get("path") and not ve.get("body"):
                add("ERROR", where, "a virtual endpoint needs a path or a base64 body")
            # Virtual endpoints run on the javascript engine only when the API's driver is
            # "javascript"; with any other driver, or none, they run on otto.
            engine = "javascript" if driver == "javascript" else "otto"
            if engine == "otto":
                shown = repr(driver) if driver else "no driver"
                add("NOTE", where, f"with {shown}, virtual endpoints run on otto (ES5, no TykStorage*); "
                    "only driver \"javascript\" runs them on the newer engine")
            source = None
            if ve.get("body"):
                try:
                    source = base64.b64decode(ve["body"], validate=True).decode("utf-8", "replace")
                except ValueError:
                    add("ERROR", where, "body is not valid base64")
            elif ve.get("path") and maps:
                found = local_path(ve["path"], maps)
                if found:
                    with open(found, encoding="utf-8", errors="replace") as handle:
                        source = handle.read()
            if source is not None:
                for level, text in scan_js(source, engine):
                    add(level, where, text)
                if name and not re.search(r"\b" + re.escape(name) + r"\b", source):
                    add("ERROR", where, f"the file does not mention {name!r}; the endpoint would fail with ReferenceError")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("definition")
    parser.add_argument("--map", action="append", default=[], metavar="GATEWAY_DIR=LOCAL_DIR",
                        help="read plugin files named under GATEWAY_DIR from LOCAL_DIR")
    args = parser.parse_args(argv)
    maps = []
    for item in args.map:
        if "=" not in item:
            parser.error(f"--map needs GATEWAY_DIR=LOCAL_DIR, got {item!r}")
        maps.append(tuple(item.split("=", 1)))
    try:
        with open(args.definition, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.definition}: {exc}", file=sys.stderr)
        return 2
    findings = check(document, maps)
    for level, where, text in findings:
        print(f"{level:5} {where}: {text}")
    counts = {lvl: sum(1 for f in findings if f[0] == lvl) for lvl in ("ERROR", "WARN", "NOTE")}
    print(f"{counts['ERROR']} error(s), {counts['WARN']} warning(s), {counts['NOTE']} note(s)")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
