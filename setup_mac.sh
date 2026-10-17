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
# Preserve any existing ledger before the status command adds schema tables.
python - <<'BACKUP'
import json,sqlite3,time
from pathlib import Path
cfg=json.loads(Path('config.json').read_text())
p=Path(cfg.get('db_path','data/hunter2.sqlite'))
if p.exists():
    target=p.with_name(p.name+'.backup-'+str(time.time_ns()))
    with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dest:
        src.backup(dest)
    print('Ledger backup:',target)
BACKUP
python -m unittest discover -s tests -v
python -m hunter2 status --config config.json
printf '\nSetup complete. PAPER ONLY. Review README.md and config.json.\n'
