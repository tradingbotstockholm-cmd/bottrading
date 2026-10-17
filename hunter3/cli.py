"""Command-line setup, observation, and explicitly confirmed single orders."""
from __future__ import annotations
import argparse,json,os,pathlib,shutil,time,sys
from dataclasses import asdict
from hunter2.chain import SolanaClient
from hunter.dex import fetch_quote
from hunter2.security import SecurityGate
from .accounts import WalletAccounts,balances,validate_funding_separation
from .orders import LiveLimits,LiveLedger,LightningClient,LiveGateway,LiveHalt,token_balance,reconcile_order,price_sol_usd

def gateway(config='live.json',need_key=True):
    limits=LiveLimits.load(config)
    ledger=LiveLedger(limits.db_path)
    rpc=SolanaClient(os.getenv('SOLANA_RPC_URL',limits.rpc_url))
    client=LightningClient(os.getenv('PUMPPORTAL_API_KEY','')) if need_key else None
    return LiveGateway(limits,ledger,rpc,client)

def main(argv=None):
    p=argparse.ArgumentParser(description='Solana Hunter V3 — address linking and isolated PumpPortal live adapter')
    sub=p.add_subparsers(dest='cmd',required=True)
    def cmd(name):
        a=sub.add_parser(name);a.add_argument('--live-config',default='live.json');return a
    init=cmd('init');init.add_argument('--accounts',default='accounts.json')
    bal=cmd('balances');bal.add_argument('--accounts',default='accounts.json')
    cmd('doctor');cmd('status');cmd('halt');cmd('unhalt')
    dash=cmd('dashboard');dash.add_argument('--port',type=int,default=8766);dash.add_argument('--accounts',default='accounts.json')
    run=cmd('run-live');run.add_argument('--scanner-config',default='config.json')
    cmd('reconcile') .add_argument('order_id')
    buy=cmd('buy');buy.add_argument('mint');buy.add_argument('--curve-owner',default='');buy.add_argument('--manual-selection',action='store_true');buy.add_argument('--execute',action='store_true')
    sell=cmd('sell');sell.add_argument('mint');sell.add_argument('--percent',type=float,default=100);sell.add_argument('--execute',action='store_true')
    a=p.parse_args(argv)
    if a.cmd=='init':
        WalletAccounts().save(a.accounts)
        if not pathlib.Path(a.live_config).exists():shutil.copyfile('live.example.json',a.live_config)
        print(f'Created {a.accounts} and {a.live_config}; fill only PUBLIC addresses. Trading disabled.')
        return
    if a.cmd=='dashboard':
        from .dashboard import serve
        serve(a.port,a.live_config,a.accounts);return
    if a.cmd=='balances':
        ac=WalletAccounts.load(a.accounts);config=LiveLimits.load(a.live_config)
        rpc=SolanaClient(os.getenv('SOLANA_RPC_URL',config.rpc_url))
        print(json.dumps(balances(ac,rpc),indent=2));return
    if a.cmd in ('halt','unhalt'):
        cfg=LiveLimits.load(a.live_config)
        f=pathlib.Path(cfg.kill_switch_path);f.parent.mkdir(exist_ok=True,parents=True)
        if a.cmd=='halt':f.write_text('HALT ALL LIVE ORDERS\n');print('Emergency stop created. No new live orders.')
        else:
            if f.exists():f.unlink()
            print('Emergency stop file cleared; live trading still requires enabled config and API key.')
        return
    cfg=LiveLimits.load(a.live_config)
    ledger=LiveLedger(cfg.db_path)
    if a.cmd=='status':
        print(json.dumps({'enabled':cfg.enabled,'mode':cfg.mode,'wallet_address':cfg.wallet_address,
            'kill_switch':pathlib.Path(cfg.kill_switch_path).exists(),'flag_halt':ledger.flag('halt'),
            'unresolved_orders':ledger.unresolved(),'positions':ledger.positions(),
            'buys_last_24h_sol':ledger.spend(86400),'buys_all_time_sol':ledger.spend(),
            'recovered_principal_sol':ledger.total_reclaimed_principal_sol(),
            'net_principal_committed_sol':ledger.net_committed_sol(),
            'unsettled_confirmed':ledger.unreconciled_confirmed()},indent=2));return
    if a.cmd=='run-live':
        from hunter2.engine import load_settings
        validate_funding_separation(WalletAccounts.load('accounts.json'),cfg.wallet_address)
        from .autolive import LiveHunter
        import asyncio
        asyncio.run(LiveHunter(load_settings(a.scanner_config),cfg).run());return
    if a.cmd=='doctor':
        rpc=SolanaClient(os.getenv('SOLANA_RPC_URL',cfg.rpc_url))
        info={'live_enabled':cfg.enabled,'api_key_present':bool(os.getenv('PUMPPORTAL_API_KEY')),
             'trading_wallet':cfg.wallet_address,'kill_switch':pathlib.Path(cfg.kill_switch_path).exists(),
             'unknown_or_pending':ledger.unresolved(),'unsettled_confirmed':ledger.unreconciled_confirmed(),
             'net_principal_committed_sol':ledger.net_committed_sol(),
             'recovered_principal_sol':ledger.total_reclaimed_principal_sol(),
             'risk_flag':ledger.flag('halt')}
        if cfg.wallet_address:
            try:info['sol_balance']=rpc.call('getBalance',[cfg.wallet_address,{'commitment':'confirmed'}])['value']/1e9
            except Exception as e:info['rpc_error']=type(e).__name__
        print(json.dumps(info,indent=2));return
    if a.cmd in ('buy','sell'):
        validate_funding_separation(WalletAccounts.load('accounts.json'),cfg.wallet_address)
    gw=gateway(a.live_config,need_key=(a.cmd=='reconcile' or getattr(a,'execute',False)))
    if a.cmd=='reconcile':
        print('confirmed' if reconcile_order(gw,a.order_id) else 'still unresolved or failed');return
    if a.cmd=='buy':
        q=fetch_quote(a.mint)
        if q is None:raise LiveHalt('No verified USD-priced DEX pair')
        from hunter2.db import Database
        from hunter2.engine import load_settings
        scanner=load_settings('config.json')
        curve=a.curve_owner or Database(scanner.db_path,scanner.initial_cash_usd).curve_owner(a.mint)
        risk=SecurityGate(os.getenv('SOLANA_RPC_URL',cfg.rpc_url),cfg.rug_score_ceiling,True).examine(a.mint,curve)
        # Requires observed or explicitly supplied curve custody. Unknown -> fail closed.
        if not risk.approved:raise LiveHalt('Token rejected: '+'; '.join(risk.reasons))
        if not a.manual_selection:raise LiveHalt('CLI buy is operator-selected: pass --manual-selection. Autonomous buys require real wallet signals.')
        amount=gw.plan_buy(q,0,risk.approved,price_sol_usd(),manual_selection=True)
        print(json.dumps({'mint':q.mint,'eligible':True,'amount_sol':amount,'market_cap_usd':q.market_cap_usd,
            'risk_score':risk.score,'signal_mode':'MANUAL_OPERATOR_OVERRIDE_NO_COPY_CONSENSUS','will_execute':bool(a.execute)},indent=2))
        if not a.execute:return
        if os.getenv('HUNTER_CONFIRM_LIVE')!='YES_I_UNDERSTAND_THE_RISK':raise LiveHalt('Explicit environment confirmation missing')
        before_sol=gw.balance_sol()
        before=token_balance(gw.rpc,cfg.wallet_address,a.mint)
        oid,sig=gw.submit('buy',a.mint,amount)
        if not gw.confirm(oid,sig):
            print('Submitted but not yet confirmed. Do NOT retry. Run status/reconcile.');return
        after=token_balance(gw.rpc,cfg.wallet_address,a.mint)
        delta=after-before
        if delta<=0:
            ledger.set_flag('halt','1');raise LiveHalt('Transaction confirmed but no token balance delta; halted pending investigation')
        spent_sol=before_sol-gw.balance_sol()
        if not 0<spent_sol<amount*1.15+cfg.max_priority_fee_sol+0.001:
            ledger.set_flag('halt','1');raise LiveHalt('Unreconciled native SOL buy cost')
        sol_usd=price_sol_usd()
        cost_usd=spent_sol*sol_usd
        ledger.finalize_filled_buy(oid,a.mint,delta,spent_sol,sol_usd,before)
        print('Confirmed buy:',sig,'units:',delta,'actual SOL cost:',spent_sol)
    elif a.cmd=='sell':
        if a.mint not in ledger.positions():raise LiveHalt('Untracked position: no sell')
        if not 0<a.percent<=100:raise LiveHalt('Invalid sell percentage')
        p0=ledger.positions()[a.mint]
        sol_before=gw.balance_sol()
        current=token_balance(gw.rpc,cfg.wallet_address,a.mint)
        if abs(current-p0['units']-p0['token_balance_before'])>max(0.000001,p0['units']*0.0005):
            raise LiveHalt('External token movement detected; reconcile balance manually')
        print(json.dumps({'mint':a.mint,'percent':a.percent,'tracked_units':p0['units'],'will_execute':bool(a.execute)},indent=2))
        if not a.execute:return
        if os.getenv('HUNTER_CONFIRM_LIVE')!='YES_I_UNDERSTAND_THE_RISK':raise LiveHalt('Explicit environment confirmation missing')
        # On-chain percent refers to entire token balance; adjust for pre-existing tokens.
        desired=p0['units']*a.percent/100.
        percent_of_wallet=100.*desired/current
        oid,sig=gw.submit('sell',a.mint,percent_of_wallet)
        if not gw.confirm(oid,sig):print('Submitted, not confirmed; DO NOT retry.');return
        after=token_balance(gw.rpc,cfg.wallet_address,a.mint)
        sold=current-after
        if sold<=0 or sold>p0['units']*1.01:
            ledger.set_flag('halt','1');raise LiveHalt('Unexpected sell result; halted')
        received_sol=gw.balance_sol()-sol_before
        if not received_sol>0:
            ledger.set_flag('halt','1');raise LiveHalt('SOL proceeds could not be verified')
        sol_usd=price_sol_usd()
        ledger.finalize_filled_sell(oid,a.mint,sold,received_sol,sol_usd,reason='manual_exit')
        print('Confirmed sell:',sig,'tokens sold:',sold,'SOL proceeds:',received_sol)

if __name__=='__main__':
    try:main()
    except (ValueError,LiveHalt,FileNotFoundError) as e:
        print('BLOCKED:',str(e),file=sys.stderr);raise SystemExit(2)
