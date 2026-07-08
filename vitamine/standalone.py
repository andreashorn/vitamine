#!/usr/bin/env python3
"""Standalone macOS launcher for a packaged VitaMine app."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from vitamine.paths import OUTPUT, ROOT, SCRIPTS, ensure_runtime_files


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"
OPEN_URL = f"{URL}/?v=standalone"
LOG = OUTPUT / "vitamine_server.log"
SERVER_APP_MODULES = ("vitamine.app:app", "projects.cv.app:app")


def wait_for_server(url: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    health_url = f"{url}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as response:
                if response.status != 200:
                    continue
                payload = json.loads(response.read().decode("utf-8"))
                return payload.get("ok") is True and payload.get("app") == "vitamine"
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.25)
    return False


def server_command() -> list[str]:
    return [sys.executable, "--vitamine-server"]


def is_vitamine_server_command(command: str) -> bool:
    return "--vitamine-server" in command or (
        "uvicorn" in command and any(module in command for module in SERVER_APP_MODULES)
    )


def cv_server_pids() -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{PORT}"],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        if not line.strip().isdigit():
            continue
        pid = int(line.strip())
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            capture_output=True,
            check=False,
        ).stdout
        if is_vitamine_server_command(command):
            pids.append(pid)
    return pids


def stop_running_server() -> None:
    pids = cv_server_pids()
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not cv_server_pids():
            return
        time.sleep(0.2)
    for pid in cv_server_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_server() -> int:
    import uvicorn

    ensure_runtime_files()
    uvicorn.run("vitamine.app:app", host=HOST, port=PORT, log_level="info")
    return 0


def run_script(name: str, args: list[str]) -> int:
    ensure_runtime_files()
    script = SCRIPTS / name
    if not script.exists():
        print(f"Unknown VitaMine script: {name}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(SCRIPTS))
    sys.argv = [str(script), *args]
    runpy.run_path(str(script), run_name="__main__")
    return 0


def launch(restart: bool = False) -> int:
    ensure_runtime_files()
    stop_running_server()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LOG.open("ab")
    subprocess.Popen(
        server_command(),
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    if wait_for_server(URL):
        webbrowser.open(OPEN_URL)
        return 0
    print(f"VitaMine did not start. See log: {LOG}", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--vitamine-server":
        return run_server()
    if len(sys.argv) >= 3 and sys.argv[1] == "--vitamine-script":
        return run_script(sys.argv[2], sys.argv[3:])

    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    return launch(restart=args.restart)


if __name__ == "__main__":
    raise SystemExit(main())
