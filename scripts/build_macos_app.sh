#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

INSTALL_ARGS=()
if [[ "${VITAMINE_INCLUDE_LOCAL_LLM:-0}" == "1" ]]; then
  INSTALL_ARGS+=(--include-local-llm)
fi

python3 scripts/install_export_tools.py "${INSTALL_ARGS[@]}"
python3 -m pip install --upgrade pyinstaller
python3 -m PyInstaller --noconfirm --clean VitaMine.spec

echo
echo "Built: $ROOT/dist/VitaMine.app"
echo "Tip: test it with open dist/VitaMine.app"
