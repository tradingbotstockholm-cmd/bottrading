# SOLANA HUNTER V3 — Funding, linking, and safeguards

## Critical reality check

- **This project can observe FOMO-wallet and Pump.fun activity. It cannot automate orders inside the FOMO app.** A FOMO account is created with its own wallet; the app does not support importing an existing Phantom wallet. No authenticated, supported FOMO-app order API has been established or tested here. The similarly named `fomo-api.com` is a separate wallet-intelligence vendor, not evidence of an official FOMO trading API.
- **Pump.fun is an interface and liquidity venue.** PumpPortal APIs can place Pump.fun/PumpSwap token orders on-chain. These are not separate duplicated purchases inside the Pump.fun and FOMO applications.
- **Use distinct funding and trading wallets.** Never paste Phantom/FOMO secret phrases or private keys into this project or into any chat. The Lightning API key can move funds in its dedicated wallet; treat it as a powerful credential.
- **V3 code has passed mocked tests, not a live mainnet fill test**. Autotrading is explicitly opt-in and is experimental. It is neither risk-free nor proven profitable.

## Four accounts / public addresses

```text
Your new email address (turn on MFA)
  ├── Phantom treasury wallet (main storage; NEVER give bot its secret)
  │    ├── Connect same Phantom treasury address to official Pump.fun website manually, if desired
  │    ├── Small SOL transfer -> isolated PumpPortal Lightning trading wallet
  │    └── Optional separate transfer -> FOMO app wallet
  ├── FOMO app account (new address created on signup, separate from Phantom)
  │    └── Copy PUBLIC Solana address only for observation
  └── Solana Hunter bot (Mac)
       ├── Scans Pump.fun/PumpSwap on-chain & FOMO wallet intelligence
       ├── Reads public balances of all four wallets
       └── Trades only from isolated PumpPortal Lightning wallet (never from Phantom/FOMO)
```

## Setup: do this in order

1. Create a new email with a long unique password and authenticator-based MFA. Do not put private keys/seed phrases in cloud notes or email.
2. Install Phantom **only** from `https://phantom.com/download`; create a new treasury wallet, back it up offline; write down its public Solana *Receive* address. Do NOT share its recovery phrase. Double-check network is Solana.
3. Sign up to the actual FOMO trading app from its official link, create a FOMO account, and copy its **public SOL receive address**. FOMO creates its own wallet; you cannot import an old Phantom wallet into it. If you want to make FOMO app trades, fund this address separately and trade inside FOMO manually. Do not transfer through unknown address links.
4. Visit the real Pump.fun website and use **Connect Wallet → Phantom**. The connected wallet's public key can be the Phantom treasury address, but avoid approving unknown transactions. Account creation alone does not make the bot authorized to trade.
5. Open PumpPortal's official **Lightning Transaction API** setup (`https://pumpportal.fun/trading-api/setup/`). Create a dedicated new wallet+API key; securely back up keys offline. Copy its **public Solana address** as bot trading address. THIS is the only address the bot will spend from. Verify official provider URLs yourself.
6. Send a **small test amount** of SOL from Phantom to that separate PumpPortal wallet. Confirm the exact first and last characters of the destination, send on Solana, and confirm receipt. Never send the full treasury amount. Fund FOMO's separate wallet only if you want to trade manually within FOMO.
7. On Mac, unzip this package and run:

```bash
bash setup_mac.sh
source .venv/bin/activate
python -m hunter3 init
```

8. Open `accounts.json` and fill the **public addresses only**: `phantom_treasury`, `fomo_app`, `pumpfun_connected` (often same as Phantom), and `pumpportal_trading`. In `live.json`, set `wallet_address` to the EXACT same address as `pumpportal_trading`. Leave `enabled: false` while testing. Check balances:

```bash
python -m hunter3 balances
python -m hunter3 doctor
```

9. Configure optional wallet-copying signals in `config.json`: `tracked_wallets` should include verified profitable public SOL addresses. FOMO wallet intelligence can be imported via optional CLI, if it works; **your own FOMO wallet** may also be observed, but does not constitute profitable trader intelligence. Keep `enable_paid_wallet_stream: false` unless the metered PumpPortal stream is deliberately configured.
10. Run the PAPER bot and dashboard first:

```bash
bash run_mac.sh
# another Terminal
bash dashboard_mac.sh
```

## Storing PumpPortal Lightning API key on macOS

Do NOT put the API key in `accounts.json`, `live.json`, `config.json`, or screenshots. On a private Mac, store it in Apple Keychain using Terminal's interactive prompt:

```bash
security add-generic-password -U -a "$USER" -s solana-hunter-pumpportal -w
```

Press Enter and paste the API key ONLY into the local secure interactive prompt. You do not need the Phantom or FOMO seed phrase to do this. The script reads the key from Keychain at runtime. **The API key can trade with the entire PumpPortal wallet balance if exposed, even if the bot's local caps would forbid it**: this is why its wallet must be isolated and funded minimally.

## Real trading (experimental; not validated end-to-end)

Only after testing with money you can afford to lose, checking wallet IDs, accepting the service fees, and reviewing the code:

