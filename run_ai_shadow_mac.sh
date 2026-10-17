#!/bin/bash
# Optional pretrained research in a separate process, never sends orders.
set -euo pipefail
cd "$(dirname "$0")"
[[ -x .venv-ai/bin/python ]] || { echo 'First run: bash setup_ai_mac.sh' >&2;exit 1; }
exec .venv-ai/bin/python -m hunter2.ai_shadow --config config.v4.json
