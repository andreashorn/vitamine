#!/usr/bin/env python3
"""Download or collect binaries used by VitaMine exports and imports."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTALL_DIR = ROOT / "vendor" / "export-tools"
DEFAULT_MODEL_DIR = ROOT / "vendor" / "models"
GITHUB_API = "https://api.github.com/repos"
TOOLS = {
    "llama-server": "ggml-org/llama.cpp",
    "pandoc": "jgm/pandoc",
    "typst": "typst/typst",
}
BREW_TOOLS = {
    "pdftotext": "poppler",
}
DEFAULT_MODEL = {
    "repo": "bartowski/Phi-3.5-mini-instruct-GGUF",
    "file": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
    "target": "vitamine-import.gguf",
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
    if tool == "llama-server":
        llama_arch = "arm64" if arch == "arm64" else "x64"
        return lower.endswith(".tar.gz") and f"bin-macos-{llama_arch}" in lower
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
        if tool == "llama-server":
            collect_archive_dylibs(extract_dir, target, install_dir / "lib")
        print(f"{tool}: installed {target}")
        return target


def collect_archive_dylibs(extract_dir: Path, binary: Path, lib_dir: Path) -> None:
    if platform.system() != "Darwin":
        return
    lib_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in extract_dir.rglob("*.dylib"):
        target = lib_dir / source.name
        shutil.copy2(source, target, follow_symlinks=True)
        mode = target.stat().st_mode
        target.chmod(mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        run_install_name_tool("-id", f"@rpath/{source.name}", str(target))
        run_install_name_tool("-add_rpath", "@loader_path", str(target))
        copied += 1
    run_install_name_tool("-add_rpath", "@executable_path/../lib", str(binary))
    if copied:
        print(f"{binary.name}: collected {copied} bundled dylib(s)")


def ensure_brew_formula(formula: str) -> None:
    if not shutil.which("brew"):
        raise SystemExit(f"Homebrew is required to install {formula}.")
    result = subprocess.run(["brew", "list", "--versions", formula], text=True, capture_output=True)
    if result.returncode == 0 and result.stdout.strip():
        return
    subprocess.run(["brew", "install", formula], check=True)


def install_brew_tool(tool: str, install_dir: Path, force: bool) -> Path:
    target = install_dir / "bin" / tool
    if target.exists() and not force:
        print(f"{tool}: already installed at {target}")
        return target
    formula = BREW_TOOLS[tool]
    ensure_brew_formula(formula)
    source = shutil.which(tool)
    if not source:
        raise SystemExit(f"{tool}: Homebrew installed {formula}, but {tool} was not found on PATH.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and force:
        target.unlink()
    shutil.copy2(source, target, follow_symlinks=True)
    mode = target.stat().st_mode
    target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    collect_macos_dylibs(target, Path(source).resolve(), install_dir / "lib")
    if tool == "llama-server":
        collect_llama_backend_dylibs(Path(source).resolve(), install_dir / "lib")
    print(f"{tool}: installed {target} from Homebrew formula {formula}")
    return target


def otool_dependencies(path: Path) -> list[str]:
    result = subprocess.run(["otool", "-L", str(path)], text=True, capture_output=True, check=True)
    deps: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        deps.append(stripped.split(" ", 1)[0])
    return deps


def is_system_dylib(dep: str) -> bool:
    return dep.startswith("/usr/lib/") or dep.startswith("/System/Library/")


def resolve_dylib(dep: str, search_dirs: list[Path]) -> Path | None:
    if is_system_dylib(dep):
        return None
    if dep.startswith("/") and Path(dep).exists():
        return Path(dep)
    if dep.startswith("@rpath/"):
        name = dep.split("/", 1)[1]
        for directory in search_dirs:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def run_install_name_tool(*args: str) -> None:
    subprocess.run(["install_name_tool", *args], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def collect_macos_dylibs(binary: Path, source_binary: Path, lib_dir: Path) -> None:
    if platform.system() != "Darwin":
        return
    lib_dir.mkdir(parents=True, exist_ok=True)
    search_dirs = [
        source_binary.parent,
        source_binary.parent.parent / "lib",
        Path("/usr/local/lib"),
        Path("/opt/homebrew/lib"),
    ]
    queue = [source_binary]
    copied: dict[str, Path] = {}
    processed: set[Path] = set()

    run_install_name_tool("-add_rpath", "@executable_path/../lib", str(binary))
    while queue:
        current = queue.pop(0)
        if current in processed:
            continue
        processed.add(current)
        try:
            deps = otool_dependencies(current)
        except subprocess.CalledProcessError:
            continue
        target_current = binary if current == source_binary else copied.get(current.name)
        for dep in deps:
            source_dep = resolve_dylib(dep, search_dirs)
            if not source_dep:
                continue
            target_dep = lib_dir / source_dep.name
            if not target_dep.exists():
                shutil.copy2(source_dep, target_dep, follow_symlinks=True)
                mode = target_dep.stat().st_mode
                target_dep.chmod(mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                copied[source_dep.name] = target_dep
                queue.append(source_dep)
                run_install_name_tool("-id", f"@rpath/{source_dep.name}", str(target_dep))
                run_install_name_tool("-add_rpath", "@loader_path", str(target_dep))
            if target_current:
                run_install_name_tool("-change", dep, f"@rpath/{source_dep.name}", str(target_current))


def collect_llama_backend_dylibs(source_binary: Path, lib_dir: Path) -> None:
    if platform.system() != "Darwin":
        return
    roots = [
        source_binary.parent.parent,
        Path("/usr/local/opt/llama.cpp"),
        Path("/opt/homebrew/opt/llama.cpp"),
    ]
    copied = 0
    for root in roots:
        if not root.exists():
            continue
        for source in root.rglob("libggml-*.dylib"):
            target = lib_dir / source.name
            if target.exists() and target.stat().st_size == source.stat().st_size:
                continue
            shutil.copy2(source, target, follow_symlinks=True)
            mode = target.stat().st_mode
            target.chmod(mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            run_install_name_tool("-id", f"@rpath/{source.name}", str(target))
            run_install_name_tool("-add_rpath", "@loader_path", str(target))
            copied += 1
    if copied:
        print(f"llama-server: collected {copied} backend dylib(s)")


def huggingface_url(repo: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{filename}?download=true"


def install_default_model(model_dir: Path, force: bool) -> Path:
    target = model_dir / DEFAULT_MODEL["target"]
    if target.exists() and not force:
        print(f"model: already installed at {target}")
        return target
    model_dir.mkdir(parents=True, exist_ok=True)
    url = huggingface_url(DEFAULT_MODEL["repo"], DEFAULT_MODEL["file"])
    print(f"model: downloading {DEFAULT_MODEL['repo']} / {DEFAULT_MODEL['file']}")
    with tempfile.NamedTemporaryFile(prefix="vitamine-model-", suffix=".gguf", delete=False) as handle:
        tmp_path = Path(handle.name)
    try:
        download(url, tmp_path)
        tmp_path.replace(target)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"model: installed {target}")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--force", action="store_true", help="redownload tools even if binaries exist")
    parser.add_argument(
        "--tool",
        choices=sorted(set(TOOLS) | set(BREW_TOOLS)),
        action="append",
        help="install only this tool",
    )
    parser.add_argument("--include-local-llm", action="store_true", help="download the default bundled GGUF model")
    return parser.parse_args()


def main() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("This installer currently supports macOS app builds only.")
    args = parse_args()
    arch = mac_arch()
    tools = args.tool or sorted(set(TOOLS) | set(BREW_TOOLS))
    for tool in tools:
        if tool in TOOLS:
            install_tool(tool, args.install_dir.resolve(), arch, args.force)
        else:
            install_brew_tool(tool, args.install_dir.resolve(), args.force)
    if args.include_local_llm:
        install_default_model(args.model_dir.resolve(), args.force)


if __name__ == "__main__":
    main()
