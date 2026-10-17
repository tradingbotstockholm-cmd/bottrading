#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f config.json ]] || cp config.example.json config.json
[[ -d .venv ]] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -q -r requirements.txt
exec python -m hunter2 run --config config.json
