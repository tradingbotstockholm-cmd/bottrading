#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f config.json ]] || cp config.example.json config.json
[[ -d .venv ]] || { echo 'Run bash setup_mac.sh first';exit 1; }
source .venv/bin/activate
python -m hunter3 status
exec python -m hunter2 run --config config.json
