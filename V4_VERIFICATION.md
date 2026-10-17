# V4 — Test and release verification

Date: 2026-10-17

## Verified locally

- 123 offline automated tests across V1, V2, V3 and V4 modules passed (including 33 V4 tests).
- Source compiles with installed Python interpreter.
- V4 paper capital recovery with cost-aware sizing tested in deterministic scenarios.
- Live order submissions are **still disabled by default**; only mocked execution and confirmed-settlement logic tested.
- New atomic buy/sell settlement operations prevent a partially committed SQL state from making a bogus recycled-principal credit after crash.
- Single-instance lock prevents two simultaneously launched autonomous runner processes from competing for same wallet ledger on macOS/Linux.
- The default V4 slippage tolerance is **0.75%**, not a statement about realized slippage, transaction-fee totals, stop-loss execution, or forecasting error.

## Not verified

- Actual PumpPortal Lightning account pairing, trading / sell fills, custody/security guarantee or full real-money path.
- Integration with an authenticated FOMO execution API.
- Real-world slippage, RPC price freshness, exact bid/ask spread or constant sub-1% error.
- Token protection against all rugs, frontrunning, or sudden loss.
- Predictive AI that reliably improves daily; V4 remains rule-based and does not claim an automated retrained model.
- Profits of any magnitude. No profitable strategy was established by these tests.

## Tests and reviews

```bash
python -m unittest discover -s tests -v
python -m compileall -q hunter hunter2 hunter3 hunter4
python -m hunter3 status --live-config live.example.json
```

Inspect `SECURITY_AND_CAPITAL.md`, especially fee costs and funding isolation, before any live authorization.
