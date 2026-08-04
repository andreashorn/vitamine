#!/bin/sh
set -eu

promo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
voice_text="$promo_root/promo-video/public/narration.txt"
voice_aiff="$promo_root/promo-video/public/voiceover.aiff"
voice_m4a="$promo_root/promo-video/public/voiceover.m4a"

if ! command -v say >/dev/null 2>&1; then
  echo "The draft narration uses macOS 'say'. Replace promo-video/public/voiceover.m4a with a recorded narration on other platforms." >&2
  exit 1
fi

say -v "Samantha (English (US))" -r 165 -f "$voice_text" -o "$voice_aiff"
ffmpeg -y -loglevel error -i "$voice_aiff" -c:a aac -b:a 192k "$voice_m4a"
npm --prefix "$promo_root/promo-video" run render
