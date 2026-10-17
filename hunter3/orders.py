"""Isolated Lightning wallet trader: explicit opt-in, durable unknown-order lock.

PumpPortal Lightning holds its own trading-wallet API key. No Phantom/FOMO
private key is imported. API orders are **not** orders inside the FOMO app.
"""
from __future__ import annotations
import dataclasses,datetime,json,math,os,sqlite3,threading,time,urllib.parse,urllib.request,uuid
from pathlib import Path
from typing import Callable
from .accounts import ADDRESS

class LiveHalt(RuntimeError):pass

@dataclasses.dataclass
class LiveLimits:
    enabled:bool=False
    wallet_address:str=''
    rpc_url:str='https://api.mainnet-beta.solana.com'
    mode:str='light'
    max_single_buy_sol:float=0.02
    max_daily_buy_sol:float=0.05
    max_lifetime_buy_sol:float=0.1
    max_open_positions:int=2
    minimum_wallet_reserve_sol:float=0.03
    min_trade_sol:float=0.001
    max_slippage_pct:float=0.75
    max_priority_fee_sol:float=0.00005
    max_spread_pct:float=0.75
    min_market_cap_usd:float=10000.
    max_market_cap_usd:float=20000.
    min_liquidity_usd:float=3000.
    max_price_age_seconds:int=45
    max_order_usd_liquidity_fraction:float=0.005
    min_wallet_consensus:int=2
    cooldown_seconds:int=60
    require_rugcheck:bool=True
    rug_score_ceiling:int=20
    max_daily_loss_pct:float=0.03
    max_drawdown_pct:float=0.06
    # Aggregate principal may be recycled only after a confirmed settlement.
    recycle_confirmed_principal:bool=True
    max_gross_turnover_sol:float=0.5
    principal_recovery_multiple:float=2.0
    principal_recovery_fee_fraction:float=0.02
    principal_recovery_buffer_fraction:float=0.02
    min_runner_fraction:float=0.05
    hard_stop_pct:float=0.07
    active_trailing_pct:float=0.08
    db_path:str='data/live_orders.sqlite' 
    kill_switch_path:str='data/STOP_ALL_LIVE_TRADING'
    @classmethod
    def load(cls,path='live.json'):
        raw=json.loads(Path(path).read_text())
        names={x.name for x in dataclasses.fields(cls)}
        if set(raw)-names:raise ValueError('Unknown live config fields: '+str(set(raw)-names))
        value=cls(**raw);value.validate();return value
    def validate(self):
        if not isinstance(self.enabled,bool):raise ValueError('enabled must be a boolean')
        if self.mode not in ('light','heavy'):raise ValueError('Invalid mode')
        if self.wallet_address and not ADDRESS.fullmatch(self.wallet_address):raise ValueError('Invalid trading wallet address')
        for name in ('max_single_buy_sol','max_daily_buy_sol','max_lifetime_buy_sol','minimum_wallet_reserve_sol','min_trade_sol','max_slippage_pct','max_priority_fee_sol','max_spread_pct','min_market_cap_usd','max_market_cap_usd','min_liquidity_usd','max_order_usd_liquidity_fraction','max_daily_loss_pct','max_drawdown_pct','max_gross_turnover_sol','principal_recovery_multiple','principal_recovery_fee_fraction','principal_recovery_buffer_fraction','min_runner_fraction','hard_stop_pct','active_trailing_pct'):
            value=getattr(self,name)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Invalid '+name)
        if not (0<self.max_single_buy_sol<=self.max_daily_buy_sol<=self.max_lifetime_buy_sol<=5):raise ValueError('Position/day/lifetime cap mismatch')
        if not (0<self.min_trade_sol<=self.max_single_buy_sol):raise ValueError('Trade minimum invalid')
        if not (0<self.max_slippage_pct<1 and 0<=self.max_priority_fee_sol<=0.001):raise ValueError('Unsafe execution fee/slippage limits')
        if not (0<self.max_order_usd_liquidity_fraction<=.01):raise ValueError('Liquidity-impact limit invalid')
        if not (self.max_gross_turnover_sol>=self.max_lifetime_buy_sol and self.max_gross_turnover_sol<=20):raise ValueError('Total turnover cap invalid')
        if not (1.01<=self.principal_recovery_multiple<=100 and 0<=self.principal_recovery_fee_fraction<=.1 and 0<=self.principal_recovery_buffer_fraction<=.25 and 0<=self.min_runner_fraction<=.75):raise ValueError('Invalid principal recovery configuration')
        if not (0<self.hard_stop_pct<.5 and 0<self.active_trailing_pct<.5):raise ValueError('Invalid defensive exit settings')
        if type(self.recycle_confirmed_principal) is not bool:raise ValueError('Capital recycling must be boolean')
        if not (0<self.max_daily_loss_pct<=.10 and 0<self.max_drawdown_pct<=.20):raise ValueError('Drawdown cap invalid')
        if not (1<=self.max_open_positions<=4 and 2<=self.min_wallet_consensus<=8):raise ValueError('Concurrency/consensus invalid')
        if not (0<=self.rug_score_ceiling<=100):raise ValueError('Risk threshold invalid')
        if not (1<=self.max_price_age_seconds<=120):raise ValueError('Stale-price threshold invalid')
        if self.enabled and not self.wallet_address:raise ValueError('Enabled live mode needs verified wallet address')
        parsed=urllib.parse.urlparse(self.rpc_url)
        if parsed.scheme!='https' or not parsed.hostname:raise ValueError('RPC must be HTTPS')
        if not self.require_rugcheck:raise ValueError('Live trading requires rug screening')
        if self.min_market_cap_usd<=0 or self.max_market_cap_usd<self.min_market_cap_usd:raise ValueError('Invalid market-cap range')
    @property
    def allocation(self):return 0.03 if self.mode=='light' else 0.15

