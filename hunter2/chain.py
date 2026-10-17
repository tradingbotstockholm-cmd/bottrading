"""Solana RPC transaction normalization. Does NOT decode opaque swap instructions.
Verifies a known DEX program, token-balance delta, and economic consideration.
Transfers and fee-only transfers are not buy/sell signals.
"""
from __future__ import annotations
import time
import threading
from dataclasses import dataclass
from typing import Any
from hunter.http import post_json, RemoteError
from hunter.fomo import B58

PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
PUMPSWAP_PROGRAM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
JUPITER_V6 = 'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'
# Conservative known-program allowlist. New protocol versions require review.
TRADING_PROGRAMS = frozenset((PUMP_PROGRAM,PUMPSWAP_PROGRAM,JUPITER_V6))
WSOL = 'So11111111111111111111111111111111111111112'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
USDT = 'Es9vMFrzaCERmJfrF4H2FYmbEoPHoozFX1Zk9F6UX'
STABLES = {USDC,USDT}

@dataclass(frozen=True)
class WalletSignal:
    wallet: str
    mint: str
    side: str
    signature: str
    slot: int
    observed_at: float
    source: str='solana-rpc'


def _pubkey(val: Any) -> str:
    return val.get('pubkey','') if isinstance(val,dict) else str(val or '')


def _programs(tx: dict) -> set[str]:
    out=set()
    msg=((tx.get('transaction') or {}).get('message') or {})
    keys=[_pubkey(k) for k in msg.get('accountKeys',[])]
    meta=tx.get('meta') or {}
    # RPC may provide inner instructions; account index normalization included.
    lists=[msg.get('instructions') or []]
    lists += [(item.get('instructions') or []) for item in meta.get('innerInstructions') or []]
    for instructions in lists:
        for inst in instructions:
            if not isinstance(inst,dict):continue
            prog=inst.get('programId')
            idx=inst.get('programIdIndex')
            if isinstance(prog,str):out.add(prog)
            elif isinstance(idx,int) and 0<=idx<len(keys):out.add(keys[idx])
    return out


def _token_deltas(tx: dict, wallet: str) -> dict[str,float]:
    meta=tx.get('meta') or {}
    def balances(key):
        d={}
        for item in meta.get(key) or []:
            if not isinstance(item,dict) or item.get('owner')!=wallet:continue
            mint=item.get('mint')
            amount=item.get('uiTokenAmount') or {}
            try:
                raw=int(amount['amount']); dec=int(amount['decimals'])
                assert 0<=dec<=18
            except (ValueError,TypeError,KeyError,AssertionError):continue
            d[mint]=d.get(mint,0.0)+raw/(10**dec)
        return d
    before=balances('preTokenBalances'); after=balances('postTokenBalances')
    return {m:after.get(m,0)-before.get(m,0) for m in set(before)|set(after)}


def _sol_change(tx: dict,wallet: str) -> float|None:
    msg=((tx.get('transaction') or {}).get('message') or {})
    keys=[_pubkey(x) for x in msg.get('accountKeys',[])]
    try:
        idx=keys.index(wallet)
        meta=tx['meta']; pre=meta['preBalances'][idx]; post=meta['postBalances'][idx]
        return (post-pre)/1e9
    except (IndexError,KeyError,TypeError,ValueError):return None


def extract_wallet_trades(tx:dict, wallet:str, signature:str, now:float|None=None) -> list[WalletSignal]:
    if not tx or tx.get('meta') is None or tx['meta'].get('err') is not None:return []
    if not TRADING_PROGRAMS.intersection(_programs(tx)):return []
    changes=_token_deltas(tx,wallet)
    native=_sol_change(tx,wallet)
    # A stake, a token transfer or insufficient proof is *not* a buy/sell.
    stable_spend=any(changes.get(m,0)<-0.001 for m in STABLES)
    stable_gain=any(changes.get(m,0)>0.001 for m in STABLES)
    wsol_spend=changes.get(WSOL,0)<-0.0005
    wsol_gain=changes.get(WSOL,0)>0.0005
    native_spend=native is not None and native < -0.001
    native_gain=native is not None and native > 0.001
    # Extra SOL account rent is possible; this is still observational evidence, not proof.
    result=[]
    for mint,delta in changes.items():
        if mint in STABLES or mint==WSOL or not isinstance(mint,str) or not B58.fullmatch(mint):continue
        if abs(delta)<1e-9:continue
        side='buy' if delta>0 else 'sell'
        if side=='buy' and not(native_spend or wsol_spend or stable_spend):continue
        if side=='sell' and not(native_gain or wsol_gain or stable_gain):continue
        result.append(WalletSignal(wallet,mint,side,signature,int(tx.get('slot') or 0),time.time() if now is None else now))
    return result

