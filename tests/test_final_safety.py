"""Paper-only regression tests for V5 release. No network or original DB access."""
from __future__ import annotations
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from hunter.models import Quote
from hunter2.db import Database
from hunter2.engine import Settings
from hunter4.paper import CapitalRecoveryBroker

MINT='7'*44
WALLET_IDS=['w-A','w-B']

class FinalSafety(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'paper.sqlite')
        self.cfg=Settings(db_path=self.path,initial_cash_usd=1000,
                          assumed_slippage_pct=.0035,min_market_cap_usd=10000,
                          max_market_cap_usd=20000,min_liquidity_usd=3000,
                          enable_paid_wallet_stream=False,enable_paid_token_stream=False)
        self.db=Database(self.path,1000)
        self.addCleanup(self.db.close)
        self.broker=CapitalRecoveryBroker(self.db,self.cfg)

    def quote(self,price=.001,liq=30000,at=None):
        return Quote(MINT,price,15000,liq,'pump',time.time() if at is None else at)

    def test_dashboard_pause_not_overwritten_by_stale_position_write(self):
        state_before=self.db.state()
        self.db.set_entries_paused(True)
        state_before['cash']=900
        self.db.save_state(state_before)  # emulate a concurrent scanner write
        self.assertTrue(self.db.is_entries_paused())
        self.assertTrue(self.db.snapshot()['paused'])
        self.assertFalse(self.broker.buy(self.quote(),WALLET_IDS))
        self.db.set_entries_paused(False)
        self.assertTrue(self.broker.buy(self.quote(),WALLET_IDS))
        self.assertEqual(len(self.db.recent_fills()),1)

    def test_legacy_pause_migration(self):
        state=self.db.state()
        state['paused']=True
        self.db.save_state(state)
        self.db.conn.execute('DELETE FROM paper_controls')
        self.db.conn.commit()
        self.db.close()
        new=Database(self.path,1000)
        try:
            self.assertTrue(new.is_entries_paused())
            new.set_entries_paused(False)
            self.assertFalse(new.is_entries_paused())
        finally:new.close()

    def test_invalid_exit_sizes_do_not_change_cash_or_fills(self):
        self.assertTrue(self.broker.buy(self.quote(),WALLET_IDS))
        before=self.db.state()
        count=len(self.db.recent_fills())
        for frac in (0,-1,1.00001,float('nan'),float('inf'),-float('inf'),'1',None):
            self.assertIsNone(self.broker.exit(MINT,self.quote(),frac))
            self.assertEqual(self.db.state()['cash'],before['cash'])
            self.assertEqual(self.db.state()['positions'][MINT]['units'],before['positions'][MINT]['units'])
        self.assertEqual(len(self.db.recent_fills()),count)

    def test_future_dated_price_cannot_create_order(self):
        self.assertFalse(self.broker.buy(self.quote(at=time.time()+3600),WALLET_IDS))
        self.assertEqual(self.db.recent_fills(),[])

    def test_failed_runner_partial_not_marked_completed(self):
        self.cfg.initial_cash_usd=100000
        self.cfg.mode='light'
        self.db.save_state(dict(cash=100000,positions={},day='',day_start=100000,paused=False,peak_equity=100000))
        self.assertTrue(self.broker.buy(self.quote(liq=1_000_000),WALLET_IDS))
        s=self.db.state();s['positions'][MINT]['principal_done']=True
        self.db.save_state(s)
        # At 5x, 15% of the runner exceeds the modeled liquidity's 2% fill cap.
        self.broker.mark(self.quote(price=.005,liq=3000))
        self.assertFalse(self.db.state()['positions'][MINT]['partial5'])
        self.assertEqual([r['side'] for r in self.db.recent_fills()],['buy'])

if __name__=='__main__':unittest.main()
