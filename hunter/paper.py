"""Paper trading only. Models slippage/fees; cannot guarantee real sell liquidity."""
from __future__ import annotations
from dataclasses import asdict
from datetime import date
from time import time
from .models import Quote, Position, Sale
from .store import read, write

class PaperBroker:
    def __init__(self,config,path='data/paper_state.json'):
        self.cfg=config
        self.path=path
        self.positions: dict[str,Position]={}
        state=read(path,None)
        if state:
            self.cash=float(state['cash'])
            self.positions={m:Position(**p) for m,p in state['positions'].items()}
            self.sales=[Sale(**s) for s in state['sales']]
            self.today=state.get('today',date.today().isoformat())
            self.day_start_equity=float(state.get('day_start_equity',self.cfg.initial_cash_usd))
        else:
            self.cash=self.cfg.initial_cash_usd
            self.sales=[]
            self.today=date.today().isoformat()
            self.day_start_equity=self.cfg.initial_cash_usd
        self.persist()

    def persist(self):
        write(self.path,{'cash':self.cash,'positions':{m:asdict(p) for m,p in self.positions.items()},
                         'sales':[asdict(x) for x in self.sales], 'today':self.today,'day_start_equity':self.day_start_equity})

    def equity(self):
        return self.cash+sum(p.units*p.last_price*(1-self.cfg.assumed_slippage_pct) for p in self.positions.values())

    def day_rollover(self):
        d=date.today().isoformat()
        if self.today!=d:
            self.today=d
            self.day_start_equity=self.equity()
            self.persist()

    def may_enter(self):
        self.day_rollover()
        return (self.equity()>self.day_start_equity*(1-self.cfg.max_daily_loss_pct)
                and len(self.positions)<self.cfg.max_open_positions)

    def buy(self,quote: Quote,wallets: list[str],now: float | None=None) -> bool:
        if quote.mint in self.positions or not self.may_enter() or quote.price_usd<=0:
            return False
        allocation=min(self.cash, self.equity()*self.cfg.allocation_pct)
        if allocation <= self.cfg.fee_per_order_usd*2 or allocation > quote.liquidity_usd*0.02:
            return False
        units=(allocation-self.cfg.fee_per_order_usd)/(quote.price_usd*(1+self.cfg.assumed_slippage_pct))
        now=time() if now is None else now
        self.cash-=allocation
        self.positions[quote.mint]=Position(quote.mint,quote.price_usd,units,allocation,now,
                                            quote.price_usd,quote.price_usd,now,sorted(set(wallets)))
        self.persist()
        return True

    def sell(self,mint: str, fraction: float, price: float, reason: str,now: float | None=None) -> Sale | None:
        p=self.positions.get(mint)
        if not p or not(0<fraction<=1): return None
        now=time() if now is None else now
        qty=p.units*fraction
        allocated_basis=p.basis_usd*fraction
        net=max(0.0,qty*max(0.0,price)*(1-self.cfg.assumed_slippage_pct)-self.cfg.fee_per_order_usd)
        pnl=net-allocated_basis
        sale=Sale(mint,qty,price,net,pnl,reason,now,list(p.wallets))
        self.cash+=net
        p.units-=qty
        p.basis_usd-=allocated_basis
        self.sales.append(sale)
        if fraction>=1 or p.units<1e-14:
            del self.positions[mint]
        self.persist()
        return sale

    def update(self,quote: Quote,now: float | None=None) -> list[Sale]:
        p=self.positions.get(quote.mint)
        if not p: return []
        now=time() if now is None else now
        if quote.price_usd<=0: return []
        p.last_price=quote.price_usd
        p.high_price=max(p.high_price,quote.price_usd)
        p.last_quote_time=now
        ratio=quote.price_usd/p.entry_price
        sold=[]
        if ratio<=1-self.cfg.stop_loss_pct:
            sold.append(self.sell(p.mint,1,quote.price_usd,'stop-loss',now))
        elif p.high_price>p.entry_price*1.5 and quote.price_usd<=p.high_price*(1-self.cfg.trailing_stop_pct):
            sold.append(self.sell(p.mint,1,quote.price_usd,'trailing-stop',now))
        elif now-p.entry_time>=self.cfg.max_hold_seconds:
            sold.append(self.sell(p.mint,1,quote.price_usd,'max-hold',now))
        else:
            if ratio>=2 and not p.partial_2x:
                p.partial_2x=True
                sold.append(self.sell(p.mint,0.25,quote.price_usd,'2x partial',now))
            # Sell a third of remaining amount, ~25% initial if no prior other exit.
            if p.mint in self.positions and ratio>=5 and not p.partial_5x:
                p.partial_5x=True
                sold.append(self.sell(p.mint,1/3,quote.price_usd,'5x partial',now))
        self.persist()
        return [s for s in sold if s]

    def data_stale(self,mint: str, now: float | None=None) -> Sale | None:
        p=self.positions.get(mint)
        now=time() if now is None else now
        if p and now-p.last_quote_time>self.cfg.max_price_age_seconds:
            # Mark as total loss in paper results, not a fabricated successful sell.
            return self.sell(mint,1,0.0,'UNPRICEABLE — marked total loss (not executable)',now)
        return None
