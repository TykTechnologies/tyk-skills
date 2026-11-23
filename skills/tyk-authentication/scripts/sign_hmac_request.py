#!/usr/bin/env python3
"""Sign a request for a Tyk API protected by HMAC request signing, and optionally send it.

Tyk checks a draft-cavage style signature in the Authorization header:

    Authorization: Signature keyId="<key>",algorithm="hmac-sha256",headers="(request-target) date",signature="<sig>"

The signed string is one line per name in `headers`, joined with a newline:
`(request-target): <lower-case method> <path>` for the pseudo-header, and
`<lower-case name>: <value>` for a real header (a `date` entry becomes
`x-aux-date: <value>` when an X-Aux-Date header is sent). The path is the path the client sends to the
Gateway, listen path included, without the query string. The signature is the HMAC of that
string, base64-encoded and then URL-encoded. A `Date` (or `X-Aux-Date`) header is always
required.

The secret is read from an environment variable, never from the command line.

    export HMAC_SECRET=<the key's hmac_string>
    sign_hmac_request.py --url http://localhost:8080/hmac/anything --key-id <key> \
        --secret-env HMAC_SECRET --algorithm hmac-sha256 --send

Without --send it prints the headers to add, one per line, ready for `curl -H`.

Standard library only. Exit status: 0 on success (with --send: any HTTP answer), 2 on a usage
or connection error.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate

ALGORITHMS = {
    "hmac-sha1": hashlib.sha1,
    "hmac-sha256": hashlib.sha256,
    "hmac-sha384": hashlib.sha384,
    "hmac-sha512": hashlib.sha512,
}


def signing_string(method: str, path: str, header_names: list[str], headers: dict[str, str]) -> str:
    """Build the string Tyk signs. `headers` maps lower-case names to values.

    For a `date` entry Tyk uses X-Aux-Date when the request has one, and then the line reads
    `x-aux-date: <value>`.
    """
    lines = []
    for name in header_names:
        lowered = name.strip().lower()
        if lowered == "(request-target)":
            lines.append(f"(request-target): {method.lower()} {path}")
        elif lowered == "date" and "x-aux-date" in headers:
            lines.append(f"x-aux-date: {headers['x-aux-date'].strip()}")
        else:
            if lowered not in headers:
                raise ValueError(f"header {name!r} is listed in --headers but has no value")
            lines.append(f"{lowered}: {headers[lowered].strip()}")
    return "\n".join(lines)


def sign(string: str, secret: str, algorithm: str) -> str:
    """HMAC, base64, then URL-encode: the form Tyk compares against."""
    if algorithm not in ALGORITHMS:
        raise ValueError(f"unknown algorithm {algorithm!r}; use one of {', '.join(ALGORITHMS)}")
    digest = hmac.new(secret.encode(), string.encode(), ALGORITHMS[algorithm]).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest).decode())


def authorization_header(key_id: str, algorithm: str, header_names: list[str], signature: str) -> str:
    return (
        f'Signature keyId="{key_id}",algorithm="{algorithm}",'
        f'headers="{" ".join(header_names)}",signature="{signature}"'
    )


def build(args, environ=os.environ) -> tuple[str, dict[str, str]]:
    """Return (url, headers to send) for the parsed arguments."""
    secret = environ.get(args.secret_env, "")
    if not secret:
        raise ValueError(f"environment variable {args.secret_env} is empty or not set")
    parsed = urllib.parse.urlsplit(args.url)
    path = args.path or parsed.path or "/"
    headers: dict[str, str] = {}
    for text in args.header:
        name, sep, value = text.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"--header must look like 'Name: value', got {text!r}")
        headers[name.strip().lower()] = value.strip()
    if "date" not in headers and "x-aux-date" not in headers:
        headers["date"] = args.date or formatdate(usegmt=True)
    header_names = args.headers.split() if args.headers else ["(request-target)", "date"]
    string = signing_string(args.method, path, header_names, headers)
    signature = sign(string, secret, args.algorithm)
    out = {name.title() if name != "x-aux-date" else "X-Aux-Date": value for name, value in headers.items()}
    out["Authorization"] = authorization_header(args.key_id, args.algorithm, header_names, signature)
    return args.url, out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True, help="full URL of the request through the Gateway")
    p.add_argument("--key-id", required=True, help="the Tyk key that has hmac_enabled")
    p.add_argument("--secret-env", required=True, help="environment variable holding the key's hmac_string")
    p.add_argument("--algorithm", default="hmac-sha256", choices=sorted(ALGORITHMS))
    p.add_argument("--method", default="GET")
    p.add_argument("--headers", default="", help='names to sign, space-separated (default "(request-target) date")')
    p.add_argument("--header", action="append", default=[], help="extra header 'Name: value' (repeatable)")
    p.add_argument("--date", default="", help="Date header value (default: now, RFC 7231 format)")
    p.add_argument("--path", default="", help="override the signed path (default: the URL's path)")
    p.add_argument("--data", default=None, help="request body to send with --send")
    p.add_argument("--send", action="store_true", help="send the request and print status and body")
    args = p.parse_args(argv)
    try:
        url, headers = build(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.send:
        for name, value in headers.items():
            print(f"{name}: {value}")
        return 0
    body = args.data.encode() if args.data is not None else None
    req = urllib.request.Request(url, data=body, method=args.method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status, text = resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as err:
        status, text = err.code, err.read().decode(errors="replace")
    except urllib.error.URLError as err:
        print(f"error: {err.reason}", file=sys.stderr)
        return 2
    print(f"HTTP {status}")
    print(text.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
