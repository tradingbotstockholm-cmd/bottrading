# Solana Hunter V4 — automatic principal recovery and security contract

## Objective

A $5 purchase is **not** guaranteed to earn anything. If the observed position's current value reaches at least a **2.0x gross position multiple**, the V4 strategy estimates how many tokens to sell to recover the entire original $5, an additional 2% buffer, and modeled sale costs, while retaining a token runner (at least 5% of initial units). If available executable liquidity is inadequate, it does not manufacture a successful exit. Actual fills must be confirmed by RPC and reconciled against SOL + token balances; optimistic price quotes NEVER create recycle credit.

**Hypothetical example:** $5 initial paid → the remaining tokens are indicated at $11 → strategy may attempt to sell approximately half the tokens to reclaim ≈ $5.10 or more after modeled costs → the remaining tokens can rise, fall, or become worthless. The recovered cash, if actually received, becomes available for subsequent entries within daily, net-principal, and gross-turnover limits. Trading "only with profit" after withdrawing principal does **not** mean the strategy is economically risk-free: future losses can consume capital and a paper runner has an opportunity cost.

## Mode policy

- Light: ≤3% of available trading-wallet equity for a new trade.
- Heavy: ≤15% of available trading-wallet equity for a new trade.
- Both: no leverage, mandatory independently confirmed signals, mandatory rug checks for automatic entries, market cap $10k–$20k, liquidity/fee/spend limits.
- V4 models a 7% hard loss trigger, 8% trailing profit-protection trigger after sufficient run-up. **Neither is an executable stop guarantee**. Volatility, liquidity, outages, mempool delays, missing prices, and blockchain conditions may produce losses beyond these values.
- Stop and cooldown can pause additional entries; emergency stop blocks **all** transactions until reviewed. If stopping the program with open tokens, manually watch the wallet and consider potential loss.

## The <1% request

We implement an **order slippage tolerance of 0.75%**, **NOT** a price-execution guarantee and **NOT** a PnL prediction error bound. PumpPortal Lightning documents a **1% provider fee on each buy and sell**, plus venue/network fees. Therefore a round trip costs more than 1% even at zero price movement. DexScreener spot prices are not firm executable bid/ask quotes and cannot validate actual spread. The 0.75% modeled-impact filter is a gate, not protection from sudden liquidity loss.

## Account & credential isolation

1. Create a distinct email protected with authenticator MFA.
2. Keep Phantom as main treasury. **Never import its seed phrase into the bot**.
3. Create the FOMO embedded wallet separately. It can be read-only monitored; **the bot cannot execute orders inside the FOMO app**.
4. You may connect Phantom to Pump.fun UI for manual use, but the bot trades only via a separate PumpPortal Lightning wallet. The same Solana transactions can appear in more than one portfolio viewer, but this is **not** dual exchange order execution.
5. Create a dedicated PumpPortal wallet/key at the official provider. The key has full trading power within that wallet, regardless of the software's local caps: **fund it minimally**, keep a tested withdrawal path, and never share its API key.
6. Store API key in macOS Keychain using `security add-generic-password -U -a "$USER" -s solana-hunter-pumpportal -w`. Key storage is under your OS session, not the project's JSON files.
7. Fill only PUBLIC addresses in `accounts.json` and `live.json`. Run `python -m hunter3 balances` and `python -m hunter3 doctor` to ensure roles are correct.
8. Test in paper mode. The explicit one-time `live.json` opt-in, sentinel, and environment confirmation remain compulsory for mainnet. Once locally armed, its future entries/exits do not require individually approving each order.

## Operational circuit breakers

- Fail-closed Rugcheck + on-chain authority checks, wallet consensus, price and market-cap bands.
- A distinct trading wallet; no Phantom/FOMO private key access.
- No duplicate concurrent live loop: operating system exclusive process lock.
- Durable SQLite WAL journal, transaction confirmations and on-chain SOL/token delta settlement.
- If an order is submitted but its result unknown, **do not retry**. An unsettled confirmed transaction also blocks subsequent orders.
- Capital recycled **only** from real settled sale proceeds earmarked for principal recovery, not from paper or unrealized profits.
- Daily SOL gross buy cap, maximum single buy, maximum number of simultaneous positions, net principal cap, and a **separate lifetime gross turnover cap**.
- Kill switch, drawdown circuit breaker, ongoing liquidity inspection, pause on missing prices.
- Local-only read-only dashboard (127.0.0.1), no remote admin endpoint.

## Important gaps / not production-audited

- No verified real mainnet execution or reliable historical PnL distribution; no authenticated FOMO trading integration; no audited contract execution.
- Cannot prove a 0.75% fill deviation (no firm route quote, no cross-venue best bid/ask, no continuous routing optimization).
- V4 uses token and SOL balance deltas after confirmation; if RPC is stale or another person uses the wallet at the same time, accounting may be wrong. Do **not** mix manual trades in the bot wallet.
- Reconciliation after a crash between confirmed broadcast and settlement may require manual review; never reset DB to silence alerts.
- Trading profits are speculative. Fee/slippage assumptions must be stress tested before any funds are used.

## Commands

```bash
bash setup_mac.sh
source .venv/bin/activate
python -m hunter3 init
bash run_v4_paper_mac.sh            # paper capital recovery only
bash dashboard_v4_mac.sh            # V4 paper ledger on localhost:8765
bash dashboard_live_mac.sh          # wallet & settled principal status
python -m hunter3 halt              # block new orders
python -m hunter3 status            # inspect unresolved and net principal
python -m unittest discover -s tests -v
```

Live remains disabled on installation. Experimental live instructions in `V3_SETUP.md` still require explicit enablement, and are NOT a recommendation to fund a live bot.

## Source documentation verified Oct 17, 2026

- https://pumpportal.fun/fees/
- https://pumpportal.fun/trading-api/
- https://pumpportal.fun/trading-api/setup/
