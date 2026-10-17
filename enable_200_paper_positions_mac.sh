#!/bin/bash
# Explicit PAPER ONLY position-capacity opt-in. Does not enable trading gateways.
set -euo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${HUNTER_TEST_PYTHON:-$PROJECT/.venv/bin/python}"
[[ -x "$PY" && -f "$PROJECT/config.v4.json" ]] || { echo 'Missing original project config or Python environment'; exit 1; }
if command -v pgrep >/dev/null && pgrep -f 'python.*-m hunter4 paper' >/dev/null; then
  echo 'Stop the paper scanner with Control+C before changing capacity.' >&2;exit 1
fi
cd "$PROJECT"
"$PY" - <<'PYCODE'
import json,time,os,tempfile
from pathlib import Path
from hunter2.engine import Settings
path=Path('config.v4.json')
original=path.read_bytes()
raw=json.loads(original)
assert not raw.get('enable_paid_wallet_stream',False)
assert not raw.get('enable_paid_token_stream',False)
raw.update(max_open_positions=200,max_position_checks_per_cycle=40,quote_parallelism=4,
           max_total_exposure_pct=.75)
Settings(**raw).validate()
backup=path.with_name(f'config.v4.json.before_capacity_{time.strftime("%Y%m%d-%H%M%S")}')
backup.write_bytes(original)
fd,tmp=tempfile.mkstemp(prefix='.capacity-',suffix='.json',dir=path.parent)
try:
    with os.fdopen(fd,'w') as f:
        json.dump(raw,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
print('Paper-only position capacity: 200 (NOT real orders or guaranteed fills)')
print('Aggregate cost-basis exposure cap: 75% of marked equity')
print('Public-data quote monitoring: 40 positions/cycle, at most 4 concurrent requests')
print('Backup:',backup)
PYCODE
