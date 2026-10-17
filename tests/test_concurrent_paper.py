"""Offline tests for bounded paper-position concurrency; no order signing."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
import math,time,unittest
from hunter.models import Quote
from hunter2.db import Database
from hunter2.engine import Hunter,Settings
from hunter4.paper import CapitalRecoveryBroker

ALPHA='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
def mint(n):
    arr=[]
    for _ in range(6):
        arr.append(ALPHA[n%58]);n//=58
    return '7'*38+''.join(reversed(arr))

class ConcurrentPaperPositions(unittest.TestCase):
    def setUp(self):
        self.cfg=Settings(initial_cash_usd=1000,mode='light',max_open_positions=200,
                          max_total_exposure_pct=.75, assumed_slippage_pct=.0035,
                          max_price_age_seconds=120,min_liquidity_usd=3000)
        self.db=Database(':memory:',1000)
        self.addCleanup(self.db.close)
        self.broker=CapitalRecoveryBroker(self.db,self.cfg)
        self.quotes={mint(i):Quote(mint(i),.001,15000,1_000_000,'test',time.time()) for i in range(200)}

    def test_200_concurrent_buys_and_200_sells_have_exact_fills(self):
        with ThreadPoolExecutor(max_workers=32) as pool:
            opened=list(pool.map(lambda q:self.broker.buy(q,['WALLET_A','WALLET_B']),self.quotes.values()))
        self.assertEqual(sum(opened),200)
        state=self.db.state()
        self.assertEqual(len(state['positions']),200)
        self.assertGreaterEqual(state['cash'],0)
        self.assertLessEqual(sum(x['basis'] for x in state['positions'].values()),
                             self.broker.equity(state)*.75+1e-6)
        self.assertEqual(sum(f['side']=='buy' for f in self.db.recent_fills(1000)),200)
        # Duplicate concurrent exit requests must be idempotent.
        def close(q):return self.broker.exit(q.mint,q,1,'concurrent paper close')
        with ThreadPoolExecutor(max_workers=32) as pool:
            exited=list(pool.map(close,list(self.quotes.values())*2))
        self.assertEqual(sum(x is not None for x in exited),200)
        self.assertEqual(len(self.db.state()['positions']),0)
        self.assertGreaterEqual(self.db.state()['cash'],0)
        self.assertEqual(sum(f['side']=='sell' for f in self.db.recent_fills(1000)),200)
        pnl=sum(f['pnl_usd'] for f in self.db.recent_fills(1000) if f['side']=='sell')
        self.assertAlmostEqual(self.db.state()['cash']-1000,pnl,places=4)
        self.assertLess(pnl,0)  # flat price with modeled fees/slippage always loses
        import csv,io
        exported=list(csv.DictReader(io.StringIO(self.db.export_fills_csv())))
        self.assertEqual(len(exported),400)
        self.assertEqual(len(self.db.snapshot()['fills']),250)

    def test_1000_racing_duplicate_buys_never_overbook(self):
        q=self.quotes[mint(0)]
        with ThreadPoolExecutor(max_workers=48) as pool:
            result=list(pool.map(lambda _:self.broker.buy(q,['WALLET_A','WALLET_B']),range(1000)))
        self.assertEqual(sum(result),1)
        self.assertEqual(len(self.db.recent_fills()),1)
        self.assertGreaterEqual(self.db.state()['cash'],0)

    def test_limits_not_weakened_by_high_position_capacity(self):
        self.assertRaises(AssertionError,Settings(max_open_positions=251).validate)
        self.assertRaises(AssertionError,Settings(max_total_exposure_pct=1.).validate)
        self.assertRaises(ValueError,Settings(quote_parallelism=99).validate)
        self.db.set_entries_paused(True)
        self.assertFalse(self.broker.buy(self.quotes[mint(1)],['A','B']))
        self.db.set_entries_paused(False)
        self.assertFalse(self.broker.buy(self.quotes[mint(1)],['ONLY_ONE']))

    def test_bound_position_monitor_rotates_to_all_200(self):
        for q in self.quotes.values():
            self.assertTrue(self.broker.buy(q,['A','B']))
        engine=Hunter(self.cfg)
        self.addCleanup(engine.db.close)
        # Use same in-memory broker for monitoring; no actual network or secret.
        engine.db.close(); engine.db=self.db
        engine.broker=self.broker
        engine.cfg.max_position_checks_per_cycle=40
        engine.cfg.quote_parallelism=4
        engine.quote_semaphore=asyncio.Semaphore(4)
        current=0;peak=0
        async def fake_one(m,p):
            nonlocal current,peak
            async with engine.quote_semaphore:
                current+=1;peak=max(peak,current)
                await asyncio.sleep(.001)
                current-=1
                engine.position_last_checked[m]=time.monotonic()
                engine.position_check_count+=1
        engine._check_position=fake_one
        async def cycles():
            for _ in range(5):await engine.check_positions()
        asyncio.run(cycles())
        self.assertEqual(engine.position_check_count,200)
        self.assertLessEqual(peak,4)
        self.assertEqual(set(engine.position_last_checked),set(self.quotes))

if __name__=='__main__':unittest.main()
