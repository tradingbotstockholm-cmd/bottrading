# Integration audit — GitHub repositories and module-level changes

Review sources: public GitHub file listings / source viewers, official Solana RPC structures, DEX Screener, and PumpPortal reference pages, consulted 17 October 2026. Read this as a **targeted compatibility audit**, not a security certification or demonstration that upstream packages were installed and run. Repositories can change after inspection.

## 12 concrete modules and compatibility requirements

| Source | Module | Existing responsibility | Adaptation / fix before combining |
|---|---|---|---|
| AFKmoney/pumpfun-agent | [`strategies/copy_trade.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/strategies/copy_trade.py) | Watches wallet swaps and emits copy/fade signals | `_decode_pumpfun_swap` calls `base64.b64decode` on instruction data. Solana JSON transaction instructions are typically base58. Decode according to RPC encoding, resolve accountKeys objects in jsonParsed, and add fixture tests; never trust this as accurate without proof. |
| AFKmoney/pumpfun-agent | [`utils/pumpfun_ix.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/utils/pumpfun_ix.py) | Pump.fun discriminators/account builders | Audit current v2 buy/sell account layouts and update for protocol/fee changes using the official Pump.fun IDLs; do not assume legacy 6-account format still applies. |
| AFKmoney/pumpfun-agent | [`strategies/anti_rugpull.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/strategies/anti_rugpull.py) | Token risk veto | Move risk checks into a shared gate enforced by *all* strategies; missing RPC/holder information must fail closed. |
| AFKmoney/pumpfun-agent | [`risk/risk_manager.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/risk/risk_manager.py) | Position limits and losses | Make it single source of truth for daily stop, max exposure, open positions; use portfolio equity rather than just available balance and persist across restarts. |
| AFKmoney/pumpfun-agent | [`risk/smart_exits.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/risk/smart_exits.py) | Take-profit and trailing stops | Exit based on fresh executable quotes, not an unfillable last chart price. Refuse to cancel the exit just because copied wallet still holds. |
| AFKmoney/pumpfun-agent | [`execution/executor.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/execution/executor.py) | Order execution | Distinguish accepted transaction from confirmed settled fill, check balances, retry idempotently; protect against duplicate buy orders after websocket reconnect. |
| AFKmoney/pumpfun-agent | [`utils/data_providers.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/utils/data_providers.py) | External prices/holders | Their `get_pair` uses the first pair. Select matching Solana base mint and the most liquid valid price instead; DEX Screener current token-pairs endpoint `/token-pairs/v1/solana/{mint}` returns an **array**. |
| AFKmoney/pumpfun-agent | [`chains/solana_adapter.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/chains/solana_adapter.py) | RPC and Solana protocol adapter | Verify network selection, address formats, transaction version and provider-specific RPC rate limits; use only one signing component. |
| AFKmoney/pumpfun-agent | [`utils/migration_detector.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/utils/migration_detector.py) | Graduation / migration detection | Verify Pump.fun -> PumpSwap switch and retest sells when the coin graduates. The market-cap strategy can hold positions across both. |
| AFKmoney/pumpfun-agent | [`analysis/social_graph.py`](https://github.com/AFKmoney/pumpfun-agent/blob/main/analysis/social_graph.py) | Trader wallet reputation | Ranking must be recent, survivorship-aware and include failed transactions + real copy latency. No fixed guru wallet. |
| aethernet404/rugcheck | [`rugcheck.py`](https://github.com/aethernet404/rugcheck/blob/main/rugcheck.py) | SPL token mint / freeze / holders risk | Use as a *supplement*, not a single fail-safe score: Pump.fun bonding-curve custody must be excluded correctly from whale concentrations; evaluate source provenance. |
| Chainstacklabs/pumpfun-bonkfun-bot | [`cookbook/README.md`](https://github.com/chainstacklabs/pumpfun-bonkfun-bot/blob/main/cookbook/README.md) | Tested per-protocol sample commands | Use its independent buy/sell examples for a **separate live execution module**, not directly in a paper runner; account versions and PumpSwap routes must be verified. |

## Other dependencies reviewed

- FOMO CLI: https://github.com/ajagatobby/fomo — read-only leaderboard/wallet discovery and `fomo watch` signed webhooks. macOS, Chrome, Node 23+, and a logged-in FOMO account are required. `fomo watch` is restricted to profiles the FOMO account follows and for which alerts are enabled.
- Yuno Copy Trading: https://github.com/yuno-research/solana_copy_trading — Rust position management + Python historical analysis. Open GitHub issues at review time include selling, manual-exit response, and migration detection. Useful for study/backtesting but unsafe to plug blindly into the live path.
- PumpPortal websocket: https://pumpportal.fun/data-api/real-time/ — new-token stream free; account/token trade subscriptions metered, require API key and provider-funded balance.
- DexScreener: https://docs.dexscreener.com/api/reference — 300 RPM token pair endpoints, not every new bonding curve has a reliable USD quote at $10–20k market cap.
- Solana transaction JSON formats: https://solana.com/docs/rpc/json-structures — `json` compiled instruction data is Base58, `jsonParsed` account keys are dictionaries, versioned transactions can have loaded addresses.

## Correct bridge topology

```
FOMO wallets JSON ---> public-key validator ----> tracked-wallet pool
                                               \
PumpPortal new-token WS ----> mint+curve map ----> consensus of >=2 wallet buys
                                                      |
DEX Screener USD pair --------------------------------> entry quote + cap/liquidity
Solana RPC mint+holders ------------------------------> fail-closed security gate
                                                      |
                                                  position sizer
                                                      |
                 paper journal <---------------------- PAPER broker (no live keys)
                       ^
                       | price refresh + trailing stop + liquidity + daily loss
                       |
                   portfolio marks
```

## Decoder bug: minimal upstream change idea (NOT applied to upstream)

The visible `copy_trade.py` includes:

```python
raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
```

With Solana `getTransaction` using `encoding='json'`, replace the string branch with Base58 decoding (`base58.b58decode(data)` from the project's existing `base58` dependency). Normalize all `accountKeys` and `loadedAddresses` before resolving numeric account indices. For `encoding='jsonParsed'`, handle both dict keys and string addresses. Add tests using one real v0 Pump.fun buy, one sell, a PumpSwap route, malformed inputs, and inner instructions. **Do not deploy using the 8-byte discriminator alone**: check program ID, accounts and actual token-balance changes.

## What's deliberately excluded from V1

- Live private-key handling and transactions
- Unreviewed third-party files copied into our project
- FOMO signed webhook listener (the CLI wallet export route is simpler/safer initially)
- Helius/paid APIs beyond provider configuration
- On-chain detection of insider clusters and malicious deployer history (this requires a deeper indexer)
- Full bonding curve pricing when DEX Screener has no pair
- Historical transaction backtesting with executable fill simulation

This is why V1 is a tested paper scaffold, not a guaranteed live profit engine.
