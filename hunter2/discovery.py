"""Bounded, read-only discovery of *candidate* public Solana trader wallets.

Uses free new-token events already collected by the existing Feed, then samples
confirmed swaps via public RPC against a bonding-curve account. Candidates are
NOT independently verified as profitable, and no real trading is performed.
"""
from __future__ import annotations
import re
import time
import math
from dataclasses import dataclass

ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def signer_wallets(tx):
    """Only return actual transaction signers, not arbitrary token owners."""
    msg = ((tx or {}).get('transaction') or {}).get('message') or {}
    keys = msg.get('accountKeys') or []
    out = []
    for key in keys:
        if isinstance(key, dict) and key.get('signer') is True:
            candidate = key.get('pubkey')
            if isinstance(candidate, str) and ADDRESS.fullmatch(candidate):
                out.append(candidate)
    return list(dict.fromkeys(out))


@dataclass(frozen=True)
class FreshScoutSignal:
    """Authenticated by confirmed RPC signer, trade parser and blockTime.

    Separate from history/research: only first-seen, very recent swaps are
    eligible for paper-only wallet consensus, never third-party FOMO hints.
    """
    mint: str
    wallet: str
    side: str
    signature: str
    event_time: float


@dataclass(frozen=True)
class ScoutStats:
    mints_scanned: int = 0
    transactions_checked: int = 0
    confirmed_trades: int = 0
    wallets_promoted: int = 0
    rpc_errors: int = 0
    budget_exhausted: bool = False
    live_signals: tuple[FreshScoutSignal, ...] = ()


@dataclass(frozen=True)
class WalletFollowStats:
    wallets_checked: int = 0
    transactions_checked: int = 0
    confirmed_trades: int = 0
    wallets_promoted: int = 0
    rpc_errors: int = 0
    budget_exhausted: bool = False


