#!/usr/bin/env python3
"""Check the client authentication settings of a Tyk OAS API definition before loading it.

Each finding is a definition that loads on Tyk Gateway 5.16.0 and then does something other
than its author meant:

    ERROR  every request, or every request of one kind, is refused
    WARN   the setting is accepted and has no effect, or lets through more than intended
    NOTE   behaviour worth telling the user about

General wiring (scheme names that do not match, OpenAPI `security` without Tyk
authentication, a `mutualTLS` scheme) is checked by the `tyk-api-definitions-routing` skill's
`check_tyk_oas.py`; run both.

    check_auth.py api.oas.json

Standard library only. Exit status: 0 with no errors, 1 with at least one error, 2 when the
file cannot be read.
"""
from __future__ import annotations

import json
import sys

EXT = "x-tyk-api-gateway"
AUTH_SOURCES = ("header", "query", "cookie")


def enabled(block) -> bool:
    return isinstance(block, dict) and bool(block.get("enabled"))


def has_source(scheme: dict) -> bool:
    return any(enabled(scheme.get(src)) for src in AUTH_SOURCES)


def oas_type(document: dict, name: str) -> tuple[str, str]:
    schemes = ((document.get("components") or {}).get("securitySchemes") or {})
    scheme = schemes.get(name) or {}
    return scheme.get("type", ""), scheme.get("scheme", "")


def operations(document: dict):
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if isinstance(op, dict) and method.lower() in (
                    "get", "put", "post", "delete", "patch", "head", "options", "trace"):
                yield path, method, op


def check(document: dict) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    def add(level, where, text):
        out.append((level, where, text))

    ext = document.get(EXT) or {}
    server = ext.get("server") or {}
    auth = server.get("authentication") or {}
    base = f"{EXT}.server.authentication"
    if not enabled(auth):
        if enabled(server.get("clientCertificates")):
            add("NOTE", f"{EXT}.server.clientCertificates",
                "client certificates without authentication: callers are checked at TLS level only and "
                "get no session, so no per-caller policy, rate limit or quota")
        return out

    schemes = auth.get("securitySchemes") or {}
    strip = bool(auth.get("stripAuthorizationData"))
    mode = auth.get("securityProcessingMode") or "legacy"

    for name, cfg in schemes.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        where = f"{base}.securitySchemes.{name}"
        typ, http_scheme = oas_type(document, name)

        if typ == "http" and http_scheme == "basic" and not has_source(cfg):
            add("ERROR", where, "Basic scheme with no `header` (or `query`/`cookie`) block: the Gateway "
                "accepts the definition and then answers every request 401 `Authorization field missing`. "
                'Add "header": {"enabled": true, "name": "Authorization"}')

        is_jwt = typ == "http" and http_scheme == "bearer" and (
            cfg.get("signingMethod") or cfg.get("source") or cfg.get("jwksURIs"))
        if is_jwt:
            if not has_source(cfg):
                add("ERROR", where, "JWT scheme with no `header` (or `query`/`cookie`) block: the Gateway refuses "
                    "the definition (`all auth sources are disabled for jwt`). Add "
                    '"header": {"enabled": true, "name": "Authorization"}')
            if cfg.get("basePolicyClaims") and not cfg.get("policyFieldName"):
                add("WARN", where, "`basePolicyClaims` without `policyFieldName`: written through the Gateway "
                    "API, the policy claim was ignored and every token got the default policy. Set "
                    f"\"policyFieldName\": \"{cfg['basePolicyClaims'][0]}\" as well")
            if not cfg.get("allowedIssuers"):
                add("WARN", where, "no `allowedIssuers`: any issuer whose token the key verifies is accepted")
            if not cfg.get("allowedAudiences"):
                add("WARN", where, "no `allowedAudiences`: a token the identity provider issued for another "
                    "API is accepted here")
            if cfg.get("allowedSubjects") and not cfg.get("skipKid") and cfg.get("jwksURIs"):
                add("WARN", where, "`allowedSubjects` without `skipKid: true`: the value compared is the "
                    "token's `kid` header when it has one, so real subjects are refused with 401 "
                    "`invalid subject value: <kid>`")
            for i, jwk in enumerate(cfg.get("jwksURIs") or []):
                if isinstance(jwk, dict) and not jwk.get("cacheTimeout"):
                    add("NOTE", f"{where}.jwksURIs[{i}]", "no `cacheTimeout`: the key set is cached for the "
                        "Gateway default (`jwks.cache.timeout`, 240 seconds), so a new signing key is refused "
                        "until then; publish new keys at least that long before using them")

        if cfg.get("providers"):
            add("NOTE", where, "`providers` is the External OAuth block, deprecated since 5.7.0. Sessions it "
                "creates carry no policy, and with `cache` on a revoked token keeps working until the cache "
                "entry expires")

        scope_check = cfg.get("scopeCheck")
        if typ == "oauth2" and enabled(scope_check):
            if strip:
                add("ERROR", where, "`scopeCheck` with `stripAuthorizationData: true`: the token is removed "
                    "before the scope check runs, so every request gets 401 `missing bearer token`")
            source = scope_check.get("scopeSource") or "union"
            if source in ("operation", "union"):
                ops_cfg = ((ext.get("middleware") or {}).get("operations") or {})
                for path, method, op in operations(document):
                    refs = [r for r in (op.get("security") or []) if isinstance(r, dict) and name in r]
                    if not refs:
                        continue
                    op_id = op.get("operationId", "")
                    if not enabled((ops_cfg.get(op_id) or {}).get("scopeCheck")):
                        add("WARN", f"paths.{path}.{method}",
                            f"operation scopes for `{name}` are not enforced: add "
                            f"middleware.operations.{op_id or '<operationId>'}.scopeCheck.enabled: true")

    hmac_cfg = auth.get("hmac")
    if enabled(hmac_cfg):
        if not has_source(hmac_cfg):
            add("ERROR", f"{base}.hmac", "HMAC with no `header` block: the Gateway refuses the definition "
                '(`all auth sources are disabled for hmac`). Add "header": {"enabled": true, "name": "Authorization"}')
        if not hmac_cfg.get("allowedClockSkew"):
            add("WARN", f"{base}.hmac", "no `allowedClockSkew`: a signed request with any old Date is "
                "accepted, so a captured request can be replayed. Set it in milliseconds, for example 5000")

    security = document.get("security") or []
    if len(security) > 1 and mode != "compliant":
        add("WARN", "security", "more than one `security` entry in legacy mode: only the first is used. "
            'For "any one of these methods" set "securityProcessingMode": "compliant"')

    if enabled(auth.get("certificateAuth")):
        add("NOTE", f"{base}.certificateAuth", "certificate authentication needs the Gateway to listen on "
            "HTTPS, and each client certificate needs a session created with its certificate id")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: check_auth.py <tyk-oas-definition.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            document = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    findings = check(document)
    for level, where, text in findings:
        print(f"{level:5} {where}: {text}")
    counts = {lvl: sum(1 for f in findings if f[0] == lvl) for lvl in ("ERROR", "WARN", "NOTE")}
    print(f"{counts['ERROR']} error(s), {counts['WARN']} warning(s), {counts['NOTE']} note(s)")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
