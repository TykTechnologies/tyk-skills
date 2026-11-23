#!/usr/bin/env python3
"""Check a Tyk Sync source directory before it is promoted.

Tyk Sync has no dry run and accepts files that later misbehave: an API without
an ID gets a new ID on every run, a Tyk OAS document without the {"oas": ...}
wrapper is created as an empty classic API, and a file that the index does not
list is ignored without a word. This script finds those cases offline.

    python3 check_sync_repo.py <directory> [--allow-unlisted]

Exit codes: 0 no errors, 1 errors found, 2 the directory or index is unusable.
Python standard library only.
"""

import argparse
import json
import pathlib
import sys

INDEX = ".tyk.json"
TYK_EXT = "x-tyk-api-gateway"


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def listed_files(index):
    """Return [(section, file name)] for every entry in the index."""
    entries = []
    for section in ("files", "policies", "assets", "client_idps"):
        for item in index.get(section) or []:
            if isinstance(item, dict) and item.get("file"):
                entries.append((section, item["file"]))
    return entries


def check_api(name, doc, errors, seen_ids, seen_routes):
    if not isinstance(doc, dict):
        errors.append(f"{name}: not a JSON object")
        return
    if "oas" in doc:
        oas = doc["oas"]
        ext = oas.get(TYK_EXT) if isinstance(oas, dict) else None
        if not isinstance(ext, dict):
            errors.append(f"{name}: the Tyk OAS document has no {TYK_EXT} section")
            return
        info = ext.get("info") or {}
        api_id = info.get("id")
        if not api_id:
            errors.append(
                f"{name}: {TYK_EXT}.info.id is missing; Tyk Sync would create the API "
                "under a new ID on every run"
            )
        server = ext.get("server") or {}
        listen = (server.get("listenPath") or {}).get("value")
        if not listen:
            errors.append(f"{name}: {TYK_EXT}.server.listenPath.value is missing")
        domain = (server.get("customDomain") or {}).get("name", "")
    elif "api_definition" in doc:
        definition = doc["api_definition"] or {}
        api_id = definition.get("api_id")
        if not api_id:
            errors.append(f"{name}: api_definition.api_id is missing")
        proxy = definition.get("proxy") or {}
        listen = proxy.get("listen_path")
        if not listen:
            errors.append(f"{name}: api_definition.proxy.listen_path is missing")
        domain = definition.get("domain", "")
    elif "openapi" in doc or TYK_EXT in doc:
        errors.append(
            f"{name}: a Tyk OAS document must be wrapped as {{\"oas\": {{...}}}}; "
            "unwrapped, Tyk Sync creates an empty classic API from it"
        )
        return
    else:
        errors.append(f"{name}: neither a wrapped Tyk OAS API nor a classic API definition")
        return

    if api_id:
        if api_id in seen_ids:
            errors.append(f"{name}: API ID {api_id!r} is also used by {seen_ids[api_id]}")
        seen_ids[api_id] = name
    if listen:
        route = (domain or "", listen)
        if route in seen_routes:
            errors.append(
                f"{name}: listen path {listen!r} is also used by {seen_routes[route]}"
            )
        seen_routes[route] = name


def check_policy(name, doc, errors, seen_ids):
    if not isinstance(doc, dict):
        errors.append(f"{name}: not a JSON object")
        return
    policy_id = doc.get("id") or doc.get("_id")
    if not policy_id:
        errors.append(f"{name}: the policy has neither id nor _id")
    elif policy_id in seen_ids:
        errors.append(f"{name}: policy ID {policy_id!r} is also used by {seen_ids[policy_id]}")
    else:
        seen_ids[policy_id] = name
    if not doc.get("org_id"):
        errors.append(f"{name}: org_id is missing; Tyk Sync stops with 'Policies must include an org ID'")


def check_asset(name, doc, errors, seen_ids):
    if not isinstance(doc, dict):
        errors.append(f"{name}: not a JSON object")
        return
    asset_id = doc.get("id")
    if not asset_id:
        errors.append(f"{name}: the asset has no id")
    elif asset_id in seen_ids:
        errors.append(f"{name}: asset ID {asset_id!r} is also used by {seen_ids[asset_id]}")
    else:
        seen_ids[asset_id] = name
    if doc.get("kind") not in ("oas-template", "fragment"):
        errors.append(f"{name}: kind must be oas-template or fragment, found {doc.get('kind')!r}")


def check_directory(directory, allow_unlisted=False):
    """Return (errors, warnings) for a Tyk Sync source directory."""
    root = pathlib.Path(directory)
    index_path = root / INDEX
    if not index_path.is_file():
        raise FileNotFoundError(f"{index_path} not found; Tyk Sync reads only what this index lists")
    index = load_json(index_path)
    if not isinstance(index, dict):
        raise ValueError(f"{index_path} is not a JSON object")

    errors, warnings = [], []
    if index.get("type") != "apidef":
        errors.append(f"{INDEX}: type must be \"apidef\", found {index.get('type')!r}")

    api_ids, routes, policy_ids, asset_ids = {}, {}, {}, {}
    listed = set()
    for section, name in listed_files(index):
        if name in listed:
            errors.append(f"{INDEX}: {name} is listed more than once")
            continue
        listed.add(name)
        path = root / name
        if not path.is_file():
            errors.append(f"{INDEX}: {section} lists {name}, which does not exist")
            continue
        try:
            doc = load_json(path)
        except (ValueError, OSError) as error:
            errors.append(f"{name}: cannot be read as JSON ({error})")
            continue
        if section == "files":
            check_api(name, doc, errors, api_ids, routes)
        elif section == "policies":
            check_policy(name, doc, errors, policy_ids)
        elif section == "assets":
            check_asset(name, doc, errors, asset_ids)

    for path in sorted(root.glob("*.json")):
        if path.name != INDEX and path.name not in listed:
            message = f"{path.name}: not listed in {INDEX}, so Tyk Sync ignores it"
            (warnings if allow_unlisted else errors).append(message)

    return errors, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory")
    parser.add_argument(
        "--allow-unlisted",
        action="store_true",
        help="report JSON files missing from the index as warnings, not errors",
    )
    args = parser.parse_args(argv)
    try:
        errors, warnings = check_directory(args.directory, args.allow_unlisted)
    except (FileNotFoundError, ValueError, OSError) as error:
        print(f"ERROR {error}")
        return 2
    for line in warnings:
        print(f"WARN  {line}")
    for line in errors:
        print(f"ERROR {line}")
    if errors:
        print(f"{len(errors)} error(s); do not promote this directory")
        return 1
    print("OK: every listed file exists, is identified and has a unique route")
    return 0


if __name__ == "__main__":
    sys.exit(main())
