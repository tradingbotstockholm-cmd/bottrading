# Solana Hunter V6 — CONSOLIDATED PAPER RESEARCH RELEASE

What's included: free Pump.fun token-creation stream, optional unofficial FOMO
research hints, bounded Solana RPC research, historical wallet follow-up,
FRESH confirmed-chain two-wallet scout consensus feeding **paper** entries,
rug/liquidity filters, paper buys, principal recovery, trailing/hard stops,
SQLite accounting and a 1-second web dashboard.

**Do not claim it will trade millions of real orders or consistently profit.**
The free RPC stage samples only a small fraction of all activity. The AI model
adapter is optional shadow-only and does not participate in entry decisions.

### Start
In Terminal A:

    cd ~/Downloads/solana_hunter_v4 && bash run_v4_paper_mac.sh

In Terminal B:

    cd ~/Downloads/solana_hunter_v4 && bash dashboard_v4_mac.sh

Open http://127.0.0.1:8765 — bound to the local computer only.
The dashboard must show a recent scanner heartbeat before trusting counters.
Open paper-trade counts may correctly remain zero when no tokens pass gates.

### Test report
`STRESS_1000_REPORT.json` uses **1000 independent synthetic $1000 accounts**,
with randomized-but-seeded price paths and mocked security approval. Positive
or negative simulated PnL cannot demonstrate an investment edge, and cannot
justify live funding. This report is separate from historical user balances.

### Optional pretrained model
Read `AI_SETUP.md` to set up Chronos-Bolt Tiny (official third-party weights).
If disabled, the bot still runs normally. No model can turn this into a proven
meme-token profit engine without real out-of-sample performance data.

### Funded mode
This source project already contains an independently guarded PumpPortal
Lightning live-order gateway (`hunter3/orders.py`, `hunter3/autolive.py`). V6
neither enables it nor tests real-money mainnet orders. Funded launch requires
separate opt-in, user-controlled account setup, reconciliation and emergency
stopping. Critically: scout-research signals CANNOT trigger funded orders.
No wallet keys should ever be sent to ChatGPT or placed in a ZIP.

### Validation threshold before considering money
Collect market-triggered paper fills, examine timestamps, true tradable quotes,
latency and modeled/actual slippage, measure market drawdown and worst-case
position exit. Confirm separate funded testnet/small mainnet order reconciliation
and kill switch before any real-money activity. One synthetic test isn't enough.

### V6 concurrent paper portfolio extension

This release adds transaction-serialized paper position accounting, bounded
asynchronous price/risk checks, explicit 200-position paper-only capacity,
portfolio exposure caps, and complete fill CSV export. The default **remains
3 positions** until the operator explicitly runs:

    cd ~/Downloads/solana_hunter_v4
    bash enable_200_paper_positions_mac.sh

Stop the scanner first, and restart it afterwards. The script backs up the
config and leaves the main SQLite state untouched. It does not enable funded
transactions. Up to 250 slots are technically supported (200 via the script);
slots do not create eligible trade signals or additional cash. The risk gates,
minimum two-wallet consensus, and modeled slippage restrictions still apply.

The quote-monitor budget defaults to **40 positions per scanner cycle** and
**four concurrent provider calls**. Thus 200 positions require at least five
cycles for full coverage; even that depends on provider latency. This is NOT
high-frequency execution, nor does it mean all positions have instant stops.
RPC failures and rapidly vanishing liquidity can make exits unavailable.

The local dashboard displays full aggregate paper-trade totals and the 250 most
recent fills; click the CSV link to export complete simulated fill history.

Separate controlled benchmark `CONCURRENCY_200_REPORT.json` records 200 opening
and 200 closing paper orders with **negative** flat-price modeled PnL after
costs. `STRESS_1000_REPORT.json` remains an unrelated test of 1,000 scripted
price paths. Neither is a historical backtest or a real-money profit estimate.
