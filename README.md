# Solana Hunter — observable paper execution

Research and simulated trading only. The paper launcher never imports the live
order gateway, signs transactions, funds a wallet, or subscribes to paid feeds.
Existing experimental V3 live modules remain disabled by default and are outside
this paper workflow.

## One-command start (macOS Intel or Linux)

Install Python 3.11+ first, then run from the extracted Git source checkout:

```bash
bash start_paper_mac.sh
```

This creates `.venv` if needed, installs `requirements.txt`, runs the tests,
copies `config.v4.example.json` only when `config.v4.json` is missing, backs up
an existing V4 SQLite database using SQLite's WAL-aware backup API, and starts
both the local dashboard and V4 paper scanner. Control+C stops both processes.
Open the dashboard locally on loopback port 8765. Cloud onboarding does not expose
a localhost preview. Run only one scanner per ledger.

Existing configs and paper/research data are preserved. `config.v4.REDACTED.json`
is an audit artifact, not a usable configuration. The old Mac launcher scripts
are retained. V2 `config.example.json` uses `data/hunter2.sqlite`; V4 uses the
separate `data/hunter4_paper.sqlite`. Do not point V2 and V4 at the same ledger.

## Policy and why some small tokens still do not trade

The V4 example config sets 200 positions, $1,000 initial simulated cash, $100
minimum liquidity, no entry market-cap band, adaptive $2–$1,000 purchase targets,
0.35% base modeled slippage and a 0.75% modeled buy-slippage ceiling. Exposure,
cash, fees, independent-address consensus, quote freshness and rug checks still
apply. A position target is reduced to fit the actual impact model before entry.
A provisional security result is stored on the position and limited to the $2
target. Known critical findings always reject, even if a later RPC call fails.

The impact model is `base_slippage + 0.5 * allocation / liquidity`. With these
settings, a $2 minimum requires at least $250 observed liquidity. Tokens with
$100–$154 liquidity still fail the impact budget; $100 is a screening threshold,
not permission to bypass the $2 minimum or fabricate liquidity. At $1,754
liquidity the largest permitted allocation is $14.032. Slippage is an estimate,
not a guaranteed executable price or evidence of profitability.

If an **existing** config has `assumed_slippage_pct >= max_paper_slippage_pct`,
no buy can satisfy the policy. Review those fields explicitly; installation does
not overwrite user configs or loosen their safeguards.

## Validation and observability

```bash
bash setup_mac.sh
source .venv/bin/activate
python -m unittest discover -s tests -v
python -m compileall -q hunter hunter2 hunter3 hunter4
python -m hunter2 status --config config.v4.json
python -m hunter2.live_probe
```

The read-only live probe subscribes only to the free launch feed and checks public
providers. It returns a failing exit status on connectivity errors. Connectivity,
a launch receipt and deterministic fixtures do not prove a market-triggered fill.

The dashboard's **Execution Diagnostics** section and `execution_diagnostics` in
`/api/state` show persisted stage/outcome codes, candidate mint, time and details:
Detected → Consensus → Quoted → Security Checked → Broker Approved → Paper Filled.
Counts are stage events including retries, not unique conversion counts. The
latest 200 stage events are displayed; observations remain in SQLite. Search
historical candidates read-only, for example:

```sql
SELECT time,mint,json_extract(detail,'$.stage') AS stage,
       json_extract(detail,'$.code') AS outcome,detail
FROM observations WHERE kind='execution' AND mint=? ORDER BY id;
```

Health includes heartbeat, last feed connection state, last event age, shared RPC
requests/errors and method cooldowns. Broker rejections distinguish missing/stale
quotes, liquidity, impact, consensus, invalid sizing confidence, cash, exposure,
position limits, duplicates, pause and daily loss limits. Security and internal
errors are distinct stages. Paper fills include modeled costs; equity/unrealized
PnL use the existing indicative `units * last_price * 0.98` mark, not an executable
liquidation quote. No synthetic aggregate stress PnL is included in paper metrics.
Provisional evidence is shown separately on open positions.

## Current evidence and limitations

See [AUDIT_REPAIR.md](AUDIT_REPAIR.md) for exact defects, test evidence and live
network blockers. `audit/broker_before_after.json` is a **synthetic** comparison;
`audit/live_probe.json` is the separate actual connectivity result. Tests use
isolated temporary databases; never import them into the observed-market ledger.

Confirmed RPC swaps are heuristics based on known trading programs, signer-owned
token deltas and economic consideration, not full instruction-level execution
verification. Distinct public signer addresses do not prove distinct humans or
exclude coordinated wallets. Trader discovery measures activity, not profit.
Public RPC throttling and sparse sampling may miss valid opportunities. No live
paper fill, actual macOS hardware run, profitability or unattended mainnet
readiness is claimed by the deterministic tests.
