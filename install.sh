#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if ! command -v pipx >/dev/null 2>&1; then
  printf '%s\n' 'Install prerequisites on Kali: sudo apt update && sudo apt install -y git python3 python3-venv pipx' >&2
  exit 1
fi
pipx install --force .
pipx ensurepath
printf '%s\n' 'Q4R installed. Open a new terminal, then run: q4r' 'For this terminal: export PATH="$HOME/.local/bin:$PATH"'
