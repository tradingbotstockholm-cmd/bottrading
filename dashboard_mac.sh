#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f config.json ]] || cp config.example.json config.json
source .venv/bin/activate
exec python -m hunter2 dashboard --config config.json --port 8765
