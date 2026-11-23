#!/usr/bin/env python3
"""Snapshot a Tyk stack's configuration state through its APIs, and diff two snapshots.

Run it before and after an upgrade (or a restore) and compare:

    tyk_state_snapshot.py snapshot --out before.json
    ... upgrade ...
    tyk_state_snapshot.py snapshot --out after.json
    tyk_state_snapshot.py diff before.json after.json

Connection settings come from the environment, so no secret is typed on the command line:

    TYK_DASHBOARD_URL      for example http://localhost:3000            (or DASH)
    TYK_DASHBOARD_API_KEY  a Dashboard user's API key                   (or DASHBOARD_API_KEY)
    TYK_GATEWAY_URL        optional, Gateway version and loaded APIs    (or GW)
    TYK_GATEWAY_SECRET     optional, the Gateway secret                 (or GW_SECRET)
    TYK_PORTAL_URL         optional, for example http://localhost:3001  (or PORTAL_URL)
    TYK_PORTAL_TOKEN       optional, a Portal admin API token           (or PORTAL_API_TOKEN)

The TYK_* name wins when both are set.

Python standard library only. Exit codes: snapshot 0 when every requested section was read,
2 when a section could not be read (the snapshot is still written and the error recorded);
diff 0 when the snapshots match, 1 when they differ.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Fields that change on every save or restart and say nothing about whether the
# configuration survived. Matched by key name at any depth. Extend with --ignore.
DEFAULT_IGNORED_KEYS = (
    "created_at",
    "updated_at",
    "last_updated",
    "date_created",
    "CreatedAt",
    "UpdatedAt",
    "LastSynced",
)

# Lists whose order carries no meaning and is not stable between reads.
UNORDERED_LIST_KEYS = ("access_rights_array",)

PORTAL_COLLECTIONS = ("products", "plans", "catalogues", "providers", "organisations", "users", "apps")

Fetcher = Callable[[str, Dict[str, str]], Tuple[int, Any]]


class FetchError(Exception):
    pass


def http_get_json(url: str, headers: Dict[str, str], timeout: float = 20.0) -> Tuple[int, Any]:
    """GET a URL and return (status, parsed JSON). Raises FetchError on transport errors
    and on a body that is not JSON (the Dashboard answers unknown routes with its HTML login page)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, body = err.code, err.read()
    except (urllib.error.URLError, OSError) as err:
        raise FetchError(f"GET {redact_url(url)}: {err}") from err
    try:
        return status, json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise FetchError(f"GET {redact_url(url)}: HTTP {status}, body is not JSON") from err


def redact_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def normalise(value: Any, ignored: Iterable[str]) -> Any:
    """Drop ignored keys at any depth and sort dict keys, so equal configuration compares equal."""
    ignored = set(ignored)
    if isinstance(value, dict):
        out = {}
        for k, v in sorted(value.items()):
            if k in ignored:
                continue
            v = normalise(v, ignored)
            if k in UNORDERED_LIST_KEYS and isinstance(v, list):
                v = sorted(v, key=lambda item: json.dumps(item, sort_keys=True))
            out[k] = v
        return out
    if isinstance(value, list):
        return [normalise(v, ignored) for v in value]
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def ok_or_raise(status: int, body: Any, what: str) -> Any:
    if status != 200:
        message = body.get("message") or body.get("Message") if isinstance(body, dict) else None
        raise FetchError(f"{what}: HTTP {status}" + (f" ({message})" if message else ""))
    return body


def collect_dashboard(base: str, key: str, fetch: Fetcher, ignored: Iterable[str]) -> Dict[str, Any]:
    headers = {"Authorization": key}
    out: Dict[str, Any] = {}

    status, body = fetch(f"{base}/api/apis?p=-1", headers)
    listing = ok_or_raise(status, body, "list APIs") or {}
    apis: Dict[str, Any] = {}
    for item in listing.get("apis") or []:
        definition = item.get("api_definition") or {}
        api_id = definition.get("api_id")
        if not api_id:
            continue
        entry = {"name": definition.get("name"), "is_oas": bool(definition.get("is_oas"))}
        if entry["is_oas"]:
            st, oas = fetch(f"{base}/api/apis/oas/{urllib.parse.quote(api_id, safe='')}", headers)
            entry["definition"] = normalise(ok_or_raise(st, oas, f"read OAS API {api_id}"), ignored)
        else:
            entry["definition"] = normalise(definition, ignored)
        entry["digest"] = digest(entry["definition"])
        apis[api_id] = entry
    out["apis"] = apis

    status, body = fetch(f"{base}/api/portal/policies?p=-1", headers)
    data = ok_or_raise(status, body, "list policies") or {}
    policies: Dict[str, Any] = {}
    for pol in data.get("Data") or []:
        pid = pol.get("id") or pol.get("_id")
        norm = normalise(pol, ignored)
        policies[pid] = {"name": pol.get("name"), "definition": norm, "digest": digest(norm)}
    out["policies"] = policies
    return out


