#!/usr/bin/env python3
"""Minimal RFC 6455 text echo check using only the Python standard library."""

import argparse
import base64
import os
import socket
import ssl
import struct
import urllib.parse


def recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError("websocket closed early")
        data += chunk
    return data


def main():
    parser = argparse.ArgumentParser()
    # The gateway's own base URL, so the handshake follows whatever scheme the
    # bundle was generated with. A separate --tls flag would be a second place
    # for the transport decision to live, and to go stale.
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--message", default="standalone-websocket-proof")
    args = parser.parse_args()

    parsed = urllib.parse.urlsplit(args.gateway_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    raw = socket.create_connection((host, port), timeout=10)
    if parsed.scheme == "https":
        # The gateway presents the demo CA's self-signed certificate.
        sock = ssl._create_unverified_context().wrap_socket(raw, server_hostname=host)
    else:
        sock = raw
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {args.path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Authorization: {args.api_key}\r\n\r\n"
    )
    sock.sendall(request.encode())
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
    if not response.startswith(b"HTTP/1.1 101"):
        raise RuntimeError(response.decode(errors="replace"))

    payload = args.message.encode()
    mask = os.urandom(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    header = bytes([0x81, 0x80 | len(payload)]) if len(payload) < 126 else bytes([0x81, 0xFE]) + struct.pack("!H", len(payload))
    sock.sendall(header + mask + masked)

    first, second = recv_exact(sock, 2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    echoed = recv_exact(sock, length).decode()
    if first & 0x0F != 1 or echoed != args.message:
        raise RuntimeError(f"unexpected websocket frame: {echoed!r}")
    print(echoed)


if __name__ == "__main__":
    main()
