#!/bin/zsh
set -euo pipefail

readonly PREVIEW_URL="http://127.0.0.1:8766/"
readonly HEALTH_URL="${PREVIEW_URL}health"
readonly SSH_KEY="${HOME}/.ssh/vitamine_strato_ed25519"
readonly SSH_TARGET="vitamine-deploy@87.106.232.66"

if [[ ! -f "${SSH_KEY}" ]]; then
  osascript -e 'display alert "VitaMine Preview" message "The VitaMine SSH key could not be found." as critical'
  exit 1
fi

if ! curl --fail --silent --max-time 1 "${HEALTH_URL}" >/dev/null 2>&1; then
  if lsof -nP -iTCP:8766 -sTCP:LISTEN >/dev/null 2>&1; then
    osascript -e 'display alert "VitaMine Preview" message "Local port 8766 is already used by another application." as critical'
    exit 1
  fi
  ssh -f -N \
    -i "${SSH_KEY}" \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L 8766:127.0.0.1:8766 \
    "${SSH_TARGET}"
fi

open "${PREVIEW_URL}"