def collect_keys(base: str, key: str, fetch: Fetcher) -> Dict[str, Any]:
    status, body = fetch(f"{base}/api/keys/detailed/?p=-1", {"Authorization": key})
    data = ok_or_raise(status, body, "list keys (needs enable_hashed_keys_listing on the Gateway when keys are hashed)")
    hashes = sorted({k.get("key_hash") or k.get("key_id") or "" for k in (data or {}).get("keys") or []} - {""})
    return {"count": len(hashes), "ids": hashes}


def collect_gateway(base: str, secret: Optional[str], fetch: Fetcher) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    status, body = fetch(f"{base}/hello", {})
    hello = ok_or_raise(status, body, "Gateway /hello") or {}
    out["version"] = hello.get("version")
    out["status"] = hello.get("status")
    if secret:
        st, apis = fetch(f"{base}/tyk/apis", {"x-tyk-authorization": secret})
        loaded = ok_or_raise(st, apis, "Gateway /tyk/apis") or []
        ids = []
        for a in loaded:
            # Classic definitions carry api_id; Tyk OAS ones carry it in the x-tyk extension.
            ids.append(a.get("api_id") or ((a.get("x-tyk-api-gateway") or {}).get("info") or {}).get("id"))
        out["loaded_api_ids"] = sorted(i for i in ids if i)
    return out


def collect_portal(base: str, token: str, fetch: Fetcher, ignored: Iterable[str], per_page: int = 100) -> Dict[str, Any]:
    headers = {"Authorization": token}
    out: Dict[str, Any] = {}
    for coll in PORTAL_COLLECTIONS:
        items: List[Any] = []
        seen_first = None
        page = 1
        while True:
            status, body = fetch(f"{base}/portal-api/{coll}?per_page={per_page}&page={page}", headers)
            batch = ok_or_raise(status, body, f"Portal {coll}") or []
            if not isinstance(batch, list) or not batch:
                break
            first = json.dumps(batch[0], sort_keys=True)
            if first == seen_first:  # server ignored the page parameter
                break
            seen_first = first
            items.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        if coll in ("users", "apps"):
            # Counts only: these hold personal data and credentials.
            out[coll] = {"count": len(items), "ids": sorted(str(i.get("ID")) for i in items)}
        else:
            by_id = {}
            for i in items:
                norm = normalise(i, ignored)
                by_id[str(i.get("ID"))] = {"name": i.get("Name"), "definition": norm, "digest": digest(norm)}
            out[coll] = by_id
    return out


def setting(env: Dict[str, str], name: str, fallback: str) -> str:
    """The TYK_* variable, or the short name the other Tyk skills use."""
    return env.get(name) or env.get(fallback) or ""


def take_snapshot(env: Dict[str, str], fetch: Fetcher = http_get_json, ignored: Iterable[str] = DEFAULT_IGNORED_KEYS, label: str = "") -> Tuple[Dict[str, Any], List[str]]:
    snap: Dict[str, Any] = {
        "taken_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "label": label,
        "ignored_keys": sorted(set(ignored)),
        "errors": {},
    }
    errors: List[str] = []

    def section(name: str, fn: Callable[[], Any]) -> None:
        try:
            snap[name] = fn()
        except FetchError as err:
            snap["errors"][name] = str(err)
            errors.append(f"{name}: {err}")

    dash, dkey = setting(env, "TYK_DASHBOARD_URL", "DASH").rstrip("/"), setting(env, "TYK_DASHBOARD_API_KEY", "DASHBOARD_API_KEY")
    if dash and dkey:
        section("dashboard", lambda: collect_dashboard(dash, dkey, fetch, ignored))
        section("keys", lambda: collect_keys(dash, dkey, fetch))
    gw = setting(env, "TYK_GATEWAY_URL", "GW").rstrip("/")
    if gw:
        section("gateway", lambda: collect_gateway(gw, setting(env, "TYK_GATEWAY_SECRET", "GW_SECRET") or None, fetch))
    portal, ptoken = setting(env, "TYK_PORTAL_URL", "PORTAL_URL").rstrip("/"), setting(env, "TYK_PORTAL_TOKEN", "PORTAL_API_TOKEN")
    if portal and ptoken:
        section("portal", lambda: collect_portal(portal, ptoken, fetch, ignored))
    if not any(k in snap for k in ("dashboard", "gateway", "portal")) and not errors:
        errors.append("nothing to read: set TYK_DASHBOARD_URL and TYK_DASHBOARD_API_KEY (or DASH and DASHBOARD_API_KEY), and optionally the Gateway and Portal variables")
    return snap, errors


