"""Offline guarantees for research-only dual-stream ingestion."""
import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from hunter2.db import Database
from hunter2.engine import Hunter, Settings, load_settings
from hunter2.faststream import Launch, fomo_event, launch_event

A='A'*44
B='B'*44
C='C'*44
D='D'*44


class FastStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Database(str(Path(self.tmp.name)/'ledger.sqlite'),1000)
        self.addCleanup(self.db.close)

    def test_pump_creation_only(self):
        event={'mint':A,'txType':'create','bondingCurveKey':B,'traderPublicKey':C}
        self.assertEqual(launch_event(event),Launch(A,B,C))
        self.assertIsNone(launch_event({**event,'txType':'buy'}))
        self.assertIsNone(launch_event({**event,'mint':'garbage'}))

    def test_batch_retains_original_observation_and_creator(self):
        self.db.remember_mints_batch([Launch(A,B,C)])
        observed=self.db.conn.execute('SELECT observed FROM candidate_mints WHERE mint=?',(A,)).fetchone()[0]
        self.db.remember_mints_batch([Launch(A,'',''),Launch(D,B,'')])
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM candidate_mints').fetchone()[0],2)
        self.assertEqual(self.db.conn.execute('SELECT observed FROM candidate_mints WHERE mint=?',(A,)).fetchone()[0],observed)
        self.assertEqual(self.db.conn.execute('SELECT curve FROM candidate_mints WHERE mint=?',(A,)).fetchone()[0],B)
        self.assertTrue(self.db.is_known_creator(C))
        self.assertEqual(self.db.state()['cash'],1000)
        self.assertEqual(self.db.recent_fills(),[])

    def test_fomo_hint_requires_fresh_solana_buy_or_sell(self):
        now=1_800_000_000
        msg={'type':'alert','alertType':'buy','chain':'solana','tokenAddress':A,
             'trader':'some-handle','ts':(now-60)*1000,'eventId':'id_123'}
        hint=fomo_event(msg,now=now)
        self.assertEqual(hint.mint,A)
        self.assertEqual(hint.event_id,'id_123')
        self.assertIsNone(fomo_event({**msg,'chain':'robinhood'},now=now))
        self.assertIsNone(fomo_event({**msg,'ts':(now-121)*1000},now=now))
        self.assertIsNone(fomo_event({**msg,'ts':(now+12)*1000},now=now))
        self.assertIsNone(fomo_event({**msg,'tokenAddress':'fake'},now=now))
        self.assertIsNone(fomo_event({**msg,'replay':True},now=now))
        self.assertIsNone(fomo_event({**msg,'type':'welcome'},now=now))

    def test_fomo_hints_dedupe_without_converting_to_wallet_vote(self):
        now=time.time()
        msg={'type':'alert','alertType':'buy','chain':'solana','tokenAddress':A,
             'trader':'someone','ts':now-60,'eventId':'constant'}
        hint=fomo_event(msg,now=now)
        self.assertTrue(self.db.remember_fomo_hint(hint))
        self.assertFalse(self.db.remember_fomo_hint(hint))
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM fomo_research_hints').fetchone()[0],1)
        self.assertEqual(self.db.scout_candidates(3,2,30),[])
        self.assertEqual(self.db.recent_fills(),[])

    def test_fomo_hints_prioritize_existing_launches_not_unknown_tokens(self):
        now=time.time()
        self.db.remember_mints_batch([Launch(A,B,C),Launch(D,B,C)])
        self.db.conn.execute('UPDATE candidate_mints SET observed=? WHERE mint=?',(now-121,A))
        self.db.conn.execute('UPDATE candidate_mints SET observed=? WHERE mint=?',(now-120,D))
        self.db.conn.commit()
        self.assertEqual(self.db.scout_targets(1,now,180,7200)[0][0],D)
        hint=fomo_event({'type':'alert','alertType':'buy','chain':'solana','tokenAddress':A,
                         'trader':'someone','ts':now-60,'eventId':'priority'},now=now)
        self.db.remember_fomo_hint(hint)
        self.assertEqual(self.db.scout_targets(1,now,180,7200)[0][0],A)
        self.assertEqual(self.db.state()['cash'],1000)

    def test_engine_launch_queue_does_not_block_and_drops_are_visible(self):
        async def run():
            cfg=Settings(db_path=str(Path(self.tmp.name)/'engine.sqlite'),market_queue_limit=1024,
                         auto_discover_traders=False,enable_fomo_research_feed=False)
            hunter=Hunter(cfg)
            try:
                for i in range(1100):
                    await hunter.enqueue_market_event({'txType':'create','mint':A,'traderPublicKey':C})
                self.assertEqual(hunter.market_received,1100)
                self.assertEqual(hunter.market_queue.qsize(),1024)
                self.assertEqual(hunter.market_dropped,76)
                runner=asyncio.create_task(hunter.market_ingest_loop())
                try:
                    await asyncio.wait_for(hunter.market_queue.join(),timeout=4)
                finally:
                    runner.cancel()
                    try:await runner
                    except asyncio.CancelledError:pass
                self.assertEqual(hunter.db.conn.execute('SELECT COUNT(*) FROM candidate_mints').fetchone()[0],1)
                self.assertEqual(hunter.db.recent_fills(),[])
            finally:
                hunter.db.close()
        asyncio.run(run())

    def test_fomo_is_not_included_in_market_consensus(self):
        async def run():
            cfg=Settings(db_path=str(Path(self.tmp.name)/'engine.sqlite'),
                         auto_discover_traders=False,enable_fomo_research_feed=False)
            hunter=Hunter(cfg)
            try:
                now=time.time()
                hint=fomo_event({'type':'alert','alertType':'buy','chain':'solana','tokenAddress':A,
                                 'trader':'popular','ts':now-60,'eventId':'a'},now=now)
                await hunter.record_fomo_hint(hint)
                self.assertFalse(hunter.consensus.consensus(A,cfg.min_distinct_wallets))
                self.assertEqual(hunter.db.recent_fills(),[])
            finally:hunter.db.close()
        asyncio.run(run())

    def test_v41_config_remains_loadable(self):
        import json
        cfgpath=Path(self.tmp.name)/'config.json'
        source=Path(__file__).parent.parent/'config.v4.example.json'
        cfgpath.write_text(source.read_text())
        cfg=load_settings(cfgpath)
        self.assertTrue(cfg.enable_fomo_research_feed)
        self.assertEqual(cfg.market_queue_limit,8192)


