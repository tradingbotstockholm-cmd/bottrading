"""Live market observation + paper execution only. Never sends signed transactions."""
from __future__ import annotations
import asyncio,logging,time,dataclasses,json,os,math
from pathlib import Path
from hunter.config import Config
from hunter.dex import fetch_quote
from .bonding_quote import fetch_curve_quote
from hunter.feed import Feed
from hunter.fomo import from_cli,from_file,B58
from hunter.signals import Consensus
from .chain import SolanaClient,WalletPoller,extract_wallet_trades
from .broker import Broker
from .db import Database
from .security import SecurityGate
from .discovery import TraderScout
from .faststream import launch_event, stream_fomo_research

log=logging.getLogger('hunter2')

@dataclasses.dataclass
class Settings(Config):
    db_path:str='data/hunter2.sqlite'
    poll_public_wallet_rpc:bool=True
    wallet_poll_seconds:int=35
    wallet_signatures_per_poll:int=8
    use_fomo_cli:bool=False
    require_rugcheck:bool=True
    rug_score_ceiling:int=20
    # PAPER ONLY; never relax funded/live order security. Provisional risk = minimum order.
    paper_exploratory_rug:bool=True
    paper_rug_score_ceiling:int=45
    risk_refresh_seconds:int=120
    max_price_checks_per_cycle:int=25
    max_position_checks_per_cycle:int=40
    quote_parallelism:int=4
    auto_discover_traders:bool=True
    enable_scout_live_consensus:bool=True  # fresh on-chain swaps, PAPER ONLY
    scout_interval_seconds:int=30
    scout_mints_per_cycle:int=3
    scout_transactions_per_cycle:int=10
    scout_min_buys:int=3
    scout_min_distinct_mints:int=2
    scout_wallets_per_cycle:int=1
    scout_wallet_transactions_per_cycle:int=4
    # Unofficial third-party FOMO feed is RESEARCH ONLY (keyless demo is delayed).
    enable_fomo_research_feed:bool=True
    fomo_research_endpoint:str='wss://api.fomoapi.io/ws/alerts'
    market_queue_limit:int=8192
    # Optional only: PumpPortal token trades are metered, NEVER enabled by default.
    enable_paid_token_stream:bool=False
    paid_token_stream_limit:int=32
    # PAPER ONLY heuristic sizing; not a trained probability of profit.
    adaptive_paper_sizing:bool=True
    # Paper entries are not constrained by token market-cap band unless opted in.
    # Recorded positive cap is still required to verify USD quotes.
    enforce_paper_market_cap:bool=False
    min_paper_entry_usd:float=2.0
    max_paper_entry_usd:float=1000.0

    def validate(self):
        super().validate()
        if (self.enable_paid_wallet_stream or self.enable_paid_token_stream) and not os.getenv('PUMPPORTAL_API_KEY'):
            raise ValueError('Paid trade subscriptions require explicit PUMPPORTAL_API_KEY')
        if type(self.adaptive_paper_sizing) is not bool:raise ValueError('invalid adaptive sizing flag')
        if type(self.enforce_paper_market_cap) is not bool:raise ValueError('invalid paper market-cap filter flag')
        if not (isinstance(self.min_paper_entry_usd,(int,float)) and isinstance(self.max_paper_entry_usd,(int,float))
                and math.isfinite(self.min_paper_entry_usd) and math.isfinite(self.max_paper_entry_usd)
                and 2<=self.min_paper_entry_usd<=self.max_paper_entry_usd<=1000):
            raise ValueError('paper purchase range must be between $2 and $1,000')
        if not 10<=self.wallet_poll_seconds<=3600:raise ValueError('wallet_poll_seconds out of range')
        if not 1<=self.wallet_signatures_per_poll<=30:raise ValueError('signature poll limit out of range')
        if not 0<=self.rug_score_ceiling<=100:raise ValueError('rug_score_ceiling out of range')
        if type(self.paper_exploratory_rug) is not bool:raise ValueError('invalid exploratory paper rug flag')
        if not self.rug_score_ceiling<=self.paper_rug_score_ceiling<=60:raise ValueError('invalid exploratory paper rug ceiling')
        if not 1<=self.max_price_checks_per_cycle<=60:raise ValueError('price check cap out of range')
        if not 1<=self.max_position_checks_per_cycle<=250:raise ValueError('position check cap out of range')
        if not 1<=self.quote_parallelism<=16:raise ValueError('quote parallelism out of range')
        if not isinstance(self.auto_discover_traders,bool):raise ValueError('auto_discover_traders must be bool')
        if not isinstance(self.enable_scout_live_consensus,bool):raise ValueError('invalid scout consensus flag')
        if not 20<=self.scout_interval_seconds<=3600:raise ValueError('scout interval out of range')
        if not 1<=self.scout_mints_per_cycle<=10:raise ValueError('scout mint cap out of range')
        if not 1<=self.scout_transactions_per_cycle<=30:raise ValueError('scout transaction cap out of range')
        if not 2<=self.scout_min_buys<=50 or not 2<=self.scout_min_distinct_mints<=20:raise ValueError('scout evidence threshold out of range')
        if not isinstance(self.enable_fomo_research_feed,bool):raise ValueError('FOMO feed flag must be bool')
        if not self.fomo_research_endpoint.startswith('wss://'):raise ValueError('FOMO feed must use wss')
        if not 1024<=self.market_queue_limit<=131072:raise ValueError('invalid market queue cap')
        if not isinstance(self.enable_paid_token_stream,bool):raise ValueError('invalid paid-token setting')
        if not 1<=self.paid_token_stream_limit<=128:raise ValueError('paid-token subscription cap exceeded')
        if not 0<=self.scout_wallets_per_cycle<=3:raise ValueError('wallet research cap out of range')
        if not 1<=self.scout_wallet_transactions_per_cycle<=10:raise ValueError('wallet follow-up transaction budget out of range')


