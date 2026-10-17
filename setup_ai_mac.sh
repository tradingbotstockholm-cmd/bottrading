#!/bin/bash
# Optional install: a separate virtualenv so we cannot damage the running bot.
set -euo pipefail
cd "$(dirname "$0")"
[[ -x .venv/bin/python ]] || { echo 'Main bot .venv not found' >&2; exit 1; }
.venv/bin/python -m venv .venv-ai
.venv-ai/bin/python -m pip install 'chronos-forecasting>=2.0,<3'
.venv-ai/bin/python -c 'from chronos import BaseChronosPipeline; import torch; print("Chronos CPU environment ready")'
echo 'AI installed (weights download on first run). Nothing changed in the normal trading environment.'
