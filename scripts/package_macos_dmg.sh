#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/dist/VitaMine.app"
DMG="$ROOT/dist/VitaMine.dmg"

if [[ ! -d "$APP" ]]; then
  echo "Missing $APP; run scripts/build_macos_app.sh first." >&2
  exit 1
fi

rm -f "$DMG"
hdiutil create \
  -volname "VitaMine" \
  -srcfolder "$APP" \
  -ov \
  -format UDZO \
  "$DMG"

echo "Packaged: $DMG"