def paper_signal_strength(*, voters:int, oldest_buy_age_seconds:float, ttl_seconds:int,
                          liquidity_usd:float, minimum_liquidity_usd:float,
                          rug_score:int|None, maximum_rug_score:int) -> float:
    """Deterministic evidence-strength index, NEVER a calibrated win probability.

    Only call AFTER independent-wallet, quote and security gates pass. No AI
    forecasts, FOMO hints or historical replayed transactions can authorize it.
    Missing security scores receive the most conservative size.
    """
    if rug_score is None:
        return 0.0
    if (voters < 2 or not all(math.isfinite(float(x)) for x in
            (oldest_buy_age_seconds,ttl_seconds,liquidity_usd,minimum_liquidity_usd,rug_score,maximum_rug_score))):
        return 0.0
    if ttl_seconds<=0 or minimum_liquidity_usd<=0 or liquidity_usd<=0:
        return 0.0
    wallet_component=min(1.0,max(0,voters-2)/3)
    fresh_component=max(0.0,1-min(1.0,max(0.0,oldest_buy_age_seconds)/ttl_seconds))
    liquidity_component=min(1.0,max(0.0,(liquidity_usd/minimum_liquidity_usd-1)/3))
    rug_component=max(0.0,1.0-float(rug_score)/max(1,maximum_rug_score))
    return round(max(0.0,min(1.0,0.35*wallet_component+0.25*fresh_component+
                            0.20*liquidity_component+0.20*rug_component)),5)


def free_scout_backoff(interval:int, rpc_errors:int, checked:int) -> int:
    """Public-RPC protection without penalizing a healthy token scan for
    unrelated wallet-history errors. Never bypass the entry signal TTL.
    """
    if rpc_errors and checked == 0:
        return max(90, interval * 3)
    if rpc_errors >= 2:
        return max(60, interval * 2)
    return interval


def load_settings(path='config.json'):
    raw=json.loads(Path(path).read_text())
    valid={x.name for x in dataclasses.fields(Settings)}
    if set(raw)-valid:raise ValueError('Unknown config keys: '+', '.join(sorted(set(raw)-valid)))
    cfg=Settings(**raw)
    if os.getenv('SOLANA_RPC_URL'):cfg.solana_rpc_url=os.getenv('SOLANA_RPC_URL')
    if os.getenv('PUMPPORTAL_API_KEY'):
        cfg.pumpportal_websocket_url='wss://pumpportal.fun/api/data?api-key='+os.getenv('PUMPPORTAL_API_KEY')
    cfg.validate();return cfg

