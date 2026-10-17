"""V4 paper trading launcher: never sends real transactions."""
import argparse,asyncio
from hunter2.engine import Hunter,load_settings
from .paper import CapitalRecoveryBroker
from .dashboard import serve

class PaperHunterV4(Hunter):
    def __init__(self,cfg):
        super().__init__(cfg)
        self.broker=CapitalRecoveryBroker(self.db,cfg)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Solana Hunter V4 auto paper trading; NOT real funds')
    parser.add_argument('command',choices=('paper','dashboard'))
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--config',default='config.json')
    args=parser.parse_args()
    settings=load_settings(args.config)
    if args.command=='paper':asyncio.run(PaperHunterV4(settings).run())
    else:serve(settings.db_path,args.port,settings.initial_cash_usd)
