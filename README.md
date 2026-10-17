# SOLANA HUNTER V4 — capital recovery and stricter safeguards

**NEW: [SECURITY_AND_CAPITAL.md](SECURITY_AND_CAPITAL.md)** — read the funding, slippage, and risk limitations first.

- Paper V4: `bash run_v4_paper_mac.sh` and `bash dashboard_v4_mac.sh` (separate V4 ledger, never mixes V2 trade history).
- Wallet setup: `bash setup_mac.sh`, `source .venv/bin/activate`, `python -m hunter3 init`.
- Automated live code is experimental, **disabled by default**, not mainnet-proven. Original V3 notes below are retained.
- Settlement ledger now limits recycling to **confirmed realized principal recovery**, with 0.75% slippage tolerance (not guaranteed execution), strict locks, 3%/15% allocation and lifetime turnover controls.

---

# SOLANA HUNTER V3 — isolated funding + experimental live executor

**Start here: [`V3_SETUP.md`](V3_SETUP.md).** It contains the complete new-email / Phantom / FOMO / Pump.fun / PumpPortal linking process, funding instructions, macOS Keychain setup, safeguards, and emergency stop.

## What is implemented

The original V2 paper trading system is retained. V3 adds public wallet identity configuration, on-chain confirmed SOL balance checks, PumpPortal Lightning order adapter, distinct live order ledger, manual and signal-driven experimental live orders, transaction-status and token-balance reconciliation, and an independently killable live-runner. It includes a read-only localhost wallet dashboard on port 8766.

The experimental live order route uses the **PumpPortal Lightning API and its own isolated wallet**, *not* your primary Phantom wallet, *not* the FOMO app embedded wallet, and *not* an authenticated API for the FOMO app. Its API key can trade with the wallet's funds and must be tightly protected. No Phantom or FOMO recovery phrase/private key is used or required.

**Important limitation**: We do not have live API credentials, a provisioned wallet, or permission to send live test transactions. Offline mocked tests passing does not prove actual execution, profitability, or exit reliability. No bot can guarantee no losses, 10x/100x, or daily profits. Do not fund with money you cannot lose.

## Quick installation (Mac)

```bash
unzip Solana_Hunter_V3_Wallet_Trading_Integration.zip
cd solana_hunter_v3
bash setup_mac.sh
source .venv/bin/activate
python -m hunter3 init
# Enter PUBLIC Solana addresses into accounts.json, live.json
python -m hunter3 balances
python -m hunter3 doctor
bash run_mac.sh
```

In separate Terminal windows (same folder), dashboards:

```bash
bash dashboard_mac.sh       # V2 paper: http://127.0.0.1:8765
bash dashboard_live_mac.sh  # V3 public wallets+read-only live ledger: http://127.0.0.1:8766
```

Live trading remains **OFF** by default. To explore explicit authorization and isolated wallet configuration, read `V3_SETUP.md`. Do not paste seed phrases, credentials, PINs or passwords into any files or the chat.

## Code map

- `hunter/`: V1 public data, signals, rug checks, paper model
- `hunter2/`: V2 scanning pipeline, copy-wallet tracking, state ledger, paper dashboard
- `hunter3/accounts.py`: public wallet identifiers and on-chain SOL balance checks
- `hunter3/orders.py`: hard caps, durable order status, PumpPortal Lightning API, RPC confirmation
- `hunter3/autolive.py`: opt-in live strategy using the V2 scanner/filters and V3 isolated wallet
- `hunter3/cli.py`: init/balances/doctor/status/manual orders/stop/reconcile/run-live
- `hunter3/dashboard.py`: read-only 127.0.0.1 live status dashboard
- `tests/test_v3.py`: mocked trade and failure tests, no real funds

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall -q hunter hunter2 hunter3
```

This repository is a research prototype, **not** audited production trading software. The most significant gaps are: provider-wallet/API-key binding cannot be verified from public APIs; FOMO app has no verified supported order API; provider/network outage, transaction ordering, token sellability, and mainnet cost assumptions need controlled real-world validation before relying on unattended operation.
