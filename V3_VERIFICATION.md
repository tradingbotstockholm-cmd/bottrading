# V3 verification and limitations

## 17 October 2026

- Python code compiled.
- V1+V2+V3 tests passed locally: **90 / 90** (mocked network and simulated trading).
- No actual money, account login, on-chain transaction, or live FOMO account was used to run tests.
- `fomo` trading app integration: observation of wallet addresses only; no authenticated trading automation.
- Pump.fun/PumpSwap execution: experimental order submission via official PumpPortal Lightning API shape; no mainnet signing/settlement validation in this container.
- Funding: solely via manually sending SOL to the isolated bot wallet from Phantom, after verifying public address; bot never gets primary-wallet secret.

## Code limits

- No guarantees against malicious third-party API, key theft, chain congestion, adverse MEV, slippage, rug pulls, failed exits or poor strategy PnL.
- A Lightning API key has authority over its dedicated wallet outside application-imposed limits. Keep only a limited amount there.
- On-chain purchase/sale price and fees are not fully reconstructed from a confirmed swap. Live ledger tracks submitted SOL and token balance changes, but is not a complete realized tax/PnL ledger.
- SOL price is obtained from a DEX price aggregator; lacking or stale upstream data may cause failed entries/exits.
- Manual provider-wallet binding must be verified by the user, because no provider authenticated wallet-key verification endpoint was established.
- A FOMO order cannot be triggered through this bot; the FOMO account is a separate wallet and FOMO social signal source.
- Backtesting and daily learning remain research-grade rather than a validated profitable model.

Never describe this version as proven profitable or fully production-ready.
