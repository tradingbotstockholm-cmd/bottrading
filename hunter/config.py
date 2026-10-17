from __future__ import annotations
import json, os
from dataclasses import dataclass, fields
from pathlib import Path

@dataclass
class Config:
    mode: str = 'light'
    initial_cash_usd: float = 1000.0
    min_market_cap_usd: float = 10000.0
    max_market_cap_usd: float = 20000.0
    min_liquidity_usd: float = 3000.0
    min_distinct_wallets: int = 2
    signal_ttl_seconds: int = 120
    max_open_positions: int = 3
    max_total_exposure_pct: float = 0.75  # Cost basis / marked equity; never use leverage
    max_daily_loss_pct: float = 0.08
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.25
    max_hold_seconds: int = 1800
    max_price_age_seconds: int = 120
    fee_per_order_usd: float = 0.05
    assumed_slippage_pct: float = 0.01
    price_poll_seconds: int = 10
    fomo_refresh_seconds: int = 900
    max_tracked_wallets: int = 20
    tracked_wallets: list[str] | None = None
    solana_rpc_url: str = 'https://api.mainnet-beta.solana.com'
    pumpportal_websocket_url: str = 'wss://pumpportal.fun/api/data'
    allow_unsafe_concentration: bool = False
    enable_paid_wallet_stream: bool = False

    @property
    def allocation_pct(self) -> float:
        return {'light': 0.03, 'heavy': 0.15}[self.mode]

    def validate(self) -> None:
        assert self.mode in ('light', 'heavy'), 'mode must be light or heavy'
        assert 0 < self.initial_cash_usd
        assert 0 < self.min_market_cap_usd <= self.max_market_cap_usd
        assert 0 <= self.min_liquidity_usd
        assert self.min_distinct_wallets >= 2, 'No single trader should authorize an entry'
        assert 0 < self.stop_loss_pct < 1 and 0 < self.trailing_stop_pct < 1
        assert 0 < self.max_daily_loss_pct < 1
        assert 1 <= self.max_open_positions <= 250
        assert 0 < self.max_total_exposure_pct <= 0.90
        assert 5 <= self.price_poll_seconds <= 60
        assert 20 <= self.max_price_age_seconds <= 3600
        assert 0 <= self.assumed_slippage_pct < 0.5
        assert self.pumpportal_websocket_url.startswith('wss://')
        assert self.solana_rpc_url.startswith('https://')

def load(path: str='config.json') -> Config:
    obj = json.loads(Path(path).read_text())
    allowed = {f.name for f in fields(Config)}
    extra = set(obj) - allowed
    if extra:
        raise ValueError(f'Unknown configuration fields: {sorted(extra)}')
    cfg = Config(**obj)
    if os.getenv('SOLANA_RPC_URL'):
        cfg.solana_rpc_url = os.environ['SOLANA_RPC_URL']
    if os.getenv('PUMPPORTAL_API_KEY'):
        base = 'wss://pumpportal.fun/api/data'
        cfg.pumpportal_websocket_url = f'{base}?api-key={os.environ["PUMPPORTAL_API_KEY"]}'
    cfg.validate()
    return cfg
