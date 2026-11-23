#!/usr/bin/env python3
"""Mint test JWTs, and print a JWKS for an RSA public key, to exercise a Tyk JWT API.

For tests only: real clients get their tokens from the identity provider. Use it to produce
the tokens an identity provider will not hand out on demand (expired, wrong issuer, wrong
audience, missing scope, another signing key) and check the Gateway's answer to each.

    # HS256 with a shared secret read from an environment variable
    export JWT_SECRET=<shared secret>
    mint_jwt.py hs256 --secret-env JWT_SECRET --claim sub=partner-a --claim pol=gold --ttl 300

    # RS256 with a private key file (needs the openssl command), kid in the header
    mint_jwt.py rs256 --key private.pem --kid key-1 --claim sub=partner-a --claim scope="orders:read"

    # JWKS document for the matching public key, to serve at the URL Tyk fetches
    mint_jwt.py jwks --key public.pem --kid key-1 > jwks.json

Claims: --claim name=value (repeatable; a value that parses as JSON is used as JSON, so
`aud=["a","b"]` and `exp=1700000000` work). --ttl sets exp relative to now (negative for an
expired token); iat is set to now unless given. Standard library only, plus the openssl
command for RS256 and JWKS.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def parse_claims(pairs: list[str]) -> dict:
    claims: dict = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or not name:
            raise ValueError(f"--claim must look like name=value, got {pair!r}")
        try:
            claims[name] = json.loads(raw)
        except json.JSONDecodeError:
            claims[name] = raw
    return claims


def build_claims(pairs: list[str], ttl: int | None, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    claims = parse_claims(pairs)
    claims.setdefault("iat", now)
    if ttl is not None:
        claims["exp"] = now + ttl
    return claims


def signing_input(header: dict, claims: dict) -> bytes:
    h = b64url(json.dumps(header, separators=(",", ":")).encode())
    p = b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{h}.{p}".encode()


def mint_hs256(claims: dict, secret: str, kid: str | None = None) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    if kid:
        header["kid"] = kid
    msg = signing_input(header, claims)
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return f"{msg.decode()}.{b64url(sig)}"


def mint_rs256(claims: dict, key_path: str, kid: str | None = None) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    if kid:
        header["kid"] = kid
    msg = signing_input(header, claims)
    sig = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_path],
        input=msg, capture_output=True, check=True,
    ).stdout
    return f"{msg.decode()}.{b64url(sig)}"


def jwks_for(public_key_path: str, kid: str) -> dict:
    text = subprocess.run(
        ["openssl", "rsa", "-pubin", "-in", public_key_path, "-noout", "-text"],
        capture_output=True, check=True, text=True,
    ).stdout
    return jwk_from_openssl_text(text, kid)


def jwk_from_openssl_text(text: str, kid: str) -> dict:
    """Turn `openssl rsa -pubin -text` output into a JWKS with one RSA signing key."""
    mod = re.search(r"Modulus:\s*\n((?:\s+[0-9a-f:]+\n)+)", text)
    exp = re.search(r"Exponent:\s*(\d+)", text)
    if not mod or not exp:
        raise ValueError("could not read modulus and exponent from the public key")
    n = int(re.sub(r"[\s:]", "", mod.group(1)), 16)
    e = int(exp.group(1))
    n_bytes = n.to_bytes((n.bit_length() + 7) // 8, "big")
    e_bytes = e.to_bytes((e.bit_length() + 7) // 8, "big")
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                      "n": b64url(n_bytes), "e": b64url(e_bytes)}]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    hs = sub.add_parser("hs256", help="sign with a shared secret")
    hs.add_argument("--secret-env", required=True, help="environment variable holding the secret")
    rs = sub.add_parser("rs256", help="sign with an RSA private key file")
    rs.add_argument("--key", required=True, help="PEM private key")
    for sp in (hs, rs):
        sp.add_argument("--kid", default=None, help="kid header value")
        sp.add_argument("--claim", action="append", default=[], help="name=value (repeatable)")
        sp.add_argument("--ttl", type=int, default=300, help="seconds until exp (negative: already expired)")
    jw = sub.add_parser("jwks", help="print a JWKS for an RSA public key")
    jw.add_argument("--key", required=True, help="PEM public key")
    jw.add_argument("--kid", required=True)
    args = p.parse_args(argv)
    try:
        if args.mode == "jwks":
            print(json.dumps(jwks_for(args.key, args.kid), indent=2))
            return 0
        claims = build_claims(args.claim, args.ttl)
        if args.mode == "hs256":
            secret = os.environ.get(args.secret_env, "")
            if not secret:
                raise ValueError(f"environment variable {args.secret_env} is empty or not set")
            print(mint_hs256(claims, secret, args.kid))
        else:
            print(mint_rs256(claims, args.key, args.kid))
    except (ValueError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