1. Edit `live.json` and set `enabled: true` (default `false`). Keep the hard caps small. Configure a reliable mainnet RPC in `SOLANA_RPC_URL` if available.
2. Confirm `live.json.mode` matches `config.json.mode`. The initial live limits include per-trade cap 0.02 SOL, daily total buys 0.05 SOL, all-time total buys 0.10 SOL and a 0.03 SOL reserve. **These are spending caps, not safety guarantees.** The existing strategy is UNPROVEN.
3. Arm the bot *on your own machine* and run it. This step gives the program permission to broadcast orders to PumpPortal's Lightning API, which executes on-chain:

```bash
mkdir -p data
printf 'I explicitly approve experimental live execution\n' > data/LIVE_ARMED_BY_USER
export HUNTER_CONFIRM_LIVE=YES_I_UNDERSTAND_THE_RISK
bash run_live_mac.sh
```

**Do not run this until you have explicitly decided to authorize live spending.** The bot is not automatically armed by installation. It requires a trading API key stored securely, public-wallet address matches, the `LIVE_ARMED_BY_USER` sentinel, matching risk modes, live enablement and a separate environment opt-in.

### EMERGENCY STOP

In another terminal, from the project folder:

```bash
source .venv/bin/activate
python -m hunter3 halt
# Then press Ctrl+C on the terminal running run_live_mac.sh.
```

Or write `data/STOP_ALL_LIVE_TRADING` in the folder. The stop blocks new order submissions—including emergency sells—until reviewed. **Stopping the program cannot reverse already broadcast transactions.** If an order had an HTTP timeout, the bot stores it as UNKNOWN and halts, rather than risking a double trade. View live order status:

```bash
python -m hunter3 status
python -m hunter3 reconcile ORDER_UUID
```

Never delete the ledger to bypass an unresolved order. First verify any corresponding on-chain transaction and actual wallet holdings. If an unknown order has no transaction signature, reconciliation requires manual blockchain review.

## Live protection summary

- Live trading is separate from paper PnL and never prints or stores your API key.
- Two independent copy-wallet buy signals and a passing rug check are mandatory for auto entry.
- Entry band $10k–$20k, configurable. Price/market-cap/liquidity must all be available.
- 3% Light / 15% Heavy **position allocation ceilings**, additionally capped by: per-buy SOL, daily SOL, lifetime SOL, reserve, liquidity impact and concurrent position limits.
- Slippage capped at 2% in the sample; priority fee capped at 0.00005 SOL. These cannot guarantee executed prices.
- Confirmed RPC transaction status and independently fetched token balances required before booking a position.
- Unresolved/unknown status triggers a full trading lock; no blind resubmission.
- Token exit logic watches stop, trailing stop, age, liquidity and risk drawdown. Missing liquidity may make any exit impossible.
- Do not use the bot trading wallet manually in other apps while the live strategy is running; external token movements can block reconciliation.
- Live results have never been tested against real money within this container. **Do not interpret unit tests as proven live profitability or proof against rug pulls.**

## Fees and hidden risks

Current PumpPortal docs specify **1% on each Lightning trade**, plus platform/bonding curve fees, Solana network fees, priority fees and any slippage. PumpPortal's Local Transaction API charges 0.5% but would require a trusted signer implementation and more audit. A sequence of small trades can easily be net-negative after fees. A metered PumpPortal wallet trade feed charges 0.01 SOL per 10,000 streamed trades and requires at least 0.02 SOL in the provider-linked wallet. Free new-token and migration feeds are separate. Your bot's first concern must be ensuring **there is a real, confirmed, liquid SELL route**, not aiming for guaranteed daily income.

## Official technical references

- `https://help.phantom.com/articles/what-if-i-lost-my-recovery-phrase-private-key-or-pin-4406393755411`
- `https://pumpportal.fun/trading-api/setup/`
- `https://pumpportal.fun/trading-api/`
- `https://pumpportal.fun/fees/`
- `https://pumpportal.fun/data-api/real-time/`
- `https://fomotrading.app/wallet/` (third-party FOMO information; verify against FOMO app documentation)

## V3 wallet dashboard (read-only)

With `accounts.json` and `live.json` configured, in a separate Terminal:

```bash
bash dashboard_live_mac.sh
```

Open `http://127.0.0.1:8766`. Displays public wallet SOL balances, unconfirmed orders, ledger positions and safety flags. It contains no buttons for sending funds or bypassing the kill switch; don't expose this dashboard to the internet.

### Manual single-token testing (not a copy signal)

The bot also has a separately labeled **operator-selected** order command. It is not an auto-copy signal. Requires an observed curve custody account or a verified `--curve-owner`, strict Rugcheck + on-chain checks, capped spend and the same live authorization. First run WITHOUT `--execute` for a preview; the command will not invent a 2-wallet consensus score:

```bash
python -m hunter3 buy TOKEN_MINT --manual-selection --curve-owner VERIFIED_CURVE_PUBKEY
# To actually submit, first explicitly authorize live mode and then append --execute.
```

An unavailable curve, failed rug check or insufficient liquidity intentionally blocks trading. Manual operation still cannot guarantee profitable or reversible trades.
