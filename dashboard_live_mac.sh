#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -d .venv ]] || { echo 'Run bash setup_mac.sh first'; exit 1; }
source .venv/bin/activate
exec python -m hunter3 dashboard --port 8766
