# V4 quick start (macOS)

## 1. Create accounts

Use a fresh email with authenticator 2FA. Create Phantom (treasury), FOMO (its separate embedded wallet), and connect Phantom on the official pump.fun website manually. For automated Pump.fun/PumpSwap trades, a *separate* PumpPortal Lightning wallet is required. Never give this bot your Phantom/FOMO seed phrase or private key. The bot does not trade in the FOMO app; it only observes public on-chain wallet activity.

## 2. Install, without funding

```bash
unzip Solana_Hunter_V4_Principal_Recovery.zip
cd solana_hunter_v4
bash setup_mac.sh
source .venv/bin/activate
python -m hunter3 init
```

Edit `accounts.json` and `live.json` using **public addresses only**, with `enabled=false` until you have independently verified and authorized all safety settings. Edit `config.v4.json` after the first launch if needed.

## 3. Start V4 paper bot

```bash
bash run_v4_paper_mac.sh
```

In another terminal:

```bash
bash dashboard_v4_mac.sh
```

Open `http://127.0.0.1:8765`.

## 4. Inspect wallet data (optional, READ-ONLY)

```bash
python -m hunter3 balances
python -m hunter3 doctor
bash dashboard_live_mac.sh
```

Open `http://127.0.0.1:8766`. No real funds need to be added to view your public wallet addresses.

## 5. Live mode (requires separate deliberate authorization)

The code contains an experimental automatic PumpPortal Lightning execution path. It is **not** validated against real money and is **not armed** by installation. The old `V3_SETUP.md` lists the opt-in procedure; review `SECURITY_AND_CAPITAL.md` first. A one-time local opt-in would allow subsequent live trading without individual confirmations. Keep funds confined to a dedicated wallet, never your main treasury.

Immediate lockout (stops buys and sells):

```bash
python -m hunter3 halt
```

An emergency halt blocks automated sells as well as buys; **open positions require manual monitoring**.

## Capital recovery example

At $5 purchase cost, a 2x+ position valuation can trigger a partial sale of enough tokens to attempt to recover ~$5.10 plus modeled sell costs. Only verified receipt of real SOL after confirmation creates reusable principal credit. The rest stays in tokens, which can still go to zero. This is not a guaranteed no-loss strategy.

## Important economics

PumpPortal Lightning charges 1% **per transaction**. A buy+sell therefore incurs a provider fee greater than 1% total, even before Pump.fun, network, slippage and priority fees. The <1% *order slippage tolerance* cannot guarantee <1% **total cost** or prediction error.