class TraderScout:
    """Persisted wallet research based on multi-mint *observed* swap behavior.

    The RPC client and parser are injected for easy isolated testing. The scout
    never modifies wallet checkpoints or creates simulated orders.
    """
    def __init__(self, client, ledger, parse_trade, *, minimum_buys=3,
                 minimum_mints=2, limit=12, tx_budget=12,
                 rescan_seconds=180, discovery_window_seconds=7200,
                 min_token_age_seconds=75, wallet_history_limit=16,
                 wallet_tx_budget=4, wallet_cooldown_seconds=1200,
                 signal_max_age_seconds=120):
        self.client = client
        self.ledger = ledger
        self.parse_trade = parse_trade
        self.minimum_buys = minimum_buys
        self.minimum_mints = minimum_mints
        self.limit = limit
        self.tx_budget = tx_budget
        self.rescan_seconds = rescan_seconds
        self.discovery_window_seconds = discovery_window_seconds
        self.min_token_age_seconds = min_token_age_seconds
        self.wallet_history_limit = wallet_history_limit
        self.wallet_tx_budget = wallet_tx_budget
        self.wallet_cooldown_seconds = wallet_cooldown_seconds
        self.signal_max_age_seconds = signal_max_age_seconds

    def run_cycle(self, max_mints=2, now=None):
        now = time.time() if now is None else now
        mints = self.ledger.scout_targets(max_mints, now, self.rescan_seconds,
                                         self.discovery_window_seconds,
                                         self.min_token_age_seconds)
        checked = trades = scanned = errors = 0
        exhausted = False
        live = []
        for mint, curve, creator in mints:
            if checked >= self.tx_budget:
                exhausted = True
                break  # Do not mark uninspected mints as scanned.
            scanned += 1
            # The bonding-curve account tends to appear in early Pump.fun swaps.
            key = curve if isinstance(curve, str) and ADDRESS.fullmatch(curve) else mint
            try:
                rows = self.client.signatures(key, self.limit)
                if not isinstance(rows, list):
                    raise ValueError('invalid signatures response')
                completed = True
                for row in rows:
                    if not isinstance(row, dict) or row.get('err'):
                        continue
                    signature = row.get('signature')
                    if not isinstance(signature, str) or not signature:
                        continue
                    if self.ledger.scout_signature_seen(mint, signature):
                        continue
                    if checked >= self.tx_budget:
                        completed = False
                        exhausted = True
                        break
                    tx = self.client.transaction(signature)
                    if tx is None:
                        # Keep unindexed transactions eligible on the next pass.
                        completed = False
                        continue
                    checked += 1
                    # The time of an *actual confirmed on-chain trade* is
                    # mandatory. Old history, missing blockTime and future-
                    # dated responses may train research but cannot trigger
                    # a delayed paper order.
                    bt=tx.get('blockTime') if isinstance(tx,dict) else None
                    fresh=(isinstance(bt,(int,float)) and not isinstance(bt,bool)
                           and math.isfinite(bt) and -3 <= now-float(bt)
                           <= self.signal_max_age_seconds)
                    for wallet in signer_wallets(tx):
                        if wallet == creator or self.ledger.is_known_creator(wallet):
                            continue  # Never promote a known launcher as a trader.
                        for signal in self.parse_trade(tx, wallet, signature, now):
                            if signal.mint != mint or signal.side not in ('buy', 'sell'):
                                continue
                            if self.ledger.record_scout_trade(wallet, mint, signal.side, signature, now):
                                trades += 1
                                if fresh:
                                    live.append(FreshScoutSignal(mint,wallet,signal.side,signature,float(bt)))
                    self.ledger.scout_mark_signature(mint, signature)
                if completed:
                    self.ledger.scout_mark_mint(mint, now)
            except Exception as exc:
                # Fail closed; do not update last_scanned on partial RPC failures.
                errors += 1
                self.ledger.record_error('scout_rpc', f'{mint[:9]}:{type(exc).__name__}')
        return ScoutStats(scanned, checked, trades, len(self.candidates()), errors, exhausted, tuple(live))

    def run_wallet_followup(self, max_wallets=1, now=None):
        """Research recent transactions of discovered buyers, without live signals.

        A buyer's other swaps may never overlap the small launch sample. This
        bounded probe gathers independent multi-mint evidence. Wallet research
        signatures are kept separate from the copy-trader's checkpoint.
        """
        now=time.time() if now is None else now
        wallets=self.ledger.scout_wallet_targets(max_wallets,now,self.wallet_cooldown_seconds)
        wallets_done=checked=trades=errors=0
        exhausted=False
        for wallet in wallets:
            if checked>=self.wallet_tx_budget:
                exhausted=True
                break
            if self.ledger.is_known_creator(wallet):
                continue
            complete=True
            try:
                rows=self.client.signatures(wallet,self.wallet_history_limit)
                if not isinstance(rows,list):raise ValueError('invalid wallet signatures')
                for row in rows:
                    if not isinstance(row,dict) or row.get('err'):continue
                    signature=row.get('signature')
                    if not isinstance(signature,str) or not signature:continue
                    if self.ledger.scout_wallet_signature_seen(wallet,signature):continue
                    if checked>=self.wallet_tx_budget:
                        exhausted=True
                        complete=False
                        break
                    tx=self.client.transaction(signature)
                    if tx is None:
                        complete=False
                        continue
                    checked+=1
                    # getSignaturesForAddress includes nonsigner interactions.
                    if wallet in signer_wallets(tx):
                        for sg in self.parse_trade(tx,wallet,signature,now):
                            mint=getattr(sg,'mint',None)
                            side=getattr(sg,'side',None)
                            if not isinstance(mint,str) or not ADDRESS.fullmatch(mint):continue
                            if side not in ('buy','sell'):continue
                            if getattr(sg,'wallet',wallet)!=wallet:continue
                            if self.ledger.record_scout_trade(wallet,mint,side,signature,now):
                                trades+=1
                    self.ledger.scout_wallet_mark_signature(wallet,signature)
            except Exception as exc:
                errors+=1
                complete=False
                self.ledger.record_error('scout_wallet_rpc',f'{wallet[:9]}:{type(exc).__name__}')
            # The research cooldown avoids hammering failing public RPCs.
            # An incomplete scan is retried later and unseen signatures remain.
            self.ledger.scout_wallet_mark_checked(wallet,now)
            wallets_done+=1
        return WalletFollowStats(wallets_done,checked,trades,len(self.candidates()),errors,exhausted)

    def candidates(self, limit=30):
        return self.ledger.scout_candidates(self.minimum_buys,self.minimum_mints,limit)