class LiveLedger:
    """Write-ahead order intent; uncertain trades block ALL further entry orders.

    Never auto-retry unknown orders: an HTTP timeout can occur AFTER broadcast.
    """
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(path,check_same_thread=False,timeout=15)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, created REAL NOT NULL,
            mint TEXT NOT NULL,side TEXT NOT NULL,amount REAL NOT NULL,status TEXT NOT NULL,
            tx_signature TEXT, detail TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS positions(mint TEXT PRIMARY KEY,opened REAL NOT NULL,
            entry_price_usd REAL NOT NULL,units REAL NOT NULL,high_price_usd REAL NOT NULL,
            original_sol REAL NOT NULL,token_balance_before REAL NOT NULL DEFAULT 0,
            partial2 INTEGER NOT NULL DEFAULT 0,partial5 INTEGER NOT NULL DEFAULT 0,
            exit_reason TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS flags(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        # Additive migration for existing V3 ledgers; never delete balances or orders.
        cols={r[1] for r in self.conn.execute('PRAGMA table_info(positions)')}
        for column,definition in (
            ('entry_cost_usd','REAL NOT NULL DEFAULT 0'),
            ('initial_units','REAL NOT NULL DEFAULT 0'),
            ('recovered_usd','REAL NOT NULL DEFAULT 0'),
            ('recovered_sol','REAL NOT NULL DEFAULT 0'),
            ('principal_done','INTEGER NOT NULL DEFAULT 0')):
            if column not in cols:self.conn.execute(f'ALTER TABLE positions ADD COLUMN {column} {definition}')
        self.conn.execute("""CREATE TABLE IF NOT EXISTS settlements(
            order_id TEXT PRIMARY KEY REFERENCES orders(id),mint TEXT NOT NULL,
            side TEXT NOT NULL, token_delta REAL NOT NULL, sol_delta REAL NOT NULL,
            sol_usd REAL NOT NULL, principal_credit_sol REAL NOT NULL DEFAULT 0,
            created REAL NOT NULL)""")
        self.conn.commit()
    def create(self,mint,side,amount):
        oid=str(uuid.uuid4())
        with self.lock,self.conn:self.conn.execute('INSERT INTO orders(id,created,mint,side,amount,status) VALUES(?,?,?,?,?,?)',
            (oid,time.time(),mint,side,amount,'unknown'))
        return oid
    def update(self,oid,status,signature='',detail=''):
        if status not in ('unknown','submitted','confirmed','failed'):raise ValueError('Invalid status')
        with self.lock,self.conn:
            self.conn.execute('UPDATE orders SET status=?,tx_signature=COALESCE(NULLIF(?,\'\'),tx_signature),detail=? WHERE id=?',
                (status,signature,str(detail)[:255],oid))
    def orders(self,limit=100):
        with self.lock:
            return [dict(zip(('id','created','mint','side','amount','status','signature','detail'),r)) for r in self.conn.execute('SELECT id,created,mint,side,amount,status,tx_signature,detail FROM orders ORDER BY created DESC LIMIT ?', (limit,)).fetchall()]
    def unresolved(self):
        with self.lock:return [dict(zip(('id','created','mint','side','amount','status','signature'),r)) for r in self.conn.execute(
            "SELECT id,created,mint,side,amount,status,tx_signature FROM orders WHERE status IN ('unknown','submitted') ORDER BY created").fetchall()]
    def spend(self,seconds=None):
        cutoff=time.time()-seconds if seconds else 0
        with self.lock:
            return float(self.conn.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE side='buy' AND status!='failed' AND created>=?",(cutoff,)).fetchone()[0])
    def total_reclaimed_principal_sol(self):
        with self.lock:
            return float(self.conn.execute('SELECT COALESCE(SUM(principal_credit_sol),0) FROM settlements').fetchone()[0])
    def net_committed_sol(self):
        return max(0.0,self.spend()-self.total_reclaimed_principal_sol())
    def unreconciled_confirmed(self):
        with self.lock:
            return self.conn.execute("""SELECT id FROM orders WHERE status='confirmed'
                AND id NOT IN (SELECT order_id FROM settlements)""").fetchall()
    def record_settlement(self,oid,mint,side,token_delta,sol_delta,sol_usd,credit_sol=0):
        """Record confirmed on-chain balance deltas exactly once; never infer a fill from a quote."""
        if side not in ('buy','sell') or any(not math.isfinite(float(v)) for v in (token_delta,sol_delta,sol_usd,credit_sol)):
            raise LiveHalt('Malformed settlement')
        if sol_usd<=0 or credit_sol<0 or (side=='buy' and not (token_delta>0 and sol_delta<0 and credit_sol==0)) or (side=='sell' and not (token_delta<0 and sol_delta>0 and 0<=credit_sol<=sol_delta)):
            raise LiveHalt('Settlement direction or amount invalid')
        with self.lock,self.conn:
            row=self.conn.execute('SELECT mint,side,status FROM orders WHERE id=?',(oid,)).fetchone()
            if not row or row!=(mint,side,'confirmed'):raise LiveHalt('Order not yet confirmed; cannot record settlement')
            existing=self.conn.execute('SELECT 1 FROM settlements WHERE order_id=?',(oid,)).fetchone()
            if existing:raise LiveHalt('Duplicate settlement')
            self.conn.execute("""INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?)""",
                (oid,mint,side,float(token_delta),float(sol_delta),float(sol_usd),float(credit_sol),time.time()))
    def positions(self):
        with self.lock:
            keys=('mint','opened','entry_price_usd','units','high_price_usd','original_sol','token_balance_before','partial2','partial5','exit_reason','entry_cost_usd','initial_units','recovered_usd','recovered_sol','principal_done')
            cur=self.conn.execute('SELECT '+','.join(keys)+' FROM positions')
            return {r[0]:dict(zip(keys,r)) for r in cur}
    def put_position(self,mint,price,units,sol,balance_before=0,*,entry_cost_usd=0):
        with self.lock,self.conn:self.conn.execute(
            'INSERT OR REPLACE INTO positions(mint,opened,entry_price_usd,units,high_price_usd,original_sol,token_balance_before,partial2,partial5,exit_reason,entry_cost_usd,initial_units) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (mint,time.time(),price,units,price,sol,balance_before,0,0,'',entry_cost_usd,units))
    def update_position(self,mint,units,high,partial2=None,partial5=None,reason=''):
        with self.lock,self.conn:
            p=self.positions().get(mint)
            if p is None:raise LiveHalt('Position not tracked')
            if units<=0:self.conn.execute('DELETE FROM positions WHERE mint=?',(mint,))
            else:self.conn.execute('''UPDATE positions SET units=?,high_price_usd=?,partial2=?,partial5=?,exit_reason=? WHERE mint=?''',
                (units,high,p['partial2'] if partial2 is None else int(partial2),p['partial5'] if partial5 is None else int(partial5),reason,mint))
    def update_recovery(self,mint,net_usd,net_sol,principal_done=False):
        with self.lock,self.conn:
            p=self.positions().get(mint)
            if p is None:raise LiveHalt('Recovery position does not exist')
            self.conn.execute("""UPDATE positions SET recovered_usd=recovered_usd+?,
                recovered_sol=recovered_sol+?, principal_done=CASE WHEN ? THEN 1 ELSE principal_done END
                WHERE mint=?""",(net_usd,net_sol,int(principal_done),mint))

    def finalize_filled_buy(self,oid,mint,token_gain,spent_sol,sol_usd,balance_before):
        """Write the buy settlement and position atomically after RPC confirmation."""
        vals=(token_gain,spent_sol,sol_usd,balance_before)
        if any(not math.isfinite(float(v)) for v in vals) or min(vals[:3])<=0 or balance_before<0:
            raise LiveHalt('Invalid confirmed buy balance deltas')
        with self.lock,self.conn:
            order=self.conn.execute('SELECT status,side,mint FROM orders WHERE id=?',(oid,)).fetchone()
            if order!=('confirmed','buy',mint):raise LiveHalt('Buy order not confirmed')
            if self.conn.execute('SELECT 1 FROM settlements WHERE order_id=?',(oid,)).fetchone():
                raise LiveHalt('Duplicate buy settlement')
            if self.conn.execute('SELECT 1 FROM positions WHERE mint=?',(mint,)).fetchone():
                raise LiveHalt('Existing position: cannot settle duplicate buy')
            unit_cost=spent_sol*sol_usd/token_gain
            self.conn.execute('''INSERT INTO positions
                (mint,opened,entry_price_usd,units,high_price_usd,original_sol,token_balance_before,
                partial2,partial5,exit_reason,entry_cost_usd,initial_units)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (mint,time.time(),unit_cost,token_gain,unit_cost,spent_sol,balance_before,0,0,'',spent_sol*sol_usd,token_gain))
            self.conn.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?,?)',
                (oid,mint,'buy',token_gain,-spent_sol,sol_usd,0,time.time()))

    def finalize_filled_sell(self,oid,mint,tokens_sold,proceeds_sol,sol_usd,*,reason='',high_price=None):
        """Write sale proceeds, principal credit, and remaining tokens atomically."""
        vals=(tokens_sold,proceeds_sol,sol_usd)
        if any(not math.isfinite(float(v)) for v in vals) or min(vals)<=0:
            raise LiveHalt('Invalid confirmed sale balance deltas')
        with self.lock,self.conn:
            order=self.conn.execute('SELECT status,side,mint FROM orders WHERE id=?',(oid,)).fetchone()
            if order!=('confirmed','sell',mint):raise LiveHalt('Sell order not confirmed')
            if self.conn.execute('SELECT 1 FROM settlements WHERE order_id=?',(oid,)).fetchone():
                raise LiveHalt('Duplicate sell settlement')
            p=self.positions().get(mint)
            if not p or tokens_sold>p['units']*1.01:raise LiveHalt('Sale exceeds tracked position')
            proceeds_usd=proceeds_sol*sol_usd
            principal_credit=0.0
            if reason=='principal_recovery':
                principal_credit=min(proceeds_sol,max(0,p['entry_cost_usd']-p['recovered_usd'])/sol_usd)
            self.conn.execute('INSERT INTO settlements VALUES(?,?,?,?,?,?,?,?)',
                (oid,mint,'sell',-tokens_sold,proceeds_sol,sol_usd,principal_credit,time.time()))
            remain=max(0.,p['units']-tokens_sold)
            done=(p['entry_cost_usd']>0 and p['recovered_usd']+proceeds_usd>=p['entry_cost_usd'])
            if remain<=max(1e-12,p['initial_units']*1e-9):
                self.conn.execute('DELETE FROM positions WHERE mint=?',(mint,))
            else:
                self.conn.execute('''UPDATE positions SET units=?,high_price_usd=?,recovered_usd=recovered_usd+?,
                    recovered_sol=recovered_sol+?,principal_done=?,partial2=?,partial5=?,exit_reason=? WHERE mint=?''',
                    (remain,max(p['high_price_usd'],high_price or p['high_price_usd']),
                     proceeds_usd,proceeds_sol,int(done or p['principal_done']),
                     int(reason=='principal_recovery' or p['partial2']),
                     int(reason=='5x_runner' or p['partial5']),reason,mint))
            return principal_credit
    def set_flag(self,name,value):
        with self.lock,self.conn:self.conn.execute('INSERT OR REPLACE INTO flags VALUES(?,?)',(name,str(value)))
    def flag(self,name,default=''):
        with self.lock:
            r=self.conn.execute('SELECT value FROM flags WHERE key=?',(name,)).fetchone()
            return r[0] if r else default

class LightningClient:
    """Strict PumpPortal Lightning adapter; secret never included in URLs/logs."""
    def __init__(self,api_key,send=None):
        if not isinstance(api_key,str) or not api_key.strip():raise ValueError('Missing API key')
        self.api_key=api_key
        self.send=send or self._send
    def _send(self,body):
        # URL contains key by provider requirement; never log it or print caught urllib errors.
        url='https://pumpportal.fun/api/trade?'+urllib.parse.urlencode({'api-key':self.api_key})
        payload=urllib.parse.urlencode(body).encode('utf-8')
        req=urllib.request.Request(url,data=payload,headers={'Content-Type':'application/x-www-form-urlencoded'},method='POST')
        with urllib.request.urlopen(req,timeout=14) as r:return json.loads(r.read(8192))
    def trade(self,side,mint,amount,limits:LiveLimits):
        if side not in ('buy','sell'):raise ValueError('Invalid side')
        if not isinstance(mint,str) or not ADDRESS.fullmatch(mint):raise ValueError('Invalid mint')
        if not isinstance(amount,(float,int)) or not math.isfinite(amount) or amount<=0:raise ValueError('Invalid amount')
        if side=='buy' and amount>limits.max_single_buy_sol+1e-9:raise LiveHalt('Buy exceeds per-order cap')
        if side=='sell' and not 0<amount<=100:raise LiveHalt('Sell percentage out of range')
        body={'action':side,'mint':mint,'amount':round(amount,9) if side=='buy' else str(round(amount,5))+'%',
              'denominatedInSol':'true' if side=='buy' else 'false','slippage':limits.max_slippage_pct,
              'priorityFee':limits.max_priority_fee_sol,'pool':'auto','skipPreflight':'false'}
        try:resp=self.send(body)
        except Exception:raise LiveHalt('Order submission uncertain; do not retry until reconciled') from None
        if isinstance(resp,str):
            sig=resp
        elif isinstance(resp,dict):
            if resp.get('errors') or resp.get('error'):raise LiveHalt('Provider rejected trade')
            sig=resp.get('signature') or resp.get('txSignature') or resp.get('result')
        else:sig=None
        if not isinstance(sig,str) or not ADDRESS.fullmatch(sig):
            # TX signatures are base58 with approx 87-88 characters; use a distinct regex.
            if not isinstance(sig,str) or not __import__('re').fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,100}',sig):
                raise LiveHalt('Provider returned no valid transaction signature; investigate before retry')
        return sig

class LiveGateway:
    def __init__(self,limits:LiveLimits,ledger:LiveLedger,rpc,client:LightningClient):
        limits.validate();self.limits=limits;self.ledger=ledger;self.rpc=rpc;self.client=client
    def check(self,side="buy"):
        if not self.limits.enabled:raise LiveHalt('Live execution disabled in live.json')
        if Path(self.limits.kill_switch_path).exists():raise LiveHalt('Emergency STOP file present')
        if self.ledger.flag('halt')=='1':raise LiveHalt('Order uncertainty lock: manually reconcile')
        if side=='buy' and self.ledger.flag('risk_pause')=='1':raise LiveHalt('Risk circuit paused new buys pending review')
        if self.ledger.unresolved() or self.ledger.unreconciled_confirmed():raise LiveHalt('Unsettled on-chain orders: manually reconcile before more orders')
    def balance_sol(self):
        data=self.rpc.call('getBalance',[self.limits.wallet_address,{'commitment':'confirmed'}])
        value=data['value']
        if type(value) is not int or value<0:raise LiveHalt('RPC returned invalid balance')
        return value/1e9
    def plan_buy(self,quote,consensus_count:int,risk_approved:bool,sol_usd:float,*,manual_selection:bool=False):
        self.check()
        if not risk_approved:raise LiveHalt('Risk review not approved')
        if not manual_selection and consensus_count<self.limits.min_wallet_consensus:raise LiveHalt('Insufficient independent wallet signals')
        if quote is None or quote.mint in self.ledger.positions():raise LiveHalt('Missing quote or duplicate position')
        if any(not math.isfinite(v) or v<=0 for v in (quote.price_usd,quote.market_cap_usd,quote.liquidity_usd)):raise LiveHalt('Invalid quote values')
        if any(o['mint']==quote.mint for o in self.ledger.unresolved()):raise LiveHalt('Duplicate pending order')
        now=time.time()
        if now-quote.timestamp<0 or now-quote.timestamp>self.limits.max_price_age_seconds:raise LiveHalt('Stale quote')
        if not self.limits.min_market_cap_usd<=quote.market_cap_usd<=self.limits.max_market_cap_usd:raise LiveHalt('Market cap out of range')
        if quote.liquidity_usd<self.limits.min_liquidity_usd:raise LiveHalt('Insufficient liquidity')
        if not isinstance(sol_usd,(int,float)) or not math.isfinite(sol_usd) or sol_usd<=0:raise LiveHalt('Invalid SOL/USD oracle')
        if len(self.ledger.positions())>=self.limits.max_open_positions:raise LiveHalt('Maximum concurrent positions')
        if any(o['mint']==quote.mint and o['side']=='buy' and o['created']>time.time()-self.limits.cooldown_seconds for o in self.ledger.orders(limit=300)):
            raise LiveHalt('Recent buy cooldown')
        if self.ledger.spend(86400)>=self.limits.max_daily_buy_sol:raise LiveHalt('Daily SOL buy budget depleted')
        if (self.ledger.net_committed_sol() if self.limits.recycle_confirmed_principal else self.ledger.spend())>=self.limits.max_lifetime_buy_sol:raise LiveHalt('Lifetime principal budget depleted')
        if self.ledger.spend()>=self.limits.max_gross_turnover_sol:raise LiveHalt('Lifetime gross turnover cap reached')
        balance=self.balance_sol()
        capacity=min(balance*self.limits.allocation,self.limits.max_single_buy_sol,
            self.limits.max_daily_buy_sol-self.ledger.spend(86400),
            self.limits.max_lifetime_buy_sol-(self.ledger.net_committed_sol() if self.limits.recycle_confirmed_principal else self.ledger.spend()),
            self.limits.max_gross_turnover_sol-self.ledger.spend(),
            max(0,balance-self.limits.minimum_wallet_reserve_sol-self.limits.max_priority_fee_sol),
            quote.liquidity_usd*self.limits.max_order_usd_liquidity_fraction/sol_usd)
        if capacity<self.limits.min_trade_sol:raise LiveHalt('No available capacity after reserves and fee buffers')
        return round(capacity,9)
    def submit(self,side,mint,amount):
        self.check(side)
        if side=='sell':
            if mint not in self.ledger.positions():raise LiveHalt('Refusing to sell an untracked token')
        elif side=='buy':
            if self.ledger.spend(86400)+amount>self.limits.max_daily_buy_sol+1e-10:raise LiveHalt('Daily buy cap')
            if self.ledger.spend()+amount>self.limits.max_gross_turnover_sol+1e-10:raise LiveHalt('Gross turnover cap')
            current_committed=self.ledger.net_committed_sol() if self.limits.recycle_confirmed_principal else self.ledger.spend()
            if current_committed+amount>self.limits.max_lifetime_buy_sol+1e-10:raise LiveHalt('Net principal cap')
        else:raise LiveHalt('Unknown side')
        oid=self.ledger.create(mint,side,amount)
        try:sig=self.client.trade(side,mint,amount,self.limits)
        except Exception:
            self.ledger.set_flag('halt','1')
            # Do not mark as failed; trade may have landed despite timeout.
            raise
        self.ledger.update(oid,'submitted',sig)
        return oid,sig
    def confirm(self,oid,sig):
        # Status values can be null; do not claim fills without confirmation.
        try:ans=self.rpc.call('getSignatureStatuses',[[sig],{'searchTransactionHistory':True}])
        except Exception:raise LiveHalt('Cannot determine tx status') from None
        statuses=ans.get('value') if isinstance(ans,dict) else None
        status=statuses[0] if isinstance(statuses,list) and len(statuses)==1 else None
        if not isinstance(status,dict):return False
        if status.get('err') is not None:
            self.ledger.update(oid,'failed',sig,'on-chain failure')
            return False
        if status.get('confirmationStatus') in ('confirmed','finalized'):
            self.ledger.update(oid,'confirmed',sig)
            return True
        return False

def token_balance(rpc,wallet,mint):
    """Read confirmed token balances for a mint; never mistake token display decimals."""
    data=rpc.call('getTokenAccountsByOwner',[wallet,{'mint':mint},{'encoding':'jsonParsed','commitment':'confirmed'}])
    if not isinstance(data,dict) or not isinstance(data.get('value'),list):raise LiveHalt('Token balance RPC malformed')
    total=0.0
    for account in data['value']:
        ui=account['account']['data']['parsed']['info']['tokenAmount']
        raw,dec=int(ui['amount']),int(ui['decimals'])
        if raw<0 or not 0<=dec<=18:raise LiveHalt('Malformed token balance')
        total+=raw/(10**dec)
    return total


def reconcile_order(gateway:LiveGateway,order_id:str):
    """Resolve a signed transaction through RPC without broadcasting any second order."""
    matches=[o for o in gateway.ledger.unresolved() if o['id']==order_id]
    if not matches:raise LiveHalt('Order not unresolved')
    o=matches[0]
    if not o['signature']:
        raise LiveHalt('Unknown HTTP outcome with no signature; review chain history manually')
    return gateway.confirm(order_id,o['signature'])


def price_sol_usd():
    """A missing quote fails closed; untrusted price is never synthesized."""
    from hunter.dex import fetch_quote
    from hunter2.chain import WSOL
    q=fetch_quote(WSOL)
    if q is None or q.price_usd<=0:raise LiveHalt('SOL/USD quote unavailable')
    return q.price_usd
