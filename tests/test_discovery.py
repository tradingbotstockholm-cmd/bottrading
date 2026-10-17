"""Offline regression tests for bounded, persistent, fail-closed wallet discovery."""
from __future__ import annotations
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hunter2.db import Database
from hunter2.discovery import TraderScout, signer_wallets

M1='A'*44
M2='B'*44
C1='C'*44
C2='G'*44
CREATOR='D'*44
TRADER='E'*44
TRANSFEREE='F'*44

class RPC:
    def __init__(self):
        self.calls=[]
        self.txs={
            'one':{'mint':M1,'transaction':{'message':{'accountKeys':[{'pubkey':TRADER,'signer':True}]}}},
            'two':{'mint':M1,'transaction':{'message':{'accountKeys':[{'pubkey':TRADER,'signer':True}]}}},
            'three':{'mint':M2,'transaction':{'message':{'accountKeys':[{'pubkey':TRADER,'signer':True}]}}},
        }
        self.by_curve={C1:['one','two'],C2:['three']}
    def signatures(self,key,limit):
        self.calls.append(('signatures',key))
        return [{'signature':sig,'err':None} for sig in self.by_curve[key]][:limit]
    def transaction(self,sig):
        self.calls.append(('transaction',sig))
        return self.txs[sig]

def parse(tx,wallet,sig,now):
    if wallet != TRADER:return []
    return [SimpleNamespace(mint=tx['mint'],side='buy')]

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'ledger.sqlite')
        self.db=Database(self.path,500)
        self.addCleanup(self.db.close)
        self.db.remember_mint(M1,C1)
        self.db.remember_mint(M2,C2)
        self.db.remember_creator(M1,CREATOR)
        self.db.remember_creator(M2,CREATOR)
        self.db.conn.execute('UPDATE candidate_mints SET observed=?',(time.time()-120,))
        self.db.conn.commit()
        self.rpc=RPC()
        self.scout=TraderScout(self.rpc,self.db,parse,minimum_buys=3,minimum_mints=2,
                               rescan_seconds=120)

    def test_real_signer_only(self):
        tx={'transaction':{'message':{'accountKeys':[
            {'pubkey':TRADER,'signer':True},
            {'pubkey':TRANSFEREE,'signer':False},
            {'pubkey':CREATOR,'signer':True},
            {'pubkey':'not-a-solana-address','signer':True}]}}}
        self.assertEqual(signer_wallets(tx),[TRADER,CREATOR])

    def test_promotes_after_evidence_across_multiple_mints(self):
        result=self.scout.run_cycle(max_mints=2)
        self.assertEqual(result.mints_scanned,2)
        self.assertEqual(result.confirmed_trades,3)
        self.assertEqual(self.scout.candidates(),[TRADER])
        self.assertEqual(self.db.snapshot()['cash'],500)
        self.assertEqual(self.db.recent_fills(),[])

    def test_duplicate_replay_not_counted(self):
        self.scout.run_cycle(max_mints=2)
        count=self.db.conn.execute('SELECT COUNT(*) FROM scout_trades').fetchone()[0]
        self.scout.run_cycle(max_mints=2,now=time.time()+150)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_trades').fetchone()[0],count)

    def test_database_survives_restart(self):
        self.scout.run_cycle(max_mints=2)
        fresh=Database(self.path,99999)
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.state()['cash'],500)
        self.assertEqual(fresh.scout_candidates(3,2,10),[TRADER])

    def test_never_promote_known_creator(self):
        self.rpc.txs['one']['transaction']['message']['accountKeys'].append({'pubkey':CREATOR,'signer':True})
        # Parser fixture pretends creator may trade. Exclusion must happen first.
        self.scout.parse_trade=lambda tx,wallet,sig,now: [SimpleNamespace(mint=tx['mint'],side='buy')]
        self.scout.run_cycle(max_mints=2)
        self.assertNotIn(CREATOR,self.scout.candidates())
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_trades WHERE wallet=?',(CREATOR,)).fetchone()[0],0)

    def test_rpc_failure_not_marked_seen(self):
        self.rpc.transaction=lambda sig: (_ for _ in ()).throw(RuntimeError('RPC unavailable'))
        self.scout.run_cycle(max_mints=2)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_signatures').fetchone()[0],0)
        self.assertNotIn(TRADER,self.scout.candidates())

    def test_budget_prevents_runaway_requests(self):
        self.scout.tx_budget=1
        s=self.scout.run_cycle(max_mints=2)
        self.assertLessEqual(s.transactions_checked,1)
        self.assertEqual(len([x for x in self.rpc.calls if x[0]=='transaction']),1)
        self.assertNotIn(TRADER,self.scout.candidates())

    def test_does_not_scan_newborn_token_before_buys_can_develop(self):
        self.db.conn.execute('UPDATE candidate_mints SET observed=?',(time.time(),))
        self.db.conn.commit()
        self.assertEqual(self.scout.run_cycle(max_mints=2).mints_scanned,0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_signatures').fetchone()[0],0)

    def test_budget_does_not_mark_uninspected_mints_scanned(self):
        self.scout.tx_budget=1
        stats=self.scout.run_cycle(max_mints=2)
        self.assertTrue(stats.budget_exhausted)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_targets WHERE last_scanned>0').fetchone()[0],0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_signatures').fetchone()[0],1)

    def test_rpc_errors_are_counted_without_erasing_progress(self):
        self.rpc.transaction=lambda sig: (_ for _ in ()).throw(RuntimeError('RPC unavailable'))
        stats=self.scout.run_cycle(max_mints=2)
        self.assertEqual(stats.rpc_errors,2)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_targets WHERE last_scanned>0').fetchone()[0],0)

    def test_no_destructive_schema_migration(self):
        self.db.observe(M1,'existing_record','kept')
        another=Database(self.path,2000)
        self.addCleanup(another.close)
        self.assertEqual(another.conn.execute("SELECT detail FROM observations WHERE kind='existing_record'").fetchone()[0],'kept')

    def test_wallet_followup_finds_independent_buy_history(self):
        now=time.time()
        self.db.record_scout_trade(TRADER,M1,'buy','one',now)
        self.db.record_scout_trade(TRADER,M1,'buy','two',now)
        self.rpc.by_curve[TRADER]=['history']
        self.rpc.txs['history']={'mint':M2,'transaction':{'message':{
            'accountKeys':[{'pubkey':TRADER,'signer':True}]}}}
        stats=self.scout.run_wallet_followup(max_wallets=1,now=now)
        self.assertEqual(stats.wallets_checked,1)
        self.assertEqual(stats.confirmed_trades,1)
        self.assertEqual(self.scout.candidates(),[TRADER])
        self.assertEqual(self.db.recent_fills(),[])
        self.assertFalse(self.db.has_wallet_checkpoint(TRADER))
        self.assertFalse(self.db.has_seen(TRADER,'history'))

    def test_wallet_followup_deduplicates_repeated_rpc_signatures(self):
        now=time.time()
        self.db.record_scout_trade(TRADER,M1,'buy','one',now)
        self.rpc.by_curve[TRADER]=['history']
        self.rpc.txs['history']={'mint':M2,'transaction':{'message':{
            'accountKeys':[{'pubkey':TRADER,'signer':True}]}}}
        self.scout.run_wallet_followup(now=now)
        first=self.db.conn.execute('SELECT COUNT(*) FROM scout_trades').fetchone()[0]
        self.assertEqual(first,2)
        self.scout.run_wallet_followup(now=now+1300)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_trades').fetchone()[0],first)

    def test_wallet_followup_does_not_count_unsigned_interactions(self):
        now=time.time()
        self.db.record_scout_trade(TRADER,M1,'buy','one',now)
        self.rpc.by_curve[TRADER]=['history']
        self.rpc.txs['history']={'mint':M2,'transaction':{'message':{
            'accountKeys':[{'pubkey':TRADER,'signer':False}]}}}
        stats=self.scout.run_wallet_followup(now=now)
        self.assertEqual(stats.confirmed_trades,0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_trades').fetchone()[0],1)

    def test_wallet_followup_rpc_failure_retries_without_disrupting_live_checkpoint(self):
        now=time.time()
        self.db.record_scout_trade(TRADER,M1,'buy','one',now)
        self.rpc.by_curve[TRADER]=['history']
        self.rpc.transaction=lambda sig: (_ for _ in ()).throw(RuntimeError('RPC unavailable'))
        stats=self.scout.run_wallet_followup(now=now)
        self.assertEqual(stats.rpc_errors,1)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM scout_wallet_signatures').fetchone()[0],0)
        self.assertFalse(self.db.has_wallet_checkpoint(TRADER))
        self.assertEqual(self.db.state()['cash'],500)

    def test_wallet_followup_budget_retains_unprocessed_signatures(self):
        now=time.time()
        self.db.record_scout_trade(TRADER,M1,'buy','one',now)
        self.rpc.by_curve[TRADER]=['history','two']
        self.rpc.txs['history']={'mint':M2,'transaction':{'message':{
            'accountKeys':[{'pubkey':TRADER,'signer':True}]}}}
        self.scout.wallet_tx_budget=1
        stats=self.scout.run_wallet_followup(now=now)
        self.assertEqual(stats.transactions_checked,1)
        self.assertTrue(stats.budget_exhausted)
        self.assertFalse(self.db.scout_wallet_signature_seen(TRADER,'two'))

if __name__=='__main__':unittest.main()
