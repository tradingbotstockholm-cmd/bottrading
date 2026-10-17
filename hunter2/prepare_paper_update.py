"""Prepare a backed-up paper-only update in a separate clean checkout.

Never activates the update or deletes/writes the original installation. Stop all
old processes before running. Backup folders can contain secrets: keep them local.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import uuid

from .migrate_paper_config import migrate, proposed_config, MigrationError

class PreparationError(ValueError):pass

def within(path,parent):return path==parent or parent in path.parents

def relative_path(value,root):
    path=Path(value)
    if path.is_absolute() or not within((root/path).resolve(),root) or '\n' in value or '\r' in value:
        raise PreparationError('External/escaping state path; back it up explicitly before using this updater')
    return path

def private_json(path,value):
    with path.open('x',encoding='utf8') as f:
        os.chmod(path,0o600);json.dump(value,f,indent=2);f.write('\n')

def prepare(existing,staged,backup_root,expected_commit,*,confirmed_stopped=False):
    if not confirmed_stopped:raise PreparationError('Stop all scanners and dashboards first')
    existing,staged,backup_root=map(lambda p:Path(p).expanduser().resolve(),(existing,staged,backup_root))
    if any(within(a,b) or within(b,a) for a,b in [(existing,staged),(existing,backup_root),(staged,backup_root)]):
        raise PreparationError('Existing project, staged checkout and backup root must be separate siblings')
    config=existing/'config.v4.json'
    if config.is_symlink() or not config.is_file():raise PreparationError('A regular existing config.v4.json is required')
    raw=json.loads(config.read_text());proposed_config(raw)
    db_path=relative_path(raw.get('db_path','data/hunter2.sqlite'),existing)
    if any((staged/name).exists() or (staged/name).is_symlink() for name in ['config.v4.json','config.json','accounts.json','live.json','data']):
        raise PreparationError('Staged checkout already contains local state; use a fresh clone')
    if not (staged/'.git').is_dir():raise PreparationError('Staged directory must be a regular Git checkout')
    sha=subprocess.check_output(['git','-C',str(staged),'rev-parse','--verify','HEAD^{commit}'],text=True).strip()
    if sha!=expected_commit:raise PreparationError('Staged commit does not match the pinned repair revision')
    for args in [('diff','--quiet'),('diff','--cached','--quiet')]:
        if subprocess.run(['git','-C',str(staged),*args]).returncode:raise PreparationError('Staged tracked files are modified')
    # All copied state must be ignored, including nonstandard database paths.
    for name in ['config.v4.json','live.json','accounts.json','data/__state__',db_path.as_posix()]:
        if subprocess.run(['git','-C',str(staged),'check-ignore','--quiet',name]).returncode:
            raise PreparationError('Staged Git ignores do not protect all private state paths')
    live=None;kill_path=Path('data/STOP_ALL_LIVE_TRADING')
    if (existing/'live.json').exists():
        if (existing/'live.json').is_symlink():raise PreparationError('Live config must not be a symlink')
        live=json.loads((existing/'live.json').read_text())
        kill_path=relative_path(live.get('kill_switch_path','data/STOP_ALL_LIVE_TRADING'),existing)
        live_db=relative_path(live.get('db_path','data/live_orders.sqlite'),existing)
        if live_db==db_path:raise PreparationError('Paper and live ledgers must have separate paths')
        if subprocess.run(['git','-C',str(staged),'check-ignore','--quiet',live_db.as_posix()]).returncode:
            raise PreparationError('Staged Git ignores do not protect the live ledger path')
    data=existing/'data'
    # Reject state symlinks instead of silently leaving an external ledger unbacked.
    if data.is_symlink():raise PreparationError('Data directory symlinks require an explicit external-state backup')
    for directory,dirs,files in os.walk(data,onerror=lambda e:(_ for _ in ()).throw(e)):
        if any((Path(directory)/name).is_symlink() for name in dirs+files):
            raise PreparationError('Data symlinks require an explicit external-state backup')
    if (staged/db_path).exists():raise PreparationError('Staged ledger already exists')
    if backup_root.is_symlink():raise PreparationError('Backup root must not be a symlink')
    backup_root.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(backup_root,0o700)
    backup=backup_root/('paper-update-'+uuid.uuid4().hex)
    backup.mkdir(mode=0o700)
    # Preserve code, configuration, research, secrets and virtualenv for rollback.
    # The caller confirms processes stopped; no racing write is assumed safe.
    shutil.copytree(existing,backup/'project',symlinks=True)
    databases=set()
    for directory,_,files in os.walk(data,onerror=lambda e:(_ for _ in ()).throw(e)):
        for name in files:
            path=Path(directory)/name
            with path.open('rb') as f:
                if f.read(16)==b'SQLite format 3\0':databases.add(path.relative_to(existing))
    primary=existing/db_path
    if primary.exists():
        with primary.open('rb') as f:
            if f.read(16)!=b'SQLite format 3\0':raise PreparationError('Configured ledger is not SQLite')
        databases.add(db_path)
    # A custom relative live ledger outside data/ is backed up as well.
    if live:
        live_db=relative_path(live.get('db_path','data/live_orders.sqlite'),existing)
        if (existing/live_db).exists():databases.add(live_db)
    for relative in databases:
        snapshot=backup/'sqlite'/relative;snapshot.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect((existing/relative).as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(snapshot) as dest:
            source.backup(dest)
            if dest.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise PreparationError('SQLite backup integrity check failed')
        os.chmod(snapshot,0o600)
    if data.exists():shutil.copytree(data,staged/'data')
    for relative in databases:
        dest=staged/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        if not within(dest.resolve(),staged):raise PreparationError('Unsafe staged ledger path')
        shutil.copy2(backup/'sqlite'/relative,dest)
        # Remove ONLY staged sidecars after installing a consolidated snapshot.
        for suffix in ['-wal','-shm']:
            sidecar=Path(str(dest)+suffix)
            if sidecar.exists():sidecar.unlink()
    shutil.copy2(config,staged/'config.v4.json');os.chmod(staged/'config.v4.json',0o600)
    result=migrate(staged/'config.v4.json',apply=True)
    if live:
        live['enabled']=False
        private_json(staged/'live.json',live)
    else:private_json(staged/'live.json',{'enabled':False})
    for name in ['accounts.json','config.json']:
        source=existing/name
        if source.is_file() and not source.is_symlink():shutil.copy2(source,staged/name);os.chmod(staged/name,0o600)
    for relative in [Path('data/STOP_ALL_LIVE_TRADING'),kill_path]:
        target=staged/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            with target.open('x') as f:f.write('PAPER ONLY update: real-money execution is disabled.\n')
            os.chmod(target,0o600)
    manifest={'original':str(existing),'staged':str(staged),'backup':str(backup),'commit':sha,
              'sqlite_snapshots':sorted(p.as_posix() for p in databases),'config_migration':result,
              'live_enabled':False,'activated':False}
    private_json(backup/'manifest.json',manifest)
    return manifest

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--existing',required=True);parser.add_argument('--staged',default='.')
    parser.add_argument('--backup-root',required=True);parser.add_argument('--expected-commit',required=True)
    parser.add_argument('--confirm-stopped',action='store_true',required=True)
    args=parser.parse_args()
    try:result=prepare(args.existing,args.staged,args.backup_root,args.expected_commit,confirmed_stopped=args.confirm_stopped)
    except (PreparationError,MigrationError,OSError,ValueError,subprocess.SubprocessError,sqlite3.Error) as exc:
        # Do not print config, private endpoint URLs or credential-bearing errors.
        parser.exit(1,'Preparation failed ('+type(exc).__name__+'). Original project was not overwritten; retain any partial backup and use a fresh staging clone.\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