def _short(value: Any) -> str:
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= 40 else text[:37] + "..."


def changed_paths(a: Any, b: Any, prefix: str = "") -> List[str]:
    """Field paths that differ between two normalised documents.

    "+path" is a field only in the second document, "-path" a field only in the first, and
    "path: old -> new" a changed value."""
    if isinstance(a, dict) and isinstance(b, dict):
        paths: List[str] = []
        for k in sorted(set(a) | set(b)):
            p = f"{prefix}.{k}" if prefix else k
            if k not in a:
                paths.append(f"+{p}")
            elif k not in b:
                paths.append(f"-{p}")
            else:
                paths.extend(changed_paths(a[k], b[k], p))
        return paths
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        paths = []
        for i, (x, y) in enumerate(zip(a, b)):
            paths.extend(changed_paths(x, y, f"{prefix}[{i}]"))
        return paths
    if a == b:
        return []
    return [f"{prefix or '(value)'}: {_short(a)} -> {_short(b)}"]


def diff_collection(name: str, a: Dict[str, Any], b: Dict[str, Any], ignored: Iterable[str], allow_new_fields: bool = False) -> Tuple[List[str], List[str]]:
    """Return (differences, notes). With allow_new_fields, an object whose only change is
    fields that appear after the upgrade is reported as a note, not a difference."""
    lines: List[str] = []
    notes: List[str] = []
    for oid in sorted(set(a) - set(b)):
        lines.append(f"{name}: removed {oid} ({a[oid].get('name')})")
    for oid in sorted(set(b) - set(a)):
        lines.append(f"{name}: added {oid} ({b[oid].get('name')})")
    for oid in sorted(set(a) & set(b)):
        da = normalise(a[oid].get("definition"), ignored)
        db = normalise(b[oid].get("definition"), ignored)
        if da == db:
            continue
        paths = changed_paths(da, db)
        shown = "; ".join(paths[:12]) + (f"; ... {len(paths) - 12} more" if len(paths) > 12 else "")
        line = f"{name}: changed {oid} ({b[oid].get('name')}): {shown}"
        if allow_new_fields and all(p.startswith("+") for p in paths):
            notes.append(line)
        else:
            lines.append(line)
    return lines, notes


def diff_snapshots(a: Dict[str, Any], b: Dict[str, Any], extra_ignored: Iterable[str] = (), allow_new_fields: bool = False) -> Tuple[List[str], List[str]]:
    """Return (differences, notes) between two snapshots."""
    ignored = set(a.get("ignored_keys", [])) | set(b.get("ignored_keys", [])) | set(extra_ignored)
    lines: List[str] = []
    notes: List[str] = []

    def add(result: Tuple[List[str], List[str]]) -> None:
        lines.extend(result[0])
        notes.extend(result[1])

    for side, snap in (("before", a), ("after", b)):
        for sec, err in (snap.get("errors") or {}).items():
            lines.append(f"{sec}: not read in the {side} snapshot: {err}")
    da, db = a.get("dashboard") or {}, b.get("dashboard") or {}
    add(diff_collection("api", da.get("apis") or {}, db.get("apis") or {}, ignored, allow_new_fields))
    add(diff_collection("policy", da.get("policies") or {}, db.get("policies") or {}, ignored, allow_new_fields))
    ka, kb = a.get("keys"), b.get("keys")
    if ka is not None and kb is not None:
        if ka.get("count") != kb.get("count"):
            lines.append(f"keys: count {ka.get('count')} -> {kb.get('count')}")
        missing = sorted(set(ka.get("ids", [])) - set(kb.get("ids", [])))
        if missing:
            lines.append(f"keys: {len(missing)} key(s) from before are missing after")
    ga, gb = a.get("gateway"), b.get("gateway")
    if ga and gb and "loaded_api_ids" in ga and "loaded_api_ids" in gb:
        lost = sorted(set(ga["loaded_api_ids"]) - set(gb["loaded_api_ids"]))
        if lost:
            lines.append(f"gateway: no longer loads {', '.join(lost)}")
    if gb and "loaded_api_ids" in gb and db.get("apis"):
        for api_id in sorted(set(db["apis"]) - set(gb["loaded_api_ids"])):
            if is_active(db["apis"][api_id]):
                lines.append(f"gateway: Dashboard API {api_id} is not loaded by the Gateway")
    pa, pb = a.get("portal") or {}, b.get("portal") or {}
    for coll in PORTAL_COLLECTIONS:
        ca, cb = pa.get(coll), pb.get(coll)
        if ca is None or cb is None:
            continue
        if coll in ("users", "apps"):
            if ca.get("count") != cb.get("count"):
                lines.append(f"portal {coll}: count {ca.get('count')} -> {cb.get('count')}")
        else:
            add(diff_collection(f"portal {coll[:-1]}", ca, cb, ignored, allow_new_fields))
    return lines, notes


