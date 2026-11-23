#!/usr/bin/env python3
"""Download, build, upload and activate a branded Developer Portal theme through the admin API.

    python3 apply_theme.py status
    python3 apply_theme.py download --output default.zip
    python3 apply_theme.py apply --kit <kit-dir> --work <dir> [--no-activate] [--version 1.2.0]
                                 [--source <default theme zip or folder>]
    python3 apply_theme.py activate <theme>
    python3 apply_theme.py rollback                 activate the default theme again
    python3 apply_theme.py delete <theme>

Environment: PORTAL_URL, PORTAL_API_TOKEN. Standard library only.

``apply`` starts from the default theme of the running Portal (or from ``--source``: after an
in-place upgrade the Portal keeps serving the default theme of the version it was first
installed with, so pass the new version's default theme there), refuses to upload an
archive that fails the pre-flight (a refused upload leaves files behind that can stop the
Portal starting), and reports success only after reading the theme back as ``Current``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import urllib.parse

import build_theme
from portal_client import PortalAPI, PortalError


def themes(api: PortalAPI) -> list[dict]:
    return api.get("/themes") or []


def find(api: PortalAPI, name: str) -> dict | None:
    # GET /themes/<unknown> answers 200 with an empty object, so look the name up in the list.
    return next((theme for theme in themes(api) if theme.get("Name") == name), None)


def download(api: PortalAPI, name: str, output: Path) -> Path:
    if not find(api, name):
        raise SystemExit(f"The Portal has no theme named {name!r}.")
    _, data = api.request("GET", f"/themes/{urllib.parse.quote(name)}/download")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return output


def upload(api: PortalAPI, archive: Path) -> str:
    problems = build_theme.check_archive(archive)
    if problems:
        raise SystemExit("Not uploaded; the archive would be refused or would not load:\n  " + "\n  ".join(problems))
    status, body = api.upload("/themes/upload", archive.name, archive.read_bytes(), content_type="application/zip", ok=())
    if status != 200:
        raise SystemExit(
            f"Upload refused (HTTP {status}): {body.strip()}\n"
            "The Portal may already have stored part of this theme. Fix the archive and upload it again under the "
            "same theme name before the Portal is restarted; see references/pitfalls.md.")
    return body.strip()


def activate(api: PortalAPI, name: str) -> dict:
    try:
        api.put(f"/themes/{urllib.parse.quote(name)}/activate", None)
    except PortalError as error:
        raise SystemExit(f"Activation of {name!r} refused (HTTP {error.status}): {error.body.strip()}") from error
    for _ in range(10):
        theme = find(api, name)
        if theme and theme.get("Status") == "Current":
            return theme
        time.sleep(1)
    raise SystemExit(f"Theme {name!r} was not read back as Current after activation.")


def cmd_status(api: PortalAPI, _args) -> int:
    print(json.dumps(sorted(themes(api), key=lambda theme: theme.get("Name", "")), indent=2))
    return 0


def cmd_download(api: PortalAPI, args) -> int:
    path = download(api, args.theme, args.output)
    print(f"{args.theme} -> {path} ({path.stat().st_size} bytes)")
    return 0


def cmd_apply(api: PortalAPI, args) -> int:
    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    source = args.source or download(api, "default", work / "default.zip")
    kit_name = build_theme.brand_kit.load(args.kit)["theme_name"]
    archive = work / f"{kit_name}.zip"
    try:
        result = build_theme.build(source, args.kit, archive, args.version, args.allow_missing)
    except (build_theme.BuildError, build_theme.brand_kit.KitError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    (work / "build-report.json").write_text(json.dumps(result, indent=2) + "\n")
    upload(api, archive)
    theme = find(api, result["theme"])
    if not theme:
        raise SystemExit(f"Upload answered 200 but {result['theme']!r} is not in the theme list.")
    outcome = {"theme": result["theme"], "version": result["version"], "bytes": result["bytes"],
               "warnings": result["warnings"], "report": str(work / "build-report.json")}
    if args.no_activate:
        outcome["status"] = theme.get("Status")
        outcome["preview"] = f"{api.base}/?preview-theme={urllib.parse.quote(result['theme'])}"
    else:
        outcome["status"] = activate(api, result["theme"]).get("Status")
    print(json.dumps(outcome, indent=2))
    return 0


def cmd_activate(api: PortalAPI, args) -> int:
    print(json.dumps(activate(api, args.theme), indent=2))
    return 0


def cmd_rollback(api: PortalAPI, _args) -> int:
    print(json.dumps(activate(api, "default"), indent=2))
    return 0


def cmd_delete(api: PortalAPI, args) -> int:
    theme = find(api, args.theme)
    if not theme:
        raise SystemExit(f"The Portal has no theme named {args.theme!r}.")
    if theme.get("Status") == "Current":
        raise SystemExit(f"{args.theme!r} is the active theme; activate another theme first.")
    api.delete(f"/themes/{urllib.parse.quote(args.theme)}")
    print(f"{args.theme} deleted (the Portal keeps its files in storage and hides the theme).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status").set_defaults(run=cmd_status)
    get = commands.add_parser("download")
    get.add_argument("--theme", default="default")
    get.add_argument("--output", type=Path, required=True)
    get.set_defaults(run=cmd_download)
    apply = commands.add_parser("apply")
    apply.add_argument("--kit", type=Path, required=True)
    apply.add_argument("--work", type=Path, required=True, help="folder for the downloaded default theme, the built archive and the build report")
    apply.add_argument("--version")
    apply.add_argument("--source", type=Path, help="default theme to build from instead of the one the Portal serves")
    apply.add_argument("--no-activate", action="store_true", help="upload only and print a preview link")
    apply.add_argument("--allow-missing", action="store_true")
    apply.set_defaults(run=cmd_apply)
    act = commands.add_parser("activate")
    act.add_argument("theme")
    act.set_defaults(run=cmd_activate)
    commands.add_parser("rollback").set_defaults(run=cmd_rollback)
    remove = commands.add_parser("delete")
    remove.add_argument("theme")
    remove.set_defaults(run=cmd_delete)
    args = parser.parse_args(argv)
    api = PortalAPI()
    try:
        return args.run(api, args)
    except PortalError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
