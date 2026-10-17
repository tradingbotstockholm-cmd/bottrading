"""V6.4: paper entries ignore market-cap bands, never safety checks."""
import asyncio,math,time,unittest
from unittest.mock import patch
from hunter.models import Quote
from hunter2.db import Database
from hunter2.engine import Settings
from hunter4.paper import CapitalRecoveryBroker
from hunter4.__main__ import PaperHunterV4
from hunter2.security import SecurityVerdict

M='7'*44
A='A'*44
B='B'*44

class NoMarketCapEntryTests(unittest.TestCase):
    def cfg(self,**kw):
        return Settings(db_path=':memory:',initial_cash_usd=1000,mode='light',
                        min_market_cap_usd=10000,max_market_cap_usd=20000,
                        min_liquidity_usd=3000,assumed_slippage_pct=.0035,**kw)
    def quote(self,cap,liq=30000,price=.001):
        return Quote(M,price,cap,liq,'verified-mock',time.time())
    def test_default_does_not_enforce_market_cap_band(self):
        cfg=self.cfg();cfg.validate()
        self.assertFalse(cfg.enforce_paper_market_cap)
        for cap in (1,3000,3100,20001,1000000000):
            db=Database(':memory:',1000)
            try:
                broker=CapitalRecoveryBroker(db,cfg)
                self.assertTrue(broker.buy(self.quote(cap),[A,B],signal_strength=.6),cap)
                self.assertEqual(len(db.recent_fills()),1)
            finally:db.close()
    def test_paper_engine_uses_sub10k_cap_only_after_all_other_gates(self):
        bot=PaperHunterV4(self.cfg());self.addCleanup(bot.db.close)
        now=time.time()
        bot.consensus.record(M,A,'buy',now-4)
        bot.consensus.record(M,B,'buy',now-5)
        bot.security.examine=lambda *_: SecurityVerdict(True,(),3)
        async def scenario():
            with patch('hunter2.engine.fetch_quote',return_value=self.quote(3000)):
                await bot.tick()
        asyncio.run(scenario())
        self.assertEqual([f['side'] for f in bot.db.recent_fills()],['buy'])
        self.assertEqual(bot.db.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='reject' AND detail LIKE '%market cap%'").fetchone()[0],0)
    def test_market_cap_filter_can_be_restored(self):
        cfg=self.cfg(enforce_paper_market_cap=True)
        db=Database(':memory:',1000);self.addCleanup(db.close)
        b=CapitalRecoveryBroker(db,cfg)
        self.assertFalse(b.buy(self.quote(3000),[A,B],signal_strength=.6))
        self.assertTrue(b.buy(self.quote(15000),[A,B],signal_strength=.6))
    def test_existing_safety_gates_still_block(self):
        cfg=self.cfg();db=Database(':memory:',1000);self.addCleanup(db.close)
        b=CapitalRecoveryBroker(db,cfg)
        self.assertFalse(b.buy(self.quote(3000),[A],signal_strength=.6))
        self.assertFalse(b.buy(self.quote(3000,liq=2000),[A,B],signal_strength=.6))
        self.assertFalse(b.buy(self.quote(3000,price=float('nan')),[A,B],signal_strength=.6))
        db.set_entries_paused(True)
        self.assertFalse(b.buy(self.quote(3000),[A,B],signal_strength=.6))
        self.assertEqual(len(db.recent_fills()),0)
    def test_invalid_toggle_rejected(self):
        cfg=self.cfg();cfg.enforce_paper_market_cap='false'
        self.assertRaises(ValueError,cfg.validate)

if __name__=='__main__':unittest.main()
