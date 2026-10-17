"""V3 offline live-safety contract tests. No real orders sent."""
import tempfile, unittest, pathlib, json, time, os
from unittest.mock import Mock
from hunter.models import Quote
from hunter3.accounts import WalletAccounts,balances
from hunter3.orders import LiveLimits, LiveLedger, LightningClient, LiveGateway, LiveHalt, token_balance

WALLET='7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU'
MINT='So11111111111111111111111111111111111111112'
SIG='4'*87

class V3Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)
        self.cfg=LiveLimits(enabled=True,wallet_address=WALLET,db_path=str(self.path/'live.sqlite'),
            kill_switch_path=str(self.path/'STOP'))
        self.db=LiveLedger(self.cfg.db_path)
        self.rpc=Mock()
        self.rpc.call.side_effect=lambda method,params: {'value':200_000_000} if method=='getBalance' else {'value':[{'confirmationStatus':'confirmed','err':None}]}
        self.sent=[]
        self.client=LightningClient('EXAMPLE_KEY',send=lambda payload:self.sent.append(payload) or {'signature':SIG})
        self.gw=LiveGateway(self.cfg,self.db,self.rpc,self.client)
        self.q=Quote(MINT,0.000001,15000,10000,'pump',time.time())
    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()
    def test_config_validation(self):
        self.cfg.max_slippage_pct=9
        with self.assertRaises(ValueError):self.cfg.validate()
    def test_live_disabled_by_default(self):
        self.cfg.enabled=False
        with self.assertRaises(LiveHalt):self.gw.check()
    def test_sizing_light(self):
        self.assertAlmostEqual(self.gw.plan_buy(self.q,2,True,100),0.006)
    def test_sizing_heavy_capped(self):
        self.cfg.mode='heavy'
        self.assertAlmostEqual(self.gw.plan_buy(self.q,2,True,100),0.02)
    def test_daily_limit_respected(self):
        self.cfg.max_daily_buy_sol=0.004
        self.assertAlmostEqual(self.gw.plan_buy(self.q,2,True,100),0.004)
    def test_lifetime_limit_respected(self):
        self.cfg.max_lifetime_buy_sol=0.005
        self.assertAlmostEqual(self.gw.plan_buy(self.q,2,True,100),0.005)
    def test_explicit_manual_mode_skips_only_consensus(self):
        self.assertGreater(self.gw.plan_buy(self.q,0,True,100,manual_selection=True),0)
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,0,False,100,manual_selection=True)
    def test_fail_closed_no_consensus(self):
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,1,True,100)
    def test_fail_closed_rug(self):
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,2,False,100)
    def test_reject_stale(self):
        self.q.timestamp=time.time()-90
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,2,True,100)
    def test_reject_low_liquidity(self):
        self.q.liquidity_usd=500
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,2,True,100)
    def test_reject_when_reserve_exhausted(self):
        self.rpc.call.side_effect=lambda method,params: {'value':29_000_000}
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,2,True,100)
    def test_emergency_stop(self):
        pathlib.Path(self.cfg.kill_switch_path).write_text('STOP')
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,2,True,100)
    def test_buy_payload_secure_and_preflight(self):
        sig=self.client.trade('buy',MINT,0.005,self.cfg)
        self.assertEqual(sig,SIG)
        self.assertEqual(self.sent[-1]['skipPreflight'],'false')
        self.assertEqual(self.sent[-1]['denominatedInSol'],'true')
        self.assertEqual(self.sent[-1]['action'],'buy')
    def test_sell_payload_percentage(self):
        self.client.trade('sell',MINT,25,self.cfg)
        self.assertEqual(self.sent[-1]['amount'],'25%')
        self.assertEqual(self.sent[-1]['denominatedInSol'],'false')
    def test_buy_hard_cap(self):
        with self.assertRaises(LiveHalt):self.client.trade('buy',MINT,0.2,self.cfg)
    def test_no_retry_after_timeout(self):
        client=LightningClient('X',send=lambda _: (_ for _ in ()).throw(TimeoutError('could have broadcast')))
        gw=LiveGateway(self.cfg,self.db,self.rpc,client)
        with self.assertRaises(LiveHalt):gw.submit('buy',MINT,0.001)
        self.assertEqual(self.db.flag('halt'),'1')
        self.assertEqual(len(self.db.unresolved()),1)
        with self.assertRaises(LiveHalt):gw.submit('buy',MINT,0.001)
    def test_sign_and_confirm(self):
        oid,sig=self.gw.submit('buy',MINT,0.005)
        self.assertEqual(self.db.unresolved()[0]['status'],'submitted')
        self.assertTrue(self.gw.confirm(oid,sig))
        self.assertEqual(len(self.db.unresolved()),0)
        self.assertAlmostEqual(self.db.spend(),0.005)
    def test_rejected_onchain_failure_not_counted(self):
        oid,sig=self.gw.submit('buy',MINT,0.005)
        self.rpc.call.side_effect=lambda method,params:{'value':[{'confirmationStatus':'confirmed','err':{'InstructionError':[0,'Custom']}}]}
        self.assertFalse(self.gw.confirm(oid,sig))
        self.assertEqual(self.db.spend(),0)
    def test_buy_risk_pause_sell_allowed(self):
        self.db.set_flag('risk_pause','1')
        with self.assertRaises(LiveHalt):self.gw.plan_buy(self.q,2,True,100)
        self.db.put_position(MINT,0.00001,100,0.001)
        oid,sig=self.gw.submit('sell',MINT,100)
        self.assertEqual(sig,SIG)
    def test_wallet_accounts_public_only(self):
        a=WalletAccounts(phantom_treasury=WALLET)
        a.save(str(self.path/'accounts.json'))
        self.assertEqual(WalletAccounts.load(str(self.path/'accounts.json')).phantom_treasury,WALLET)
        (self.path/'bad').write_text('{"phantom_treasury":"private words"}')
        with self.assertRaises(ValueError):WalletAccounts.load(str(self.path/'bad'))
    def test_invalid_wallet_identity(self):
        with self.assertRaises(ValueError):WalletAccounts(phantom_treasury='private words').validate()
    def test_read_balances_only(self):
        a=WalletAccounts(phantom_treasury=WALLET)
        result=balances(a,self.rpc)
        self.assertEqual(result[0]['sol'],.2)
        self.assertFalse(result[1]['connected'])
    def test_token_amount_from_rpc(self):
        self.rpc.call.side_effect=lambda method,params:{'value':[{'account':{'data':{'parsed':{'info':{'tokenAmount':{'amount':'1234','decimals':2}}}}}}]}
        self.assertAlmostEqual(token_balance(self.rpc,WALLET,MINT),12.34)
    def test_never_store_secret_in_order_log(self):
        self.gw.submit('buy',MINT,0.001)
        self.assertNotIn('EXAMPLE_KEY',str(self.db.orders()))
    def test_unknown_order_blocks_repeat(self):
        self.db.create(MINT,'buy',.002)
        with self.assertRaises(LiveHalt):self.gw.check()
    def test_invalid_tx_signature_halts(self):
        client=LightningClient('EXAMPLE_KEY',send=lambda p:{'result':'NOT A SOL SIGNATURE'})
        gw=LiveGateway(self.cfg,self.db,self.rpc,client)
        with self.assertRaises(LiveHalt):gw.submit('buy',MINT,.002)
        self.assertEqual(self.db.flag('halt'),'1')

if __name__=='__main__':unittest.main()
