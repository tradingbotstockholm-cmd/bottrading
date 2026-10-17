"""Only observed USD-priced DEX pairs; never fabricate a price or market cap."""
from __future__ import annotations
from .http import get_json
from .models import Quote

def quote_from_pairs(mint: str, pairs: list[dict]) -> Quote | None:
    candidates=[]
    for pair in pairs:
        if not isinstance(pair, dict) or pair.get('chainId')!='solana':
            continue
        # Reject reversed pairs: priceUsd on DexScreener prices baseToken.
        if pair.get('baseToken', {}).get('address') != mint:
            continue
        try:
            price=float(pair.get('priceUsd') or 0)
            mcap=float(pair.get('marketCap') or 0)
            liquidity=float((pair.get('liquidity') or {}).get('usd') or 0)
        except (ValueError, TypeError):
            continue
        if price<=0 or mcap<=0 or liquidity<=0:
            continue
        candidates.append(Quote(mint, price, mcap, liquidity, str(pair.get('dexId','unknown'))))
    return max(candidates, key=lambda q:q.liquidity_usd) if candidates else None

def fetch_quote(mint: str) -> Quote | None:
    data=get_json(f'https://api.dexscreener.com/token-pairs/v1/solana/{mint}')
    if not isinstance(data, list):
        return None
    return quote_from_pairs(mint, data)
