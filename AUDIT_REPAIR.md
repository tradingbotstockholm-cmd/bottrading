# Paper execution audit and repair — 2026-10-17

## Scope and evidence

Branch: `fix/paper-execution-pipeline`. Original ZIP preserved byte-for-byte.
Commit `4591670` imports its original 88 source files so the following repair
commit is reviewable as a normal source diff. There were no deployed scanner logs,
user wallet-history databases or complete production config in the checkout; the
redacted config cannot establish the exact historical cause of every zero-fill
session. The defects below are independently reproduced against the shipped code.
No real orders, wallet funding, signing, paid feeds or credentials were used.

## Prioritized defects fixed

1. **Impossible V4 default impact policy.** V2 Settings inherited 1% base modeled
   slippage while V4 capped total buy slippage at 0.75%. Every attempted V4 buy
   failed before persistence. V2 Settings and runnable examples now use 0.35%;
   existing user configs remain unchanged and incompatible settings produce
   `estimated_price_impact_too_high` with the feasible allocation. This is a paper
   model correction, not an assertion about real exchange fees.
2. **All-or-nothing target sizing.** With 0.35% base slippage, a $30 target at
   $1,754 liquidity modeled 1.205188% impact and was rejected outright. Adaptive
   V4 now caps that target to $14.032, exactly within 0.75%, while preserving the
   $2 floor, cash, exposure, confidence, position and risk checks. At liquidity
   below $250 the current minimum order remains infeasible and is rejected.
3. **RPC cooldown bypass and concealed JSON-RPC throttling.** Pricing and safety
   used raw POST calls, bypassing the scanner limiter. HTTP JSON-RPC errors were
   flattened before their 429 code could reach it. All scanner, wallet, on-chain
   risk and curve calls now share endpoint-scoped pacing/cooldowns. A method's
   429 opens a cooldown without immediate retries; Retry-After is never shortened
   to the previous 300-second cap. Non-provider backoff is bounded with jitter.
   Confirmed transaction requests are deduplicated for 30 seconds (256 entries),
   concurrency is one network request per endpoint, wait-for-slot is capped at
   eight seconds, and each HTTP request has a six-second timeout. Active blocking
   urllib requests cannot be force-cancelled; cancelling an async entry prevents
   its subsequent broker call while any already-started read finishes boundedly.
4. **Historical data could become fresh votes.** WalletPoller and wallet_poll
   previously used processing time. Signals now retain blockTime, require a
   confirmed signer-owned trade, reject missing/old/future times and preserve
   original timestamps when inserted into consensus. Websocket trade hints alone
   no longer vote. Pending wallet and scout retrievals are persisted before
   fetching; moving latest-signature windows no longer erase queued 429 retries.
   Freshness still applies when retrieval finally succeeds.
5. **Stale approval and scheduling starvation.** try_entry cached voters before
   slow quotes/security. It now rechecks current consensus before execution; the
   broker independently rechecks quote age. New-entry scheduling excludes held
   positions and rotates least-recently-attempted candidates. A successful buy
   consumes its consensus so an exit cannot reuse the identical votes to rebuy.
6. **Critical risk could be lost on a later RPC failure.** On-chain authority or
   concentration findings accumulated before an exception were discarded by the
   exception handler. They now survive, so a later 429 plus a clean Rugcheck
   summary cannot turn a known mint-authority hazard into provisional clearance. The supplied
   custody address must also match the canonical Pump PDA; a forged feed address
   cannot be used to exempt attacker-owned token concentration.
7. **Partial-exit state and initialization durability.** Base-broker take-profit
   flags were set before an exit filled. They now advance only on a persisted
   sale; pending exit fractions survive retries. V4 initial principal metadata and realized principal-recovery metadata are
   written in the same transaction as their position/fill, rather than post-fill
   saves. Injected sell-write failure verifies rollback and restart recovery.
   Broker instances sharing a DB path share a process lock. Dashboard snapshots
   use one read transaction, avoiding mixed state/fill reads during concurrent
   commits. Multiple simultaneous scanner processes on one ledger remain
   unsupported; run one scanner per ledger.
8. **Invisible rejections and misleading dashboard claims.** Added persisted
   stage/code/detail events and Execution Diagnostics, specific quote, consensus,
   security, cash, sizing, impact, exposure, duplicate, pause, position and internal
   error codes. Open positions retain approved/provisional security evidence.
   Feed connection state, event age and RPC method cooldowns supplement heartbeat.
   Costs and indicative unrealized PnL come from ledger quantities; the UI no
   longer labels all selected-ledger fills as real-market observations. Equity's
   existing 0.98 mark is explicitly an assumption, not executable liquidity.
9. **Archive packaging broke installation and four tests.** Added usable V2/V4
   examples and a plainly labeled synthetic seven-event replay fixture, replacing
   neither market data nor failed production behavior. Added one-command paper
   startup and WAL-consistent backups. Mac launchers and user configs are retained.

