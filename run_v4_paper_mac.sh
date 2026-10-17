#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -d .venv ]] || { echo 'Run bash setup_mac.sh first'; exit 1; }
source .venv/bin/activate
[[ -f config.v4.json ]] || cp config.v4.example.json config.v4.json
exec python -m hunter4 paper --config config.v4.json
