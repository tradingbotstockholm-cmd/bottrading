from __future__ import annotations
from dataclasses import dataclass, field
import time

@dataclass
class Quote:
    mint: str
    price_usd: float
    market_cap_usd: float
    liquidity_usd: float
    dex_id: str
    timestamp: float = field(default_factory=time.time)

@dataclass
class RiskResult:
    allowed: bool
    reasons: list[str]
    largest_non_curve_share: float | None = None

@dataclass
class Position:
    mint: str
    entry_price: float
    units: float
    basis_usd: float
    entry_time: float
    high_price: float
    last_price: float
    last_quote_time: float
    wallets: list[str]
    partial_2x: bool = False
    partial_5x: bool = False

@dataclass
class Sale:
    mint: str
    units: float
    price: float
    net_usd: float
    pnl_usd: float
    reason: str
    time: float
    wallets: list[str] = field(default_factory=list)
