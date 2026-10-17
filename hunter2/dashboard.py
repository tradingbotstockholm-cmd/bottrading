"""Loopback-only dashboard. Only paper-entry pause/resume are writable."""
from __future__ import annotations
import json,html
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from .db import Database

PAGE='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Solana Hunter V2 • PAPER ONLY</title>
<style>body{font-family:system-ui;background:#0c1018;color:#f3f4f8;margin:0;padding:26px;max-width:1240px}
h1{font-size:27px;margin:0}p{color:#aeb8ce}.pill{background:#193d36;color:#8df0bb;padding:5px 11px;border-radius:12px}
.top{display:flex;align-items:center;gap:16px;margin-bottom:22px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card,section{border:1px solid #253146;border-radius:13px;background:#141d2b;padding:17px}
.big{font-size:26px;font-weight:750;margin-top:10px}.muted{color:#aeb8ce;font-size:12px}section{margin-top:20px;overflow:auto}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:10px;border-bottom:1px solid #2b394c}th{color:#9da9ba}
.warning{color:#ffcb83}code{color:#96e5ff}a{color:#d7e7ff}</style></head><body>
<div class="top"><h1>Solana Hunter V2</h1><span class="pill">SIMULATION · NO LIVE ORDERS</span></div>
<p>Local paper ledger. Values are modeled, not realized on-chain profits. Open risk checks may reject every token.</p>
<button id="pause" onclick="control(true)">Pause paper entries</button> <button id="resume" onclick="control(false)">Resume paper entries</button>
<div class="cards"><div class="card">Cash<div class="big" id="cash">—</div></div><div class="card">Marked equity<div class="big" id="equity">—</div></div><div class="card">Positions<div class="big" id="num">—</div></div><div class="card">Daily PnL (approx.)<div class="big" id="daily">—</div></div><div class="card">Risk state<div class="big" id="risk">—</div></div></div>
<section><h2>Positions</h2><table><thead><tr><th>Mint</th><th>Units</th><th>Entry</th><th>Last quote</th><th>Pending exit</th></tr></thead><tbody id="pos"></tbody></table></section>
<section><h2>Latest simulated trades</h2><table><thead><tr><th>Time UTC</th><th>Mint</th><th>Side</th><th>Nominal USD</th><th>Net PnL USD</th><th>Reason</th></tr></thead><tbody id="fills"></tbody></table></section>
<section><h2>Wallet ranking (local simulated attribution)</h2><table><thead><tr><th>Wallet</th><th>Observed closes</th><th>Win rate</th><th>Score</th></tr></thead><tbody id="wallets"></tbody></table></section>
<section><h2>Recent observations and rejects</h2><table><thead><tr><th>Time UTC</th><th>Mint</th><th>Kind</th><th>Details</th></tr></thead><tbody id="obs"></tbody></table></section>
<p class="warning">Only paper entries can be paused/resumed here. Stop network scanning with Ctrl+C in Terminal. No private-key integration exists.</p>
<script>
async function control(paused){try{let r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','X-Hunter-Control':'loopback'},body:JSON.stringify({paused})});if(!r.ok)throw new Error('Denied');await refresh();}catch(e){alert('Control failed: '+e.message)}}
const money=n=>Number.isFinite(Number(n))?'$'+Number(n).toFixed(2):'—';
const short=s=>String(s||'').slice(0,9)+'…';
const when=t=>new Date(t*1000).toISOString().slice(0,19).replace('T',' ');
const cell=(tag,value)=>{let x=document.createElement(tag);x.textContent=value;return x};
function table(id,rows){let body=document.getElementById(id);body.replaceChildren();for(let row of rows){let tr=document.createElement('tr');for(let v of row)tr.append(cell('td',v));body.append(tr)}}
async function refresh(){try{let r=await fetch('/api/state',{cache:'no-store'});let s=await r.json();let positions=Object.values(s.positions||{});let eq=s.cash+positions.reduce((a,p)=>a+p.units*p.last_price*0.98,0);
for(let [k,v] of [['cash',money(s.cash)],['equity',money(eq)],['num',positions.length],['daily',money(eq-s.day_start)],['risk',s.paused?'PAUSED':'PAPER']])document.getElementById(k).textContent=v;
table('pos',positions.map(p=>[short(p.mint),p.units.toFixed(3),money(p.entry_price),money(p.last_price),p.pending_exit||'none']));
table('fills',(s.fills||[]).map(f=>[when(f.time),short(f.mint),f.side,money(f.net_usd),money(f.pnl_usd),f.reason]));
table('wallets',(s.leaderboard||[]).map(w=>[short(w.wallet),w.trades,Math.round(w.wins/w.trades*100)+'%',w.score]));
table('obs',(s.observations||[]).map(x=>[when(x.time),short(x.mint),x.kind,x.detail]));
}catch(e){document.getElementById('risk').textContent='OFFLINE';}}
refresh();setInterval(refresh,5000);
</script></body></html>'''

def serve(db_path='data/hunter2.sqlite',port=8765,initial_cash=1000):
    db=Database(db_path,initial_cash)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            p=urlparse(self.path).path
            if p=='/':raw=PAGE.encode();typ='text/html; charset=utf-8'
            elif p=='/api/state':raw=json.dumps(db.snapshot()).encode();typ='application/json'
            elif p=='/health':raw=b'{"mode":"paper","status":"ok"}';typ='application/json'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',typ)
            self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'self' 'unsafe-inline'")
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            # Browser only: same-origin control, no CORS, custom header prevents simple CSRF.
            if urlparse(self.path).path!='/api/control' or self.headers.get('X-Hunter-Control')!='loopback':
                self.send_error(403);return
            origin=self.headers.get('Origin','')
            expected=f'http://127.0.0.1:{port}'
            if origin not in ('',expected):self.send_error(403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=200:self.send_error(400);return
                obj=json.loads(self.rfile.read(length))
                if type(obj.get('paused')) is not bool:self.send_error(400);return
            except (ValueError,KeyError,TypeError):self.send_error(400);return
            st=db.state();st['paused']=obj['paused'];db.save_state(st)
            raw=json.dumps({'paused':st['paused']}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    s=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'PAPER Dashboard: http://127.0.0.1:{port} (Ctrl+C to stop)',flush=True)
    try:s.serve_forever()
    finally:s.server_close()
