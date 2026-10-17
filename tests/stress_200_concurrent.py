"""Independent offline concurrency and accounting benchmark; NO mainnet orders."""
import json,time,math,sys
from concurrent.futures import ThreadPoolExecutor
from hunter.models import Quote
from hunter2.engine import Settings
from hunter2.db import Database
from hunter4.paper import CapitalRecoveryBroker

alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
def mint(i):
    digits=[]
    for _ in range(6):digits.append(alphabet[i%58]);i//=58
    return '7'*38+''.join(reversed(digits))

def run():
    cfg=Settings(max_open_positions=200,max_total_exposure_pct=.75,assumed_slippage_pct=.0035)
    cfg.validate()
    db=Database(':memory:',1000)
    broker=CapitalRecoveryBroker(db,cfg)
    quotes=[Quote(mint(i),.001,15000,1_000_000,'controlled',time.time()) for i in range(200)]
    a=time.perf_counter()
    with ThreadPoolExecutor(max_workers=32) as pool: buys=sum(pool.map(lambda q:broker.buy(q,['buyer_1','buyer_2']),quotes))
    peak=len(broker.state()['positions']);cash_mid=broker.state()['cash'];b=time.perf_counter()
    with ThreadPoolExecutor(max_workers=32) as pool:
        sold=sum(x is not None for x in pool.map(lambda q:broker.exit(q.mint,q,1,'controlled flat-price exit'),quotes))
    c=time.perf_counter()
    perf=db.performance();end=db.state()
    assert buys==200 and sold==200 and not end['positions']
    assert perf['paper_buy_fills']==200 and perf['paper_sell_fills']==200
    assert math.isclose(perf['realized_pnl_usd'],end['cash']-1000,abs_tol=.001)
    outcome={
        'mode':'OFFLINE SIMULATED PAPER ORDERS; NEVER REAL EXCHANGE THROUGHPUT',
        'parallel_requests':32,'positions_opened':buys,'positions_closed':sold,
        'peak_open_positions':peak,'cash_after_opens_usd':round(cash_mid,4),
        'final_cash_usd':round(end['cash'],4),'net_pnl_usd':round(end['cash']-1000,4),
        'buy_requests_elapsed_seconds':round(b-a,4),'sell_requests_elapsed_seconds':round(c-b,4),
        'fees_and_slippage':'0.35% baseline entry/exit slippage plus $0.05 per modeled order and market impact',
        'real_time_data':'NONE','live_order_rate':'UNTESTED',
        'interpretation':'Accounting and duplicate protections only. Flat-price trial intentionally loses to costs.'
    }
    print(json.dumps(outcome,indent=2))
    db.close()
    return outcome

if __name__=='__main__':run()
