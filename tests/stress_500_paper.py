"""500 isolated, deterministic end-to-end scenarios using mocked market data.

Not a backtest, not real-market performance, and NEVER uses the saved user ledger.
It tests consensus→risk→buy→mark→sell→account reconciliation.
"""
from __future__ import annotations
import asyncio, math, time, sys
from unittest.mock import patch
from hunter.models import Quote
from hunter2.engine import Settings
from hunter2.security import SecurityVerdict
from hunter4.__main__ import PaperHunterV4

MINT='7'*44

async def run_500():
    outcomes={'stop':0,'trailing':0,'principal_recovered':0,'time_exit':0,'unfilled_exit':0}
    for i in range(500):
        # An independent SQLite in-memory paper account for EVERY trial.
        cfg=Settings(db_path=':memory:',initial_cash_usd=1000,
            assumed_slippage_pct=.0035,min_market_cap_usd=10000,
            max_market_cap_usd=20000,min_liquidity_usd=3000,
            min_distinct_wallets=2,fee_per_order_usd=.05,
            enable_paid_wallet_stream=False,enable_paid_token_stream=False,
            require_rugcheck=True)
        bot=PaperHunterV4(cfg)
        price=.001*(1+(i%17)*.0002)
        liquidity=15000+5000*(i%8)
        def q(ratio=1.,liq=None):
            return Quote(MINT,price*ratio,15000,liquidity if liq is None else liq,
                         'pump',time.time())
        try:
            bot.consensus.record(MINT,'walletA','buy')
            bot.consensus.record(MINT,'walletB','buy')
            bot.security.examine=lambda *_:SecurityVerdict(True,(),0)
            with patch('hunter2.engine.fetch_quote',return_value=q()):
                await bot.tick()
            assert [f['side'] for f in bot.db.recent_fills()]==['buy'],('no entry',i)
            assert MINT in bot.db.state()['positions']
            assert bot.broker._slip(bot.db.state()['positions'][MINT]['basis'],liquidity)<=.0075
            path=i%5
            if path==0:
                course=[.89]
                outcomes['stop']+=1
            elif path==1:
                course=[2.4,1.97]
                outcomes['principal_recovered']+=1
            elif path==2:
                course=[1.5,1.2]
                outcomes['trailing']+=1
            elif path==3:
                course=[2.3,.88]
                outcomes['principal_recovered']+=1
            else:
                # Simulate a large passage of holding time for the last mark.
                s=bot.db.state();s['positions'][MINT]['entry_time']-=cfg.max_hold_seconds+10
                bot.db.save_state(s)
                course=[1.01]
                outcomes['time_exit']+=1
            for ratio in course:
                with patch('hunter2.engine.fetch_quote',return_value=q(ratio)):
                    await bot.tick()
            # A second valid mark must finish the scenario even if previous
            # mark was a principal recovery partial sell.
            if bot.db.state()['positions']:
                with patch('hunter2.engine.fetch_quote',return_value=q(.87)):
                    await bot.tick()
            state=bot.db.state()
            fills=list(reversed(bot.db.recent_fills()))
            assert not state['positions'],('position remained',i,course)
            assert fills[0]['side']=='buy' and fills[-1]['side']=='sell'
            assert len([f for f in fills if f['side']=='buy'])==1
            assert state['cash']>=0 and math.isfinite(state['cash'])
            assert math.isclose(state['cash'],1000+sum(f['net_usd'] for f in fills),abs_tol=.0001),(i,state,fills)
        finally:
            bot.db.close()
        if (i+1)%100==0:print(f'PASS {i+1}/500 isolated paper trading trials',flush=True)
    print('COMPLETE: 500/500 synthetic paper scenarios; zero live orders; no user database touched.')
    print('Scenarios:',outcomes)

if __name__=='__main__':
    try:asyncio.run(run_500())
    except Exception as e:
        print('STRESS FAILURE:',repr(e),file=sys.stderr)
        raise
