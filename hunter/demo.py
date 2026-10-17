"""Offline deterministic smoke demonstration. Not market performance."""
from __future__ import annotations
import tempfile
from pathlib import Path
from .config import Config
from .paper import PaperBroker
from .models import Quote
from .signals import Consensus

def main():
    cfg=Config(); cfg.validate()
    x=Consensus(ttl=120)
    x.record('DEMO_MINT','wallet_A','buy',100)
    x.record('DEMO_MINT','wallet_B','buy',100)
    assert x.consensus('DEMO_MINT',2,101)
    with tempfile.TemporaryDirectory() as tmp:
        bot=PaperBroker(cfg,str(Path(tmp)/'state.json'))
        first=Quote('DEMO_MINT',0.000015,15000,9000,'demo')
        bot.buy(first,['wallet_A','wallet_B'],100)
        assert 'DEMO_MINT' in bot.positions
        for price,now in [(0.000030,105),(0.000075,110),(0.000052,115)]:
            sold=bot.update(Quote('DEMO_MINT',price,15000*price/first.price_usd,9000,'demo'),now)
            for s in sold:
                print(f'{s.reason:16s} realized net={s.net_usd:8.2f} pnl={s.pnl_usd:8.2f}')
        print(f'Paper cash ${bot.cash:.2f}, open {len(bot.positions)} — offline test only')

if __name__=='__main__':main()
