"""Main orchestrator. Does not import or expose live wallet keys or signing logic."""
from __future__ import annotations
import argparse, asyncio, logging, time
from collections import defaultdict
from .config import load
from .feed import Feed
from .signals import Consensus
from .paper import PaperBroker
from .dex import fetch_quote
from .fomo import from_cli, from_file, B58
from .rug import SolanaRPC

log=logging.getLogger('hunter')

class Hunter:
    def __init__(self,cfg):
        self.cfg=cfg
        self.consensus=Consensus(cfg.signal_ttl_seconds)
        self.broker=PaperBroker(cfg)
        for sale in self.broker.sales:
            self.consensus.update(sale.wallets, sale.pnl_usd)
        self.rug=SolanaRPC(cfg.solana_rpc_url)
        self.curve_owner={}
        self.last_eligible_attempt={}
        self.tracked=[]
        self.risk_last_check={}
        self.last_refresh=0.0

    async def refresh_wallets(self):
        if time.time()-self.last_refresh<self.cfg.fomo_refresh_seconds: return
        self.last_refresh=time.time()
        wallets=list(self.cfg.tracked_wallets or [])
        try:
            wallets+=await asyncio.to_thread(from_cli,self.cfg.max_tracked_wallets)
            log.info('FOMO CLI wallet scan succeeded')
        except Exception as exc:
            log.warning('FOMO CLI not available: %s',str(exc)[:150])
        wallets+=from_file('data/fomo_wallets.json',self.cfg.max_tracked_wallets)
        unique=list(dict.fromkeys([w for w in wallets if isinstance(w,str) and B58.fullmatch(w)]))
        # Dynamic ranking uses local attributable signal histories; neutrals remain eligible.
        self.tracked=sorted(unique,key=self.consensus.score,reverse=True)[:self.cfg.max_tracked_wallets]
        log.info('Tracking %d FOMO-associated wallets',len(self.tracked))

    async def on_event(self,event):
        mint=event.get('mint')
        if not isinstance(mint,str) or not B58.fullmatch(mint): return
        curve=event.get('bondingCurveKey') or event.get('bondingCurve')
        if isinstance(curve,str) and B58.fullmatch(curve):
            self.curve_owner[mint]=curve
        trader=event.get('traderPublicKey')
        side=str(event.get('txType','')).lower()
        if trader in self.tracked and side in ('buy','sell'):
            self.consensus.record(mint,trader,side)
            if side=='buy':
                log.info('Wallet signal %s mint=%s voters=%s',trader[:6],mint[:8],len(self.consensus.voters(mint)))

    async def check_entry(self,mint):
        if mint in self.broker.positions or not self.consensus.consensus(mint,self.cfg.min_distinct_wallets): return
        if time.time()-self.last_eligible_attempt.get(mint,0)<30: return
        self.last_eligible_attempt[mint]=time.time()
        try:
            quote=await asyncio.to_thread(fetch_quote,mint)
            if not quote: return
            if not (self.cfg.min_market_cap_usd<=quote.market_cap_usd<=self.cfg.max_market_cap_usd): return
            if quote.liquidity_usd<self.cfg.min_liquidity_usd: return
            risk=await asyncio.to_thread(self.rug.examine,mint,self.curve_owner.get(mint))
            if not risk.allowed:
                log.info('REJECT %s due to %s',mint,', '.join(risk.reasons)); return
            voters=self.consensus.voters(mint)
            if self.broker.buy(quote,voters):
                log.warning('PAPER BUY %s %.2f cap %.0f (voters=%d)',mint,quote.price_usd,quote.market_cap_usd,len(voters))
        except Exception as exc:
            log.warning('Entry check failed closed: %s',str(exc)[:200])

    async def tick(self):
        for mint in list(self.consensus.events):
            if self.consensus.consensus(mint,self.cfg.min_distinct_wallets):
                await self.check_entry(mint)
        for mint in list(self.broker.positions):
            try:
                quote=await asyncio.to_thread(fetch_quote,mint)
                if quote:
                    if quote.liquidity_usd<self.cfg.min_liquidity_usd:
                        sale=self.broker.sell(mint,1,quote.price_usd,'liquidity deterioration')
                        if sale:
                            self.consensus.update(sale.wallets,sale.pnl_usd)
                            log.warning('PAPER RISK EXIT %s on liquidity deterioration',mint)
                        continue
                    if time.time()-self.risk_last_check.get(mint,0)>90:
                        self.risk_last_check[mint]=time.time()
                        report=await asyncio.to_thread(self.rug.examine,mint,self.curve_owner.get(mint))
                        if not report.allowed:
                            sale=self.broker.sell(mint,1,quote.price_usd,'risk deteriorated / RPC unavailable')
                            if sale:
                                self.consensus.update(sale.wallets,sale.pnl_usd)
                                log.warning('PAPER RISK EXIT %s: %s',mint,report.reasons)
                            continue
                    signal_wallets=list(self.broker.positions[mint].wallets)
                    for sale in self.broker.update(quote):
                        self.consensus.update(signal_wallets,sale.pnl_usd)
                        log.warning('PAPER SELL %s reason=%s pnl=$%.2f',mint,sale.reason,sale.pnl_usd)
                else:
                    sale=self.broker.data_stale(mint)
                    if sale: log.error('PAPER MARK LOSS %s (no executable price)',mint)
            except Exception as exc:
                log.error('Pricing error %s: %s',mint[:8],str(exc)[:200])
                sale=self.broker.data_stale(mint)
                if sale: log.error('PAPER MARK LOSS %s (unpriceable)',mint)
        log.info('EQUITY $%.2f CASH $%.2f OPEN %d',self.broker.equity(),self.broker.cash,len(self.broker.positions))

    async def loop_prices(self):
        while True:
            await self.tick()
            await asyncio.sleep(self.cfg.price_poll_seconds)

    async def loop_feed(self):
        while True:
            try:
                await self.refresh_wallets()
                current=list(self.tracked)
                if not self.cfg.enable_paid_wallet_stream:
                    log.warning('Account-trade subscriptions OFF (metered by provider): only token discovery active; no buy signals.')
                    current=[]
                elif not current:
                    log.error('No FOMO wallets. Set tracked_wallets or load data/fomo_wallets.json. No entries possible.')
                supplier=lambda: self.tracked if self.cfg.enable_paid_wallet_stream else []
                await Feed(self.cfg.pumpportal_websocket_url).stream(current,self.on_event,supplier)
            except Exception as exc:
                log.error('PumpPortal stream unavailable: %s',type(exc).__name__)
            await asyncio.sleep(5)

    async def loop_refresh(self):
        while True:
            await asyncio.sleep(self.cfg.fomo_refresh_seconds)
            await self.refresh_wallets()
            log.info('Wallet rankings refreshed. Websocket subscription set is adjusted on the existing connection.')

async def run(cfg):
    bot=Hunter(cfg)
    await asyncio.gather(bot.loop_prices(),bot.loop_feed(),bot.loop_refresh())

def main():
    parser=argparse.ArgumentParser(description='Pump.fun multi-wallet PAPER scanner')
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--once',action='store_true',help='run one pricing cycle without websocket')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    cfg=load(args.config)
    print(f'PAPER ONLY | {cfg.mode.upper()}={cfg.allocation_pct:.0%} allocation | ${cfg.min_market_cap_usd:,.0f}-${cfg.max_market_cap_usd:,.0f} cap')
    asyncio.run(Hunter(cfg).tick() if args.once else run(cfg))

if __name__=='__main__': main()
