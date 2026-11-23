#!/usr/bin/env python3
"""Open a WebSocket through Tyk Gateway, send messages and print what comes back.

Python standard library only, so it runs where wscat or websocat is not installed.

    python3 ws_probe.py ws://localhost:8080/ws-echo/websocket/echo --send hello
    python3 ws_probe.py ws://localhost:8080/ws/ -H "Authorization: $KEY" --send a --send b
    python3 ws_probe.py ws://localhost:8080/stream/subscribe --listen 10   # read frames for 10 s
    python3 ws_probe.py ws://localhost:8080/ws/ --hold 70 --keepalive 5    # how long until closed?

Exit code 0 when the upgrade succeeded (HTTP 101), 1 otherwise. When the Gateway refuses the
upgrade, the status line and body are printed so you can match them against the docs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import socket
import ssl
import struct
import sys
import time
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_value(key: str) -> str:
    """Return the Sec-WebSocket-Accept value a server must send for this key (RFC 6455)."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = 0x1, mask_key: bytes | None = None) -> bytes:
    """Build one final, masked client frame."""
    mask_key = mask_key if mask_key is not None else os.urandom(4)
    head = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        head += bytes([0x80 | length])
    elif length < 65536:
        head += bytes([0x80 | 126]) + struct.pack("!H", length)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", length)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return head + mask_key + masked


def decode_frame(buf: bytes) -> tuple[int, bytes, int] | None:
    """Parse one frame from buf. Return (opcode, payload, bytes used) or None if incomplete."""
    if len(buf) < 2:
        return None
    opcode = buf[0] & 0x0F
    masked = buf[1] & 0x80
    length = buf[1] & 0x7F
    pos = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length = struct.unpack("!H", buf[2:4])[0]
        pos = 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length = struct.unpack("!Q", buf[2:10])[0]
        pos = 10
    mask_key = b""
    if masked:
        if len(buf) < pos + 4:
            return None
        mask_key = buf[pos:pos + 4]
        pos += 4
    if len(buf) < pos + length:
        return None
    payload = buf[pos:pos + length]
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload, pos + length


def parse_headers(raw: list[str]) -> list[tuple[str, str]]:
    out = []
    for item in raw:
        if ":" not in item:
            raise SystemExit(f"bad header {item!r}: use 'Name: value'")
        name, value = item.split(":", 1)
        out.append((name.strip(), value.strip()))
    return out


def connect(url: str, headers: list[tuple[str, str]], timeout: float, insecure: bool):
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise SystemExit("URL must start with ws:// or wss://")
    port = parts.port or (443 if parts.scheme == "wss" else 80)
    sock = socket.create_connection((parts.hostname, port), timeout=timeout)
    if parts.scheme == "wss":
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=parts.hostname)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {parts.netloc}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ] + [f"{n}: {v}" for n, v in headers]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    head_lines = head.decode(errors="replace").split("\r\n")
    status = int(head_lines[0].split()[1]) if head_lines and len(head_lines[0].split()) > 1 else 0
    resp_headers = {}
    for line in head_lines[1:]:
        if ":" in line:
            n, v = line.split(":", 1)
            resp_headers[n.strip().lower()] = v.strip()
    return sock, status, head_lines[0], resp_headers, rest, key


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url")
    p.add_argument("-H", "--header", action="append", default=[], help="extra request header, 'Name: value'")
    p.add_argument("--send", action="append", default=[], help="text message to send (repeatable)")
    p.add_argument("--listen", type=float, default=3.0, help="seconds to read frames after sending (default 3)")
    p.add_argument("--hold", type=float, default=0.0,
                   help="keep the connection open up to this many seconds and report when it closed")
    p.add_argument("--keepalive", type=float, default=0.0,
                   help="while holding, send a text frame 'keepalive' every this many seconds")
    p.add_argument("--timeout", type=float, default=10.0, help="connect and handshake timeout")
    p.add_argument("--insecure", action="store_true", help="wss:// without certificate checks (lab only)")
    args = p.parse_args(argv)

    start = time.monotonic()
    sock, status, status_line, resp_headers, rest, key = connect(
        args.url, parse_headers(args.header), args.timeout, args.insecure)
    if status != 101:
        sock.settimeout(2)
        body = rest
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body += chunk
        except (socket.timeout, OSError):
            pass
        sock.close()
        print(f"handshake refused: {status_line}")
        if body:
            print(body.decode(errors="replace").strip())
        return 1
    if resp_headers.get("sec-websocket-accept") != accept_value(key):
        sock.close()
        print("handshake: 101 but Sec-WebSocket-Accept does not match", file=sys.stderr)
        return 1
    print("handshake: 101 Switching Protocols")
    for msg in args.send:
        sock.sendall(encode_frame(msg.encode()))
        print(f"> {msg}")

    wait = args.hold if args.hold > 0 else args.listen
    deadline = time.monotonic() + wait
    buf = rest
    closed_by_peer = False
    keepalive_echoes = 0
    next_keepalive = time.monotonic() + args.keepalive if args.keepalive > 0 else None
    while time.monotonic() < deadline:
        if next_keepalive is not None and time.monotonic() >= next_keepalive:
            try:
                sock.sendall(encode_frame(b"keepalive"))
            except OSError as exc:
                print(f"connection error: {exc}")
                closed_by_peer = True
                break
            next_keepalive += args.keepalive
        sock.settimeout(max(0.05, min(1.0, deadline - time.monotonic())))
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        except OSError as exc:
            print(f"connection error: {exc}")
            closed_by_peer = True
            break
        if not chunk:
            closed_by_peer = True
            break
        buf += chunk
        while True:
            frame = decode_frame(buf)
            if frame is None:
                break
            opcode, payload, used = frame
            buf = buf[used:]
            if opcode == 0x8:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else None
                print(f"< close {code} {payload[2:].decode(errors='replace')}".rstrip())
                closed_by_peer = True
                break
            if opcode == 0x9:
                sock.sendall(encode_frame(payload, opcode=0xA))
                continue
            if opcode == 0x0:
                print(f"< (continued) {payload.decode(errors='replace')}")
                continue
            if opcode in (0x1, 0x2):
                if payload == b"keepalive":
                    keepalive_echoes += 1
                else:
                    print(f"< {payload.decode(errors='replace')}")
        if closed_by_peer:
            break
    elapsed = time.monotonic() - start
    if args.keepalive > 0:
        print(f"keepalive frames echoed: {keepalive_echoes}")
    if args.hold > 0:
        if closed_by_peer:
            print(f"closed by the other side after {elapsed:.1f} s")
        else:
            print(f"still open after {elapsed:.1f} s")
    try:
        sock.sendall(encode_frame(struct.pack("!H", 1000), opcode=0x8))
    except OSError:
        pass
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
