"""V4 regression tests, offline only: no credential or RPC required."""
import tempfile,unittest,time,math
from pathlib import Path
from hunter.models import Quote
from hunter2.engine import Settings
from hunter2.db import Database
from hunter4.paper import CapitalRecoveryBroker
from hunter4.principal import plan_principal_recovery,estimate_exit_net_usd,defensive_exit
from hunter3.orders import LiveLedger,LiveLimits,LiveHalt,LiveGateway,LightningClient

MINT='So11111111111111111111111111111111111111112'
WALLET='7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU'
TX='4'*87

class RecoveryMath(unittest.TestCase):
    def opts(self,**kwargs):
        p=dict(remaining_units=10000,initial_units=10000,entry_cost_usd=5,
               recovered_usd=0,current_usd_price=.0011,liquidity_usd=25000,
               sell_fee_fraction=.02,maximum_slippage_fraction=.0075,network_cost_usd=.05)
        p.update(kwargs);return p
    def test_recovers_5_dollars_plus_costs(self):
        p=plan_principal_recovery(**self.opts())
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p.estimated_net_usd,5.1-1e-9)
        self.assertLess(p.fraction,1)
        self.assertGreater(p.estimated_remaining_units,0)
    def test_does_not_trigger_below_2x(self):
        self.assertIsNone(plan_principal_recovery(**self.opts(current_usd_price=.0009)))
    def test_trigger_multiple_is_configurable(self):
        self.assertIsNone(plan_principal_recovery(**self.opts(trigger_multiple=3)))
    def test_liquidity_insufficient(self):
        self.assertIsNone(plan_principal_recovery(**self.opts(liquidity_usd=10)))
    def test_no_repeat_once_recovered(self):
        self.assertIsNone(plan_principal_recovery(**self.opts(recovered_usd=5.11)))
    def test_min_runner_survives(self):
        p=plan_principal_recovery(**self.opts(min_runner_fraction=.25))
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p.estimated_remaining_units,2500-1e-5)
    def test_unaffordable_larger_principal(self):
        self.assertIsNone(plan_principal_recovery(**self.opts(entry_cost_usd=50)))
    def test_fee_dependent_exit_qty(self):
        a=plan_principal_recovery(**self.opts(sell_fee_fraction=0))
        b=plan_principal_recovery(**self.opts(sell_fee_fraction=.06))
        self.assertGreater(b.units_to_sell,a.units_to_sell)
    def test_network_cost_dependent_exit_qty(self):
        a=plan_principal_recovery(**self.opts(network_cost_usd=0))
        b=plan_principal_recovery(**self.opts(network_cost_usd=.20))
        self.assertGreater(b.units_to_sell,a.units_to_sell)
    def test_reject_nan_and_inf(self):
        for value in (math.nan,math.inf,-1,0):
            with self.assertRaises(ValueError):estimate_exit_net_usd(value,1,10000)
    def test_slippage_guard_less_than_one_percent(self):
        with self.assertRaises(ValueError):estimate_exit_net_usd(100,1,1000)
        with self.assertRaises(ValueError):estimate_exit_net_usd(1,1,1000,maximum_slippage_fraction=.01)
    def test_defensive_stops(self):
        self.assertEqual(defensive_exit(entry_price=1,current_price=.92,high_price=1),'stop_loss')
        self.assertEqual(defensive_exit(entry_price=1,current_price=1.18,high_price=1.3),'trailing_profit_protection')
        self.assertIsNone(defensive_exit(entry_price=1,current_price=1.15,high_price=1.16))

class PaperRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=Settings(initial_cash_usd=200,assumed_slippage_pct=.0035,max_hold_seconds=1800)
        self.db=Database(str(Path(self.tmp.name)/'paper.sqlite'),200)
        self.b=CapitalRecoveryBroker(self.db,self.settings)
    def q(self,price=.001,liq=30000):return Quote(MINT,price,15000,liq,'pump',time.time())
    def test_3_percent_size_and_recovery(self):
        self.assertTrue(self.b.buy(self.q(),['A','B']))
        p=self.b.state()['positions'][MINT]
        self.assertAlmostEqual(p['initial_basis_usd'],6)
        self.b.mark(self.q(.0023))
        s=self.b.state();r=s['positions'][MINT]
        self.assertTrue(r['principal_done'])
        self.assertGreaterEqual(r['proceeds_usd'],6)
        self.assertGreater(r['units'],0)
        self.assertGreater(s['cash'],200)
    def test_runner_does_not_falsely_claim_guaranteed_profit(self):
        self.b.buy(self.q(),['A','B']);self.b.mark(self.q(.0023))
        st=self.b.state();self.assertGreater(st['positions'][MINT]['units'],0)
        self.assertGreater(st['positions'][MINT]['basis'],0)
    def test_no_recovery_when_price_up_not_enough(self):
        self.b.buy(self.q(),['A','B']);self.b.mark(self.q(.0015))
        self.assertFalse(self.b.state()['positions'][MINT]['principal_done'])
    def test_stop_at_seven_percent(self):
        self.b.buy(self.q(),['A','B']);self.b.mark(self.q(.00090))
        self.assertNotIn(MINT,self.b.state()['positions'])
    def test_no_entry_when_impact_exceeds_guard(self):
        self.settings.assumed_slippage_pct=.009
        self.assertFalse(self.b.buy(self.q(),['A','B']))
    def test_legacy_position_no_false_principal_recovery(self):
        st=self.b.state();st['positions'][MINT]=dict(mint=MINT,units=3000,basis=4,entry_price=.001,entry_time=time.time(),high=.001,
            last_price=.001,last_quote=time.time(),liquidity=30000,partial2=False,partial5=False,pending_exit='',wallets=['A','B'])
        self.db.save_state(st)
        self.b.mark(self.q(.0025))
        self.assertEqual(len(self.db.recent_fills()),0)

