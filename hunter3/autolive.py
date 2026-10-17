"""Experimental, explicitly armed real-order loop reusing V2 signal/rug pipeline.

No direct connection to FOMO app order execution exists. Uses isolated PumpPortal
Lightning account only. All fills require confirmed RPC + token balance delta.
"""
from __future__ import annotations
import asyncio,datetime,logging,math,os,time,fcntl
from pathlib import Path
from hunter.dex import fetch_quote
from hunter2.engine import Hunter,Settings
from hunter2.chain import WSOL
from .orders import LiveLimits,LiveLedger,LightningClient,LiveGateway,LiveHalt,token_balance
from hunter4.principal import plan_principal_recovery,defensive_exit

log=logging.getLogger('hunter3.live')

class LiveHunter(Hunter):
    def accept_fresh_scout_signals(self, stats):
        """Never let discovery-stage PAPER votes arm funded transactions.

        A scout's signer/DEX check is not a fully audited, profitable trader.
        Live trading retains the original explicit wallet-consensus route and
        independent risk/settlement safeguards.
        """
        return None

    def __init__(self,cfg:Settings,limits:LiveLimits):
        if os.getenv('HUNTER_CONFIRM_LIVE')!='YES_I_UNDERSTAND_THE_RISK':
            raise LiveHalt('Autonomous live trading requires explicit HUNTER_CONFIRM_LIVE opt-in')
        if not Path('data/LIVE_ARMED_BY_USER').exists():
            raise LiveHalt('Create data/LIVE_ARMED_BY_USER manually; live mode remains disarmed')
        if not limits.enabled:raise LiveHalt('live.json enabled=false')
        # Locking is necessary because two trading processes could otherwise race
        # between the spend check and the HTTP submission.
        Path('data').mkdir(exist_ok=True)
        self._single_instance_lock=open('data/live_runner.lock','a+')
        try:fcntl.flock(self._single_instance_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self._single_instance_lock.close()
            raise LiveHalt('Another live trading runner owns the exclusive process lock')
        if not cfg.require_rugcheck or cfg.rug_score_ceiling>limits.rug_score_ceiling:
            raise LiveHalt('Scanner risk policy weaker than live risk policy')
        if cfg.mode!=limits.mode:raise LiveHalt('Paper and live mode disagreement')
        if cfg.enable_paid_wallet_stream and not os.getenv('PUMPPORTAL_API_KEY'):
            raise LiveHalt('Paid wallet stream needs separately configured credentials')
        super().__init__(cfg)
        self.limits=limits
        self.live_ledger=LiveLedger(limits.db_path)
        self.gateway=LiveGateway(limits,self.live_ledger,self.client,LightningClient(os.getenv('PUMPPORTAL_API_KEY','')))
        self.recent_sell={}  # prevent back-to-back duplicate requests
        self.entry_attempts={}
        self.max_network_confirmation_seconds=30

    async def confirmed(self,oid,sig):
        for _ in range(15):
            try:
                if await asyncio.to_thread(self.gateway.confirm,oid,sig):return True
            except Exception:pass
            await asyncio.sleep(2)
        self.live_ledger.set_flag('halt','1')
        self.db.observe('','live_unknown','Broadcast but confirmation unavailable. Do not retry. Stop and reconcile.')
        return False

    async def settled_balance(self,mint,prior,direction):
        for _ in range(10):
            try:
                result=await asyncio.to_thread(token_balance,self.client,self.limits.wallet_address,mint)
                if (direction=='buy' and result>prior) or (direction=='sell' and result<prior):return result
            except Exception:pass
            await asyncio.sleep(1)
        self.live_ledger.set_flag('halt','1')
        raise LiveHalt('Token balance did not reconcile after confirmation')

    async def try_entry(self,mint):
        now=time.time()
        if now-self.entry_attempts.get(mint,0)<max(30,self.limits.cooldown_seconds):return
        self.entry_attempts[mint]=now
        voters=self.consensus.voters(mint)
        if len(set(voters))<self.limits.min_wallet_consensus:return
        try:
            q=await asyncio.to_thread(fetch_quote,mint)
            if q is None:return
            curve=self.db.curve_owner(mint)
            if not curve:return  # require observed curve address
            verdict=await asyncio.to_thread(self.security.examine,mint,curve)
            if not verdict.approved:return
            sol_q=await asyncio.to_thread(fetch_quote,WSOL)
            solusd=sol_q.price_usd if sol_q else 0
            amount=await asyncio.to_thread(self.gateway.plan_buy,q,len(set(voters)),True,solusd)
            before_sol=await asyncio.to_thread(self.gateway.balance_sol)
            before=await asyncio.to_thread(token_balance,self.client,self.limits.wallet_address,mint)
            oid,sig=await asyncio.to_thread(self.gateway.submit,'buy',mint,amount)
            self.db.observe(mint,'LIVE_SUBMITTED_BUY',f'order={oid} amountSOL={amount} tx={sig}')
            if not await self.confirmed(oid,sig):return
            after=await self.settled_balance(mint,before,'buy')
            after_sol=await asyncio.to_thread(self.gateway.balance_sol)
            spent_sol=before_sol-after_sol
            if not (0<spent_sol<amount*1.15+self.limits.max_priority_fee_sol+0.001):
                self.live_ledger.set_flag('halt','1')
                raise LiveHalt('Could not reconcile actual SOL expenditure: manual review required')
            cost_usd=spent_sol*solusd
            self.live_ledger.finalize_filled_buy(oid,mint,after-before,spent_sol,solusd,before)
            self.db.observe(mint,'LIVE_CONFIRMED_BUY',f'order={oid} units={after-before:.6f} spentSOL={spent_sol:.9f} sig={sig}')
        except LiveHalt as e:self.db.observe(mint,'live_entry_block',str(e))
        except Exception as e:self.db.record_error('live_buy',type(e).__name__)

    async def sell(self,mint,p,percentage,reason,quote=None):
        if time.time()-self.recent_sell.get(mint,0)<12:return
        self.recent_sell[mint]=time.time()
        try:
            if quote is None:quote=await asyncio.to_thread(fetch_quote,mint)
            if quote is None or quote.liquidity_usd<=0:raise LiveHalt('No known executable liquidity')
            balance=await asyncio.to_thread(token_balance,self.client,self.limits.wallet_address,mint)
            sol_before=await asyncio.to_thread(self.gateway.balance_sol)
            tracked=p['units']
            if abs(balance-tracked-p['token_balance_before'])>max(0.00001,tracked*0.0005):
                raise LiveHalt('Out-of-band token change detected; refuse potentially wrong sell amount')
            to_sell=tracked*percentage/100.
            of_wallet=to_sell/balance*100 if balance>0 else 0
            if not 0<of_wallet<=100:raise LiveHalt('Invalid sale percentage')
            # Approximate planned USD size must not exceed modeled liquid-exit fraction.
            if to_sell*quote.price_usd>quote.liquidity_usd*self.limits.max_order_usd_liquidity_fraction:
                raise LiveHalt('Exit liquidity fraction exceeds configured risk tolerance; position remains at risk')
            oid,sig=await asyncio.to_thread(self.gateway.submit,'sell',mint,of_wallet)
            self.db.observe(mint,'LIVE_SUBMITTED_SELL',f'order={oid} reason={reason} sig={sig}')
            if not await self.confirmed(oid,sig):return
            after=await self.settled_balance(mint,balance,'sell')
            sold=balance-after
            if sold<=0 or sold>tracked*1.01:raise LiveHalt('Unexpected amount sold')
            sol_after=await asyncio.to_thread(self.gateway.balance_sol)
            received_sol=sol_after-sol_before
            if not (received_sol>0 and math.isfinite(received_sol)):
                self.live_ledger.set_flag('halt','1')
                raise LiveHalt('SOL sale proceeds missing; transaction cannot be credited')
            solq=await asyncio.to_thread(fetch_quote,WSOL)
            if solq is None or solq.price_usd<=0:
                self.live_ledger.set_flag('halt','1')
                raise LiveHalt('SOL/USD oracle unavailable after sale; settlement requires review')
            proceeds_usd=received_sol*solq.price_usd
            recovered_credit_sol=self.live_ledger.finalize_filled_sell(
                oid,mint,sold,received_sol,solq.price_usd,reason=reason,
                high_price=max(p['high_price_usd'],quote.price_usd))
            self.db.observe(mint,'LIVE_CONFIRMED_SELL',f'order={oid} sold={sold:.6f} proceedsSOL={received_sol:.9f} principalCreditSOL={recovered_credit_sol:.9f} reason={reason} sig={sig}')
        except LiveHalt as e:
            self.db.observe(mint,'live_sell_block',str(e))
        except Exception as e:
            self.live_ledger.set_flag('halt','1')
            self.db.record_error('live_sell_unknown',type(e).__name__)

    async def check_positions(self):
        positions=self.live_ledger.positions()
        if not positions:return
        solq=await asyncio.to_thread(fetch_quote,WSOL)
        if solq is None or solq.price_usd<=0:
            self.live_ledger.set_flag('risk_pause','1')
            self.db.observe('','live_risk','SOL price unavailable; new buys blocked')
            return
        solusd=solq.price_usd
        balance=await asyncio.to_thread(self.gateway.balance_sol)
        worth=balance*solusd
        quotes={}
        for mint,p in positions.items():
            try:
                q=await asyncio.to_thread(fetch_quote,mint)
                if q is None:raise LiveHalt('No quote; cannot safely trade')
                worth+=p['units']*q.price_usd;quotes[mint]=q
            except Exception:
                self.live_ledger.set_flag('risk_pause','1')
                self.db.observe(mint,'live_risk','Missing price, new buys paused')
                return
        date=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
        if self.live_ledger.flag('day')!=date:
            self.live_ledger.set_flag('day',date)
            self.live_ledger.set_flag('day_start_usd',repr(worth))
        peak=max(worth,float(self.live_ledger.flag('peak_usd',str(worth))))
        self.live_ledger.set_flag('peak_usd',repr(peak))
        day_start=float(self.live_ledger.flag('day_start_usd',repr(worth)))
        circuit=(worth<=day_start*(1-self.limits.max_daily_loss_pct) or worth<=peak*(1-self.limits.max_drawdown_pct))
        if circuit:
            self.live_ledger.set_flag('risk_pause','1')
            self.db.observe('','live_circuit','Equity drawdown limit crossed; entries paused, sell checks continue')
        for mint,p in positions.items():
            q=quotes[mint]
            high=max(p['high_price_usd'],q.price_usd)
            self.live_ledger.update_position(mint,p['units'],high)
            ratio=q.price_usd/p['entry_price_usd']
            reason=None;percent=100.
            if circuit:reason='equity_circuit'
            elif q.liquidity_usd<self.limits.min_liquidity_usd:reason='liquidity_drop'
            else:
                reason=defensive_exit(entry_price=p['entry_price_usd'],current_price=q.price_usd,
                    high_price=high,hard_stop_fraction=self.limits.hard_stop_pct,
                    trailing_fraction=self.limits.active_trailing_pct)
                if reason is None and time.time()-p['opened']>=self.cfg.max_hold_seconds:
                    reason='time_exit'
            if reason is None and not p['principal_done'] and p['entry_cost_usd']>0:
                plan=plan_principal_recovery(remaining_units=p['units'],initial_units=p['initial_units'],
                    entry_cost_usd=p['entry_cost_usd'],recovered_usd=p['recovered_usd'],
                    current_usd_price=q.price_usd,liquidity_usd=q.liquidity_usd,
                    trigger_multiple=self.limits.principal_recovery_multiple,
                    sell_fee_fraction=self.limits.principal_recovery_fee_fraction,
                    maximum_slippage_fraction=self.limits.max_slippage_pct/100,
                    network_cost_usd=0.05,min_runner_fraction=self.limits.min_runner_fraction,
                    min_buffer_fraction=self.limits.principal_recovery_buffer_fraction)
                if plan is not None:
                    reason='principal_recovery';percent=plan.fraction*100
                    self.db.observe(mint,'RECOVERY_PLAN',f'predictedUSD={plan.estimated_net_usd:.4f}; amount={plan.units_to_sell:.6f}; NOT a firm quote')
            if reason is None and p['principal_done'] and ratio>=5 and not p['partial5']:
                reason='5x_runner';percent=15.
            if reason:await self.sell(mint,p,percent,reason,q)

    async def tick(self):
        # Every live entry requires both V2 consensus+rug gating and V3 wallet limits.
        await self.check_positions()
        if self.live_ledger.flag('risk_pause')=='1':return
        if self.live_ledger.unresolved():return
        pool=[m for m in list(self.consensus.events) if self.consensus.consensus(m,self.limits.min_wallet_consensus)]
        for mint in pool[:self.cfg.max_price_checks_per_cycle]:await self.try_entry(mint)

    async def run(self):
        log.warning('EXPERIMENTAL LIVE MODE ENABLED. Only PumpPortal trading wallet is transacting. FOMO not connected for execution.')
        await super().run()
