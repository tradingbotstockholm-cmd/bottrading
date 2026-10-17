import json,os,tempfile,time,unittest
from pathlib import Path
from hunter.models import Quote
from hunter.fomo import B58
from hunter2.engine import Settings,load_settings
from hunter2.chain import (extract_wallet_trades,_programs,SolanaClient,WalletPoller,
                           PUMP_PROGRAM,PUMPSWAP_PROGRAM,JUPITER_V6,WSOL,USDC)
from hunter2.db import Database
from hunter2.broker import Broker
from hunter2.security import SecurityGate
from hunter2.replay import replay

W='4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU'
X='D3hV8K7V1tqkVN3qxET9PnARFPHhnTJvBGbQQSNbqLXT'
M='7ih1sC2za2dQvxbeooJLrA9YsJhHGSdb3JX3mRjFSwsM'
T=time.time()
def quote(p=.001,cap=15000,liq=10000):return Quote(M,p,cap,liq,'pump',time.time())
def token(m,amt,dec=6,owner=W):return {'mint':m,'owner':owner,'uiTokenAmount':{'amount':str(amt),'decimals':dec}}
def tx(program=PUMP_PROGRAM, before=None,after=None,native_pre=2_000_000_000,native_post=1_900_000_000,err=None):
    return {'slot':900,'transaction':{'message':{'accountKeys':[{'pubkey':W},{'pubkey':program}],
          'instructions':[{'programIdIndex':1,'data':'1234'}]}},
          'meta':{'err':err,'preBalances':[native_pre,0],'postBalances':[native_post,0],
          'preTokenBalances':before or [],'postTokenBalances':after or []}}
class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'db.sqlite')
        self.cfg=Settings();self.db=Database(self.path,1000);self.bot=Broker(self.db,self.cfg)
    def test_modes(self):
        self.assertAlmostEqual(self.cfg.allocation_pct,.03)
        self.cfg.mode='heavy';self.assertAlmostEqual(self.cfg.allocation_pct,.15)
    def test_buy_sizing(self):
        self.assertTrue(self.bot.buy(quote(),[W,X]));p=self.db.state()['positions'][M]
        self.assertAlmostEqual(p['basis'],30)
    def test_heavy_liquidity_cap(self):
        self.cfg.mode='heavy';self.bot.buy(quote(liq=5000),[W,X])
        self.assertAlmostEqual(self.db.state()['positions'][M]['basis'],100)
    def test_reject_sole_wallet(self):self.assertFalse(self.bot.buy(quote(),[W,W]))
    def test_reject_wrong_cap_when_enabled(self):
        self.cfg.enforce_paper_market_cap=True
        self.assertFalse(self.bot.buy(quote(cap=30000),[W,X]))
    def test_reject_no_liquidity(self):self.assertFalse(self.bot.buy(quote(liq=2000),[W,X]))
    def test_reject_zero_price(self):self.assertFalse(self.bot.buy(quote(p=0),[W,X]))
    def test_reject_nan(self):self.assertFalse(self.bot.buy(quote(p=float('nan')),[W,X]))
    def test_reject_stale(self):
        q=quote();q.timestamp=time.time()-1000;self.assertFalse(self.bot.buy(q,[W,X]))
    def test_idempotent_mint(self):
        self.assertTrue(self.bot.buy(quote(),[W,X]));self.assertFalse(self.bot.buy(quote(),[W,X]))
        self.assertEqual(len(self.db.recent_fills()),1)
    def test_ledger_restart(self):
        self.bot.buy(quote(),[W,X]);self.assertIn(M,Database(self.path).state()['positions'])
    def test_stop_loss(self):
        self.bot.buy(quote(),[W,X]);self.bot.mark(quote(p=.0007))
        self.assertFalse(self.db.state()['positions']);self.assertLess(self.db.state()['cash'],1000)
    def test_trailing(self):
        self.bot.buy(quote(),[W,X]);self.bot.mark(quote(p=.0017));self.bot.mark(quote(p=.0011))
        self.assertFalse(self.db.state()['positions'])
    def test_partial_2x(self):
        self.bot.buy(quote(),[W,X]);self.bot.mark(quote(p=.002))
        p=self.db.state()['positions'][M];self.assertTrue(p['partial2']);self.assertGreater(p['units'],0)
        self.assertEqual(len(self.db.recent_fills()),2)
    def test_partial_5x(self):
        self.bot.buy(quote(),[W,X]);self.bot.mark(quote(p=.005))
        self.assertTrue(self.db.state()['positions'][M]['partial5'])
    def test_no_double_partial(self):
        self.bot.buy(quote(),[W,X]);self.bot.mark(quote(p=.0021));self.bot.mark(quote(p=.0022));self.assertEqual(len(self.db.recent_fills()),2)
    def test_no_false_stale_sale(self):
        self.bot.buy(quote(),[W,X]);self.bot.request_exit(M,'no prices')
        self.assertIn(M,self.db.state()['positions'])
        self.assertEqual(len(self.db.recent_fills()),1)
        self.assertEqual(self.db.state()['positions'][M]['pending_exit'],'no prices')
    def test_pending_exit_restart_and_retry(self):
        self.bot.buy(quote(),[W,X]);self.bot.exit(M,None,1,'oracle unavailable')
        self.assertTrue(Database(self.path).state()['positions'][M]['pending_exit'])
        self.bot.mark(quote(p=.0009));self.assertFalse(self.db.state()['positions'])
    def test_exit_no_liquidity(self):
        self.bot.buy(quote(),[W,X]);self.assertIsNone(self.bot.exit(M,quote(liq=.01),1,'risk'))
        self.assertIn(M,self.db.state()['positions'])
    def test_exit_slippage_costs(self):
        self.bot.buy(quote(),[W,X]);pnl=self.bot.exit(M,quote(),1,'test')
        self.assertLess(pnl,0)
    def test_pause_new_entries(self):
        self.bot.pause();self.assertFalse(self.bot.buy(quote(),[W,X]))
        self.bot.pause(False);self.assertTrue(self.bot.buy(quote(),[W,X]))
    def test_daily_circuit(self):
        self.bot.buy(quote(),[W,X]);st=self.db.state();st['day_start']=1500
        st['day']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d')
        self.db.save_state(st);self.assertFalse(self.bot.buy(Quote(X,.001,15000,10000,'pump'),[W,X]))
    def test_score_computation(self):
        self.bot.buy(quote(),[W,X]);self.bot.exit(M,quote(p=.0015),1,'test')
        board=self.db.leaderboard();self.assertEqual(len(board),2)
        self.assertTrue(all(x['trades']==1 for x in board))
    def test_seen_and_checkpoints(self):
        self.assertFalse(self.db.has_wallet_checkpoint(W))
        self.db.mark_seen(W,['a','a']);self.assertTrue(self.db.has_seen(W,'a'))
        self.assertTrue(self.db.has_wallet_checkpoint(W))
    def test_curve_owner(self):
        self.db.remember_mint(M,W);self.assertEqual(self.db.curve_owner(M),W)
        self.db.remember_mint(M,'');self.assertEqual(self.db.curve_owner(M),W)
    def test_broker_still_disabled_no_secret(self):
        self.assertFalse(hasattr(self.bot,'send_transaction'))

