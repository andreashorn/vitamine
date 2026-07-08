#!/usr/bin/env python3
"""Download Pandoc and Typst binaries used by VitaMine exports."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTALL_DIR = ROOT / "vendor" / "export-tools"
GITHUB_API = "https://api.github.com/repos"
TOOLS = {
    "pandoc": "jgm/pandoc",
    "typst": "typst/typst",
}


def mac_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    raise SystemExit(f"Unsupported macOS architecture: {platform.machine()}")


def request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_release(repo: str) -> dict:
    return request_json(f"{GITHUB_API}/{repo}/releases/latest")


def asset_matches(name: str, tool: str, arch: str) -> bool:
    lower = name.lower()
    if tool == "pandoc":
        return lower.endswith(".zip") and f"{arch}-macos" in lower and "wasm" not in lower
    if tool == "typst":
        typst_arch = "aarch64" if arch == "arm64" else "x86_64"
        return lower == f"typst-{typst_arch}-apple-darwin.tar.xz"
    return False


def choose_asset(release: dict, tool: str, arch: str) -> dict:
    for asset in release.get("assets", []):
        if asset_matches(str(asset.get("name", "")), tool, arch):
            return asset
    names = ", ".join(str(asset.get("name", "")) for asset in release.get("assets", []))
    raise SystemExit(f"No {tool} macOS {arch} asset found in release {release.get('tag_name')}. Assets: {names}")


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "VitaMine export tool installer"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as file:
        shutil.copyfileobj(response, file)


def extract_archive(archive: Path, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(destination)
    elif archive.name.endswith((".tar.xz", ".tar.gz", ".tgz")):
        with tarfile.open(archive) as tar_file:
            tar_file.extractall(destination)
    else:
        raise SystemExit(f"Unsupported archive format: {archive.name}")


def find_executable(root: Path, names: Iterable[str]) -> Path:
    allowed = set(names)
    for path in root.rglob("*"):
        if path.name in allowed and path.is_file():
            return path
    raise SystemExit(f"Could not find executable {', '.join(sorted(allowed))} in {root}")


def install_tool(tool: str, install_dir: Path, arch: str, force: bool) -> Path:
    binary_name = f"{tool}.exe" if os.name == "nt" else tool
    target = install_dir / "bin" / binary_name
    if target.exists() and not force:
        print(f"{tool}: already installed at {target}")
        return target

    release = latest_release(TOOLS[tool])
    asset = choose_asset(release, tool, arch)
    print(f"{tool}: downloading {asset['name']} from {release['tag_name']}")

    with tempfile.TemporaryDirectory(prefix=f"vitamine-{tool}-") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / asset["name"]
        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir()
        download(asset["browser_download_url"], archive)
        extract_archive(archive, extract_dir)

        source = find_executable(extract_dir, [binary_name, tool])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        mode = target.stat().st_mode
        target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"{tool}: installed {target}")
        return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL_DIR)
    parser.add_argument("--force", action="store_true", help="redownload tools even if binaries exist")
    parser.add_argument("--tool", choices=sorted(TOOLS), action="append", help="install only this tool")
    return parser.parse_args()


def main() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("This installer currently supports macOS app builds only.")
    args = parse_args()
    arch = mac_arch()
    tools = args.tool or sorted(TOOLS)
    for tool in tools:
        install_tool(tool, args.install_dir.resolve(), arch, args.force)


if __name__ == "__main__":
    main()
