from __future__ import annotations
import argparse,asyncio,json,logging
from .engine import Hunter,load_settings
from .dashboard import serve
from .replay import replay
from .db import Database

def main():
    ap=argparse.ArgumentParser(description='Solana Hunter V2 — PAPER ONLY')
    sub=ap.add_subparsers(dest='command',required=True)
    for name in ('run','tick','replay','dashboard','status','pause','resume','report','evaluate'):
        cmd=sub.add_parser(name)
        cmd.add_argument('--config',default='config.json')
        if name in ('replay','evaluate'):cmd.add_argument('events')
        if name=='replay':cmd.add_argument('--db',default='data/replay.sqlite')
        if name=='dashboard':cmd.add_argument('--port',type=int,default=8765)
    a=ap.parse_args();logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    cfg=load_settings(a.config)
    if a.command=='run':asyncio.run(Hunter(cfg).run())
    elif a.command=='tick':asyncio.run(Hunter(cfg).tick())
    elif a.command=='replay':print(json.dumps(replay(a.events,cfg,a.db),indent=2))
    elif a.command=='evaluate':
        from .analytics import evaluate_events
        print(json.dumps(evaluate_events(a.events,cfg),indent=2))
    elif a.command=='report':
        from .analytics import trading_report
        print(json.dumps(trading_report(cfg.db_path,cfg.initial_cash_usd),indent=2))
    elif a.command=='dashboard':serve(cfg.db_path,a.port,cfg.initial_cash_usd)
    elif a.command=='status':print(json.dumps(Database(cfg.db_path,cfg.initial_cash_usd).snapshot(),indent=2))
    elif a.command in ('pause','resume'):
        from .broker import Broker
        Broker(Database(cfg.db_path,cfg.initial_cash_usd),cfg).pause(a.command=='pause')
        print('Paper entries '+('paused' if a.command=='pause' else 'resumed'))
if __name__=='__main__':main()
