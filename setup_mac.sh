#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then echo 'Install Python 3.11+ first.'; exit 1; fi
python3 - <<'PY'
import sys
if sys.version_info<(3,11):
    raise SystemExit('Python 3.11+ required')
PY
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
if [[ ! -f config.json ]]; then cp config.example.json config.json; fi
python -m unittest discover -s tests -v
python -m hunter2 status --config config.json
printf '\nSetup complete. PAPER ONLY. Review README.md and config.json.\n'
