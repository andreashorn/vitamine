#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 scripts/install_export_tools.py
python3 -m pip install --upgrade pyinstaller
python3 -m PyInstaller --noconfirm --clean VitaMine.spec

echo
echo "Built: $ROOT/dist/VitaMine.app"
echo "Tip: test it with open dist/VitaMine.app"
