"""PumpPortal WS adapter. Free new-token stream; account trade subscriptions can incur costs."""
from __future__ import annotations
import json, asyncio, logging
import websockets

log=logging.getLogger(__name__)

class Feed:
    def __init__(self,url: str):
        self.url=url
        self.connected=False

    async def stream(self, wallets: list[str], on_event, wallet_supplier=None, token_supplier=None):
        # A SINGLE websocket for all token and wallet events.
        # Changes to wallet set happen when the caller reconnects the feed.
        try:
            async with websockets.connect(self.url, open_timeout=15, ping_interval=20, max_size=2_000_000) as ws:
                self.connected=True
                await ws.send(json.dumps({'method':'subscribeNewToken'}))
                await ws.send(json.dumps({'method':'subscribeMigration'}))
                current=set(wallets)
                current_tokens=set()
                if current:
                    await ws.send(json.dumps({'method':'subscribeAccountTrade','keys':sorted(current)}))
                log.warning('Pump.fun launch stream connected; paid account subscriptions=%d (separate public RPC tracking unaffected)',len(current))
                while True:
                    try:
                        raw=await asyncio.wait_for(ws.recv(),timeout=10)
                        event=json.loads(raw)
                        if isinstance(event,dict):
                            await on_event(event)
                    except asyncio.TimeoutError:
                        pass
                    except (ValueError, TypeError):
                        continue
                    if wallet_supplier:
                        new=set(wallet_supplier())
                        added=sorted(new-current)
                        removed=sorted(current-new)
                        if removed:
                            await ws.send(json.dumps({'method':'unsubscribeAccountTrade','keys':removed}))
                        if added:
                            await ws.send(json.dumps({'method':'subscribeAccountTrade','keys':added}))
                        current=new
                    # Token-trade streams are METERED; the engine passes no supplier
                    # unless explicit opt-in and provider key validation both pass.
                    if token_supplier:
                        new_tokens=set(token_supplier())
                        if new_tokens-current_tokens:
                            await ws.send(json.dumps({'method':'subscribeTokenTrade','keys':sorted(new_tokens-current_tokens)}))
                        if current_tokens-new_tokens:
                            await ws.send(json.dumps({'method':'unsubscribeTokenTrade','keys':sorted(current_tokens-new_tokens)}))
                        current_tokens=new_tokens
        finally:
            self.connected=False
