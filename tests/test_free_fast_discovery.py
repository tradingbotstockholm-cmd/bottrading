"""Tests for free-RPC timely research schedule. No live orders or internet."""
import unittest,time,tempfile
from pathlib import Path
from hunter2.db import Database
from hunter2.engine import Settings, free_scout_backoff
from hunter2.discovery import TraderScout
from hunter2.faststream import Launch

A='A'*44
B='B'*44
C='C'*44
D='D'*44

class FreeFastResearchTests(unittest.TestCase):
    def test_bounded_fast_schedule_defaults(self):
        cfg=Settings()
        self.assertEqual(cfg.scout_interval_seconds,30)
        self.assertEqual(cfg.scout_mints_per_cycle,3)
        self.assertFalse(cfg.enable_paid_wallet_stream)
        self.assertFalse(cfg.enable_paid_token_stream)
        cfg.validate()

    def test_public_rpc_failures_backoff_without_slowing_good_scans(self):
        self.assertEqual(free_scout_backoff(30,0,3),30)
        self.assertEqual(free_scout_backoff(30,1,3),30)
        self.assertEqual(free_scout_backoff(30,2,3),60)
        self.assertEqual(free_scout_backoff(30,1,0),90)

    def test_recent_launches_beat_stale_unscanned_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(str(Path(tmp)/'scratch.sqlite'),1000)
            try:
                now=time.time()
                db.remember_mints_batch([Launch(A,D,C),Launch(B,D,C)])
                # Older mint is FOMO-prioritized but is too old for entry timing.
                db.conn.execute('UPDATE candidate_mints SET observed=? WHERE mint=?',(now-900,A))
                db.conn.execute('UPDATE candidate_mints SET observed=? WHERE mint=?',(now-20,B))
                db.conn.execute('INSERT INTO fomo_research_hints VALUES(?,?,?,?,?)',('alert',A,'buy',now-60,now))
                db.conn.commit()
                self.assertEqual(db.scout_targets(1,now,35,7200,12)[0][0],B)
            finally:db.close()

    def test_old_swaps_cannot_be_entry_votes(self):
        from hunter2.discovery import signer_wallets
        self.assertEqual(signer_wallets({'transaction':{'message':{'accountKeys':[
            {'pubkey':A,'signer':False}]}}}),[])

if __name__=='__main__':unittest.main()
