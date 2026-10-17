"""Opt-in V4 paper broker: keeps initial capital accounting separate from runner PnL.

The mark is a simulation on indicative prices; no fill is guaranteed.
"""
from __future__ import annotations
import time
from hunter2.broker import Broker,serialized
from .principal import plan_principal_recovery,defensive_exit

class CapitalRecoveryBroker(Broker):
    def __init__(self,db,config):
        super().__init__(db,config)
        self.max_estimated_slippage=0.0075
        self.recovery_multiple=2.0
    @serialized
    def buy(self,q,wallets,now=None,*,signal_strength=None):
        if q is None:return False
        s=self.state()
        # Match the *actual* allocation in Broker.buy. Using a smaller
        # hypothetical size here understates execution slippage and can
        # approve fills above the paper broker's own 0.75% safety limit.
        amount=self.planned_allocation(s,q.liquidity_usd,signal_strength) if q.liquidity_usd>0 else 0
        if amount<=0 or self._slip(amount,q.liquidity_usd)>self.max_estimated_slippage:return False
        ok=super().buy(q,wallets,now,signal_strength=signal_strength)
        if ok:
            s=self.state();p=s['positions'][q.mint]
            p.update(initial_basis_usd=p['basis'],initial_units=p['units'],
                     proceeds_usd=0.0,principal_done=False)
            self.db.save_state(s)
        return ok
    @serialized
    def exit(self,mint,q,fraction=1,reason='exit',now=None):
        result=super().exit(mint,q,fraction,reason,now)
        if result is not None and reason=='principal_recovery':
            rows=self.db.recent_fills(1)
            if rows:
                proceeds=float(rows[0]['net_usd'])
                s=self.state();p=s['positions'].get(mint)
                if p is not None:
                    p['proceeds_usd']=p.get('proceeds_usd',0)+proceeds
                    p['principal_done']=p['proceeds_usd']>=p.get('initial_basis_usd',float('inf'))
                    p['partial2']=p['principal_done']
                    self.db.save_state(s)
        return result
    @serialized
    def mark(self,q,now=None):
        now=time.time() if now is None else now
        if not self._eligible_quote(q):return []
        s=self.state();p=s['positions'].get(q.mint)
        if not p:return []
        p['last_price']=q.price_usd;p['high']=max(p['high'],q.price_usd)
        p['last_quote']=now;p['liquidity']=q.liquidity_usd
        self._day(s,now)
        eq=self.equity(s)
        s['peak_equity']=max(s.get('peak_equity',eq),eq)
        self.db.save_state(s)
        reason=None
        if eq<=s['day_start']*(1-self.cfg.max_daily_loss_pct):reason='daily loss circuit breaker'
        elif q.liquidity_usd<self.cfg.min_liquidity_usd:reason='liquidity deterioration'
        else:
            reason=defensive_exit(entry_price=p['entry_price'],current_price=q.price_usd,
                high_price=p['high'],hard_stop_fraction=.07,trailing_fraction=.08)
        if not reason and now-p['entry_time']>=self.cfg.max_hold_seconds:reason='time_exit'
        if not reason and p.get('pending_exit'):reason=p['pending_exit']
        if reason:return [self.exit(q.mint,q,1,reason,now)]
        if not p.get('principal_done') and p.get('initial_basis_usd') and p.get('initial_units'):
            plan=plan_principal_recovery(remaining_units=p['units'],initial_units=p['initial_units'],
                entry_cost_usd=p['initial_basis_usd'],recovered_usd=p.get('proceeds_usd',0),
                current_usd_price=q.price_usd,liquidity_usd=q.liquidity_usd,
                trigger_multiple=self.recovery_multiple,sell_fee_fraction=0.02,
                maximum_slippage_fraction=self.max_estimated_slippage,
                network_cost_usd=self.cfg.fee_per_order_usd,
                min_runner_fraction=.05,min_buffer_fraction=.02)
            if plan:
                return [self.exit(q.mint,q,plan.fraction,'principal_recovery',now)]
        if p.get('principal_done') and q.price_usd/p['entry_price']>=5 and not p['partial5']:
            # Record completion only after an actual simulated fill. If modeled
            # liquidity prevents the exit, keep the runner eligible to retry.
            realized=self.exit(q.mint,q,.15,'5x_runner',now)
            if realized is not None:
                updated=self.state()
                if q.mint in updated['positions']:
                    updated['positions'][q.mint]['partial5']=True
                    self.db.save_state(updated)
            return [realized]
        return []
