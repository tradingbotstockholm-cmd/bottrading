#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -x .venv/bin/python ]] || bash setup_mac.sh
[[ -f config.v4.json ]] || cp config.v4.example.json config.v4.json
# SQLite's backup API captures WAL consistently; never copy a live .sqlite alone.
.venv/bin/python - <<'PY'
import json, sqlite3, time
from pathlib import Path
cfg=json.loads(Path('config.v4.json').read_text())
p=Path(cfg['db_path'])
if p.exists():
    backup=p.with_name(p.name+'.backup-'+str(time.time_ns()))
    with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(backup) as dest: src.backup(dest)
    print('Ledger backup:',backup)
PY
.venv/bin/python -m hunter4 dashboard --config config.v4.json &
dashboard_pid=$!
trap 'kill "$dashboard_pid" 2>/dev/null || true' EXIT INT TERM
# Fail if the dashboard process cannot start; do not reuse an unrelated listener.
.venv/bin/python - <<'PY'
import json,time,urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=1) as response:
            assert json.load(response)['mode']=='paper'
        break
    except OSError: time.sleep(.1)
else: raise SystemExit('Paper dashboard did not become ready')
PY
kill -0 "$dashboard_pid"
.venv/bin/python -m hunter4 paper --config config.v4.json
