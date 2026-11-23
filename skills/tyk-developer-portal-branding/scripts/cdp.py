#!/usr/bin/env python3
"""Drive a local headless Chrome, Chromium or Edge over the DevTools protocol.

Standard library only: a throwaway browser profile, a minimal WebSocket client and the few
protocol calls the screenshot and contrast checks need. Set CHROME to the browser binary when
it is not in a usual place.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
)


def find_browser() -> str:
    override = os.environ.get("CHROME")
    for candidate in ((override,) if override else CANDIDATES):
        path = candidate if os.path.isfile(candidate) else shutil.which(candidate)
        if path:
            return path
    raise SystemExit("No Chrome, Chromium or Edge found. Install one or set CHROME to its binary.")


class WebSocket:
    def __init__(self, url: str, timeout: float = 60):
        host_port, _, path = url.removeprefix("ws://").partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("browser closed the connection during the handshake")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise ConnectionError("browser refused the WebSocket upgrade")
        self.buffer = response.split(b"\r\n\r\n", 1)[1]

    def _read(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("browser closed the connection")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send(self, text: str) -> None:
        payload = text.encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", len(payload))
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", len(payload))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def receive(self) -> str:
        message = b""
        while True:
            first, second = self._read(2)
            opcode, length = first & 0x0F, second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(length)
            if opcode == 0x8:
                raise ConnectionError("browser closed the connection")
            if opcode == 0x9:  # ping: answer with an empty masked pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if first & 0x80:
                    return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """One headless browser with one page. Use as a context manager."""

    def __init__(self, width: int = 1440, height: int = 900):
        self.profile = tempfile.mkdtemp(prefix="portal-branding-browser-")
        self.process = subprocess.Popen([
            find_browser(), "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars",
            "--disable-extensions", "--use-mock-keychain", "--password-store=basic",
            f"--window-size={width},{height}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(self.profile) / "DevToolsActivePort"
        for _ in range(100):
            if port_file.is_file() and port_file.read_text().strip():
                break
            time.sleep(0.1)
        else:
            self.close()
            raise SystemExit("The browser did not open its DevTools port.")
        port = port_file.read_text().split()[0]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
            pages = [target for target in json.loads(response.read()) if target.get("type") == "page"]
        self.socket = WebSocket(pages[0]["webSocketDebuggerUrl"])
        self.next_id = 0
        self.events: list[dict] = []
        for domain in ("Page", "Network", "Runtime"):
            self.call(f"{domain}.enable")
        self.viewport(width, height)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        if getattr(self, "socket", None):
            self.socket.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        wanted = self.next_id
        self.socket.send(json.dumps({"id": wanted, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.socket.receive())
            if message.get("id") == wanted:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error'].get('message')}")
                return message.get("result", {})
            self.events.append(message)

    def viewport(self, width: int, height: int) -> None:
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600})

    def set_cookies(self, cookies: list[dict], url: str) -> None:
        self.call("Network.setCookies", {"cookies": [{**cookie, "url": url} for cookie in cookies]})

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(f"page script failed: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    def navigate(self, url: str, settle: float = 1.2) -> dict:
        """Load a page and wait for it to settle. Returns the main document's status and final
        URL, the hosts other than the page's own that were asked for anything, and the asset
        requests that failed."""
        self.events.clear()
        self.call("Page.navigate", {"url": url})
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    break
            except RuntimeError:
                pass
            time.sleep(0.2)
        try:
            self.evaluate("document.fonts ? document.fonts.ready.then(() => true) : true")
        except RuntimeError:
            pass
        time.sleep(settle)
        self.evaluate("1")  # drain events that arrived while settling
        final = self.evaluate("location.href")
        own = final.split("/")[2] if "://" in final else ""
        status, hosts, failed = None, set(), []
        for event in self.events:
            params = event.get("params", {})
            if event.get("method") == "Network.requestWillBeSent":
                target = params.get("request", {}).get("url", "")
                if target.startswith("http") and target.split("/")[2] != own:
                    hosts.add(target.split("/")[2])
            elif event.get("method") == "Network.responseReceived":
                response = params.get("response", {})
                if params.get("type") == "Document" and status is None:
                    status = response.get("status")
                elif response.get("status", 200) >= 400 and response.get("url", "").split("/")[2:3] == [own]:
                    failed.append(f"{response.get('status')} {response.get('url')}")
        return {"status": status, "url": final, "third_party_hosts": sorted(hosts), "failed_requests": failed}

    def screenshot(self, path: Path, full_page: bool = True) -> None:
        params: dict = {"format": "png"}
        if full_page:
            size = self.call("Page.getLayoutMetrics").get("cssContentSize", {})
            params.update({"captureBeyondViewport": True, "clip": {
                "x": 0, "y": 0, "width": size.get("width", 1440), "height": min(size.get("height", 900), 12000), "scale": 1}})
        data = self.call("Page.captureScreenshot", params)["data"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
