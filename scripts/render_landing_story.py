#!/usr/bin/env python3
"""Render VitaMine's landing-story visuals to a deterministic MP4.

The landing page uses scroll position as an animation timeline.  This script
drives that timeline through Chrome's DevTools protocol, isolates each visual
from the surrounding landing page, and sends the rendered PNG frames to
ffmpeg.  It does not record a desktop or browser window.

Chrome and ffmpeg must be installed locally.  On macOS the standard Google
Chrome application location is detected automatically.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import http.server
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT = Path(__file__).resolve().parent.parent
CLOUD_STATIC = PROJECT / "vitamine" / "cloud_static"
LOGO = PROJECT / "vitamine" / "logo" / "vitamine_logo.png"
DEFAULT_OUTPUT = PROJECT / "output" / "vitamine-landing-story.mp4"

SCENES = (
    ("problem", "feature-one"),
    ("import", "feature-import"),
    ("sync", "feature-3"),
    ("prompt", "feature-4"),
    ("profile", "feature-5"),
    ("metrics", "feature-6"),
    ("network", "feature-7"),
)


class LandingAssets(http.server.BaseHTTPRequestHandler):
    """Serve only the public landing-page assets required by the renderer."""

    server_version = "VitaMineLandingRenderer/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        routes = {
            "/": (CLOUD_STATIC / "account.html", "text/html; charset=utf-8"),
            "/assets/account.css": (CLOUD_STATIC / "account.css", "text/css; charset=utf-8"),
            "/assets/account.js": (CLOUD_STATIC / "account.js", "text/javascript; charset=utf-8"),
            "/assets/vitamine-logo.png": (LOGO, "image/png"),
        }
        path = self.path.split("?", 1)[0]
        if path == "/api/session":
            body = b'{"account":null}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        source = routes.get(path)
        if source is None or not source[0].is_file():
            self.send_error(404)
            return
        asset, content_type = source
        body = asset.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # Chrome makes predictable incidental requests; keep renders quiet.
        return


class DevToolsSocket:
    """Small dependency-free WebSocket client for Chrome's DevTools protocol."""

    def __init__(self, address: str) -> None:
        host, path = address.split("/", 1)
        hostname, port = host.split(":", 1)
        self.socket = socket.create_connection((hostname, int(port)), timeout=30)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = self._read_headers()
        if not response.startswith("HTTP/1.1 101"):
            raise RuntimeError(f"Chrome refused the DevTools connection: {response.splitlines()[0]}")
        self.sequence = 0

    def _read_headers(self) -> str:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.socket.recv(1)
            if not chunk:
                raise RuntimeError("Chrome closed the DevTools connection during its handshake.")
            data.extend(chunk)
        return data.decode("iso-8859-1")

    def _read_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self.socket.recv(remaining)
            if not chunk:
                raise RuntimeError("Chrome closed the DevTools connection.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_frame(self, text: str) -> None:
        payload = text.encode("utf-8")
        length = len(payload)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.extend((0x80 | 126, *length.to_bytes(2, "big")))
        else:
            header.extend((0x80 | 127, *length.to_bytes(8, "big")))
        mask = secrets.token_bytes(4)
        header.extend(mask)
        encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.socket.sendall(bytes(header) + encoded)

    def _receive_frame(self) -> tuple[int, bytes]:
        first, second = self._read_exact(2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return opcode, payload

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        identifier = self.sequence
        message: dict[str, Any] = {"id": identifier, "method": method}
        if params:
            message["params"] = params
        self._send_frame(json.dumps(message, separators=(",", ":")))
        while True:
            opcode, payload = self._receive_frame()
            if opcode == 0x8:
                raise RuntimeError("Chrome closed the DevTools connection.")
            if opcode == 0x9:
                self.socket.sendall(b"\x8a\x80\x00\x00\x00\x00")
                continue
            if opcode != 0x1:
                continue
            response = json.loads(payload.decode("utf-8"))
            if response.get("id") != identifier:
                continue
            if "error" in response:
                raise RuntimeError(f"Chrome could not run {method}: {response['error'].get('message')}")
            return response.get("result", {})

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.socket.close()


def chrome_binary(value: str | None) -> str:
    candidates = [value] if value else []
    candidates.extend(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Google Chrome or Chromium is required. Pass its executable with --chrome.")


def wait_for_debugger(port: int, timeout: float = 15) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    endpoint = f"http://127.0.0.1:{port}/json/list"
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=1) as response:  # noqa: S310 - localhost Chrome endpoint
                return json.load(response)
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Chrome did not open its DevTools endpoint.")


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def new_page(port: int, url: str) -> DevToolsSocket:
    endpoint = f"http://127.0.0.1:{port}/json/new?{quote(url, safe=':/?=&')}"
    request = Request(endpoint, method="PUT")
    with urlopen(request, timeout=10) as response:  # noqa: S310 - localhost Chrome endpoint
        target = json.load(response)
    websocket_url = target["webSocketDebuggerUrl"].removeprefix("ws://")
    return DevToolsSocket(websocket_url)


def evaluate(client: DevToolsSocket, expression: str, *, await_promise: bool = False) -> Any:
    response = client.call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True,
        },
    )
    result = response.get("result", {})
    if result.get("subtype") == "error" or "exceptionDetails" in response:
        exception = response.get("exceptionDetails", {}).get("exception", {})
        detail = exception.get("description") or exception.get("value") or response.get("exceptionDetails", {}).get("text")
        raise RuntimeError(f"The landing page renderer failed: {detail or result.get('description', result)}")
    return result.get("value")


def ease_in_out(value: float) -> float:
    return 4 * value * value * value if value < 0.5 else 1 - ((-2 * value + 2) ** 3) / 2


