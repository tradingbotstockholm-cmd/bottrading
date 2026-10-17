#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f config.v4.json ]] || cp config.v4.example.json config.v4.json
source .venv/bin/activate
exec python -m hunter4 dashboard --config config.v4.json --port 8765
