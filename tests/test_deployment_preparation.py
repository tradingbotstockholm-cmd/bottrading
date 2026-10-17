"""Local-only migration, consistent backups and rollback preservation tests."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hunter2.migrate_paper_config import migrate, proposed_config, MigrationError
from hunter2.prepare_paper_update import prepare, PreparationError

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.config=self.root/'config.v4.json'
        self.raw={'assumed_slippage_pct':.01,'max_paper_slippage_pct':.0075,
                  'min_liquidity_usd':100,'max_open_positions':200,'mode':'heavy',
                  'tracked_wallets':['4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU'],
                  'db_path':'data/my-history.sqlite','stop_loss_pct':.12,
                  'solana_rpc_url':'https://rpc.invalid/?api-key=PRIVATE_TEST_PLACEHOLDER'}
        self.original=json.dumps(self.raw).encode();self.config.write_bytes(self.original)
    def test_dry_run_does_not_touch_config_or_create_backup(self):
        result=migrate(self.config)
        self.assertFalse(result['applied']);self.assertEqual(self.config.read_bytes(),self.original)
        self.assertFalse((self.root/'.paper-config-backups').exists())
        self.assertNotIn('PRIVATE_TEST_PLACEHOLDER',json.dumps(result))
    def test_only_legacy_slippage_changes_and_original_backup_is_exact(self):
        result=migrate(self.config,apply=True)
        expected=self.raw|{'assumed_slippage_pct':.0035}
        self.assertEqual(json.loads(self.config.read_text()),expected)
        self.assertEqual(Path(result['backup']).read_bytes(),self.original)
        self.assertEqual(os.stat(result['backup']).st_mode&0o777,0o600)
        self.assertEqual(os.stat(self.config).st_mode&0o777,0o600)
        self.assertFalse(migrate(self.config,apply=True)['applied'])
        self.assertEqual(len(list((self.root/'.paper-config-backups').iterdir())),1)
    def test_implicit_ceiling_and_compatible_custom_values_are_preserved(self):
        raw=dict(self.raw);raw.pop('max_paper_slippage_pct')
        self.assertEqual(proposed_config(raw),raw|{'assumed_slippage_pct':.0035})
        raw['assumed_slippage_pct']=.002;raw['max_paper_slippage_pct']=.006
        self.assertEqual(proposed_config(raw),raw)
    def test_custom_conflicts_live_fields_paid_streams_and_nonfinite_values_refuse(self):
        for updates in [{'assumed_slippage_pct':.02},{'max_paper_slippage_pct':.005},
                        {'enabled':True},{'enable_paid_token_stream':True},
                        {'assumed_slippage_pct':float('nan')},{'max_paper_slippage_pct':True}]:
            with self.subTest(updates=updates):
                self.config.write_text(json.dumps(self.raw|updates))
                before=self.config.read_bytes()
                with self.assertRaises(MigrationError):migrate(self.config,apply=True)
                self.assertEqual(self.config.read_bytes(),before)
    def test_write_failure_keeps_original_and_backup(self):
        with patch('hunter2.migrate_paper_config.os.replace',side_effect=OSError('injected')):
            with self.assertRaises(OSError):migrate(self.config,apply=True)
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertEqual(next((self.root/'.paper-config-backups').iterdir()).read_bytes(),self.original)
    def test_symlink_refuses_without_changing_target(self):
        link=self.root/'link.json';link.symlink_to(self.config)
        with self.assertRaises(MigrationError):migrate(link,apply=True)
        self.assertEqual(self.config.read_bytes(),self.original)

class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.old=self.root/'solana_hunter_v4';self.old.mkdir()
        self.staged=self.root/'staged';self.staged.mkdir();self.backups=self.root/'backups'
        self.raw={'assumed_slippage_pct':.01,'min_liquidity_usd':100,'max_open_positions':200,
                  'db_path':'data/paper.sqlite','tracked_wallets':[]}
        (self.old/'config.v4.json').write_text(json.dumps(self.raw))
        (self.old/'live.json').write_text(json.dumps({'enabled':True,'kill_switch_path':'data/CUSTOM_STOP','max_single_buy_sol':.01}))
        (self.old/'data').mkdir();(self.old/'data/wallet-research.json').write_text('{"keep":"original"}')
        (self.old/'private-local-file.txt').write_text('KEEP_LOCAL_ONLY_TEST_PLACEHOLDER')
        self.db=sqlite3.connect(self.old/'data/paper.sqlite');self.addCleanup(self.db.close)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('CREATE TABLE history(id INTEGER PRIMARY KEY,value TEXT)')
        self.db.execute("INSERT INTO history(value) VALUES('do not erase')");self.db.commit()
        # A tiny local Git checkout suffices to test revision pinning and ignores.
        self.git('init','-q')
        (self.staged/'.gitignore').write_text('data/\nconfig*.json\nlive.json\naccounts.json\n.paper-config-backups/\n')
        (self.staged/'source.py').write_text('# replacement code\n')
        self.git('add','.');self.git('-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','fixture')
        self.sha=self.git('rev-parse','HEAD').strip()
    def git(self,*args):return subprocess.check_output(['git','-C',str(self.staged),*args],text=True)
    def prepare(self,**kwargs):
        return prepare(self.old,self.staged,self.backups,self.sha,confirmed_stopped=True,**kwargs)
    def test_consistent_wal_backup_state_copy_original_and_rollback_are_preserved(self):
        original=(self.old/'config.v4.json').read_bytes()
        result=self.prepare()
        self.assertFalse(result['activated']);self.assertFalse(result['live_enabled'])
        self.assertEqual((self.old/'config.v4.json').read_bytes(),original)
        self.assertTrue(json.loads((self.old/'live.json').read_text())['enabled'])
        self.assertEqual(json.loads((self.staged/'config.v4.json').read_text()),self.raw|{'assumed_slippage_pct':.0035})
        self.assertFalse(json.loads((self.staged/'live.json').read_text())['enabled'])
        self.assertEqual(json.loads((self.staged/'live.json').read_text())['max_single_buy_sol'],.01)
        for name in ['STOP_ALL_LIVE_TRADING','CUSTOM_STOP']:self.assertTrue((self.staged/'data'/name).exists())
        self.assertEqual((self.staged/'data/wallet-research.json').read_bytes(),(self.old/'data/wallet-research.json').read_bytes())
        with sqlite3.connect(self.staged/'data/paper.sqlite') as db:
            self.assertEqual(db.execute('SELECT value FROM history').fetchall(),[('do not erase',)])
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
        backup=Path(result['backup'])
        self.assertEqual((backup/'project/private-local-file.txt').read_bytes(),(self.old/'private-local-file.txt').read_bytes())
        self.assertFalse((self.staged/'private-local-file.txt').exists())
        with sqlite3.connect(backup/'sqlite/data/paper.sqlite') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0],1)
        self.assertEqual(self.git('status','--porcelain'),'')
        # Rollback: keep the prepared tree, restore the untouched original by rename.
        preserved=self.root/'previous-original';self.old.rename(preserved)
        self.staged.rename(self.old);self.old.rename(self.root/'failed-update');preserved.rename(self.old)
        self.assertEqual((self.old/'config.v4.json').read_bytes(),original)
        self.assertTrue((self.root/'failed-update/data/paper.sqlite').exists())
    def test_wrong_revision_or_running_process_confirmation_refuses_before_backup(self):
        with self.assertRaises(PreparationError):prepare(self.old,self.staged,self.backups,'0'*40,confirmed_stopped=True)
        with self.assertRaises(PreparationError):prepare(self.old,self.staged,self.backups,self.sha)
        self.assertFalse(self.backups.exists())
    def test_external_ledger_and_symlinked_data_are_refused(self):
        (self.old/'config.v4.json').write_text(json.dumps(self.raw|{'db_path':'../outside.sqlite'}))
        with self.assertRaises(PreparationError):self.prepare()
        (self.old/'config.v4.json').write_text(json.dumps(self.raw))
        (self.old/'data/external').symlink_to(self.root)
        with self.assertRaises(PreparationError):self.prepare()
        self.assertFalse(self.backups.exists())
    def test_existing_staged_state_is_not_overwritten(self):
        (self.staged/'config.v4.json').write_text('original staged state')
        with self.assertRaises(PreparationError):self.prepare()
        self.assertEqual((self.staged/'config.v4.json').read_text(),'original staged state')
        self.assertFalse(self.backups.exists())

if __name__=='__main__':unittest.main()
