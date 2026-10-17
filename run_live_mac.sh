#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f live.json && -f config.json ]] || { echo 'Missing live.json/config.json: run setup + python -m hunter3 init'; exit 1; }
[[ -f data/LIVE_ARMED_BY_USER ]] || { echo 'LIVE NOT ARMED. See V3_SETUP.md'; exit 1; }
[[ "${HUNTER_CONFIRM_LIVE:-}" == YES_I_UNDERSTAND_THE_RISK ]] || { echo 'Explicit HUNTER_CONFIRM_LIVE is missing'; exit 1; }
[[ -d .venv ]] || { echo 'Run bash setup_mac.sh first'; exit 1; }
source .venv/bin/activate
if [[ -z "${PUMPPORTAL_API_KEY:-}" ]]; then
  PUMPPORTAL_API_KEY="$(security find-generic-password -a "$USER" -s solana-hunter-pumpportal -w 2>/dev/null)" || { echo 'No API key in Keychain; see V3_SETUP.md'; exit 1; }
  export PUMPPORTAL_API_KEY
fi
exec python -m hunter3 run-live --scanner-config config.json --live-config live.json
