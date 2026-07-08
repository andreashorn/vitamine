#!/usr/bin/env python3
"""Start VitaMine and open it in a browser."""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import argparse
import os
import signal
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"
OPEN_URL = f"{URL}/?v=20260708-repo-db"
LOG = ROOT / "output" / "vitamine_server.log"
RUNTIME_DIR = Path.home() / "Library" / "Application Support" / "vitamine"
VENV_DIR = RUNTIME_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
REQUIRED_PACKAGES = ("fastapi", "uvicorn", "python-docx", "eval-type-backport")


def wait_for_server(url: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    health_url = f"{url}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    return False


def server_command() -> list[str]:
    return [
        str(runtime_python()),
        "-m",
        "uvicorn",
        "vitamine.app:app",
        "--host",
        HOST,
        "--port",
        str(PORT),
    ]


def runtime_python() -> Path:
    if not VENV_PYTHON.exists():
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)

    probe = subprocess.run(
        [
            str(VENV_PYTHON),
            "-c",
            "import fastapi, uvicorn, docx, eval_type_backport",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        subprocess.run(
            [
                str(VENV_PYTHON),
                "-m",
                "pip",
                "install",
                "--upgrade",
                *REQUIRED_PACKAGES,
            ],
            cwd=ROOT,
            check=True,
        )
    return VENV_PYTHON


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
    pids = []
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
        if "uvicorn" in command and "vitamine.app:app" in command:
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


def open_running_server(open_existing: bool = False) -> bool:
    if wait_for_server(URL, timeout=0.75):
        if open_existing:
            webbrowser.open(OPEN_URL)
            print(f"VitaMine already running; opened browser: {OPEN_URL}")
        else:
            print(f"VitaMine already running: {OPEN_URL}")
        return True
    return False


def start_background(restart: bool = False, open_existing: bool = False) -> int:
    if restart:
        stop_running_server()
    elif open_running_server(open_existing=open_existing):
        return 0

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
        print(f"VitaMine: {OPEN_URL}")
        print(f"Log: {LOG}")
        return 0
    print(f"Server did not respond at {URL}/health within the timeout. Log: {LOG}", file=sys.stderr)
    return 1


def start_foreground(restart: bool = False, open_existing: bool = False) -> int:
    if restart:
        stop_running_server()
    elif open_running_server(open_existing=open_existing):
        return 0

    process = subprocess.Popen(server_command(), cwd=ROOT)
    try:
        if wait_for_server(URL):
            webbrowser.open(OPEN_URL)
            print(f"VitaMine: {OPEN_URL}")
        else:
            print(f"Server did not respond at {URL}/health within the timeout.", file=sys.stderr)
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return 130


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true", help="start server detached and return after opening the browser")
    parser.add_argument("--restart", action="store_true", help="stop the existing CV tool server before starting")
    parser.add_argument(
        "--open-existing",
        action="store_true",
        help="bring the browser forward even when the CV tool server is already running",
    )
    args = parser.parse_args()
    if args.background:
        return start_background(args.restart, args.open_existing)
    return start_foreground(args.restart, args.open_existing)


if __name__ == "__main__":
    raise SystemExit(main())