class SettlementSafety(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=LiveLimits(enabled=True,wallet_address=WALLET,db_path=str(Path(self.tmp.name)/'orders.sqlite'),kill_switch_path=str(Path(self.tmp.name)/'STOP'))
        self.ledger=LiveLedger(self.cfg.db_path)
        class RPC:
            def call(self,method,params):
                if method=='getBalance':return {'value':200_000_000}
                if method=='getSignatureStatuses':return {'value':[{'err':None,'confirmationStatus':'confirmed'}]}
                raise AssertionError(method)
        self.gw=LiveGateway(self.cfg,self.ledger,RPC(),LightningClient('SECRET',send=lambda x:{'signature':TX}))
    def tearDown(self):self.ledger.conn.close()
    def settle(self,side,token,sol,credit=0):
        oid,sig=self.gw.submit(side,MINT,.005 if side=='buy' else 25)
        self.assertTrue(self.gw.confirm(oid,sig))
        self.ledger.record_settlement(oid,MINT,side,token,sol,100,credit)
        return oid
    def test_unsettled_confirmed_blocks_more_orders(self):
        oid,sig=self.gw.submit('buy',MINT,.005)
        self.gw.confirm(oid,sig)
        with self.assertRaises(LiveHalt):self.gw.check()
        self.ledger.record_settlement(oid,MINT,'buy',100,-.005,100)
        self.gw.check()
    def test_recycling_requires_confirmed_sale(self):
        self.settle('buy',100,-.005)
        self.ledger.put_position(MINT,.0001,100,.005,entry_cost_usd=.50)
        self.assertAlmostEqual(self.ledger.net_committed_sol(),.005)
        self.settle('sell',-55,.0051,credit=.005)
        self.assertAlmostEqual(self.ledger.net_committed_sol(),0)
        self.assertAlmostEqual(self.ledger.total_reclaimed_principal_sol(),.005)
    def test_unconfirmed_sale_cannot_add_credit(self):
        self.ledger.put_position(MINT,.001,100,.005)
        oid,sig=self.gw.submit('sell',MINT,40)
        with self.assertRaises(LiveHalt):self.ledger.record_settlement(oid,MINT,'sell',-40,.004,100,.004)
    def test_duplicate_settlement_denied(self):
        oid=self.settle('buy',100,-.005)
        with self.assertRaises(LiveHalt):self.ledger.record_settlement(oid,MINT,'buy',100,-.005,100)
    def test_refuse_negative_sale_credit(self):
        self.ledger.put_position(MINT,.001,100,.005)
        oid,sig=self.gw.submit('sell',MINT,20)
        self.gw.confirm(oid,sig)
        with self.assertRaises(LiveHalt):self.ledger.record_settlement(oid,MINT,'sell',-10,.002,100,.004)
    def test_gross_turnover_budget(self):
        self.cfg.max_gross_turnover_sol=.1
        self.cfg.max_lifetime_buy_sol=.1
        self.cfg.validate()
        self.settle('buy',100,-.005)
        self.ledger.put_position(MINT,.001,100,.005)
        self.settle('sell',-50,.005,credit=.005)
        self.assertAlmostEqual(self.ledger.net_committed_sol(),0)
    def test_recovery_progress_survives_restart(self):
        self.ledger.put_position(MINT,.001,100,.01,entry_cost_usd=2)
        self.ledger.update_recovery(MINT,2.04,.013,principal_done=True)
        a=LiveLedger(self.cfg.db_path)
        self.assertTrue(a.positions()[MINT]['principal_done'])
        self.assertAlmostEqual(a.positions()[MINT]['recovered_usd'],2.04)
        a.conn.close()
    def test_entry_never_bypasses_live_mode(self):
        self.cfg.enabled=False
        with self.assertRaises(LiveHalt):self.gw.check()
    def test_v3_ledger_additive_migration(self):
        self.assertIn('entry_cost_usd', self.ledger.positions().get(MINT, {'entry_cost_usd':0}))
    def test_live_execution_slippage_is_below_one_percent(self):
        self.assertLess(self.cfg.max_slippage_pct,1)
        self.assertTrue(self.cfg.recycle_confirmed_principal)
    def test_atomic_buy_adds_position_and_settlement_together(self):
        oid,sig=self.gw.submit('buy',MINT,.005)
        self.assertTrue(self.gw.confirm(oid,sig))
        self.ledger.finalize_filled_buy(oid,MINT,500,.005,150,0)
        p=self.ledger.positions()[MINT]
        self.assertAlmostEqual(p['entry_cost_usd'],.75)
        self.assertAlmostEqual(p['initial_units'],500)
        self.assertEqual(self.ledger.unreconciled_confirmed(),[])
        with self.assertRaises(LiveHalt):
            self.ledger.finalize_filled_buy(oid,MINT,500,.005,150,0)
    def test_atomic_sell_recycles_only_original_principal(self):
        oid,sig=self.gw.submit('buy',MINT,.005)
        self.gw.confirm(oid,sig)
        self.ledger.finalize_filled_buy(oid,MINT,500,.005,100,0)
        oid,sig=self.gw.submit('sell',MINT,50)
        self.gw.confirm(oid,sig)
        credit=self.ledger.finalize_filled_sell(oid,MINT,260,.009,100,reason='principal_recovery')
        self.assertAlmostEqual(credit,.005)
        self.assertAlmostEqual(self.ledger.total_reclaimed_principal_sol(),.005)
        self.assertEqual(self.ledger.positions()[MINT]['principal_done'],1)
        self.assertAlmostEqual(self.ledger.positions()[MINT]['units'],240)
        with self.assertRaises(LiveHalt):
            self.ledger.finalize_filled_sell(oid,MINT,260,.009,100,reason='principal_recovery')
    def test_regular_exit_cannot_falsely_create_recycle_credit(self):
        oid,sig=self.gw.submit('buy',MINT,.005)
        self.gw.confirm(oid,sig)
        self.ledger.finalize_filled_buy(oid,MINT,500,.005,100,0)
        oid,sig=self.gw.submit('sell',MINT,50)
        self.gw.confirm(oid,sig)
        self.assertEqual(self.ledger.finalize_filled_sell(oid,MINT,250,.007,100,reason='stop_loss'),0)
        self.assertEqual(self.ledger.total_reclaimed_principal_sol(),0)
    def test_crash_safe_buy_rejects_invalid_without_partial_position(self):
        oid,sig=self.gw.submit('buy',MINT,.005)
        self.gw.confirm(oid,sig)
        with self.assertRaises(LiveHalt):
            self.ledger.finalize_filled_buy(oid,MINT,500,-.005,100,0)
        self.assertEqual(self.ledger.positions(),{})
        self.assertEqual(len(self.ledger.unreconciled_confirmed()),1)
    def test_capital_recycle_can_be_switched_off(self):
        self.cfg.recycle_confirmed_principal=False
        self.cfg.validate()
        self.assertEqual(self.cfg.recycle_confirmed_principal,False)

if __name__=='__main__':unittest.main()