class RpcRateLimited(RemoteError):
    """The public node refused an RPC method; do not instantly retry."""


class SolanaClient:
    def __init__(self, endpoint:str):
        self.endpoint=endpoint
        self._lock=threading.Lock()
        self._last_call=0.0
        self._last_transaction=0.0
        self._cooldown_until={}
        self._rate_limit_strikes={}
        # Public shared endpoints cannot sustain the scanner's original bursts.
        # Custom RPC users retain the earlier faster defaults.
        self._public_rpc=endpoint.rstrip('/').split('?',1)[0] in (
            'https://api.mainnet-beta.solana.com','https://api.mainnet.solana.com')

    def call(self,method:str,params:list):
        # All scanner and wallet-poller calls share one RPC client. Serialize
        # network activity so discovery cannot burst through the public limit.
        with self._lock:
            now=time.monotonic()
            remaining=self._cooldown_until.get(method,0.0)-now
            if remaining>0:
                raise RpcRateLimited(f'{method}: HTTP 429 cooldown; retry in {remaining:.0f}s', status=429,
                                     retry_after=remaining)
            gap=max(0.0,0.35-(now-self._last_call))
            if method=='getTransaction' and self._public_rpc:
                # Conservative request pacing, not a guarantee of access.
                gap=max(gap,max(0.0,3.0-(now-self._last_transaction)))
            if gap:time.sleep(gap)
            self._last_call=time.monotonic()
            if method=='getTransaction':self._last_transaction=self._last_call
            last_error=None
            for attempt in range(3):
                try:
                    response=post_json(self.endpoint,{'jsonrpc':'2.0','id':1,'method':method,'params':params})
                    if not isinstance(response,dict):raise ValueError('invalid RPC response')
                    if response.get('error'):
                        err=response['error']
                        code=err.get('code') if isinstance(err,dict) else None
                        if code==429:
                            raise RemoteError('RPC 429: too many requests',status=429)
                        raise ValueError('RPC error '+str(code if code is not None else 'unknown'))
                    if 'result' not in response:raise ValueError('missing RPC result')
                    self._rate_limit_strikes[method]=0
                    return response['result']
                except Exception as exc:
                    last_error=exc
                    if isinstance(exc,RemoteError) and exc.status==429:
                        strikes=min(5,self._rate_limit_strikes.get(method,0)+1)
                        self._rate_limit_strikes[method]=strikes
                        provider_wait=exc.retry_after
                        wait=provider_wait if provider_wait is not None else 30*(2**(strikes-1))
                        wait=min(300.0,max(15.0,float(wait)))
                        self._cooldown_until[method]=time.monotonic()+wait
                        # No immediate retry: that can extend the block.
                        raise RpcRateLimited(f'{method}: HTTP 429; retry after {wait:.0f}s',
                                             status=429,retry_after=wait) from exc
                    if attempt==2:break
                    time.sleep(min(1.2,0.35*(2**attempt)))
            raise last_error

    def signatures(self,wallet:str,limit:int=12):
        return self.call('getSignaturesForAddress',[wallet,{'limit':limit,'commitment':'confirmed'}])
    def transaction(self,signature:str):
        return self.call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':0}])

class WalletPoller:
    """Per-wallet checkpoints, no historical backfill or double processing.
    First sighting establishes baseline (rather than triggering late copy trades).
    All observed signatures marked as seen before RPC details fetch to avoid replay.
    """
    def __init__(self, client, ledger,limit:int=12):self.client,self.ledger,self.limit=client,ledger,limit
    def poll(self,wallet:str,now:float|None=None) -> list[WalletSignal]:
        rows=self.client.signatures(wallet,self.limit)
        if not isinstance(rows,list):raise ValueError('invalid signatures response')
        signatures=[x['signature'] for x in rows if isinstance(x,dict) and x.get('signature') and not x.get('err')]
        # First observation is a baseline, not a signal. RPC failures are retried.
        baseline=self.ledger.has_wallet_checkpoint(wallet)
        unseen=[sig for sig in signatures if not self.ledger.has_seen(wallet,sig)]
        if not baseline:
            self.ledger.mark_seen(wallet,unseen)
            return []
        signals=[]
        for signature in reversed(unseen):
            try:
                tx=self.client.transaction(signature)
                if tx is None:continue  # not yet indexed; retry on next poll
                parsed=extract_wallet_trades(tx,wallet,signature,now)
                self.ledger.mark_seen(wallet,[signature])
                signals.extend(parsed)
            except Exception as exc:
                self.ledger.record_error('wallet_tx',f'{wallet[:7]}:{type(exc).__name__}')
        return signals
