"""Non-blocking research ingestion for free PumpPortal launches + optional FOMO alerts.

No orders, wallets, authenticated FOMO session scraping, or trust in alert PnL.
An alert is a third-party hint, NEVER a verified independent wallet vote.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import time

B58 = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


@dataclass(frozen=True)
class Launch:
    mint: str
    curve: str
    creator: str
    source: str = 'pumpportal'


@dataclass(frozen=True)
class FomoHint:
    mint: str
    side: str
    event_id: str
    event_time: float
    trader_label: str


def launch_event(message: object) -> Launch | None:
    if not isinstance(message, dict) or str(message.get('txType', '')).lower() != 'create':
        return None
    mint = message.get('mint')
    if not isinstance(mint, str) or not B58.fullmatch(mint):
        return None
    curve = message.get('bondingCurveKey') or message.get('bondingCurve')
    creator = message.get('traderPublicKey')
    return Launch(mint, curve if isinstance(curve, str) and B58.fullmatch(curve) else '',
                  creator if isinstance(creator, str) and B58.fullmatch(creator) else '')


def _event_time(val: object) -> float | None:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        ts = float(val)
        return ts / 1000 if ts > 100_000_000_000 else ts
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None
    return None


def fomo_event(message: object, now: float | None = None, max_age: float = 120) -> FomoHint | None:
    """Only Solana buy/sell alerts with a usable, recent upstream timestamp."""
    if not isinstance(message, dict) or message.get('type') != 'alert':
        return None
    if message.get('chain') != 'solana' or message.get('alertType') not in ('buy', 'sell'):
        return None
    mint = message.get('tokenAddress')
    if not isinstance(mint, str) or not B58.fullmatch(mint):
        return None
    if message.get('replay') or message.get('retracted'):
        return None
    ts = _event_time(message.get('execTs')) or _event_time(message.get('ts'))
    current = time.time() if now is None else now
    if ts is None or ts > current + 10 or ts < current - max_age:
        return None
    trader = message.get('trader')
    if isinstance(trader, dict):
        trader = trader.get('handle') or trader.get('wallet') or ''
    trader = trader if isinstance(trader, str) else ''
    # EventId may be absent, so canonical content provides conservative dedupe.
    event_id = message.get('eventId')
    if not isinstance(event_id, str) or not event_id:
        payload = [mint, message['alertType'], ts, trader, message.get('txHash')]
        event_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return FomoHint(mint, message['alertType'], event_id[:128], ts, trader[:60])

async def stream_fomo_research(endpoint: str, on_hint, key: str = '') -> None:
    """Unofficial third-party, read-only Solana alert stream. Never trades."""
    import websockets
    from urllib.parse import urlencode
    params = {'chain': 'solana'}
    if key:
        params['key'] = key
    url = endpoint.rstrip('?') + '?' + urlencode(params)
    async with websockets.connect(url, open_timeout=12, ping_interval=20,
                                  max_size=1_000_000, max_queue=256) as socket:
        async for raw in socket:
            try:
                message = json.loads(raw)
                hint = fomo_event(message)
                if hint is not None:
                    await on_hint(hint)
            except (ValueError, TypeError):
                continue
