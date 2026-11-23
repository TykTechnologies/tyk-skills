#!/usr/bin/env python3
"""Compare a Tyk Sync source directory with a fresh `tyk-sync dump`.

Tyk Sync has no diff command and its `sync` output is not a drift report: it
rewrites every policy and classic API on each run. This script compares the
two directories object by object and ignores only the fields that the
Dashboard owns (database IDs, organisation ID, timestamps, generated server
URLs).

    tyk-sync dump -d <dashboard-url> -t /tmp/readback
    python3 sync_drift.py <source-directory> /tmp/readback [--ignore-extra]

Exit codes: 0 the Dashboard matches the source, 1 drift found, 2 a directory
or index is unusable. Python standard library only.
"""

import argparse
import json
import pathlib
import sys

INDEX = ".tyk.json"
TYK_EXT = "x-tyk-api-gateway"

# Fields the Dashboard writes or rewrites, by object kind.
POLICY_IGNORED = {"_id", "org_id", "last_updated", "date_created"}
ASSET_IGNORED = {"_id", "org_id", "last_updated"}
IDP_IGNORED = {"_id", "org_id", "date_created", "last_updated"}
CLASSIC_IGNORED = {"id", "org_id", "updated_at", "created_at"}
WRAPPER_IGNORED = {"created_at", "updated_at"}
# Timestamps the Dashboard refreshes on every write, at any depth.
TIMESTAMP_KEYS = {"updated_at", "created_at", "last_updated", "date_created"}


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def drop(doc, keys):
    return {key: value for key, value in doc.items() if key not in keys}


def prune_empty(value):
    """Drop timestamps and values that equal an unset default.

    The Dashboard fills in defaults (false, 0, empty lists and objects) that a
    hand-written file leaves out. Removing them from both sides keeps a real
    change visible: true against false still shows as a key on one side only.
    """
    if isinstance(value, dict):
        cleaned = {
            key: prune_empty(item) for key, item in value.items() if key not in TIMESTAMP_KEYS
        }
        return {key: item for key, item in cleaned.items() if not is_unset(item)}
    if isinstance(value, list):
        return [prune_empty(item) for item in value]
    return value


def is_unset(value):
    return value is None or value is False or value == 0 or value in ({}, [], "")


def normalise_api(doc):
    """Return (identifier, comparable form) for an API file."""
    doc = drop(doc, WRAPPER_IGNORED)
    if isinstance(doc.get("oas"), dict):
        oas = dict(doc["oas"])
        oas.pop("servers", None)
        ext = dict(oas.get(TYK_EXT) or {})
        info = drop(ext.get("info") or {}, {"dbId", "orgId"})
        ext["info"] = info
        oas[TYK_EXT] = ext
        doc["oas"] = oas
        doc.pop("api_definition", None)
        return f"api:{info.get('id', '')}", prune_empty(doc)
    definition = drop(doc.get("api_definition") or {}, CLASSIC_IGNORED)
    doc["api_definition"] = definition
    return f"api:{definition.get('api_id', '')}", prune_empty(doc)


def normalise_policy(doc):
    identifier = doc.get("id") or doc.get("_id", "")
    return f"policy:{identifier}", prune_empty(drop(doc, POLICY_IGNORED))


def normalise_asset(doc):
    return f"asset:{doc.get('id', '')}", prune_empty(drop(doc, ASSET_IGNORED))


def normalise_idp(doc):
    identifier = doc.get("client_idp_id") or doc.get("_id", "")
    return f"idp:{identifier}", prune_empty(drop(doc, IDP_IGNORED))


NORMALISERS = {
    "files": normalise_api,
    "policies": normalise_policy,
    "assets": normalise_asset,
    "client_idps": normalise_idp,
}


def load_directory(directory):
    """Return {identifier: comparable document} for everything the index lists."""
    root = pathlib.Path(directory)
    index = load_json(root / INDEX)
    objects = {}
    for section, normalise in NORMALISERS.items():
        for item in index.get(section) or []:
            name = item.get("file") if isinstance(item, dict) else None
            if not name:
                continue
            identifier, comparable = normalise(load_json(root / name))
            objects[identifier] = comparable
    return objects


def first_differences(left, right, path="", limit=5):
    """Return up to `limit` JSON paths where two documents differ."""
    found = []

    def walk(a, b, where):
        if len(found) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    found.append(f"{where}.{key} (only in the Dashboard)")
                elif key not in b:
                    found.append(f"{where}.{key} (only in the source)")
                else:
                    walk(a[key], b[key], f"{where}.{key}")
                if len(found) >= limit:
                    return
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for position, (item_a, item_b) in enumerate(zip(a, b)):
                walk(item_a, item_b, f"{where}[{position}]")
        elif a != b:
            found.append(f"{where}: source {json.dumps(a)[:60]} != Dashboard {json.dumps(b)[:60]}")

    walk(left, right, path)
    return found


def compare(source, dashboard, ignore_extra=False):
    """Return a list of drift lines; empty means the two directories agree."""
    lines = []
    for identifier in sorted(set(source) - set(dashboard)):
        lines.append(f"MISSING  {identifier}: in the source, not in the Dashboard")
    if not ignore_extra:
        for identifier in sorted(set(dashboard) - set(source)):
            lines.append(f"EXTRA    {identifier}: in the Dashboard, not in the source")
    for identifier in sorted(set(source) & set(dashboard)):
        if source[identifier] != dashboard[identifier]:
            lines.append(f"CHANGED  {identifier}")
            for difference in first_differences(source[identifier], dashboard[identifier]):
                lines.append(f"           {difference}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", help="directory under version control")
    parser.add_argument("dashboard", help="directory written by tyk-sync dump")
    parser.add_argument(
        "--ignore-extra",
        action="store_true",
        help="do not report objects that exist only in the Dashboard "
        "(for an organisation that other tools also write to)",
    )
    args = parser.parse_args(argv)
    try:
        source = load_directory(args.source)
        dashboard = load_directory(args.dashboard)
    except (OSError, ValueError) as error:
        print(f"ERROR {error}")
        return 2
    lines = compare(source, dashboard, args.ignore_extra)
    for line in lines:
        print(line)
    if lines:
        print("DRIFT: the Dashboard does not match the source directory")
        return 1
    print(f"IN SYNC: {len(source)} object(s) match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
