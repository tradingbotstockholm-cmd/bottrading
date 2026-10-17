"""Adaptive PAPER-ONLY sizing. Synthetic quotes, zero API calls and zero real orders."""
import asyncio,math,time,unittest
from unittest.mock import patch
from hunter.models import Quote
from hunter2.db import Database
from hunter2.engine import Settings,paper_signal_strength
from hunter4.paper import CapitalRecoveryBroker
from hunter4.__main__ import PaperHunterV4
from hunter2.security import SecurityVerdict
from hunter4.dashboard import PAGE

A='A'*44;B='B'*44;M='7'*44

class AdaptiveSizingTests(unittest.TestCase):
    def cfg(self,**kw):
        return Settings(initial_cash_usd=1000,mode='light',max_open_positions=200,
                 min_liquidity_usd=3000,assumed_slippage_pct=.0035,
                 adaptive_paper_sizing=True,min_paper_entry_usd=2,max_paper_entry_usd=1000,**kw)
    def quote(self,mint=M,liq=30000):return Quote(mint,.001,15000,liq,'verified_test',time.time())
    def test_strength_is_evidence_index_not_model_probability(self):
        base=dict(voters=2,oldest_buy_age_seconds=60,ttl_seconds=120,
                  liquidity_usd=6000,minimum_liquidity_usd=3000,rug_score=10,maximum_rug_score=20)
        neutral=paper_signal_strength(**base)
        self.assertLess(neutral,paper_signal_strength(**(base|dict(voters=4))))
        self.assertLess(neutral,paper_signal_strength(**(base|dict(oldest_buy_age_seconds=2))))
        self.assertLess(neutral,paper_signal_strength(**(base|dict(liquidity_usd=30000))))
        self.assertLess(neutral,paper_signal_strength(**(base|dict(rug_score=0))))
        self.assertEqual(paper_signal_strength(**(base|dict(rug_score=None))),0.0)
        self.assertTrue(0<=neutral<=1)
    def test_confidence_adjusts_order_size_with_all_safety_caps(self):
        cfg=self.cfg();db=Database(':memory:',1000);self.addCleanup(db.close)
        broker=CapitalRecoveryBroker(db,cfg)
        # Initial paper account has 3% equity per-entry cap ($30), 75% total cap.
        low=broker.planned_allocation(db.state(),30000,signal_strength=0)
        mid=broker.planned_allocation(db.state(),30000,signal_strength=.4)
        high=broker.planned_allocation(db.state(),30000,signal_strength=1)
        self.assertAlmostEqual(low,2,places=6)
        self.assertGreater(mid,low)
        self.assertGreater(high,mid)
        self.assertLessEqual(high,30+1e-9)
        self.assertLessEqual(high,1000)
        self.assertTrue(broker.buy(self.quote(),[A,B],signal_strength=.4))
        p=db.state()['positions'][M]
        self.assertAlmostEqual(p['basis'],mid,places=5)
        self.assertAlmostEqual(p['signal_strength'],.4)
        self.assertLessEqual(p['basis'],1000)
        self.assertLessEqual(p['basis'],cfg.initial_cash_usd*cfg.allocation_pct)
    def test_weak_signal_respects_minimum_and_inadmissible_scores_fail(self):
        cfg=self.cfg();db=Database(':memory:',1000);self.addCleanup(db.close)
        b=CapitalRecoveryBroker(db,cfg)
        for strength in (-.1,1.01,float('nan'),float('inf'),'high'):
            self.assertFalse(b.buy(self.quote(),[A,B],signal_strength=strength))
        self.assertEqual(len(db.recent_fills()),0)
    def test_minimum_never_forces_trade_when_risk_budget_is_insufficient(self):
        cfg=self.cfg();cfg.max_total_exposure_pct=.001
        db=Database(':memory:',1000);self.addCleanup(db.close)
        b=CapitalRecoveryBroker(db,cfg)
        self.assertFalse(b.buy(self.quote(),[A,B],signal_strength=.9))
        self.assertEqual(db.state()['positions'],{})
    def test_risk_and_consensus_are_still_enforced(self):
        cfg=self.cfg();db=Database(':memory:',1000);self.addCleanup(db.close)
        b=CapitalRecoveryBroker(db,cfg)
        self.assertFalse(b.buy(self.quote(),[A],signal_strength=1))
        db.set_entries_paused(True)
        self.assertFalse(b.buy(self.quote(),[A,B],signal_strength=1))
        db.set_entries_paused(False)
        self.assertFalse(b.buy(self.quote(liq=3000),[A,B],signal_strength=1))  # slippage guard
        self.assertEqual(db.recent_fills(),[])
    def test_configuration_and_dashboard(self):
        cfg=self.cfg();cfg.validate()
        for low,high in ((1,1000),(2,1001),(200,10),(float('nan'),1000),(2,float('inf'))):
            cfg.min_paper_entry_usd=low;cfg.max_paper_entry_usd=high
            self.assertRaises(ValueError,cfg.validate)
        self.assertIn('signal_strength',PAGE)
        self.assertIn('Adaptive paper sizing',PAGE)
    def test_engine_scoring_to_fill_then_exit(self):
        cfg=self.cfg()
        cfg.db_path=':memory:'
        bot=PaperHunterV4(cfg);self.addCleanup(bot.db.close)
        bot.consensus.record(M,A,'buy',time.time()-7)
        bot.consensus.record(M,B,'buy',time.time()-8)
        bot.security.examine=lambda *_: SecurityVerdict(True,(),3)
        async def run():
            with patch('hunter2.engine.fetch_quote',return_value=self.quote()):
                await bot.tick()
            self.assertEqual([x['side'] for x in bot.db.recent_fills()],['buy'])
            p=bot.broker.state()['positions'][M]
            self.assertTrue(0<=p['signal_strength']<=1)
            self.assertTrue(2<=p['basis']<=30)
            with patch('hunter2.engine.fetch_quote',return_value=Quote(M,.0008,15000,30000,'test',time.time())):
                await bot.tick()
            self.assertEqual([x['side'] for x in reversed(bot.db.recent_fills())],['buy','sell'])
        asyncio.run(run())

if __name__=='__main__':unittest.main()
