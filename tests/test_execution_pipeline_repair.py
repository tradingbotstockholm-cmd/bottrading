"""Deterministic RPC-format fixtures, NOT recordings or live-market fills.

Exercise the real decoder, scout, consensus, quote parser, risk gate, broker,
SQLite, restart and HTTP dashboard. Only remote I/O is replaced.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from hunter.models import Quote, RiskResult
from hunter.http import RemoteError, post_json
from hunter.signals import Consensus
from hunter2.chain import SolanaClient, WalletPoller, extract_wallet_trades, PUMP_PROGRAM, shared_client, _CLIENTS
from hunter2.db import Database
from hunter2.engine import Settings, load_settings
from hunter2.discovery import TraderScout
from hunter2.security import SecurityGate, SecurityVerdict
from hunter4.__main__ import PaperHunterV4
from hunter4.paper import CapitalRecoveryBroker
from hunter2.broker import Broker
from hunter.rug import SolanaRPC

M='6NLUC3veg7NGuQBNewE1UM7G6ir2mymJM4PvwdkGpump'
A='4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU'
B='D3hV8K7V1tqkVN3qxET9PnARFPHhnTJvBGbQQSNbqLXT'
from hunter2.bonding_quote import canonical_curve_address
CURVE=canonical_curve_address(M)
CREATOR='C'*44

def transaction(wallet,stamp):
    # Representative jsonParsed format; these numeric balances are synthetic.
    return {'slot':900,'blockTime':stamp,
        'transaction':{'message':{'accountKeys':[{'pubkey':wallet,'signer':True},
            {'pubkey':PUMP_PROGRAM,'signer':False}], 'instructions':[{'programIdIndex':1,'accounts':[0]}]}},
        'meta':{'err':None,'preBalances':[2_000_000_000,0],'postBalances':[1_900_000_000,0],
            'preTokenBalances':[], 'postTokenBalances':[{'mint':M,'owner':wallet,
                'uiTokenAmount':{'amount':'1000000','decimals':6}}]}}

def pair(price='.001',liquidity=1754):
    return {'chainId':'solana','baseToken':{'address':M},'priceUsd':price,'marketCap':80000,
            'liquidity':{'usd':liquidity},'dexId':'pumpfun'}

def quote(price=.001,liquidity=1754):return Quote(M,price,80000,liquidity,'pumpfun')

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'paper.sqlite'
        self.cfg=Settings(db_path=str(self.path),min_liquidity_usd=100,max_open_positions=200,
                          assumed_slippage_pct=.0035,enable_fomo_research_feed=False)
        self.bot=PaperHunterV4(self.cfg);self.addCleanup(self.bot.db.close)

    def test_complete_rpc_format_launch_to_buy_exit_restart_and_dashboard(self):
        async def scenario():
            now=time.time()
            await self.bot.event({'mint':M,'txType':'create','bondingCurveKey':CURVE,'traderPublicKey':CREATOR})
            with self.bot.db.conn:
                self.bot.db.conn.execute('UPDATE candidate_mints SET observed=?',(now-20,))
            client=SolanaClient('https://fixture.invalid')
            self.bot.scout=TraderScout(client,self.bot.db,extract_wallet_trades,min_token_age_seconds=12)
            responses={'a':transaction(A,now-4),'b':transaction(B,now-2)}
            def remote(url,payload):
                if payload['method']=='getSignaturesForAddress':return {'result':[{'signature':s,'err':None} for s in responses]}
                return {'result':responses[payload['params'][0]]}
            with patch('hunter2.chain.post_json',side_effect=remote),patch('hunter2.chain.time.sleep'):
                stats=self.bot.scout.run_cycle(1,now)
            self.assertEqual(stats.confirmed_trades,2)
            self.bot.accept_fresh_scout_signals(stats)
            self.assertTrue(self.bot.consensus.consensus(M,2))
            # Real security policy, deterministic evidence returned by each I/O boundary.
            self.bot.security.rpc.examine=lambda *_:RiskResult(True,[],.1)
            with patch('hunter.dex.get_json',return_value=[pair()]),patch('hunter2.security.get_json',return_value={'score_normalised':0,'risks':[]}):
                await self.bot.tick()
            buy=self.bot.db.recent_fills()[0]
            self.assertEqual(buy['side'],'buy')
            position=self.bot.db.state()['positions'][M]
            self.assertGreaterEqual(position['basis'],2)
            self.assertLessEqual(self.bot.broker._slip(position['basis'],1754),.0075+1e-12)
            # A rule-based -20% market quote invokes the actual V4 stop loss.
            self.bot.last_risk[M]=time.time()
            with patch('hunter.dex.get_json',return_value=[pair('.0008')]):await self.bot.check_positions()
            fills=list(reversed(self.bot.db.recent_fills()))
            self.assertEqual([f['side'] for f in fills],['buy','sell'])
            self.assertEqual(self.bot.db.state()['positions'],{})
            self.assertAlmostEqual(self.bot.db.state()['cash'],1000+sum(f['net_usd'] for f in fills))
            self.assertAlmostEqual(fills[1]['pnl_usd'],fills[1]['net_usd']+fills[0]['net_usd'])
            before=self.bot.db.snapshot()
            self.assertEqual(before['performance']['paper_buy_fills'],1)
            self.assertEqual(before['performance']['paper_sell_fills'],1)
            stages={(x['stage'],x['code']) for x in before['execution_diagnostics']['counts']}
            for stage in [('consensus','confirmed'),('quote','verified'),('security','approved'),('broker','approved'),('paper','filled')]:self.assertIn(stage,stages)
            restarted=Database(self.path);self.addCleanup(restarted.close)
            self.assertEqual(restarted.state(),self.bot.db.state())
            # Exercise actual HTTP handler and compare ledger values, not only PAGE text.
            from hunter4 import dashboard
            from http.server import ThreadingHTTPServer
            servers=[]
            def bind(address,handler):
                server=ThreadingHTTPServer(('127.0.0.1',0),handler);servers.append(server);return server
            with patch.object(dashboard,'ThreadingHTTPServer',side_effect=bind):
                thread=threading.Thread(target=dashboard.serve,args=(str(self.path),0,1000),daemon=True);thread.start()
                try:
                    for _ in range(100):
                        if servers:break
                        time.sleep(.01)
                    with urlopen(f'http://127.0.0.1:{servers[0].server_port}/api/state',timeout=2) as response:actual=json.load(response)
                    for key in ['cash','positions','fills','performance','execution_diagnostics']:
                        self.assertEqual(actual[key],before[key])
                finally:
                    if servers:servers[0].shutdown()
                    thread.join(3)
        asyncio.run(scenario())

    def test_oversized_target_is_capped_not_rejected(self):
        self.assertTrue(self.bot.broker.buy(quote(),[A,B],signal_strength=1))
        allocation=self.bot.db.state()['positions'][M]['basis']
        self.assertAlmostEqual(allocation,1754*.008)
        self.assertLessEqual(self.bot.broker._slip(allocation,1754),.0075+1e-12)

    def test_impossible_base_slippage_is_visible_and_never_filled(self):
        self.cfg.assumed_slippage_pct=.01
        self.assertFalse(self.bot.broker.buy(quote(),[A,B],signal_strength=1))
        self.assertEqual(self.bot.db.recent_fills(),[])
        self.assertEqual(self.bot.broker.last_rejection['code'],'estimated_price_impact_too_high')

    def test_low_liquidity_cannot_force_two_dollar_entry(self):
        for liquidity in [93,100,124,154]:
            self.assertFalse(self.bot.broker.buy(quote(liquidity=liquidity),[A,B],signal_strength=0))
        self.assertEqual(self.bot.db.recent_fills(),[])

    def test_rejection_codes(self):
        cases=[(None,[A,B],.5,'price_unavailable'),
               (quote(liquidity=0),[A,B],.5,'liquidity_unavailable'),
               (quote(liquidity=93),[A,B],.5,'liquidity_below_minimum'),
               (quote(),[A,A],.5,'consensus_missing_or_expired'),
               (quote(),[A,B],float('nan'),'invalid_confidence_score')]
        old=quote();old.timestamp-=1000
        cases.append((old,[A,B],.5,'quote_stale'))
        for q,w,strength,code in cases:
            with self.subTest(code=code):
                self.assertFalse(self.bot.broker.buy(q,w,signal_strength=strength))
                self.assertEqual(self.bot.broker.last_rejection['code'],code)
        self.bot.broker.pause(True)
        self.assertFalse(self.bot.broker.buy(quote(),[A,B],signal_strength=0))
        self.assertEqual(self.bot.broker.last_rejection['code'],'trading_paused')

    def test_security_latency_cannot_refresh_expired_votes(self):
        self.bot.consensus.record(M,A,'buy');self.bot.consensus.record(M,B,'buy')
        def slow(*args):
            self.bot.consensus.events[M]={A:time.time()-121,B:time.time()-121}
            return SecurityVerdict(True,(),0)
        self.bot.security.examine=slow
        with patch('hunter2.engine.fetch_quote',return_value=quote()):asyncio.run(self.bot.try_entry(M))
        self.assertEqual(self.bot.db.recent_fills(),[])
        self.assertEqual(self.bot.db.execution_diagnostics()['recent'][0]['code'],'consensus_missing_or_expired')

    def test_security_latency_cannot_make_stale_quote_executable(self):
        self.bot.consensus.record(M,A,'buy');self.bot.consensus.record(M,B,'buy')
        q=quote()
        def slow(*args):q.timestamp-=1000;return SecurityVerdict(True,(),0)
        self.bot.security.examine=slow
        with patch('hunter2.engine.fetch_quote',return_value=q):asyncio.run(self.bot.try_entry(M))
        self.assertEqual(self.bot.db.recent_fills(),[])
        self.assertEqual(self.bot.broker.last_rejection['code'],'quote_stale')

    def test_future_votes_and_websocket_hints_cannot_authorize(self):
        c=Consensus();c.record(M,A,'buy',time.time()+1);c.record(M,B,'buy')
        self.assertFalse(c.consensus(M,2))
        self.bot.tracked=[A,B]
        for wallet in [A,B]:asyncio.run(self.bot.event({'mint':M,'traderPublicKey':wallet,'txType':'buy'}))
        self.assertFalse(self.bot.consensus.consensus(M,2))

    def test_entry_scheduler_does_not_starve_later_candidates(self):
        self.cfg.max_price_checks_per_cycle=1
        for mint in ['7'*44,'8'*44]:
            for wallet in [A,B]:self.bot.consensus.record(mint,wallet,'buy')
        attempted=[]
        async def entry(mint):attempted.append(mint);self.bot.last_attempt[mint]=time.time()
        with patch.object(self.bot,'_bounded_entry',side_effect=entry):
            asyncio.run(self.bot.tick());asyncio.run(self.bot.tick())
        self.assertEqual(len(set(attempted)),2)

    def test_duplicate_parallel_buys_persist_one_fill(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.bot.broker.buy(quote(),[A,B],signal_strength=0),range(20)))
        self.assertEqual(sum(results),1);self.assertEqual(len(self.bot.db.recent_fills()),1)
        self.assertAlmostEqual(self.bot.db.state()['cash'],998)

    def test_unfilled_partial_exit_does_not_consume_take_profit(self):
        db=Database(':memory:');self.addCleanup(db.close)
        broker=Broker(db,self.cfg)
        self.assertTrue(broker.buy(quote(liquidity=30000),[A,B]))
        broker.mark(quote(price=.0021,liquidity=10))
        self.assertFalse(db.state()['positions'][M]['partial2'])
        self.assertEqual(db.state()['positions'][M]['pending_exit_fraction'],.25)
        broker.mark(quote(price=.0021,liquidity=30000))
        self.assertTrue(db.state()['positions'][M]['partial2'])
        self.assertEqual(len(db.recent_fills()),2)

    def test_critical_risk_survives_later_rpc_outage(self):
        gate=SecurityGate('https://fixture.invalid',paper_exploratory=True)
        def rpc(method,params):
            if method=='getAccountInfo':return {'value':{'data':{'parsed':{'type':'mint','info':{'mintAuthority':A,'freezeAuthority':None,'supply':'1000000'}}}}}
            raise RemoteError('HTTP 429',status=429)
        with patch.object(gate.rpc,'call',side_effect=rpc),patch('hunter2.security.get_json',return_value={'score_normalised':0,'risks':[]}):result=gate.examine(M,CURVE)
        self.assertFalse(result.approved)
        self.assertTrue(any('mint authority active' in r for r in result.reasons))

    def test_pending_wallet_retry_survives_rpc_window_eviction_and_restart(self):
        class Client:
            rows=[{'signature':'old'}];fail=True
            def signatures(self,*args):return self.rows
            def transaction(self,sig):
                if self.fail:raise RemoteError('429',status=429)
                return transaction(A,time.time()-5)
        client=Client();poller=WalletPoller(client,self.bot.db)
        poller.poll(A)
        client.rows=[{'signature':'new'}];self.assertEqual(poller.poll(A),[])
        client.rows=[];client.fail=False
        reopened=Database(self.path);self.addCleanup(reopened.close)
        signals=WalletPoller(client,reopened).poll(A)
        self.assertEqual(len(signals),1)
        self.assertLess(signals[0].observed_at,time.time()-4)
        self.assertTrue(reopened.has_seen(A,'new'))
        self.assertEqual(reopened.pending_signatures(A,20),[])

    def test_wallet_old_missing_future_or_nonsigner_transactions_cannot_vote(self):
        class Client:
            obj=None
            def signatures(self,*args):return [{'signature':str(self.obj)}]
            def transaction(self,*args):return self.obj
        client=Client();poller=WalletPoller(client,self.bot.db);self.bot.db.mark_seen(A,[])
        for stamp in [None,time.time()-300,time.time()+60]:
            client.obj=transaction(A,stamp)
            self.assertEqual(poller.poll(A),[])
        client.obj=transaction(A,time.time());client.obj['transaction']['message']['accountKeys'][0]['signer']=False
        self.assertEqual(poller.poll(A),[])

    def test_consumed_consensus_cannot_rebuy_after_exit(self):
        for wallet in [A,B]:self.bot.consensus.record(M,wallet,'buy')
        self.bot.security.examine=lambda *_:SecurityVerdict(True,(),0)
        with patch('hunter2.engine.fetch_quote',return_value=quote()):asyncio.run(self.bot.try_entry(M))
        self.assertTrue(self.bot.broker.exit(M,quote(),reason='test') is not None)
        self.bot.last_attempt.clear()
        with patch('hunter2.engine.fetch_quote',return_value=quote()):asyncio.run(self.bot.tick())
        self.assertEqual([f['side'] for f in reversed(self.bot.db.recent_fills())],['buy','sell'])

    def test_multiple_connections_share_broker_lock(self):
        other=Database(self.path);self.addCleanup(other.close)
        second=CapitalRecoveryBroker(other,self.cfg)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda broker:broker.buy(quote(),[A,B],signal_strength=0),[self.bot.broker,second]))
        self.assertEqual(sum(results),1)
        self.assertEqual(len(other.recent_fills()),1)

    def test_account_rejection_codes_are_specific(self):
        initial=self.bot.db.state()
        for code,changes in [('insufficient_simulated_cash',{'cash':0}),
                             ('position_limit_reached',{'positions':{'other':{'units':1,'last_price':1,'basis':1}}})]:
            self.bot.db.save_state(initial|changes)
            self.cfg.max_open_positions=1 if code=='position_limit_reached' else 200
            self.assertFalse(self.bot.broker.buy(quote(),[A,B],signal_strength=0))
            self.assertEqual(self.bot.broker.last_rejection['code'],code)
        self.cfg.max_open_positions=200
        st=deepcopy(initial)
        st['positions']={'other':{'units':1,'last_price':1,'basis':1000}}
        self.bot.db.save_state(st)
        self.assertFalse(self.bot.broker.buy(quote(),[A,B],signal_strength=0))
        self.assertEqual(self.bot.broker.last_rejection['code'],'portfolio_exposure_exceeded')

    def test_scout_retry_is_not_lost_when_provider_window_moves(self):
        now=time.time()
        self.bot.db.remember_mint(M,CURVE)
        with self.bot.db.conn:self.bot.db.conn.execute('UPDATE candidate_mints SET observed=?',(now-20,))
        class Client:
            fail=True
            def signatures(self,*args):return [{'signature':'pending-scout'}] if self.fail else []
            def transaction(self,*args):
                if self.fail:raise RemoteError('429',status=429)
                return transaction(A,now-2)
        client=Client();scout=TraderScout(client,self.bot.db,extract_wallet_trades,min_token_age_seconds=12)
        self.assertEqual(scout.run_cycle(1,now).rpc_errors,1)
        self.assertEqual(self.bot.db.pending_signatures('scout:'+M,12),['pending-scout'])
        client.fail=False
        result=scout.run_cycle(1,now+1)
        self.assertEqual(len(result.live_signals),1)
        self.assertEqual(self.bot.db.pending_signatures('scout:'+M,12),[])

    def test_forged_curve_cannot_exempt_attacker_concentration(self):
        gate=SecurityGate('https://fixture.invalid',paper_exploratory=True)
        gate.rpc.examine=lambda *_:RiskResult(True,[],0)
        with patch('hunter2.security.get_json',return_value={'score_normalised':0,'risks':[]}):
            self.assertFalse(gate.examine(M,A).approved)
            self.assertTrue(gate.examine(M,CURVE).approved)

    def test_principal_metadata_and_fill_rollback_and_recover_together(self):
        import sqlite3
        self.assertTrue(self.bot.broker.buy(quote(liquidity=30000),[A,B],signal_strength=0))
        before=self.bot.db.state()
        self.bot.db.conn.executescript("CREATE TRIGGER fail_sell BEFORE INSERT ON fills WHEN NEW.side='sell' BEGIN SELECT RAISE(ABORT,'injected write failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            self.bot.broker.exit(M,quote(.0023,30000),.5,'principal_recovery')
        self.assertEqual(self.bot.db.state(),before)
        self.assertEqual(len(self.bot.db.recent_fills()),1)
        self.bot.db.conn.execute('DROP TRIGGER fail_sell');self.bot.db.conn.commit()
        self.assertIsNotNone(self.bot.broker.exit(M,quote(.0023,30000),.5,'principal_recovery'))
        reopened=Database(self.path);self.addCleanup(reopened.close)
        position=reopened.state()['positions'][M]
        self.assertEqual(position['proceeds_usd'],reopened.recent_fills()[0]['net_usd'])
        self.assertTrue(position['principal_done'])
        self.assertEqual(position['initial_basis_usd'],2)

    def test_example_policy_is_loadable_and_paper_only(self):
        cfg=load_settings(Path(__file__).resolve().parents[1]/'config.v4.example.json')
        self.assertEqual(cfg.max_open_positions,200);self.assertEqual(cfg.min_liquidity_usd,100)
        self.assertLess(cfg.assumed_slippage_pct,cfg.max_paper_slippage_pct)
        self.assertFalse(cfg.enforce_paper_market_cap)
        self.assertFalse(cfg.enable_paid_wallet_stream or cfg.enable_paid_token_stream)

class SharedRpcTests(unittest.TestCase):
    def setUp(self):_CLIENTS.clear()
    def tearDown(self):_CLIENTS.clear()
    def test_security_and_scanner_share_method_cooldown(self):
        client=shared_client('https://shared.invalid')
        with patch('hunter2.chain.post_json',side_effect=RemoteError('429',status=429,retry_after=900)):
            with self.assertRaises(RemoteError):client.call('getAccountInfo',[M])
        with patch('hunter.rug.post_json') as remote:
            with self.assertRaises(RemoteError):SolanaRPC('https://shared.invalid').call('getAccountInfo',[M])
            remote.assert_not_called()
        self.assertGreater(client._cooldown_until['getAccountInfo']-time.monotonic(),899)
    def test_concurrent_transaction_requests_are_deduplicated(self):
        client=SolanaClient('https://shared.invalid')
        with patch('hunter2.chain.post_json',return_value={'result':{'blockTime':100}}) as remote,patch('hunter2.chain.time.sleep'):
            with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(client.transaction,['sig']*5))
        self.assertEqual(remote.call_count,1)
        self.assertTrue(all(r=={'blockTime':100} for r in results))
    def test_waiting_for_busy_rpc_has_a_timeout(self):
        client=SolanaClient('https://shared.invalid');client.lock_wait_timeout=.01
        client._lock.acquire()
        try:
            with self.assertRaisesRegex(RemoteError,'concurrency wait timeout'):
                client.transaction('sig')
        finally:client._lock.release()

    def test_jsonrpc_429_preserves_rate_limit_status_in_real_http_wrapper(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"error":{"code":429,"message":"limited"}}'
        client=SolanaClient('https://shared.invalid')
        with patch('hunter.http.urllib.request.urlopen',return_value=Response()):
            with self.assertRaises(RemoteError) as caught:client.transaction('sig')
        self.assertEqual(caught.exception.status,429)
        self.assertGreater(client._cooldown_until['getTransaction'],time.monotonic())

if __name__=='__main__':unittest.main()
