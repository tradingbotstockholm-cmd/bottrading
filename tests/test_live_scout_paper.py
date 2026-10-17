"""Paper-only integration: independently confirmed, timely scout swaps -> consensus.

No external RPC, trading wallet, network access or user database is used.
"""
from __future__ import annotations
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hunter.models import Quote
from hunter2.engine import Settings
from hunter2.discovery import TraderScout, FreshScoutSignal, ScoutStats
from hunter2.security import SecurityVerdict
from hunter4.__main__ import PaperHunterV4

MINT='7'*44
CURVE='8'*44
A='A'*44
B='B'*44
CREATOR='C'*44

class RPCTest:
    def __init__(self,signals,base):
        self.signals=signals
        self.txs={}
        for i, (wallet,side,age) in enumerate(signals):
            tx={'mint':MINT,'side':side,'transaction':{'message':{'accountKeys':[
                {'pubkey':wallet,'signer':True},{'pubkey':CREATOR,'signer':False}]}}}
            if age is not None:tx['blockTime']=base-age
            self.txs[f'sig{i}']=tx
    def signatures(self,key,limit):
        assert key==CURVE
        return [{'signature':k,'err':None} for k in self.txs][:limit]
    def transaction(self,sig):return self.txs[sig]

def parse(tx,wallet,signature,now):
    return [SimpleNamespace(mint=tx['mint'],side=tx['side'])]

class LiveScoutPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        cfg=Settings(db_path=str(Path(self.tmp.name)/'isolated.sqlite'),
                     min_distinct_wallets=2,signal_ttl_seconds=120,
                     min_market_cap_usd=10000,max_market_cap_usd=20000,
                     min_liquidity_usd=3000,assumed_slippage_pct=.0035)
        self.bot=PaperHunterV4(cfg)
        self.addCleanup(self.bot.db.close)
        self.bot.db.remember_mint(MINT,CURVE)
        self.bot.db.remember_creator(MINT,CREATOR)
        self.bot.db.conn.execute('UPDATE candidate_mints SET observed=?',(time.time()-90,))
        self.bot.db.conn.commit()

    def scout(self,signals,now=None):
        now=now or time.time()
        rpc=RPCTest(signals,now)
        scout=TraderScout(rpc,self.bot.db,parse,minimum_buys=3,minimum_mints=2,
                          signal_max_age_seconds=self.bot.cfg.signal_ttl_seconds)
        return scout.run_cycle(2,now)

    @staticmethod
    def quote(price=.001):return Quote(MINT,price,15000,30000,'pump',time.time())

    def test_two_independent_recent_swaps_trigger_paper_buy_and_sells(self):
        stats=self.scout([(A,'buy',20),(B,'buy',12)])
        self.assertEqual(stats.confirmed_trades,2)
        self.assertEqual(len(stats.live_signals),2)
        self.bot.accept_fresh_scout_signals(stats)
        self.assertTrue(self.bot.consensus.consensus(MINT,2))
        self.assertEqual(self.bot.db.recent_fills(),[])
        self.bot.security.examine=lambda *_:SecurityVerdict(True,(),0)
        async def scenario():
            with patch('hunter2.engine.fetch_quote',return_value=self.quote()):
                await self.bot.tick()
            self.assertEqual([r['side'] for r in self.bot.db.recent_fills()],['buy'])
            with patch('hunter2.engine.fetch_quote',return_value=self.quote(.0023)):
                await self.bot.tick()
            with patch('hunter2.engine.fetch_quote',return_value=self.quote(.00195)):
                await self.bot.tick()
        asyncio.run(scenario())
        self.assertEqual([r['side'] for r in reversed(self.bot.db.recent_fills())],['buy','sell','sell'])
        self.assertEqual(self.bot.db.state()['positions'],{})

    def test_old_transactions_are_research_only_not_trading_votes(self):
        stats=self.scout([(A,'buy',600),(B,'buy',601)])
        self.assertEqual(stats.confirmed_trades,2)
        self.assertFalse(stats.live_signals)
        self.bot.accept_fresh_scout_signals(stats)
        self.assertFalse(self.bot.consensus.consensus(MINT,2))
        self.assertEqual(self.bot.db.recent_fills(),[])

    def test_missing_blocktime_cannot_vote(self):
        stats=self.scout([(A,'buy',None),(B,'buy',None)])
        self.assertFalse(stats.live_signals)
        self.assertEqual(stats.confirmed_trades,2)

    def test_future_dated_blocks_cannot_vote(self):
        stats=self.scout([(A,'buy',-60),(B,'buy',-90)])
        self.assertFalse(stats.live_signals)

    def test_one_wallet_cannot_create_two_independent_votes(self):
        stats=self.scout([(A,'buy',10),(A,'buy',9)])
        self.bot.accept_fresh_scout_signals(stats)
        self.assertEqual(self.bot.consensus.voters(MINT),[A])
        self.assertEqual(self.bot.db.recent_fills(),[])

    def test_research_duplicates_are_not_replayed_as_fresh_votes(self):
        now=time.time()
        first=self.scout([(A,'buy',10)],now)
        self.assertEqual(len(first.live_signals),1)
        again=self.scout([(A,'buy',10)],now+1)
        self.assertEqual(again.live_signals,())

    def test_switch_can_disable_live_scout_votes(self):
        stats=self.scout([(A,'buy',8),(B,'buy',7)])
        self.bot.cfg.enable_scout_live_consensus=False
        self.bot.accept_fresh_scout_signals(stats)
        self.assertEqual(self.bot.consensus.voters(MINT),[])

    def test_no_quote_produces_a_real_rejection_not_a_fill(self):
        self.bot.accept_fresh_scout_signals(self.scout([(A,'buy',8),(B,'buy',7)]))
        async def scenario():
            with patch('hunter2.engine.fetch_quote',return_value=None):await self.bot.tick()
        asyncio.run(scenario())
        self.assertEqual(self.bot.db.recent_fills(),[])
        self.assertEqual(self.bot.db.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='reject'").fetchone()[0],1)

    def test_risk_gate_blocks_entry_even_after_independent_buyers(self):
        self.bot.accept_fresh_scout_signals(self.scout([(A,'buy',8),(B,'buy',7)]))
        self.bot.security.examine=lambda *_: SecurityVerdict(False,('test rug risk',),65)
        async def scenario():
            with patch('hunter2.engine.fetch_quote',return_value=self.quote()):await self.bot.tick()
        asyncio.run(scenario())
        self.assertEqual(self.bot.db.recent_fills(),[])
        self.assertEqual(self.bot.db.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='rug_reject'").fetchone()[0],1)

    def test_out_of_range_market_cap_is_logged_when_enabled(self):
        self.bot.cfg.enforce_paper_market_cap=True
        self.bot.accept_fresh_scout_signals(self.scout([(A,'buy',8),(B,'buy',7)]))
        async def scenario():
            with patch('hunter2.engine.fetch_quote',return_value=Quote(MINT,.001,100000,30000,'pump',time.time())):
                await self.bot.tick()
        asyncio.run(scenario())
        self.assertEqual(self.bot.db.recent_fills(),[])
        self.assertEqual(self.bot.db.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='reject'").fetchone()[0],1)

if __name__=='__main__': unittest.main()