def prepare_scene(client: DevToolsSocket, scene_id: str, width: int, height: int) -> None:
    client.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
    )
    ready = evaluate(
        client,
        """(async () => {
          for (let attempt = 0; attempt < 100; attempt += 1) {
            if (document.getElementById(%s) && document.querySelectorAll('.feature-chapter').length === 7) break;
            await new Promise((resolve) => setTimeout(resolve, 25));
          }
          const image = document.getElementById(%s).querySelector('img');
          if (image && !image.complete) await new Promise((resolve) => { image.addEventListener('load', resolve, { once: true }); image.addEventListener('error', resolve, { once: true }); });
          return Boolean(document.getElementById(%s));
        })()"""
        % (json.dumps(scene_id), json.dumps(scene_id), json.dumps(scene_id)),
        await_promise=True,
    )
    if not ready:
        raise RuntimeError(f"The {scene_id!r} landing-story scene did not load.")
    evaluate(
        client,
        """(() => {
          const chapter = document.getElementById(%s);
          const visual = chapter.querySelector('.feature-visual');
          document.documentElement.style.setProperty('scroll-behavior', 'auto', 'important');
          document.documentElement.style.setProperty('scroll-snap-type', 'none', 'important');
          chapter.style.setProperty('transition', 'none', 'important');
          chapter.style.setProperty('opacity', '1', 'important');
          chapter.style.setProperty('transform', 'none', 'important');
          visual.style.setProperty('position', 'fixed', 'important');
          visual.style.setProperty('inset', '0', 'important');
          visual.style.setProperty('width', '100vw', 'important');
          visual.style.setProperty('height', '100vh', 'important');
          visual.style.setProperty('min-height', '0', 'important');
          visual.style.setProperty('margin', '0', 'important');
          visual.style.setProperty('overflow', 'hidden', 'important');
          visual.style.setProperty('z-index', '1000', 'important');
          visual.style.setProperty('background', getComputedStyle(chapter).background, 'important');
          visual.querySelectorAll('*').forEach((node) => node.style.setProperty('animation', 'none', 'important'));
          const caret = visual.querySelector('.typing-caret');
          if (caret) caret.style.setProperty('opacity', '1', 'important');
        })()"""
        % json.dumps(scene_id),
    )


def set_scene_progress(client: DevToolsSocket, scene_id: str, progress: float) -> None:
    evaluate(
        client,
        """new Promise((resolve) => {
          const chapter = document.getElementById(%s);
          const distance = Math.max(1, chapter.offsetHeight - window.innerHeight);
          const target = window.scrollY + chapter.getBoundingClientRect().top - 14 + (%s * distance);
          window.scrollTo({ top: target, left: 0, behavior: 'auto' });
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        })"""
        % (json.dumps(scene_id), progress),
        await_promise=True,
    )


def capture_frame(client: DevToolsSocket, path: Path, width: int, height: int) -> None:
    scroll_y = evaluate(client, "window.scrollY")
    capture = client.call(
        "Page.captureScreenshot",
        {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": False,
            "clip": {"x": 0, "y": scroll_y, "width": width, "height": height, "scale": 1},
        },
    )
    path.write_bytes(base64.b64decode(capture["data"]))


def encode_video(frames: Path, output: Path, fps: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-start_number",
        "0",
        "-i",
        str(frames / "frame-%05d.png"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="MP4 output path.")
    parser.add_argument("--width", type=int, default=1920, help="Output width in pixels (default: 1920).")
    parser.add_argument("--height", type=int, default=1080, help="Output height in pixels (default: 1080).")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second (default: 30).")
    parser.add_argument("--seconds-per-scene", type=float, default=3, help="Animation duration for each scene (default: 3).")
    parser.add_argument("--scene", choices=[name for name, _ in SCENES], action="append", help="Render only a named scene; repeat to select several.")
    parser.add_argument("--chrome", help="Path to a Chrome or Chromium executable.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.width < 320 or args.height < 180 or args.fps < 1 or args.seconds_per_scene <= 0:
        raise SystemExit("Width, height, fps, and seconds per scene must be positive and sensible.")
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required to encode the movie but was not found on PATH.")
    chrome = chrome_binary(args.chrome)
    selected = [(name, identifier) for name, identifier in SCENES if not args.scene or name in args.scene]
    frame_count = max(2, round(args.fps * args.seconds_per_scene))
    port = available_port()

    with tempfile.TemporaryDirectory(prefix="vitamine-landing-render-") as temporary:
        workspace = Path(temporary)
        frames = workspace / "frames"
        frames.mkdir()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LandingAssets)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        chrome_process = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={workspace / 'chrome-profile'}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--hide-scrollbars",
                f"--window-size={args.width},{args.height}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_debugger(port)
            base_url = f"http://127.0.0.1:{server.server_port}/"
            index = 0
            for name, scene_id in selected:
                print(f"Rendering {name}…", flush=True)
                client = new_page(port, base_url)
                try:
                    prepare_scene(client, scene_id, args.width, args.height)
                    for frame in range(frame_count):
                        progress = ease_in_out(frame / (frame_count - 1))
                        set_scene_progress(client, scene_id, progress)
                        capture_frame(client, frames / f"frame-{index:05d}.png", args.width, args.height)
                        index += 1
                finally:
                    client.close()
            encode_video(frames, args.output.resolve(), args.fps)
        finally:
            server.shutdown()
            server.server_close()
            chrome_process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                chrome_process.wait(timeout=5)
            if chrome_process.poll() is None:
                chrome_process.kill()
    print(f"Rendered {len(selected)} scenes to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Landing-story render failed: {error}", file=sys.stderr)
        raise SystemExit(1)