def is_active(api: Dict[str, Any]) -> bool:
    definition = api.get("definition") or {}
    if api.get("is_oas"):
        state = ((definition.get("x-tyk-api-gateway") or {}).get("info") or {}).get("state") or {}
        return state.get("active") is not False
    return definition.get("active") is not False


SAFE_API_ID = re.compile(r"^[A-Za-z0-9._~-]+$")


def _path_of(target: str) -> str:
    """The path part of a rewrite target, which may be a full URL or a path."""
    if "://" in target:
        return urllib.parse.urlsplit(target).path or "/"
    return target


def _rewrite_hits_listen_path(target: str, listen_path: str) -> bool:
    lp = listen_path.rstrip("/")
    path = _path_of(target)
    return bool(lp) and (path == lp or path.startswith(lp + "/"))


def preflight_5_16(snap: Dict[str, Any]) -> List[str]:
    """Findings for the breaking changes listed in the Gateway and Dashboard 5.16.0 release notes,
    read from a snapshot taken on the old version."""
    findings: List[str] = []
    for api_id, api in sorted(((snap.get("dashboard") or {}).get("apis") or {}).items()):
        d = api.get("definition") or {}
        name = api.get("name")
        if not SAFE_API_ID.match(api_id) or not re.search(r"[A-Za-z0-9]", api_id):
            findings.append(f"unsafe-api-id: {api_id} ({name}): after Dashboard 5.16.0 an update through the Dashboard API is refused with 400 unless allow_unsafe_api_ids is true")
        if api.get("is_oas"):
            x = d.get("x-tyk-api-gateway") or {}
            server = x.get("server") or {}
            lp = (server.get("listenPath") or {})
            ops = ((x.get("middleware") or {}).get("operations") or {})
            if lp.get("strip"):
                for op_id, op in sorted(ops.items()):
                    rw = (op or {}).get("urlRewrite") or {}
                    if not rw.get("enabled"):
                        continue
                    targets = [rw.get("rewriteTo", "")] + [t.get("rewriteTo", "") for t in rw.get("triggers") or []]
                    if any(t and _rewrite_hits_listen_path(t, lp.get("value", "")) for t in targets):
                        findings.append(f"rewrite-strip: {api_id} ({name}) operation {op_id}: rewrite target starts with the listen path {lp.get('value')}; from Gateway 5.16.0 the listen path is no longer stripped from it unless the Gateway sets apply_strip_listen_path_to_url_rewrite: true")
            for op_id, op in sorted(ops.items()):
                for kind in ("transformRequestBody", "transformResponseBody"):
                    t = (op or {}).get(kind) or {}
                    path = t.get("path") or ""
                    if t.get("enabled") and path and (path.startswith("/") or ".." in path):
                        findings.append(f"template-path: {api_id} ({name}) operation {op_id} {kind}: file template {path}: from Gateway 5.16.0 a file-mode template must resolve inside the Gateway template_path; check that it does (preflight cannot see template_path)")
            if (server.get("detailedActivityLogs") or {}).get("enabled"):
                findings.append(f"detailed-logs: {api_id} ({name}): from Gateway 5.16.0 detailed records store the Authorization and custom auth headers redacted")
        else:
            proxy = d.get("proxy") or {}
            versions = ((d.get("version_data") or {}).get("versions") or {})
            for vname, v in sorted(versions.items()):
                ext = (v or {}).get("extended_paths") or {}
                if proxy.get("strip_listen_path"):
                    for rw in ext.get("url_rewrites") or []:
                        targets = [rw.get("rewrite_to", "")] + [t.get("rewrite_to", "") for t in rw.get("triggers") or []]
                        if any(t and _rewrite_hits_listen_path(t, proxy.get("listen_path", "")) for t in targets):
                            findings.append(f"rewrite-strip: {api_id} ({name}) version {vname} path {rw.get('path')}: rewrite target starts with the listen path; from Gateway 5.16.0 the listen path is no longer stripped from it unless the Gateway sets apply_strip_listen_path_to_url_rewrite: true")
                for kind in ("transform", "transform_response"):
                    for t in ext.get(kind) or []:
                        td = t.get("template_data") or {}
                        src = td.get("template_source") or ""
                        if td.get("template_mode") == "file" and (src.startswith("/") or ".." in src):
                            findings.append(f"template-path: {api_id} ({name}) version {vname} path {t.get('path')}: file template {src}: from Gateway 5.16.0 a file-mode template must resolve inside the Gateway template_path; check that it does (preflight cannot see template_path)")
            if d.get("enable_detailed_recording"):
                findings.append(f"detailed-logs: {api_id} ({name}): from Gateway 5.16.0 detailed records store the Authorization and custom auth headers redacted")
    return findings


