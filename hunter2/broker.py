"""Paper broker. Prices are observed; simulated fills are assumptions, never transactions."""
from __future__ import annotations
import time,math,datetime,threading,functools
from hunter.models import Quote

def serialized(method):
    """Ensure each paper read/modify/write/fill transaction is indivisible."""
    @functools.wraps(method)
    def wrapped(self,*args,**kwargs):
        with self._trade_lock:
            return method(self,*args,**kwargs)
    return wrapped

class Broker:
    def __init__(self,db, config):
        self.db,self.cfg=db,config
        self._trade_lock=db.lock
        self.last_rejection=None
    def reject(self,mint,code,**details):
        self.last_rejection={'code':code,**details}
        self.db.observe(mint,'reject',code)
        self.db.diagnostic(mint,'broker',code,details)
        return False
    def quote_rejection(self,q):
        if q is None:return 'price_unavailable'
        if not all(isinstance(x,(int,float)) and math.isfinite(x) and x>0
                   for x in (q.price_usd,q.market_cap_usd)):
            return 'price_unavailable'
        if not math.isfinite(q.timestamp) or not 0<=time.time()-q.timestamp<=self.cfg.max_price_age_seconds:
            return 'quote_stale'
        if not math.isfinite(q.liquidity_usd) or q.liquidity_usd<=0:return 'liquidity_unavailable'
        return None
    def position_fields(self,allocation,units):return {}
    def state(self):return self.db.state()
    def equity(self,s=None):
        s=s or self.state()
        return s['cash']+sum(p['units']*p['last_price']*0.98 for p in s['positions'].values() if p.get('last_price',0)>0)
    def _day(self,s,now):
        today=datetime.datetime.fromtimestamp(now,datetime.timezone.utc).strftime('%Y-%m-%d')
        if s['day']!=today:
            s['day']=today;s['day_start']=self.equity(s)
    def _eligible_quote(self,q):
        return bool(q) and all(math.isfinite(x) and x>0 for x in (q.price_usd,q.market_cap_usd,q.liquidity_usd)) and 0 <= time.time()-q.timestamp <= self.cfg.max_price_age_seconds
    def _slip(self,nominal,liq):
        # Approximate—not an executable quote. Scales up sharply near low liquidity.
        return min(0.50,self.cfg.assumed_slippage_pct+0.5*nominal/max(liq,1))
    @serialized
    def mark(self,q,now=None):
        now=time.time() if now is None else now
        if not self._eligible_quote(q):return []
        s=self.state();p=s['positions'].get(q.mint)
        if not p:return []
        p['last_price']=q.price_usd;p['high']=max(p['high'],q.price_usd);p['last_quote']=now;p['liquidity']=q.liquidity_usd
        self._day(s,now)
        eq=self.equity(s)
        if eq>s.get('peak_equity',0):s['peak_equity']=eq
        self.db.save_state(s)
        ratio=q.price_usd/p['entry_price']; reason=None
        if eq<=s['day_start']*(1-self.cfg.max_daily_loss_pct):reason='daily loss circuit breaker'
        elif ratio<=1-self.cfg.stop_loss_pct:reason='stop loss'
        elif p['high']>=p['entry_price']*1.5 and q.price_usd<=p['high']*(1-self.cfg.trailing_stop_pct):reason='trailing stop'
        elif now-p['entry_time']>=self.cfg.max_hold_seconds:reason='max holding time'
        elif p.get('pending_exit'):reason=p['pending_exit']
        if reason:return [self.exit(q.mint,q,p.get('pending_exit_fraction',1) if reason==p.get('pending_exit') else 1,reason,now)]
        if ratio>=5 and not p['partial5']:
            return [self.exit(q.mint,q,1/3,'5x profit take',now)]
        if ratio>=2 and not p['partial2']:
            return [self.exit(q.mint,q,0.25,'2x profit take',now)]
        return []
    def apply_exit_metadata(self,position,net,reason):
        if reason=='2x profit take':position['partial2']=True
        if reason=='5x profit take':position['partial5']=True
    def planned_allocation(self,s,liquidity_usd,signal_strength=None):
        """Bound every entry by cash, exposure and remaining position slots.

        High position limits distribute the budget rather than reserving 3%
        of equity per trade until the first few trades consume the cash.
        No borrowed capital; portfolio exposure never intentionally exceeds
        the configured fraction of marked equity.
        """
        slots=self.cfg.max_open_positions-len(s['positions'])
        if slots<=0 or not math.isfinite(liquidity_usd) or liquidity_usd<=0:return 0.0
        current_basis=sum(max(0,float(p.get('basis',0))) for p in s['positions'].values())
        budget=max(0.,self.equity(s)*self.cfg.max_total_exposure_pct-current_basis)
        if signal_strength is not None and getattr(self.cfg,'adaptive_paper_sizing',False):
            if not isinstance(signal_strength,(int,float)) or not math.isfinite(signal_strength) or not 0<=signal_strength<=1:
                return 0.0
            low=self.cfg.min_paper_entry_usd
            high=self.cfg.max_paper_entry_usd
            # Fourth power stops a weak heuristic from receiving a large budget.
            # This is an order-size target, NOT permission to override risk caps.
            target=low+(high-low)*signal_strength**4
            alloc=max(0.,min(s['cash'],self.equity(s)*self.cfg.allocation_pct,
                              liquidity_usd*.02,budget,target))
        else:
            # Backward compatibility for existing paper history and stress tests.
            alloc=max(0.,min(s['cash'],self.equity(s)*self.cfg.allocation_pct,
                              liquidity_usd*.02,budget,budget/slots))
        if alloc<=self.cfg.fee_per_order_usd:return 0.0
        # Account for the paper buy fee and indicative price impact before
        # enforcing the portfolio cost-basis exposure cap. Without this,
        # the model could exceed its own cap immediately after a fill.
        marked_fraction=(alloc-self.cfg.fee_per_order_usd)*.98/(1+self._slip(alloc,liquidity_usd))/alloc
        alloc=min(alloc,budget/(1+self.cfg.max_total_exposure_pct*(1-marked_fraction)))
        # Re-evaluate because fixed fees make the price impact non-linear
        # when reducing a small allocation.
        current_equity=self.equity(s)
        for _ in range(12):
            expected_mark=max(0,alloc-self.cfg.fee_per_order_usd)*.98/(1+self._slip(alloc,liquidity_usd))
            excess=current_basis+alloc-self.cfg.max_total_exposure_pct*(current_equity-alloc+expected_mark)
            if excess<=1e-9:break
            alloc=max(0.,alloc-excess)
        return alloc

    @serialized
    def buy(self,q,wallets,now=None,*,signal_strength=None,security_evidence=None):
        now=time.time() if now is None else now
        self.last_rejection=None
        mint=getattr(q,'mint','')
        reason=self.quote_rejection(q)
        if reason:return self.reject(mint,reason)
        if getattr(self.cfg,'enforce_paper_market_cap',False) and not (
            self.cfg.min_market_cap_usd<=q.market_cap_usd<=self.cfg.max_market_cap_usd
        ):return self.reject(mint,'market_cap_outside_policy')
        if q.liquidity_usd<self.cfg.min_liquidity_usd:
            return self.reject(mint,'liquidity_below_minimum',actual=q.liquidity_usd,minimum=self.cfg.min_liquidity_usd)
        if len(set(wallets))<max(2,self.cfg.min_distinct_wallets):return self.reject(mint,'consensus_missing_or_expired')
        if signal_strength is not None and (isinstance(signal_strength,bool) or not isinstance(signal_strength,(int,float))
                or not math.isfinite(signal_strength) or not 0<=signal_strength<=1):
            return self.reject(mint,'invalid_confidence_score')
        s=self.state();self._day(s,now)
        if self.db.is_entries_paused():return self.reject(mint,'trading_paused')
        if mint in s['positions']:return self.reject(mint,'duplicate_trade')
        if len(s['positions'])>=self.cfg.max_open_positions:return self.reject(mint,'position_limit_reached')
        if s['cash']<max(1,getattr(self.cfg,'min_paper_entry_usd',2)):
            return self.reject(mint,'insufficient_simulated_cash')
        if self.equity(s)<=s['day_start']*(1-self.cfg.max_daily_loss_pct):return self.reject(mint,'daily_loss_limit')
        basis=sum(p['basis'] for p in s['positions'].values())
        if basis>=self.equity(s)*self.cfg.max_total_exposure_pct:
            return self.reject(mint,'portfolio_exposure_exceeded')
        alloc=self.planned_allocation(s,q.liquidity_usd,signal_strength)
        minimum=self.cfg.min_paper_entry_usd if signal_strength is not None and self.cfg.adaptive_paper_sizing else max(1,3*self.cfg.fee_per_order_usd)
        if alloc+1e-9<minimum:
            maximum=getattr(self,'max_estimated_slippage',None)
            feasible=2*q.liquidity_usd*max(0,maximum-self.cfg.assumed_slippage_pct) if maximum is not None else None
            code='estimated_price_impact_too_high' if feasible is not None and feasible+1e-9<minimum else 'allocation_below_minimum'
            return self.reject(mint,code,allocation=alloc,minimum=minimum,max_slippage_allocation=feasible)
        slip=self._slip(alloc,q.liquidity_usd)
        maximum=getattr(self,'max_estimated_slippage',None)
        if maximum is not None and slip>maximum+1e-12:
            return self.reject(mint,'estimated_price_impact_too_high',estimated_slippage=slip,maximum=maximum)
        self.db.diagnostic(mint,'broker','approved',{'allocation_usd':alloc,'estimated_slippage':slip,'fee_usd':self.cfg.fee_per_order_usd})
        units=(alloc-self.cfg.fee_per_order_usd)/(q.price_usd*(1+slip))
        if not math.isfinite(units) or units<=0:return self.reject(mint,'invalid_allocation')
        s['cash']-=alloc;s['positions'][q.mint]={
            'mint':q.mint,'units':units,'basis':alloc,'entry_price':q.price_usd,
            'entry_time':now,'high':q.price_usd,'last_price':q.price_usd,'last_quote':now,
            'liquidity':q.liquidity_usd,'partial2':False,'partial5':False,'pending_exit':'','wallets':list(sorted(set(wallets))),
            'signal_strength':round(float(signal_strength),5) if signal_strength is not None else None,
            'security_evidence':security_evidence or {'status':'not_supplied_to_broker'},
            **self.position_fields(alloc,units),
        }
        self.db.save_state(s,dict(time=now,mint=q.mint,side='buy',units=units,price=q.price_usd,
                                  net_usd=-alloc,pnl_usd=0,reason='multi-wallet entry',wallets=wallets,liquidity=q.liquidity_usd))
        self.db.diagnostic(mint,'paper','filled',{'side':'buy','allocation_usd':alloc})
        return True
    @serialized
    def exit(self,mint,q,fraction=1,reason='exit',now=None):
        now=time.time() if now is None else now
        # Never let malformed partial-exit sizes manufacture/erase paper cash.
        if not isinstance(fraction,(int,float)) or not math.isfinite(fraction) or not 0 < fraction <= 1:
            self.db.diagnostic(mint,'exit','invalid_exit_fraction');return None
        s=self.state();p=s['positions'].get(mint)
        if not p:
            self.db.diagnostic(mint,'exit','position_missing');return None
        if not q or q.mint!=mint or not self._eligible_quote(q) or q.liquidity_usd<=0:
            p['pending_exit']=reason;p['pending_exit_fraction']=fraction;self.db.save_state(s)
            code='quote_mint_mismatch' if q and q.mint!=mint else self.quote_rejection(q) or 'liquidity_unavailable'
            self.db.diagnostic(mint,'exit',code,{'reason':reason,'fraction':fraction})
            self.db.observe(mint,'unfilled_exit','no valid price / liquidity: '+reason)
            return None
        nominal=p['units']*fraction*q.price_usd
        # no assumption of infinite liquidity: execution unfilled while exit order too large
        if nominal>q.liquidity_usd*0.02:
            p['pending_exit']=reason;p['pending_exit_fraction']=fraction;self.db.save_state(s)
            self.db.diagnostic(mint,'exit','estimated_exit_impact_too_high',{'nominal_usd':nominal,'liquidity_usd':q.liquidity_usd,'fraction':fraction})
            self.db.observe(mint,'unfilled_exit','insufficient modeled liquidity: '+reason)
            return None
        qty=p['units']*fraction;basis=p['basis']*fraction
        slip=self._slip(nominal,q.liquidity_usd)
        net=max(0,qty*q.price_usd*(1-slip)-self.cfg.fee_per_order_usd)
        pnl=net-basis;s['cash']+=net
        p['units']-=qty;p['basis']-=basis;p['pending_exit']='';p.pop('pending_exit_fraction',None)
        self.apply_exit_metadata(p,net,reason)
        if fraction>=1-1e-12 or p['units']<=1e-14:del s['positions'][mint]
        self.db.save_state(s,dict(time=now,mint=mint,side='sell',units=qty,price=q.price_usd,
                                   net_usd=net,pnl_usd=pnl,reason=reason,wallets=p['wallets'],liquidity=q.liquidity_usd))
        self.db.diagnostic(mint,'paper','filled',{'side':'sell','pnl_usd':pnl,'modeled_slippage':slip,'fee_usd':self.cfg.fee_per_order_usd})
        return pnl
    @serialized
    def request_exit(self,mint,reason='stale price / risk unknown'):
        s=self.state();p=s['positions'].get(mint)
        if p:
            p['pending_exit']=reason;p['pending_exit_fraction']=1;self.db.save_state(s)
            self.db.observe(mint,'exit_pending',reason)
    def pause(self,flag=True):
        self.db.set_entries_paused(bool(flag))
