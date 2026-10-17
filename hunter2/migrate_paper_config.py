"""Explicit paper-config migration. No network, live config, or ledger writes.

Dry-run is the default. Only the historical 1% / 0.75% conflict is rewritten;
all other user fields are preserved. Backups may contain private endpoint URLs,
so they remain local with restrictive permissions and are never printed.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import uuid

from .engine import Settings

class MigrationError(ValueError):
    pass

def proposed_config(raw):
    if not isinstance(raw,dict):raise MigrationError('Paper config must be a JSON object')
    names=set(Settings.__dataclass_fields__)
    if set(raw)-names:raise MigrationError('Unknown/non-paper config fields; file was not changed')
    result=dict(raw)
    old=raw.get('assumed_slippage_pct',Settings.assumed_slippage_pct)
    ceiling=raw.get('max_paper_slippage_pct',Settings.max_paper_slippage_pct)
    if (isinstance(old,bool) or isinstance(ceiling,bool) or not isinstance(old,(float,int))
            or not isinstance(ceiling,(float,int)) or not math.isfinite(old) or not math.isfinite(ceiling)):
        raise MigrationError('Slippage settings must be finite numbers')
    if old>=ceiling:
        if math.isclose(old,.01,rel_tol=0,abs_tol=1e-12) and math.isclose(ceiling,.0075,rel_tol=0,abs_tol=1e-12):
            result['assumed_slippage_pct']=.0035
        else:
            raise MigrationError('Custom slippage conflict: review it explicitly; no settings changed')
    if result.get('enable_paid_wallet_stream',False) or result.get('enable_paid_token_stream',False):
        raise MigrationError('Paid streams are enabled; resolve explicitly for this free paper-only workflow')
    try:Settings(**result).validate()
    except Exception:
        # Do not echo a validator exception containing user config values.
        raise MigrationError('Proposed config failed Settings validation; file was not changed') from None
    return result

def migrate(path,*,apply=False):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise MigrationError('Use a regular paper config file, not a symlink')
    original=path.read_bytes()
    try:raw=json.loads(original)
    except (ValueError,UnicodeError):raise MigrationError('Invalid JSON; file was not changed') from None
    proposed=proposed_config(raw)
    changes={k:{'before':raw.get(k),'after':v} for k,v in proposed.items() if raw.get(k)!=v}
    result={'mode':'PAPER ONLY','applied':False,'changes':changes,'backup':None}
    if not apply or not changes:return result
    backup_dir=path.parent/'.paper-config-backups'
    if backup_dir.is_symlink():raise MigrationError('Backup directory must not be a symlink')
    backup_dir.mkdir(mode=0o700,exist_ok=True)
    if not stat.S_ISDIR(backup_dir.stat().st_mode):raise MigrationError('Invalid backup directory')
    os.chmod(backup_dir,0o700)
    backup=backup_dir/(path.name+'.backup-'+uuid.uuid4().hex)
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(original);stream.flush();os.fsync(stream.fileno())
    fd,temporary=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as stream:
            json.dump(proposed,stream,indent=2,allow_nan=False);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        if path.is_symlink() or path.read_bytes()!=original:
            raise MigrationError('Config changed during migration; original was not overwritten')
        os.replace(temporary,path)
    finally:
        if Path(temporary).exists():Path(temporary).unlink()
    result.update(applied=True,backup=str(backup))
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config.v4.json')
    parser.add_argument('--paper-only',action='store_true',required=True)
    parser.add_argument('--apply',action='store_true',help='Back up and atomically apply; default is dry-run')
    args=parser.parse_args()
    try:result=migrate(args.config,apply=args.apply)
    except MigrationError as exc:
        parser.exit(1,'Paper migration refused: '+str(exc)+'\n')
    except OSError:
        parser.exit(1,'Paper migration refused: file/backup access failed; ledger was not written.\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
