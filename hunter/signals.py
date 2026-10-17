"""Multi-wallet, short lived event consensus and re-ranking. No permanent guru."""
from __future__ import annotations
from collections import defaultdict
from time import time

class Consensus:
    def __init__(self, ttl: float=120):
        self.ttl=ttl
        self.events=defaultdict(dict)
        self.wallet_stats=defaultdict(lambda:{'wins':0,'losses':0,'pnl':0.0})

    def record(self, mint: str, wallet: str, side: str, now: float | None=None):
        now=time() if now is None else now
        if side=='buy':
            self.events[mint][wallet]=now
        elif side=='sell':
            self.events[mint].pop(wallet,None)

    def voters(self, mint: str, now: float | None=None) -> list[str]:
        now=time() if now is None else now
        self.events[mint]={w:t for w,t in self.events[mint].items() if 0<=now-t<=self.ttl}
        return list(self.events[mint])

    def consensus(self, mint: str, minimum: int, now: float | None=None) -> bool:
        return len(self.voters(mint,now)) >= max(2,minimum)

    def score(self, wallet: str) -> float:
        s=self.wallet_stats[wallet]
        n=s['wins']+s['losses']
        # Bayesian shrinkage; cannot crown a trader on one lucky outcome.
        rate=(s['wins']+2)/(n+4)
        return rate + max(-0.2,min(0.2,s['pnl']/1000))

    def update(self, wallets: list[str], pnl: float):
        # Signal contribution, not causal attribution of profit.
        if not wallets: return
        for w in wallets:
            s=self.wallet_stats[w]
            s['wins' if pnl>0 else 'losses']+=1
            s['pnl']+=pnl/len(wallets)