## Execution path and exact references

| Runtime stage | Implementation |
| --- | --- |
| Free launches / batch discovery | `hunter2/engine.py:245` |
| Confirmed swap scouting / persistent retries | `hunter2/discovery.py:91` |
| Method-aware shared RPC / deduplication | `hunter2/chain.py:138` |
| Wallet decoder and chain timestamp | `hunter2/chain.py:87` |
| Checkpointed wallet follow / signer validation | `hunter2/chain.py:207` |
| Fresh distinct-address consensus | `hunter/signals.py:19` |
| DEX / verified bonding-curve pricing | `hunter2/engine.py:373` |
| Rug and provisional evidence | `hunter2/security.py:32` |
| Quote → security → current consensus → broker | `hunter2/engine.py:391` |
| Adaptive V4 allocation / impact cap | `hunter4/paper.py:15` |
| Atomic paper cash / position / fill | `hunter2/broker.py:116` |
| Rule-based exits / pending fractions | `hunter2/broker.py:168` |
| Consistent SQLite / diagnostics snapshot | `hunter2/db.py:11` |
| Actual HTTP dashboard | `hunter4/dashboard.py:159` |

Primary regression coverage is `tests/test_execution_pipeline_repair.py` (25 new
tests) plus the existing concurrency, V4, freshness, pricing and risk suites.
The integration test decodes representative timestamped jsonParsed-format
transactions through the real scout and parser, forms distinct-signer consensus,
uses the real DEX quote parser and security policy with remote I/O fixtures,
executes a buy and stop-loss exit, checks cash/net PnL and restart persistence,
and compares the actual dashboard HTTP JSON with SQLite. These are invented,
deterministic fixtures, not captured chain recordings or observed-market fills.

## Before and after diagnostics

See `audit/broker_before_after.json` for the actual isolated synthetic comparison.
Both cases use the same $1,000 account, $1,754 liquidity, 0.35% base slippage,
two wallet labels and strength=1. The buy spot is $0.001; the exit spot is $0.0008.

| Result | Original archive | Repaired broker |
| --- | ---: | ---: |
| Planned allocation | $30 | $14.032 |
| Modeled buy slippage | 1.205188% | 0.75% |
| Paper buy | rejected | persisted |
| Buy + rule-based stop-loss sell fills | 0 | 2 |
| Ending synthetic cash | $1,000 | $996.946337 |

This deliberately losing scenario proves cost/PnL plumbing, not profitability.
The full integration test separately proves the decoder-to-dashboard path.

## Validation

- Initial archive: 236 tests, 232 passed, four errors from absent example/fixture.
- Final `bash setup_mac.sh`: 261 tests passed, zero skipped/failed/errors; dependency
  installation, existing-ledger backup and status command completed successfully.
- Python compileall, pip dependency check, shell syntax and Git whitespace checks
  passed. Validation host is Linux/Python 3.12.14/websockets 16.1.1; actual Intel
  macOS hardware was not available.
- `bash start_paper_mac.sh`: dashboard HTTP, actual scanner heartbeat, WAL-aware
  backup and clean signal-based shutdown verified; paper cash stayed $1,000,
  with zero market-observed fills. See `audit/startup_validation.json`.
- Separate actual `python -m hunter2.live_probe`: failed, exit status 1. Solana,
  DexScreener, Coinbase and Rugcheck HTTPS probes hit the cloud proxy's 403;
  the free PumpPortal websocket hit InvalidProxyStatus. See `audit/live_probe.json`.
  A known wrapped-SOL address was used for read-only connectivity, not token safety
  approval. No live scanner opportunity was fabricated to compensate.

The cloud configuration draft now includes api.mainnet-beta.solana.com,
api.mainnet.solana.com, pumpportal.fun, api.dexscreener.com, api.rugcheck.xyz,
api.coinbase.com and api.kraken.com. Saving the draft did not apply live networking.
Review/save those settings and publish the environment, then rerun the live probe
and observe eligible confirmed swaps reaching `paper/filled` on the running
scanner before claiming live paper readiness. No authenticated data is needed.

## Remaining limits

Sampling public endpoints cannot guarantee finding two fresh buyers before TTL.
Queueing cannot recover signatures never returned by a provider; unindexed oldest
pending transactions may reduce a bounded poll's throughput. Known-program,
signer-owned balance deltas are heuristics, not complete DEX instruction decoding
or proof of independent wallet ownership. No true execution quote, sellability
simulation, real provider success, mainnet fills or profitability is established.
Security reports can be incomplete; provisional entries remain explicitly limited.
Blocking HTTP work already in progress may finish after task cancellation, but
cancelled entry coroutines cannot execute the broker afterwards.

The repair commits are local and have not been pushed. Publication and fresh-task
restoration have not been tested; do not assume remote `main` contains this branch.
The original ZIP and earlier onboarding ledger/research directory were preserved.
