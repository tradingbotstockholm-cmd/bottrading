"""Transparent daily review and trader re-ranking; never auto-promotes strategies."""
from __future__ import annotations
import math,datetime,json
from .db import Database

def trading_report(db_path,initial_cash=1000.,lookback_days=30):
    db=Database(db_path,initial_cash);st=db.snapshot()
    now=datetime.datetime.now(datetime.timezone.utc).timestamp()
    cutoff=now-lookback_days*86400
    fills=[f for f in db.recent_fills(100000) if f['time']>=cutoff and f['time']<=now]
    closed=[f for f in fills if f['side']=='sell']
    opening=[f for f in fills if f['side']=='buy']
    pnl=sum(f['pnl_usd'] for f in closed)
    wins=sum(f['pnl_usd']>0 for f in closed)
    losses=sum(f['pnl_usd']<0 for f in closed)
    gain=sum(f['pnl_usd'] for f in closed if f['pnl_usd']>0)
    pain=-sum(f['pnl_usd'] for f in closed if f['pnl_usd']<0)
    reasons={}
    for f in closed:reasons[f['reason']]=reasons.get(f['reason'],0)+1
    pos=list(st['positions'].values());unknown=sum((now-p.get('last_quote',0)>120) for p in pos)
    return dict(mode='paper',days=lookback_days,closed_fills=len(closed),buy_fills=len(opening),
        realized_pnl=pnl,win_rate=wins/len(closed) if closed else None,
        gross_profit=gain,gross_loss=pain,profit_factor=gain/pain if pain else None,
        exits=reasons,open_positions=len(pos),positions_with_stale_quotes=unknown,
        open_liquidity_risk='Not modeled beyond latest observed DEX pool depth',
        warnings=['Paper fills are not trades on-chain','Attribution does not establish causal trader skill',
           'No training/promotion occurs automatically; review independent out-of-sample results'],
        leaderboard=db.leaderboard())

def evaluate_events(path,cfg, output_prefix='data/evaluation'):
    """Compare frozen candidate parameter settings on the same chronological event window.
    Diagnostics only, NOT an unbiased performance claim or out-of-sample validation.
    """
    from pathlib import Path
    from dataclasses import replace
    from .replay import replay
    import tempfile
    settings=[('baseline',cfg),('tight_stop',replace(cfg,stop_loss_pct=0.10)),
              ('wide_stop',replace(cfg,stop_loss_pct=0.25)),('light_mode',replace(cfg,mode='light'))]
    rows=[]
    with tempfile.TemporaryDirectory() as temp:
        for i,(name,conf) in enumerate(settings):
            r=replay(path,conf,str(Path(temp)/f'{i}.sqlite'))
            r.update(parameter_set=name, stop_loss_pct=conf.stop_loss_pct,mode=conf.mode)
            rows.append(r)
    return {'market_data':'external fixture; verify source before treating as real',
            'promotion_allowed':False,'results':rows,
            'note':'Diagnostic comparisons share the same dataset; this is NOT walk-forward or proof of predictive edge.'}
