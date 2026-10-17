"""Operational dashboard tests, intentionally never submitting real orders."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from hunter2.db import Database
from hunter2.engine import Hunter, Settings
from hunter4.dashboard import PAGE

class LiveDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(str(Path(self.tmp.name)/'paper.sqlite'),1000)
    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_stopped_scanner_explicit_in_dashboard_snapshot(self):
        state=self.db.snapshot()
        self.assertFalse(state['runtime']['scanner_online'])
        self.assertIsNone(state['runtime']['heartbeat_age_seconds'])
        self.assertEqual(state['cash'],1000)
        self.assertEqual(state['fills'],[])
        self.assertEqual(state['latest_launches'],[])

    def test_recent_heartbeat_reports_rates_and_stale_safely(self):
        stamp=time.time()
        self.db.record_runtime({'market_received':20,'launch_events_per_second':3.5,
            'queue_depth':4,'scout_min_buys':4,'scout_min_distinct_mints':2},now=stamp)
        snap=self.db.snapshot()
        self.assertTrue(snap['runtime']['scanner_online'])
        self.assertEqual(snap['runtime']['launch_events_per_second'],3.5)
        self.assertEqual(snap['runtime']['queue_depth'],4)
        self.assertEqual(snap['cash'],1000)
        self.assertEqual(snap['fills'],[])
        self.db.record_runtime({'market_received':21,'launch_events_per_second':0},now=stamp-20)
        self.assertFalse(self.db.snapshot()['runtime']['scanner_online'])

    def test_paper_history_checkpoints_unchanged_by_telemetry(self):
        self.db.mark_seen('wallet123',['signature123'])
        state_before=self.db.state()
        for i in range(30):
            self.db.record_runtime({'market_received':i,'queue_depth':0})
        self.assertEqual(self.db.state(),state_before)
        self.assertTrue(self.db.has_seen('wallet123','signature123'))
        self.assertEqual(len(self.db.recent_fills()),0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM runtime_status').fetchone()[0],1)

    def test_dashboard_has_freshness_and_no_fake_fills(self):
        for element in ('launchRate','queue','scoutTrades','scoutWallets','qualified',
                        'status','tokens','scans','errors'):
            self.assertIn('id="'+element+'"',PAGE)
        self.assertIn('setTimeout(refresh,1000)',PAGE)
        self.assertIn('scanner_online',PAGE)
        self.assertIn('No qualified paper entries yet',PAGE)
        self.assertIn('textContent',PAGE)

    def test_runtime_loop_heartbeat_and_no_live_trades(self):
        async def run():
            cfg=Settings(db_path=str(Path(self.tmp.name)/'engine.sqlite'),
                         enable_fomo_research_feed=False, auto_discover_traders=False)
            bot=Hunter(cfg)
            task=asyncio.create_task(bot.metrics_loop())
            try:
                await asyncio.sleep(.06)
                snap=bot.db.snapshot()
                self.assertTrue(snap['runtime']['scanner_online'])
                self.assertEqual(snap['runtime']['mode'],'PAPER')
                self.assertFalse(snap['runtime']['paid_token_stream'])
                self.assertEqual(snap['cash'],cfg.initial_cash_usd)
                self.assertFalse(bot.db.recent_fills())
            finally:
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
                bot.db.close()
        asyncio.run(run())

if __name__=='__main__':unittest.main()
