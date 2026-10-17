"""V6 test coverage is about correctness, not trading profitability."""
import math,sqlite3,time,unittest
from types import SimpleNamespace
from hunter.models import Quote
from hunter2.db import Database
from hunter2.ai_shadow import forecast_quantiles
from hunter3.autolive import LiveHunter
from hunter4.dashboard import PAGE

class LedgerDashboardTests(unittest.TestCase):
    def setUp(self):
        self.db=Database(':memory:',1000)
        self.addCleanup(self.db.close)
    def test_empty_live_metrics_are_honest(self):
        v=self.db.snapshot()['performance']
        self.assertEqual(v['paper_buy_fills'],0)
        self.assertEqual(v['paper_sell_fills'],0)
        self.assertEqual(v['realized_pnl_usd'],0)
        self.assertIsNone(v['win_rate_sell_fills'])
    def test_gross_win_loss_sum_and_decisions(self):
        s=self.db.state()
        for pnl in (9,-12,3):
            self.db.save_state(s,{'time':time.time(),'mint':'7'*44,'side':'sell','units':1,'price':1,
                                  'net_usd':max(0,10+pnl),'pnl_usd':pnl,'reason':'stress','wallets':[], 'liquidity':20000})
        self.db.observe('7'*44,'reject','market cap')
        perf=self.db.performance()
        self.assertEqual(perf['paper_sell_fills'],3)
        self.assertEqual(perf['realized_pnl_usd'],0)
        self.assertEqual(perf['gross_profit_usd'],12)
        self.assertEqual(perf['gross_loss_usd'],-12)
        self.assertEqual(perf['decisions']['reject'],1)
    def test_price_tape_and_shadow_roundtrip(self):
        mint='7'*44
        for i in range(300):
            q=Quote(mint,.0001*(i+1),15000,25000,'test',time.time())
            self.assertTrue(self.db.record_price_sample(q))
        series=self.db.recent_price_series(mint,300)
        self.assertEqual(len(series),256)
        self.assertAlmostEqual(series[-1],.03)
        self.db.record_ai_shadow(mint,-.10,.05,.20,256,'amazon/chronos-bolt-tiny')
        self.assertEqual(self.db.snapshot()['ai_shadow'][0]['median'],.05)
    def test_nonfinite_price_rejected(self):
        q=Quote('7'*44,float('nan'),15000,25000,'test',time.time())
        self.assertFalse(self.db.record_price_sample(q))
    def test_dashboard_contains_real_fill_metrics(self):
        for val in ('tradeCounts','realized','profitLoss','winRate','rejections','aiShadow'):
            self.assertIn(f'id="{val}"',PAGE)
    def test_missing_model_history_does_not_fabricate_forecast(self):
        self.assertIsNone(forecast_quantiles(None,[.1]*31))
    def test_live_trader_path_excludes_paper_scout_votes(self):
        # Call without initializing LiveHunter; no wallet keys or order gateway.
        self.assertIsNone(LiveHunter.accept_fresh_scout_signals(None,SimpleNamespace(live_signals=[1])))

if __name__=='__main__':unittest.main()
