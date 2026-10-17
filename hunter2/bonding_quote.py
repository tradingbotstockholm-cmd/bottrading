"""Free, read-only Pump bonding-curve USD valuation for PAPER research.

No synthetic prices. Fail closed unless:
- the curve is the canonical program-derived account for this mint;
- Solana RPC returns the correct account owner and Anchor discriminator;
- the curve is active and has positive *real* reserves;
- a fresh public exchange SOL/USD spot quote is available.

Spot is not an executable quote. Slippage and Pump fees may be materially worse.
Supports SOL-quoted classic and Token-2022 Pump curves when the on-chain
quote_mint field confirms native SOL. Rejects token-paired and graduated pools.
"""
from __future__ import annotations
import base64,hashlib,math,struct,threading,time
from dataclasses import dataclass
from hunter.http import get_json,post_json
from hunter.models import Quote

PUMP_PROGRAM='6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
SPL_TOKEN_PROGRAM='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022_PROGRAM='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
DISCRIMINATOR=bytes([23,183,248,55,96,216,172,96])
BASE58='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
P=(1<<255)-19
D=(-121665*pow(121666,-1,P))%P
SQRT_M1=pow(2,(P-1)//4,P)
_ORACLE_LOCK=threading.Lock()
_ORACLE_CACHE=(0.0,0.0)


def _b58decode(value:str)->bytes:
    if not isinstance(value,str) or not value or len(value)>64:raise ValueError('invalid address')
    n=0
    for c in value:
        try:digit=BASE58.index(c)
        except ValueError as exc:raise ValueError('invalid base58') from exc
        n=n*58+digit
    payload=n.to_bytes((n.bit_length()+7)//8,'big') if n else b''
    return b'\x00'*(len(value)-len(value.lstrip('1')))+payload


def _b58encode(value:bytes)->str:
    n=int.from_bytes(value,'big')
    chars=''
    while n:
        n,r=divmod(n,58)
        chars=BASE58[r]+chars
    return '1'*(len(value)-len(value.lstrip(b'\x00')))+chars


def _ed25519_point(compressed:bytes)->bool:
    """Edwards-Y decompressibility test, as required by Solana PDA derivation.

    Pure Python to avoid adding native dependencies to macOS Monterey.
    """
    if len(compressed)!=32:return False
    raw=int.from_bytes(compressed,'little')
    sign=raw>>255
    y=raw&((1<<255)-1)
    if y>=P:return False
    yy=y*y%P
    den=(D*yy+1)%P
    if den==0:return False
    xx=((yy-1)*pow(den,-1,P))%P
    x=pow(xx,(P+3)//8,P)
    if x*x%P!=xx:x=(x*SQRT_M1)%P
    if x*x%P!=xx or (x==0 and sign):return False
    return True


def canonical_curve_address(mint:str)->str:
    mint_bytes=_b58decode(mint)
    program_bytes=_b58decode(PUMP_PROGRAM)
    if len(mint_bytes)!=32 or len(program_bytes)!=32:raise ValueError('invalid Solana public key')
    for bump in range(255,-1,-1):
        digest=hashlib.sha256(b'bonding-curve'+mint_bytes+bytes([bump])+program_bytes+b'ProgramDerivedAddress').digest()
        if not _ed25519_point(digest):return _b58encode(digest)
    raise ValueError('no PDA bump found')


@dataclass(frozen=True)
class Reserves:
    virtual_tokens:int
    virtual_lamports:int
    real_tokens:int
    real_lamports:int
    total_supply:int


def read_reserves(data:bytes)->Reserves|None:
    if not isinstance(data,bytes) or len(data)<49 or data[:8]!=DISCRIMINATOR:return None
    vt,vs,rt,rs,total=struct.unpack_from('<5Q',data,8)
    complete=data[48]
    if complete!=0 or not (vt>0 and vs>0 and rt>0 and rs>0 and total>0):return None
    if rt>total or vt<rt:return None
    return Reserves(vt,vs,rt,rs,total)


def price_from_reserves(mint:str, reserves:Reserves, sol_usd:float, now:float|None=None)->Quote|None:
    if not (math.isfinite(sol_usd) and 0<sol_usd<10000):return None
    # Pump classic tokens have 6 decimals; SOL has 9. The supply comes from the
    # *verified* on-chain curve, never a hardcoded billion-token assumption.
    usd_price=(reserves.virtual_lamports/1e9)/(reserves.virtual_tokens/1e6)*sol_usd
    mcap=usd_price*(reserves.total_supply/1e6)
    # Real SOL deposited is conservative, not synthetic virtual liquidity.
    available_usd=reserves.real_lamports/1e9*sol_usd
    if not all(math.isfinite(v) and v>0 for v in (usd_price,mcap,available_usd)):return None
    return Quote(mint,usd_price,mcap,available_usd,'pump_curve_onchain',time.time() if now is None else now)


def _read_sol_usd()->float:
    # Both endpoints offer public, unauthenticated spot quotes. Do not fill in
    # old or guessed prices if both fail. Cache for <=30s to protect free APIs.
    try:
        x=get_json('https://api.coinbase.com/v2/prices/SOL-USD/spot',timeout=4)
        value=x['data']
        if value.get('base')!='SOL' or value.get('currency')!='USD':raise ValueError('unexpected SOL spot pair')
        return float(value['amount'])
    except Exception:
        x=get_json('https://api.kraken.com/0/public/Ticker?pair=SOLUSD',timeout=4)
        if x.get('error'):raise ValueError('Kraken exchange error')
        results=x['result']
        if len(results)!=1:raise ValueError('ambiguous SOL spot quote')
        return float(next(iter(results.values()))['c'][0])


def fresh_sol_usd(now:float|None=None)->float:
    global _ORACLE_CACHE
    now=time.monotonic() if now is None else now
    with _ORACLE_LOCK:
        seen,price=_ORACLE_CACHE
        if seen<=now and now-seen<=30 and 0<price<10000:return price
        price=_read_sol_usd()
        if not math.isfinite(price) or not 0<price<10000:raise ValueError('invalid SOL/USD spot price')
        _ORACLE_CACHE=(time.monotonic(),price)
        return price


def fetch_curve_quote(mint:str, expected_curve:str|None, rpc_url:str)->Quote|None:
    if not expected_curve:return None
    try:
        if canonical_curve_address(mint)!=expected_curve:return None
    except ValueError:
        return None
    # Fetch both accounts at ONE confirmed slot. A newer curve has `quote_mint`
    # (32 bytes at offset 83); it MUST be the zero pubkey for SOL-denominated
    # pairs. Never misread a USDC/pump-coin quote reserve as native lamports.
    res=post_json(rpc_url,{'jsonrpc':'2.0','id':1,'method':'getMultipleAccounts',
        'params':[[expected_curve,mint],{'encoding':'base64','commitment':'confirmed'}]},timeout=5)
    result=res.get('result') or {}
    accounts=result.get('value')
    if not isinstance(accounts,list) or len(accounts)!=2:return None
    curve_account,mint_account=accounts
    if not isinstance(curve_account,dict) or curve_account.get('owner')!=PUMP_PROGRAM:return None
    if not isinstance(mint_account,dict) or mint_account.get('owner') not in (SPL_TOKEN_PROGRAM,TOKEN_2022_PROGRAM):return None

    def decoded(account):
        data=account.get('data')
        if not isinstance(data,list) or len(data)!=2 or data[1]!='base64':return None
        try:return base64.b64decode(data[0],validate=True)
        except (ValueError,TypeError):return None

    raw=decoded(curve_account)
    mint_bytes=decoded(mint_account)
    if raw is None or mint_bytes is None or len(mint_bytes)<82:return None
    if mint_bytes[44]!=6 or mint_bytes[45]!=1:return None  # decimals, initialized
    if len(raw)>=115:
        # Quote mint: 8 discriminator + 5*8 reserves + 1 complete + 32 creator
        # + 1 mayhem + 1 cashback = byte 83. Zero indicates native SOL.
        if raw[83:115]!=bytes(32):return None
    elif mint_account['owner']==TOKEN_2022_PROGRAM:
        # create_v2 uses a newer curve layout; a short curve cannot reliably
        # establish quote denomination, so reject it.
        return None
    reserves=read_reserves(raw)
    if reserves is None:return None
    if int.from_bytes(mint_bytes[36:44],'little')!=reserves.total_supply:return None
    return price_from_reserves(mint,reserves,fresh_sol_usd())
