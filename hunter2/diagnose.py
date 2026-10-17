"""Read-only diagnosis of recent confirmed public-wallet transactions.

Run on the user's Mac: python -m hunter2.diagnose --config config.v4.json
No checkpoint, ledger, or order state is modified.
"""
from __future__ import annotations
import argparse
from hunter2.chain import SolanaClient, extract_wallet_trades, _programs, _token_deltas, _sol_change, TRADING_PROGRAMS
from hunter2.engine import load_settings


def main():
    parser=argparse.ArgumentParser(description='Read-only public transaction parser diagnostic')
    parser.add_argument('--config',default='config.v4.json')
    parser.add_argument('--limit',type=int,default=5)
    args=parser.parse_args()
    cfg=load_settings(args.config)
    client=SolanaClient(cfg.solana_rpc_url)
    for wallet in (cfg.tracked_wallets or [])[:3]:
        print('Wallet:',wallet)
        try:rows=client.signatures(wallet,max(1,min(args.limit,15)))
        except Exception as exc:
            print('RPC signatures ERROR:',type(exc).__name__);continue
        for row in rows:
            sig=row.get('signature','') if isinstance(row,dict) else ''
            if not sig or row.get('err'):continue
            try:
                tx=client.transaction(sig)
                if tx is None:print(sig[:14],': not yet indexed');continue
                programs=_programs(tx)
                known=sorted(programs.intersection(TRADING_PROGRAMS))
                changes={k:round(v,6) for k,v in _token_deltas(tx,wallet).items() if abs(v)>1e-9}
                detected=extract_wallet_trades(tx,wallet,sig)
                print(sig[:14], 'known_programs=',len(known),'token_deltas=',len(changes),
                      'SOL_delta=',_sol_change(tx,wallet),'signals=',[(x.side,x.mint) for x in detected])
            except Exception as exc:print(sig[:14], 'ERROR:',type(exc).__name__)

if __name__=='__main__':main()