class TxTests(unittest.TestCase):
    def test_program_resolution(self):self.assertIn(PUMP_PROGRAM,_programs(tx()))
    def test_buy(self):
        out=extract_wallet_trades(tx(after=[token(M,1000000)]),W,'SIG',T)
        self.assertEqual(len(out),1);self.assertEqual(out[0].side,'buy')
    def test_pumpswap(self):
        out=extract_wallet_trades(tx(program=PUMPSWAP_PROGRAM,after=[token(M,1000000)]),W,'SIG')
        self.assertEqual(len(out),1)
    def test_jupiter(self):self.assertEqual(len(extract_wallet_trades(tx(program=JUPITER_V6,after=[token(M,1000000)]),W,'SIG')),1)
    def test_sell(self):
        out=extract_wallet_trades(tx(before=[token(M,1000000)],native_post=2100000000),W,'SIG')
        self.assertEqual(len(out),1);self.assertEqual(out[0].side,'sell')
    def test_no_fake_transfer(self):
        out=extract_wallet_trades(tx(program='11111111111111111111111111111111',after=[token(M,1000000)]),W,'SIG')
        self.assertEqual(out,[])
    def test_no_tx_failed(self):self.assertEqual(extract_wallet_trades(tx(after=[token(M,1000000)],err={'InstructionError':[0,'failed']}),W,'SIG'),[])
    def test_no_fee_only(self):
        self.assertEqual(extract_wallet_trades(tx(after=[token(M,1000000)],native_post=1999995000),W,'SIG'),[])
    def test_no_nonowner(self):
        self.assertEqual(extract_wallet_trades(tx(after=[token(M,1000000,owner=X)]),W,'SIG'),[])
    def test_no_zero_delta(self):
        self.assertEqual(extract_wallet_trades(tx(before=[token(M,500000)],after=[token(M,500000)]),W,'SIG'),[])
    def test_usdc_spend(self):
        obj=tx(before=[token(USDC,2_000_000)],after=[token(M,1_000_000),token(USDC,500_000)],native_post=1999995000)
        self.assertEqual(extract_wallet_trades(obj,W,'SIG')[0].side,'buy')
    def test_null_tx(self):self.assertEqual(extract_wallet_trades({},W,'SIG'),[])

class WalletPollTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Database(str(Path(self.temp.name)/'a.sqlite'))
        class Client:
            rows=[{'signature':'s1'}];calls=[];fail=False
            def signatures(self,*args):return self.rows
            def transaction(self,sig):
                self.calls.append(sig)
                if self.fail:raise RuntimeError('temporary')
                return tx(after=[token(M,1000000)])
        self.client=Client();self.poller=WalletPoller(self.client,self.db)
    def test_first_poll_baselines(self):
        self.assertEqual(self.poller.poll(W),[])
        self.assertEqual(self.client.calls,[])
    def test_new_buy_one_shot(self):
        self.poller.poll(W);self.client.rows=[{'signature':'s2'},{'signature':'s1'}]
        self.assertEqual(len(self.poller.poll(W)),1)
        self.assertEqual(len(self.poller.poll(W)),0)
    def test_retries_rpc_failure(self):
        self.poller.poll(W);self.client.rows=[{'signature':'s2'}];self.client.fail=True
        self.assertEqual(self.poller.poll(W),[]);self.assertFalse(self.db.has_seen(W,'s2'))
        self.client.fail=False;self.assertEqual(len(self.poller.poll(W)),1)
    def test_restart_checkpoint(self):
        self.poller.poll(W);self.assertTrue(self.db.has_seen(W,'s1'))

class RiskTests(unittest.TestCase):
    def setUp(self):self.gate=SecurityGate('https://a',max_rug_score=20,require_rugcheck=True)
    def test_fail_closed_rpc(self):
        class Bad:
            def examine(self,*a):return type('R',(),{'allowed':False,'reasons':['offline']})()
        self.gate.rpc=Bad()
        from unittest.mock import patch
        with patch('hunter2.security.get_json',return_value={'score_normalised':0,'risks':[]}):
            r=self.gate.examine(M,W)
        self.assertFalse(r.approved)
    def test_reject_score(self):
        class Good:
            def examine(self,*a):return type('R',(),{'allowed':True,'reasons':[]})()
        self.gate.rpc=Good()
        from unittest.mock import patch
        with patch('hunter2.security.get_json',return_value={'score_normalised':55,'risks':[]}):
            self.assertFalse(self.gate.examine(M,W).approved)
    def test_accept_when_complete(self):
        class Good:
            def examine(self,*a):return type('R',(),{'allowed':True,'reasons':[]})()
        self.gate.rpc=Good()
        from unittest.mock import patch
        with patch('hunter2.security.get_json',return_value={'score_normalised':10,'risks':[]}):
            self.assertTrue(self.gate.examine(M,W).approved)
    def test_reject_critical(self):
        class Good:
            def examine(self,*a):return type('R',(),{'allowed':True,'reasons':[]})()
        self.gate.rpc=Good()
        from unittest.mock import patch
        with patch('hunter2.security.get_json',return_value={'score_normalised':10,'risks':[{'name':'unsafe','level':'danger'}]}):
            self.assertFalse(self.gate.examine(M,W).approved)
    def test_reject_missing_rugcheck(self):
        class Good:
            def examine(self,*a):return type('R',(),{'allowed':True,'reasons':[]})()
        self.gate.rpc=Good()
        from unittest.mock import patch
        with patch('hunter2.security.get_json',side_effect=ConnectionError):
            self.assertFalse(self.gate.examine(M,W).approved)

class SettingsReplayTests(unittest.TestCase):
    def test_replay_nonempty(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(__file__).parent.parent/'fixtures/synthetic_replay.jsonl'
            r=replay(path,Settings(),str(Path(t)/'replay.sqlite'))
            self.assertEqual(r['events'],7);self.assertEqual(r['open_positions'],0)
    def test_validation_missing_paid_credentials(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'config.json';p.write_text(json.dumps({'enable_paid_wallet_stream':True}))
            from unittest.mock import patch
            with patch.dict(os.environ,{},clear=True):
                with self.assertRaises(ValueError):load_settings(str(p))
    def test_validation_no_single_leader(self):
        c=Settings(min_distinct_wallets=1)
        with self.assertRaises(AssertionError):c.validate()
if __name__=='__main__':unittest.main()

class AnalyticsTests(unittest.TestCase):
    def test_empty_report_is_not_false_positive(self):
        from hunter2.analytics import trading_report
        with tempfile.TemporaryDirectory() as t:
            r=trading_report(str(Path(t)/'empty.sqlite'))
            self.assertIsNone(r['win_rate']);self.assertEqual(r['realized_pnl'],0)
            self.assertIn('Paper fills',r['warnings'][0])
    def test_synthetic_evaluation_never_promotes(self):
        from hunter2.analytics import evaluate_events
        p=Path(__file__).parent.parent/'fixtures/synthetic_replay.jsonl'
        r=evaluate_events(str(p),Settings())
        self.assertFalse(r['promotion_allowed']);self.assertEqual(len(r['results']),4)
    def test_double_replay_protection(self):
        with tempfile.TemporaryDirectory() as t:
            d=str(Path(t)/'replay.sqlite');p=Path(__file__).parent.parent/'fixtures/synthetic_replay.jsonl'
            replay(str(p),Settings(),d)
            with self.assertRaises(FileExistsError):replay(str(p),Settings(),d)