def summary(snap: Dict[str, Any]) -> str:
    d = snap.get("dashboard") or {}
    parts = [f"apis={len(d.get('apis') or {})}", f"policies={len(d.get('policies') or {})}"]
    if snap.get("keys") is not None:
        parts.append(f"keys={snap['keys'].get('count')}")
    g = snap.get("gateway")
    if g:
        parts.append(f"gateway={g.get('version')}")
        if "loaded_api_ids" in g:
            parts.append(f"gateway_loaded={len(g['loaded_api_ids'])}")
    p = snap.get("portal")
    if p:
        for coll in ("products", "plans", "catalogues"):
            parts.append(f"portal_{coll}={len(p.get(coll) or {})}")
        for coll in ("users", "apps"):
            if coll in p:
                parts.append(f"portal_{coll}={p[coll].get('count')}")
    if snap.get("errors"):
        parts.append(f"errors={len(snap['errors'])}")
    return " ".join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="read the stack's state into a JSON file")
    s.add_argument("--out", required=True)
    s.add_argument("--label", default="", help="free text stored in the snapshot, for example the versions")
    s.add_argument("--ignore", action="append", default=[], help="another key name to ignore at any depth")
    d = sub.add_parser("diff", help="compare two snapshots")
    d.add_argument("before")
    d.add_argument("after")
    d.add_argument("--ignore", action="append", default=[], help="another key name to ignore at any depth")
    d.add_argument("--allow-new-fields", action="store_true", help="report objects whose only change is new fields as notes, not differences")
    f = sub.add_parser("preflight", help="list objects affected by the 5.16.0 breaking changes, from a snapshot of the old version")
    f.add_argument("snapshot")
    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        snap, errors = take_snapshot(dict(os.environ), ignored=tuple(DEFAULT_IGNORED_KEYS) + tuple(args.ignore), label=args.label)
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"wrote {args.out}: {summary(snap)}")
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 2 if errors else 0

    if args.cmd == "preflight":
        with open(args.snapshot, encoding="utf-8") as fh:
            snap = json.load(fh)
        findings = preflight_5_16(snap)
        for line in findings:
            print(line)
        print(f"{len(findings)} finding(s)")
        print("not read: the Gateway configuration file; check analytics_config.enable_detailed_recording,"
              " template_path and apply_strip_listen_path_to_url_rewrite there")
        return 1 if findings else 0

    with open(args.before, encoding="utf-8") as fh:
        a = json.load(fh)
    with open(args.after, encoding="utf-8") as fh:
        b = json.load(fh)
    print(f"before ({a.get('taken_at')} {a.get('label', '')}): {summary(a)}")
    print(f"after  ({b.get('taken_at')} {b.get('label', '')}): {summary(b)}")
    lines, notes = diff_snapshots(a, b, args.ignore, args.allow_new_fields)
    for line in notes:
        print(f"note: {line}")
    for line in lines:
        print(line)
    print("no differences" if not lines else f"{len(lines)} difference(s)")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
