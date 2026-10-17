"""Loopback-only, read-only V3 live status and public wallet balances."""
from __future__ import annotations
import html,json,os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from hunter2.chain import SolanaClient
from .accounts import WalletAccounts,balances
from .orders import LiveLimits,LiveLedger

def serve(port=8766,config='live.json',accounts_file='accounts.json'):
    cfg=LiveLimits.load(config)
    ledger=LiveLedger(cfg.db_path)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/health':data={'status':'ok','live_enabled':cfg.enabled}
            elif path in ('/','/api/state'):
                try: wallets=balances(WalletAccounts.load(accounts_file),SolanaClient(os.getenv('SOLANA_RPC_URL',cfg.rpc_url)))
                except Exception: wallets=[{'role':'wallets','error':'account configuration unavailable'}]
                data={'mode':'EXPERIMENTAL LIVE ENABLED' if cfg.enabled else 'DISARMED',
                      'accounts':wallets,'positions':ledger.positions(),'orders':ledger.orders(30),
                      'unknown_orders':ledger.unresolved(), 'risk_pause':ledger.flag('risk_pause'),
                      'halt':ledger.flag('halt'), 'daily_spend_sol':ledger.spend(86400),
                      'lifetime_spend_sol':ledger.spend(),
                      'reclaimed_principal_sol':ledger.total_reclaimed_principal_sol(),
                      'net_committed_principal_sol':ledger.net_committed_sol(),
                      'unsettled_confirmed':ledger.unreconciled_confirmed()}
            else:self.send_error(404);return
            if path=='/':
                escaped=html.escape(json.dumps(data,indent=2))
                body=(f'<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="15">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Solana Hunter V4</title>'
                    '<style>body{background:#0b1020;color:#e9f1fd;font:16px system-ui;max-width:1050px;padding:30px;margin:auto}'
                    'pre{white-space:pre-wrap;background:#171f32;border:1px solid #37425d;border-radius:12px;padding:15px}'
                    'b{color:#ffcb83}small{color:#9baaca}</style></head><body>'
                    '<h1>Solana Hunter V4 — Wallet status</h1><b>Read-only view. Live orders require separate explicit authorization.</b>'
                    '<p><small>Only local public addresses and ledger values shown; refresh every 15 seconds.</small></p>'
                    f'<pre>{escaped}</pre></body></html>').encode()
                mime='text/html; charset=utf-8'
            else:body=json.dumps(data).encode();mime='application/json'
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'")
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'V3 READ-ONLY wallet dashboard: http://127.0.0.1:{port}',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
