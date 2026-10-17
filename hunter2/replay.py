"""Deterministic event replay for strategy tests. Entirely simulated.
JSONL lines: {"time": float, "mint": str, "price": float, "cap": float,
"liquidity": float, "event": "buy" | "sell" | "quote", "wallet": str (if event buy/sell)}
"""
import json
from hunter.models import Quote
from hunter.signals import Consensus
from .broker import Broker
from .db import Database

def replay(path,cfg,db_path):
    from pathlib import Path
    if Path(db_path).exists():
        raise FileExistsError('Replay database already exists. Choose a new --db path to avoid double-counting synthetic trades.')
    ledger=Database(db_path,cfg.initial_cash_usd);broker=Broker(ledger,cfg)
    consensus=Consensus(cfg.signal_ttl_seconds);count=0
    with open(path,encoding='utf8') as f:
        for line in f:
            if not line.strip():continue
            e=json.loads(line);now=float(e['time']);mint=e['mint']
            event=e.get('event','quote');wallet=e.get('wallet','')
            if event in ('buy','sell'):
                consensus.record(mint,wallet,event,now)
            if 'price' not in e:continue
            q=Quote(mint,float(e['price']),float(e['cap']),float(e['liquidity']),'REPLAY-SYNTHETIC',now)
            # replay quotes are allowed to have historical timestamps; broker quote-freshness check
            # uses current time, so rebase to now for simulation without claiming a live quote.
            q.timestamp=__import__('time').time()
            if mint in broker.state()['positions']:broker.mark(q,now)
            elif consensus.consensus(mint,cfg.min_distinct_wallets,now):broker.buy(q,consensus.voters(mint,now),now)
            count+=1
    state=ledger.snapshot()
    return dict(events=count,cash=state['cash'],open_positions=len(state['positions']),trades=len(state['fills']),
                equity=broker.equity(state),note='SYNTHETIC/IMPORTED REPLAY ONLY — no on-chain execution')
