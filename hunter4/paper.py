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
        self.max_estimated_slippage=getattr(config,'max_paper_slippage_pct',0.0075)
        self.recovery_multiple=2.0
    def planned_allocation(self,s,liquidity_usd,signal_strength=None):
        amount=super().planned_allocation(s,liquidity_usd,signal_strength)
        # Solve the broker's actual impact model before choosing the order size.
        # Preserve legacy non-adaptive policy; never enlarge a target or relax risk.
        if signal_strength is not None and self.cfg.adaptive_paper_sizing:
            impact_budget=max(0.0,self.max_estimated_slippage-self.cfg.assumed_slippage_pct)
            amount=min(amount,2*liquidity_usd*impact_budget)
        return amount
    def position_fields(self,allocation,units):
        # Written atomically with the position and buy fill, before any commit.
        return dict(initial_basis_usd=allocation,initial_units=units,
                    proceeds_usd=0.0,principal_done=False)
    def apply_exit_metadata(self,position,net,reason):
        super().apply_exit_metadata(position,net,reason)
        if reason=='principal_recovery':
            position['proceeds_usd']=position.get('proceeds_usd',0)+net
            position['principal_done']=position['proceeds_usd']>=position.get('initial_basis_usd',float('inf'))
            position['partial2']=position['principal_done']
        if reason=='5x_runner':position['partial5']=True
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
        if reason:return [self.exit(q.mint,q,p.get('pending_exit_fraction',1) if reason==p.get('pending_exit') else 1,reason,now)]
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
            return [self.exit(q.mint,q,.15,'5x_runner',now)]
        return []