class Hunter:
    def __init__(self,cfg:Settings):
        self.cfg=cfg;self.db=Database(cfg.db_path,cfg.initial_cash_usd)
        self.broker=Broker(self.db,cfg);self.consensus=Consensus(cfg.signal_ttl_seconds)
        self.security=SecurityGate(cfg.solana_rpc_url,cfg.rug_score_ceiling,cfg.require_rugcheck,
                                  paper_exploratory=cfg.paper_exploratory_rug,
                                  paper_rug_score_ceiling=cfg.paper_rug_score_ceiling)
        self.client=SolanaClient(cfg.solana_rpc_url)
        self.poller=WalletPoller(self.client,self.db,cfg.wallet_signatures_per_poll)
        self.scout=TraderScout(self.client,self.db,extract_wallet_trades,
            minimum_buys=cfg.scout_min_buys,minimum_mints=cfg.scout_min_distinct_mints,
            tx_budget=cfg.scout_transactions_per_cycle,
            wallet_tx_budget=cfg.scout_wallet_transactions_per_cycle,
            signal_max_age_seconds=cfg.signal_ttl_seconds,
            limit=6,rescan_seconds=35,min_token_age_seconds=12)
        self.tracked=[];self.last_wallet_refresh=0.;self.last_risk={};self.last_attempt={}
        self.last_cand_check={};self.candidates=set()
        self.recent_quote={}
        self.known_outcomes=set()
        self.market_queue=asyncio.Queue(maxsize=cfg.market_queue_limit)
        self.market_dropped=0
        self.market_received=0
        self.fomo_observed=0
        self.scanner_started_at=time.time()
        self.quote_semaphore=asyncio.Semaphore(cfg.quote_parallelism)
        self.position_last_checked={}
        self.position_check_count=0
    async def metrics_loop(self):
        """Publish measured launch rates once per second, outside the hot path."""
        previous_count=self.market_received
        previous_clock=time.monotonic()
        while True:
            current_clock=time.monotonic()
            received=self.market_received
            rate=max(0,received-previous_count)/max(.001,current_clock-previous_clock)
            previous_count=received
            previous_clock=current_clock
            report={
                'market_received':received,
                'position_checks_completed':self.position_check_count,
                'max_open_positions':self.cfg.max_open_positions,
                'max_position_checks_per_cycle':self.cfg.max_position_checks_per_cycle,
                'quote_parallelism':self.cfg.quote_parallelism,
                'max_total_exposure_pct':self.cfg.max_total_exposure_pct,
                'adaptive_paper_sizing':self.cfg.adaptive_paper_sizing,
                'min_paper_entry_usd':self.cfg.min_paper_entry_usd,
                'max_paper_entry_usd':self.cfg.max_paper_entry_usd,
                'launch_events_per_second':round(rate,2),
                'market_dropped':self.market_dropped,
                'queue_depth':self.market_queue.qsize(),
                'queue_capacity':self.cfg.market_queue_limit,
                'fomo_alerts_received':self.fomo_observed,
                'tracked_rpc_wallets':len(self.tracked),
                'scanner_started_at':self.scanner_started_at,
                'scout_min_buys':self.cfg.scout_min_buys,
                'scout_min_distinct_mints':self.cfg.scout_min_distinct_mints,
                'min_distinct_wallets':self.cfg.min_distinct_wallets,
                'scout_live_consensus':self.cfg.enable_scout_live_consensus,
                'paid_token_stream':self.cfg.enable_paid_token_stream,
                'paid_wallet_stream':self.cfg.enable_paid_wallet_stream,
                'mode':'PAPER',
            }
            try:await asyncio.to_thread(self.db.record_runtime,report)
            except asyncio.CancelledError:raise
            except Exception as exc:log.warning('Scanner heartbeat failed: %s',type(exc).__name__)
            await asyncio.sleep(1)
    async def wallets(self):
        if time.time()-self.last_wallet_refresh <self.cfg.fomo_refresh_seconds and self.tracked:return
        self.last_wallet_refresh=time.time()
        # User-pinned wallets always take precedence over auto-discovered ones.
        src=list(self.cfg.tracked_wallets or [])
        try:src+=from_file('data/fomo_wallets.json',self.cfg.max_tracked_wallets)
        except Exception as exc:self.db.record_error('fomo_file',type(exc).__name__)
        if self.cfg.use_fomo_cli:
            try:src+=await asyncio.to_thread(from_cli,self.cfg.max_tracked_wallets)
            except Exception as exc:self.db.record_error('fomo_cli',type(exc).__name__)
        pinned=list(dict.fromkeys(x for x in src if isinstance(x,str) and B58.fullmatch(x)))
        suggested=[]
        if self.cfg.auto_discover_traders:
            try:suggested=self.scout.candidates(self.cfg.max_tracked_wallets)
            except Exception as exc:self.db.record_error('scout_candidates',type(exc).__name__)
        self.tracked=list(dict.fromkeys(pinned+suggested))[:self.cfg.max_tracked_wallets]
        self.db.observe('','wallet_refresh',f'{len(self.tracked)} wallets; FOMO CLI enabled={self.cfg.use_fomo_cli}')
    async def event(self,event):
        if not isinstance(event,dict):return
        mint=event.get('mint');curve=event.get('bondingCurveKey') or event.get('bondingCurve') or ''
        if not isinstance(mint,str) or not B58.fullmatch(mint):return
        side=str(event.get('txType','')).lower()
        if side=='create':
            self.db.remember_mint(mint,curve if isinstance(curve,str) and B58.fullmatch(curve) else '', 'pumpportal')
            creator=event.get('traderPublicKey')
            if isinstance(creator,str) and B58.fullmatch(creator):
                self.db.remember_creator(mint,creator)
        elif isinstance(curve,str) and B58.fullmatch(curve):
            self.db.remember_mint(mint,curve,'pumpportal')
        trader=event.get('traderPublicKey')
        if trader in self.tracked and side in ('buy','sell'):
            self.consensus.record(mint,trader,side)
            self.db.observe(mint,'wallet_'+side,trader[:12]+'… / pumpportal')
    async def enqueue_market_event(self,event):
        # No database or network I/O on the PumpPortal websocket receiver.
        launch=launch_event(event)
        if launch is not None:
            self.market_received+=1
            try:self.market_queue.put_nowait(launch)
            except asyncio.QueueFull:self.market_dropped+=1
        else:
            # Only paid trade streams, when deliberately enabled, use this path.
            if isinstance(event,dict) and str(event.get('txType','')).lower() in ('buy','sell'):
                await self.event(event)

    async def market_ingest_loop(self):
        # Writes launch batches in one transaction; retries failures without
        # claiming the queued launches were processed. Bounded queue / memory.
        last_report=time.monotonic()
        pending=[]
        while True:
            try:
                if not pending:
                    item=await asyncio.wait_for(self.market_queue.get(),timeout=.2)
                    pending.append(item)
                    while len(pending)<256:
                        try:pending.append(self.market_queue.get_nowait())
                        except asyncio.QueueEmpty:break
                if pending:
                    await asyncio.to_thread(self.db.remember_mints_batch,pending)
                    for _ in pending:self.market_queue.task_done()
                    pending=[]
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                # An incomplete batch has not been falsely acknowledged. Events
                # already received are not guaranteed persisted on abrupt exit;
                # monitor backlog and drop metrics before calling feed complete.
                raise
            except Exception as exc:
                self.db.record_error('market_ingest_error',type(exc).__name__)
                await asyncio.sleep(1)
            if time.monotonic()-last_report>=30:
                last_report=time.monotonic()
                self.db.observe('','market_ingest',
                    f'Pump.fun launches received={self.market_received}; queue={self.market_queue.qsize()+len(pending)}; dropped={self.market_dropped}; research FOMO alerts={self.fomo_observed}')

    async def record_fomo_hint(self,hint):
        # No fake trader votes, paper fills or automatic purchases from third-party alerts.
        if await asyncio.to_thread(self.db.remember_fomo_hint,hint):
            self.fomo_observed+=1

    async def fomo_research_loop(self):
        if not self.cfg.enable_fomo_research_feed:return
        while True:
            try:
                await stream_fomo_research(self.cfg.fomo_research_endpoint,
                        self.record_fomo_hint,os.getenv('FOMOAPI_KEY',''))
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.db.record_error('fomo_research_feed',type(exc).__name__)
                log.warning('Unofficial FOMO research feed disconnected: %s',type(exc).__name__)
            await asyncio.sleep(15)

    def accept_fresh_scout_signals(self, stats):
        """Only fresh, signer-verified, parser-confirmed scout swaps may vote.

        Wallet follow-up and FOMO hint history intentionally never calls this.
        Consensus deduplicates by wallet so sources cannot double-count.
        Broker and security gates remain unchanged.
        """
        if not self.cfg.enable_scout_live_consensus:return
        for sg in stats.live_signals:
            self.consensus.record(sg.mint,sg.wallet,sg.side,now=sg.event_time)
            self.db.observe(sg.mint,'scout_live_'+sg.side,
                f'{sg.wallet[:10]} signature={sg.signature[:12]} age={max(0,time.time()-sg.event_time):.0f}s')
            if sg.side=='buy' and self.consensus.consensus(sg.mint,self.cfg.min_distinct_wallets):
                self.db.observe(sg.mint,'scout_consensus',
                    f'{len(self.consensus.voters(sg.mint))} unique buy wallets; pending paper risk checks')

    async def scout_loop(self):
        if not self.cfg.auto_discover_traders:return
        while True:
            try:
                stats=await asyncio.to_thread(self.scout.run_cycle,self.cfg.scout_mints_per_cycle)
                if stats.transactions_checked or stats.confirmed_trades or stats.rpc_errors:
                    self.db.observe('','scout_scan',
                        f'{stats.mints_scanned} launches; {stats.transactions_checked} tx; '
                        f'{stats.confirmed_trades} swap signals; {stats.wallets_promoted} activity-qualified wallets; '
                        f'RPC errors={stats.rpc_errors}; budget_limited={stats.budget_exhausted}')
                self.accept_fresh_scout_signals(stats)
                if stats.wallets_promoted:
                    self.last_wallet_refresh=0.0
                    await self.wallets()
                # Follow up previously discovered buyers to research their *other*
                # trades. This never records a live consensus signal or paper fill.
                follow_errors=0
                if self.cfg.scout_wallets_per_cycle:
                    follow=await asyncio.to_thread(self.scout.run_wallet_followup,
                        self.cfg.scout_wallets_per_cycle)
                    follow_errors=follow.rpc_errors
                    if follow.wallets_checked or follow.rpc_errors:
                        self.db.observe('','scout_wallet_followup',
                            f'{follow.wallets_checked} wallets; {follow.transactions_checked} tx; '
                            f'{follow.confirmed_trades} swaps; {follow.wallets_promoted} activity-qualified wallets; '
                            f'RPC errors={follow.rpc_errors}; budget_limited={follow.budget_exhausted}')
                    if follow.wallets_promoted:
                        self.last_wallet_refresh=0.0
                        await self.wallets()
                # Back off on provider failures rather than making a burst worse.
                delay=free_scout_backoff(self.cfg.scout_interval_seconds,stats.rpc_errors,stats.transactions_checked)
            except Exception as exc:
                self.db.record_error('scout_cycle',type(exc).__name__)
                delay=max(self.cfg.scout_interval_seconds,240)
            await asyncio.sleep(delay)

    async def wallet_poll(self):
        while True:
            try:
                await self.wallets()
                if self.cfg.poll_public_wallet_rpc:
                    for wallet in list(self.tracked):
                        try:
                            signals=await asyncio.to_thread(self.poller.poll,wallet)
                            for sg in signals:
                                self.consensus.record(sg.mint,sg.wallet,sg.side)
                                self.db.observe(sg.mint,'rpc_'+sg.side,f'{sg.wallet[:9]} signature={sg.signature[:12]}')
                        except Exception as exc:self.db.record_error('wallet_rpc',type(exc).__name__)
            except Exception as exc:self.db.record_error('wallet_cycle',type(exc).__name__)
            await asyncio.sleep(self.cfg.wallet_poll_seconds)
    def market_quote(self,mint):
        """Use indexed DEX price first, canonical active Pump curve otherwise.

        A missing or unavailable oracle never becomes a guessed paper price.
        All provider failures remain observable and fail closed.
        """
        quote=None
        try:quote=fetch_quote(mint)
        except Exception as exc:
            self.db.record_error('quote_dex',f'{mint[:12]}:{type(exc).__name__}')
        if quote is not None:return quote
        curve=self.db.curve_owner(mint)
        if not curve:return None
        try:return fetch_curve_quote(mint,curve,self.cfg.solana_rpc_url)
        except Exception as exc:
            self.db.record_error('quote_curve',f'{mint[:12]}:{type(exc).__name__}')
            return None

    async def try_entry(self,mint):
        now=time.time()
        if mint in self.broker.state()['positions'] or now-self.last_attempt.get(mint,0)<30:return
        voters=self.consensus.voters(mint)
        if len(set(voters))<self.cfg.min_distinct_wallets:return
        self.last_attempt[mint]=now
        try:
            quote=await asyncio.to_thread(self.market_quote,mint)
            if quote is None:
                self.db.observe(mint,'reject','no indexed DEX price or verified on-chain USD bonding-curve quote');return
            self.db.record_price_sample(quote)
            if self.cfg.enforce_paper_market_cap and not (
                self.cfg.min_market_cap_usd<=quote.market_cap_usd<=self.cfg.max_market_cap_usd
            ):
                self.db.observe(mint,'reject',f'market cap ${quote.market_cap_usd:.0f} outside configured range');return
            curve=self.db.curve_owner(mint)
            risk=await asyncio.to_thread(self.security.examine,mint,curve)
            if not risk.approved:
                self.db.observe(mint,'rug_reject','; '.join(risk.reasons));return
            vote_times=self.consensus.events.get(mint,{})
            oldest_age=max((now-float(vote_times.get(w,now)) for w in voters),default=self.cfg.signal_ttl_seconds)
            strength=paper_signal_strength(voters=len(set(voters)),oldest_buy_age_seconds=oldest_age,
                ttl_seconds=self.cfg.signal_ttl_seconds,liquidity_usd=quote.liquidity_usd,
                minimum_liquidity_usd=self.cfg.min_liquidity_usd,rug_score=risk.score,
                maximum_rug_score=self.cfg.rug_score_ceiling)
            provisional=getattr(risk,'provisional',False)
            if provisional:
                # A partial rug audit does not get to scale up with wallet votes.
                # Default adaptive broker maps strength 0 to the $2 minimum;
                # if sizing is disabled, REFUSE this experimental entry.
                if not self.cfg.adaptive_paper_sizing:
                    self.db.observe(mint,'rug_reject','provisional security requires adaptive $2 sizing');return
                strength=0.0
                self.db.observe(mint,'paper_rug_uncertain','; '.join(getattr(risk,'notes',())))
            if self.broker.buy(quote,voters,signal_strength=strength if self.cfg.adaptive_paper_sizing else None):
                basis=self.broker.state()['positions'][mint]['basis']
                self.db.observe(mint,'paper_buy',f'{len(voters)} independent wallets; '
                    f'paper allocation=${basis:.2f}; signal strength={strength:.3f} (heuristic, not win probability); '
                    f'cap=${quote.market_cap_usd:.0f}; rug score={risk.score}; provisional={provisional}')
            else:
                self.db.observe(mint,'reject','paper broker allocation, liquidity, slippage or account risk limit')
        except Exception as exc:
            self.db.record_error('entry',f'{mint[:12]} {type(exc).__name__}')
    async def _bounded_entry(self,mint):
        # Bound total quote/security calls to protect the RPC/data providers.
        async with self.quote_semaphore:
            await self.try_entry(mint)

    async def _check_position(self,mint,p):
        async with self.quote_semaphore:
            try:
                quote=await asyncio.to_thread(self.market_quote,mint)
                if quote:self.db.record_price_sample(quote)
                if not quote:
                    if time.time()-p['last_quote']>self.cfg.max_price_age_seconds:
                        self.broker.request_exit(mint,'price unavailable')
                    return
                if quote.liquidity_usd<self.cfg.min_liquidity_usd:
                    pnl=self.broker.exit(mint,quote,1,'liquidity deterioration')
                    if pnl is None:self.broker.request_exit(mint,'liquidity deterioration')
                    return
                if time.time()-self.last_risk.get(mint,0)>self.cfg.risk_refresh_seconds:
                    self.last_risk[mint]=time.time()
                    risk=await asyncio.to_thread(self.security.examine,mint,self.db.curve_owner(mint))
                    if not risk.approved:
                        self.db.observe(mint,'risk_exit','; '.join(risk.reasons))
                        self.broker.exit(mint,quote,1,'security check deteriorated')
                        return
                self.broker.mark(quote)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.db.record_error('position_price',f'{mint[:10]} {type(exc).__name__}')
                self.broker.request_exit(mint,'provider or risk error')
            finally:
                self.position_check_count+=1
                self.position_last_checked[mint]=time.monotonic()

    async def check_positions(self):
        # Every open position stays in the rotation. Pending exits first,
        # then the least recently checked. Never issue unbounded RPC bursts.
        positions=self.broker.state()['positions']
        self.position_last_checked={m:t for m,t in self.position_last_checked.items() if m in positions}
        ordered=sorted(positions.items(),key=lambda item:(
            0 if item[1].get('pending_exit') else 1,
            self.position_last_checked.get(item[0],0)))
        selected=ordered[:self.cfg.max_position_checks_per_cycle]
        if selected:
            await asyncio.gather(*(self._check_position(m,p) for m,p in selected))

    async def tick(self):
        pool=[m for m in list(self.consensus.events) if self.consensus.consensus(m,self.cfg.min_distinct_wallets)]
        if pool:await asyncio.gather(*(self._bounded_entry(m) for m in pool[:self.cfg.max_price_checks_per_cycle]))
        await self.check_positions()
    async def price_loop(self):
        while True:
            try:await self.tick()
            except Exception as exc:self.db.record_error('price_cycle',type(exc).__name__)
            await asyncio.sleep(self.cfg.price_poll_seconds)
    async def feed_loop(self):
        while True:
            try:
                await self.wallets()
                streamwallets=self.tracked if self.cfg.enable_paid_wallet_stream else []
                supplier=(lambda:self.db.paid_token_targets(self.cfg.paid_token_stream_limit)) if self.cfg.enable_paid_token_stream else None
                await Feed(self.cfg.pumpportal_websocket_url).stream(streamwallets,self.enqueue_market_event,
                    lambda:self.tracked if self.cfg.enable_paid_wallet_stream else [],token_supplier=supplier)
            except Exception as exc:
                self.db.record_error('pumpportal_feed',type(exc).__name__)
                log.warning('PumpPortal disconnected: %s',type(exc).__name__)
            await asyncio.sleep(5)
    async def run(self):
        log.warning('PAPER ONLY — no signing and no real orders; paid account stream=%s; paid token stream=%s',self.cfg.enable_paid_wallet_stream,self.cfg.enable_paid_token_stream)
        log.warning('Wallet scanner: RPC=%s; automatic PUBLIC wallet research=%s; paid trade feed=%s',
                    self.cfg.poll_public_wallet_rpc,self.cfg.auto_discover_traders,(self.cfg.enable_paid_wallet_stream or self.cfg.enable_paid_token_stream))
        await self.wallets()
        log.warning('Active PUBLIC RPC wallet targets=%d (startup WebSocket count may be zero because paid subscriptions are off)',len(self.tracked))
        log.warning('Streams: free Pump.fun launches + optional UNOFFICIAL FOMO research=%s (no FOMO votes used for orders)',self.cfg.enable_fomo_research_feed)
        await asyncio.gather(self.price_loop(),self.feed_loop(),self.wallet_poll(),
                             self.scout_loop(),self.market_ingest_loop(),self.fomo_research_loop(),self.metrics_loop())
