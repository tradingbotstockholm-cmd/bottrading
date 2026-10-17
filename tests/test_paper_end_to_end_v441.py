"""Regression checks for actual V4 paper buy/partial-sell/final-sell plumbing.

All inputs are mocked/synthetic. A dedicated temporary database is used.
These tests do not modify the normal paper ledger or send blockchain orders.
"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hunter.models import Quote
from hunter2.engine import Settings
from hunter2.db import Database
from hunter2.security import SecurityVerdict
from hunter4.__main__ import PaperHunterV4
from hunter4.paper import CapitalRecoveryBroker

MINT = '7' * 44

class EndToEndPaper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Settings(db_path=str(Path(self.tmp.name) / 'isolated-paper.sqlite'),
                            initial_cash_usd=1000,
                            assumed_slippage_pct=.0035,
                            min_liquidity_usd=3000,
                            min_market_cap_usd=10000,
                            max_market_cap_usd=20000,
                            min_distinct_wallets=2,
                            enable_paid_wallet_stream=False,
                            enable_paid_token_stream=False)

    def quote(self, price=.001, liq=30000):
        return Quote(MINT, price, 15000, liq, 'pump', time.time())

    def test_paper_buy_recovery_and_final_exit_from_engine_tick(self):
        async def scenario():
            bot = PaperHunterV4(self.cfg)
            try:
                bot.consensus.record(MINT, 'wallet_A', 'buy')
                bot.consensus.record(MINT, 'wallet_B', 'buy')
                bot.security.examine = lambda *_: SecurityVerdict(True, (), 0)
                with patch('hunter2.engine.fetch_quote', return_value=self.quote()):
                    await bot.tick()
                self.assertEqual([x['side'] for x in bot.db.recent_fills()], ['buy'])
                self.assertIn(MINT, bot.broker.state()['positions'])

                with patch('hunter2.engine.fetch_quote', return_value=self.quote(.0023)):
                    await bot.tick()
                self.assertEqual([x['reason'] for x in bot.db.recent_fills()[:1]], ['principal_recovery'])
                self.assertIn(MINT, bot.broker.state()['positions'])

                with patch('hunter2.engine.fetch_quote', return_value=self.quote(.00195)):
                    await bot.tick()
                self.assertEqual([x['side'] for x in reversed(bot.db.recent_fills())], ['buy', 'sell', 'sell'])
                self.assertNotIn(MINT, bot.broker.state()['positions'])
            finally:
                bot.db.close()
        asyncio.run(scenario())

    def test_actual_purchase_size_must_obey_slippage_guard(self):
        db = Database(self.cfg.db_path, self.cfg.initial_cash_usd)
        try:
            broker = CapitalRecoveryBroker(db, self.cfg)
            # At $3k liquidity, $30 allocated => 0.85% modeled slip.
            self.assertGreater(broker._slip(30, 3000), broker.max_estimated_slippage)
            self.assertFalse(broker.buy(self.quote(liq=3000), ['wallet_A', 'wallet_B']))
            self.assertEqual(db.recent_fills(), [])
        finally:
            db.close()

    def test_valid_purchase_still_succeeds(self):
        db = Database(self.cfg.db_path, self.cfg.initial_cash_usd)
        try:
            broker = CapitalRecoveryBroker(db, self.cfg)
            self.assertTrue(broker.buy(self.quote(liq=30000), ['wallet_A', 'wallet_B']))
            position = broker.state()['positions'][MINT]
            self.assertLessEqual(broker._slip(position['basis'], 30000),
                                 broker.max_estimated_slippage)
        finally:
            db.close()

if __name__ == '__main__':
    unittest.main()
