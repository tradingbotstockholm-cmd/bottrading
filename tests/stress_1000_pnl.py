"""1,000 independent, synthetic-paper fills with explicit economic results.

No model predictions, historical market data, actual strategy alpha, remote RPC,
funded wallets, nor user's live SQLite are included. Expected outcome is not
forced positive; historical futures are not known at order time.
"""
import asyncio,csv,json,math,random,statistics,sys,time
from pathlib import Path
from unittest.mock import patch
from hunter.models import Quote
from hunter2.engine import Settings
from hunter2.security import SecurityVerdict
from hunter4.__main__ import PaperHunterV4

MINT='7'*44

def quote(price,liq=45000):return Quote(MINT,price,15000,liq,'synthetic',time.time())

async def run(count=1000,seed=20261017):
    rng=random.Random(seed)
    result=[]
    counts={}
    for i in range(count):
        # New, independent paper account every scenario. Reusing an account
        # would cause intentional daily risk breakers to suppress test entries.
        cfg=Settings(db_path=':memory:',initial_cash_usd=1000,
                     assumed_slippage_pct=.0035,fee_per_order_usd=.05,
                     min_market_cap_usd=10000,max_market_cap_usd=20000,
                     min_liquidity_usd=3000,min_distinct_wallets=2,
                     enable_paid_wallet_stream=False,enable_paid_token_stream=False)
        bot=PaperHunterV4(cfg)
        entry=.001*(1+rng.uniform(-.001,.001))
        liq=rng.uniform(33000,85000)
        try:
            bot.consensus.record(MINT,'buyerA','buy')
            bot.consensus.record(MINT,'buyerB','buy')
            bot.security.examine=lambda *_:SecurityVerdict(True,(),0)
            with patch('hunter2.engine.fetch_quote',return_value=quote(entry,liq)):
                await bot.tick()
            buys=bot.db.recent_fills()
            if not buys or buys[0]['side']!='buy':
                raise AssertionError(f'Paper buy missing in scenario {i}')
            # Fixed, mixed market regimes, not hindsight-selected trades.
            regime=rng.random()
            if regime<.19:
                name='explosive rise then pullback';ratios=[2.20,1.85,.85]
            elif regime<.33:
                name='short pop then reversal';ratios=[1.33,1.17,.92]
            elif regime<.80:
                name='rapid dump';ratios=[rng.uniform(.60,.92)]
            else:
                name='sideways/timeout';ratios=[rng.uniform(.94,1.07)]
                st=bot.db.state();st['positions'][MINT]['entry_time']-=cfg.max_hold_seconds+10;bot.db.save_state(st)
            counts[name]=counts.get(name,0)+1
            for ratio in ratios:
                with patch('hunter2.engine.fetch_quote',return_value=quote(entry*ratio,liq)):
                    await bot.tick()
            if bot.db.state()['positions']:
                # Liquidate the still-open synthetic position at the last price,
                # not a fictional favorable price. This is the test horizon.
                last=quote(entry*ratios[-1],liq)
                bot.broker.exit(MINT,last,1,'synthetic horizon close')
            fills=bot.db.recent_fills(15)
            assert not bot.db.state()['positions'],('position remains',i)
            assert len([f for f in fills if f['side']=='buy'])==1
            assert len([f for f in fills if f['side']=='sell'])>=1
            paper_pnl=bot.db.state()['cash']-1000
            realized=sum(f['pnl_usd'] for f in fills if f['side']=='sell')
            assert math.isclose(realized,paper_pnl,abs_tol=1e-6),(realized,paper_pnl)
            result.append((paper_pnl,name, len(fills)))
        finally:bot.db.close()
        if (i+1)%250==0:print(f'PASS {i+1}/{count} isolated synthetic buy/sell scenarios',flush=True)
    pnls=[x[0] for x in result]
    wins=[x for x in pnls if x>0]
    losses=[x for x in pnls if x<0]
    # One-normalized-trade-at-a-time theoretical curve; not a single feasible
    # real account (separate $1k accounts and synthetic paths were used).
    equity=0.;peak=0.;maxdd=0.
    for pnl in pnls:
        equity+=pnl;peak=max(peak,equity);maxdd=max(maxdd,peak-equity)
    values={
        'scenario_type':'SYNTHETIC STRESS TEST; NOT HISTORICAL MARKET DATA OR STRATEGY BACKTEST',
        'seed':seed,'scenarios':count,'initial_usd_per_independent_account':1000,
        'winning_scenarios':len(wins),'losing_scenarios':len(losses),
        'breakeven_scenarios':count-len(wins)-len(losses),
        'win_rate_pct':round(100*len(wins)/count,2),
        'gross_wins_usd':round(sum(wins),4),
        'gross_losses_usd':round(sum(losses),4),
        'net_pnl_usd_sum_normalized_trials':round(sum(pnls),4),
        'mean_pnl_per_trial_usd':round(statistics.mean(pnls),4),
        'median_pnl_per_trial_usd':round(statistics.median(pnls),4),
        'worst_trial_usd':round(min(pnls),4),
        'best_trial_usd':round(max(pnls),4),
        'hypothetical_max_drawdown_usd_sequential_normalized_pnls':round(maxdd,4),
        'profit_factor':round(sum(wins)/-sum(losses),4) if losses else None,
        'regimes':counts,
        'fees_slippage':'Broker models nominal $0.05 per order and price-impact/slippage 0.35% base plus size/liquidity. Actual fills may be much worse.',
        'interpretation':'No observed real market returns, no model scoring, no backtested edge, no real capital. Synthetic trial sum is NOT realizable account profit.'
    }
    print(json.dumps(values,indent=2))
    return values

if __name__=='__main__':
    report=asyncio.run(run())
    if '--save' in sys.argv:
        dest=Path(__file__).resolve().parents[1]/'STRESS_1000_REPORT.json'
        dest.write_text(json.dumps(report,indent=2)+'\n')
        # Exact individual modeled results; useful for independent inspection.
        # Captured deterministically by a second replay with the same seed.
        print('Saved',dest)
