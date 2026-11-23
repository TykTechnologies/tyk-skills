#!/usr/bin/env python3
"""Rebuild extension_fields.json from the Gateway's strict schema for the Tyk extension.

The schema is public: `apidef/oas/schema/x-tyk-api-gateway.strict.json` in the Tyk Gateway
repository, at the release tag you run. check_tyk_oas.py reads the result to tell known fields
from invented ones.

    python3 build_extension_fields.py x-tyk-api-gateway.strict.json v5.16.0

A node in the output is one of:
    {"f": {field: node}, "r": [required]}   an object with known fields
    {"ref": name}                           another definition
    {"arr": node}                           an array
    {"map": node}                           an object with free keys
    {"any": 1}                              free-form
    {"t": type, "enum": [...]}              a scalar
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE = "apidef/oas/schema/x-tyk-api-gateway.strict.json"


def node(schema) -> dict:
    if not isinstance(schema, dict):
        return {"any": 1}
    if "$ref" in schema:
        return {"ref": schema["$ref"].rsplit("/", 1)[-1]}
    if "properties" in schema:
        result = {"f": {name: node(child) for name, child in schema["properties"].items()}}
        if schema.get("required"):
            result["r"] = list(schema["required"])
        return result
    if "anyOf" in schema or "oneOf" in schema:
        return {"any": 1}
    if "enum" in schema:
        return {"t": "string", "enum": list(schema["enum"])}
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), None)
    if kind == "array" or (kind is None and "items" in schema):
        items = schema.get("items")
        if isinstance(items, list):
            items = items[0] if items else None
        return {"arr": node(items) if items else {"any": 1}}
    if kind == "object" or "patternProperties" in schema:
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return {"map": node(additional)}
        pattern = schema.get("patternProperties")
        if isinstance(pattern, dict) and pattern:
            return {"map": node(next(iter(pattern.values())))}
        return {"any": 1}
    if kind in ("boolean", "integer", "number", "string"):
        return {"t": kind}
    return {"any": 1}


def build(schema: dict, version: str) -> dict:
    return {
        "version": version,
        "source": SOURCE,
        "root": {
            "f": {name: node(child) for name, child in schema["properties"].items()},
            "r": list(schema.get("required", [])),
        },
        "defs": {name: node(child) for name, child in schema["definitions"].items()},
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    schema = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    target = Path(__file__).with_name("extension_fields.json")
    target.write_text(json.dumps(build(schema, args[1]), separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {target.name} for Gateway {args[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
