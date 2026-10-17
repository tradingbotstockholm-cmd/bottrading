"""Bounded read-only checks of free market providers. Never opens a broker/ledger.

This probe does NOT establish market-triggered paper fills or profitability.
"""
import asyncio
import json
from hunter.http import get_json, post_json
from hunter2.faststream import launch_event

HTTP_CHECKS={
    'solana_health':('https://api.mainnet-beta.solana.com',{'jsonrpc':'2.0','id':1,'method':'getHealth','params':[]}),
    'dex_pairs':('https://api.dexscreener.com/token-pairs/v1/solana/So11111111111111111111111111111111111111112',None),
    'sol_usd':('https://api.coinbase.com/v2/prices/SOL-USD/spot',None),
    'rugcheck_response':('https://api.rugcheck.xyz/v1/tokens/So11111111111111111111111111111111111111112/report/summary',None),
}

def http_probe(name,url,payload):
    try:
        result=post_json(url,payload,timeout=6) if payload else get_json(url,timeout=6)
        if name=='solana_health' and result.get('result')!='ok':raise ValueError('RPC health not ok')
        if name=='dex_pairs' and not isinstance(result,list):raise ValueError('not a pair list')
        if name=='sol_usd' and not 0<float(result['data']['amount'])<10000:raise ValueError('invalid spot')
        return {'check':name,'status':'responded','note':'Connectivity only; not a token approval or paper fill'}
    except Exception as exc:
        return {'check':name,'status':'failed','error_type':type(exc).__name__,
                'http_status':getattr(exc,'status',None)}

async def feed_probe():
    import websockets
    try:
        async with websockets.connect('wss://pumpportal.fun/api/data',open_timeout=6) as ws:
            await ws.send(json.dumps({'method':'subscribeNewToken'}))
            async def receive_launch():
                while True:
                    event=json.loads(await ws.recv())
                    if launch_event(event) is not None:return event['mint']
            mint=await asyncio.wait_for(receive_launch(),timeout=12)
        return {'check':'free_launch_feed','status':'observed_launch','mint':mint,'paper_fills':0}
    except Exception as exc:
        return {'check':'free_launch_feed','status':'failed','error_type':type(exc).__name__}

async def main():
    checks=[asyncio.to_thread(http_probe,name,*args) for name,args in HTTP_CHECKS.items()]
    results=await asyncio.gather(*checks,feed_probe())
    print(json.dumps({'mode':'READ_ONLY_LIVE_PROBE','results':results,
        'paper_fill_validation':'NOT ESTABLISHED; needs qualifying observed swaps and full scanner run'},indent=2))
    return 0 if all(r['status']!='failed' for r in results) else 1

if __name__=='__main__':raise SystemExit(asyncio.run(main()))