if __name__=='__main__':unittest.main()

class SubscriptionSafetyTests(unittest.TestCase):
    def test_paid_token_feed_defaults_off_and_requires_explicit_key(self):
        import os
        from unittest.mock import patch
        cfg=Settings()
        self.assertFalse(cfg.enable_paid_token_stream)
        with patch.dict(os.environ,{'PUMPPORTAL_API_KEY':''}):
            cfg.enable_paid_token_stream=True
            with self.assertRaisesRegex(ValueError,'PUMPPORTAL_API_KEY'):
                cfg.validate()

    def test_paid_token_research_targets_respect_limit(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            db=Database(str(Path(d)/'ledger.sqlite'),200)
            try:
                db.remember_mints_batch([Launch(A,B,C),Launch(D,B,C)])
                self.assertEqual(len(db.paid_token_targets(1)),1)
                self.assertEqual(len(db.paid_token_targets(32)),2)
                self.assertEqual(db.state()['cash'],200)
            finally:db.close()

    def test_free_feed_sends_no_paid_subscriptions(self):
        from unittest.mock import patch
        import json
        from hunter.feed import Feed
        class Socket:
            def __init__(self):self.sent=[]
            async def send(self,val):self.sent.append(json.loads(val))
            async def recv(self):
                raise RuntimeError('stop test')
        class Connection:
            def __init__(self,socket):self.socket=socket
            async def __aenter__(self):return self.socket
            async def __aexit__(self,*args):pass
        async def run():
            sock=Socket()
            async def callback(event):pass
            with patch('hunter.feed.websockets.connect',return_value=Connection(sock)):
                with self.assertRaisesRegex(RuntimeError,'stop test'):
                    await Feed('wss://example.com').stream([],callback,lambda:[])
            self.assertEqual(sock.sent,[{'method':'subscribeNewToken'},{'method':'subscribeMigration'}])
        asyncio.run(run())
