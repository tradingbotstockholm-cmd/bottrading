"""Local-only, read-only LIVE observability dashboard; paper entry pause/resume only.

Refreshes the SQLite snapshot every second, and separates ingestion events from
confirmed on-chain swaps and simulated fills. No market-data or PnL fabrication.
"""
from __future__ import annotations
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from hunter2.db import Database

PAGE='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Solana Hunter V6 • Live PAPER monitor</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;background:#0b111a;color:#f1f5fa;margin:0 auto;padding:22px;max-width:1420px}
h1{font-size:26px;margin:0}h2{font-size:17px;margin:0 0 12px}p{color:#a9b8cb;line-height:1.5}small{color:#91a5b9}
header{display:flex;gap:14px;align-items:center;flex-wrap:wrap;justify-content:space-between;margin-bottom:14px}.labels{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tag{background:#253445;border:1px solid #3c5466;color:#deebff;padding:6px 11px;border-radius:99px;font-size:12px;font-weight:700}.paper{background:#293922;color:#c4ed95}
.good{color:#75edaa}.bad{color:#ff9898}.warn{color:#ffe49b}.muted{color:#a6b6cb;font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(182px,1fr));gap:12px}.card,section{border:1px solid #28364a;border-radius:13px;background:#142033;padding:15px}
.big{font-size:26px;letter-spacing:-.5px;font-weight:750;margin:8px 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mini{color:#aebcd0;font-size:11px}
section{margin-top:15px;overflow:auto}.columns{display:grid;grid-template-columns:1fr 1fr;gap:15px}@media(max-width:850px){.columns{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:340px}td,th{text-align:left;padding:8px;border-bottom:1px solid #29394f;vertical-align:top}th{color:#afbed1}tr:last-child td{border-bottom:0}
button{background:#1d3750;color:#edf4ff;border:1px solid #355a7b;border-radius:7px;padding:9px;cursor:pointer}button:hover{background:#265173}button:disabled{opacity:.5}
#lastRefresh{font-size:11px;color:#8eacbd}footer{margin:20px 0;color:#94acc4;font-size:12px}
</style></head><body>
<header><div><h1>Solana Hunter V6</h1><div class="muted">Live research and PAPER execution telemetry</div></div>
<div class="labels"><span class="tag paper">PAPER ONLY · NO REAL ORDERS</span><span id="status" class="tag">Connecting…</span></div></header>
<p>New tokens, qualified trader signals, and simulated fills are different events. If a token isn't approved for paper trading, the simulated cash and PnL will not move.</p>
<div class="labels" style="margin-bottom:14px"><button onclick="control(true)">Pause paper entries</button><button onclick="control(false)">Resume paper entries</button><span id="lastRefresh">Loading dashboard data…</span></div>
<div class="cards">
<div class="card">Pump.fun launches / second<div class="big" id="launchRate">—</div><div class="mini">Actual incoming creation events; not trades</div></div>
<div class="card">Launches / last 10 min<div class="big" id="launch10">—</div><div class="mini">Stored unique token creations</div></div>
<div class="card">Launches received / session<div class="big" id="received">—</div><div class="mini">Raw stream receipts since scanner startup</div></div>
<div class="card">Queue / capacity<div class="big" id="queue">—</div><div class="mini">Dropped launch events: <span id="drops">—</span></div></div>
<div class="card">Recorded swap evidence<div class="big" id="scoutTrades">—</div><div class="mini">Confirmed on-chain trades in research database</div></div>
<div class="card">Discovered wallets<div class="big" id="scoutWallets">—</div><div class="mini">Distinct wallets observed, not performance-rated</div></div>
<div class="card">Qualified wallet candidates<div class="big" id="qualified">—</div><div class="mini">Activity-qualified only</div></div>
<div class="card">FOMO research hints / 10 min<div class="big" id="fomo">—</div><div class="mini">Unofficial delayed hints; not verified votes</div></div>
<div class="card">Monitored RPC wallets<div class="big" id="rpcWallets">—</div><div class="mini">Public on-chain wallet tracking</div></div>
<div class="card">Open paper positions<div class="big" id="positions">—</div><div class="mini">Requires independent wallet consensus</div></div>
<div class="card">Paper buys / sells<div class="big" id="tradeCounts">—</div><div class="mini">Simulated fills in selected ledger; verify input provenance</div></div>
<div class="card">Realized paper PnL<div class="big" id="realized">—</div><div class="mini">Net of modeled execution costs, not on-chain cash</div></div>
<div class="card">Gross profit / gross loss<div class="big" id="profitLoss">—</div><div class="mini">From sell fills only</div></div>
<div class="card">Winning sell fills<div class="big" id="winRate">—</div><div class="mini">Partial exits count separately; not trade-level win rate</div></div>
<div class="card">Rejected / rug-rejected<div class="big" id="rejections">—</div><div class="mini">Entry gate failures logged by scanner</div></div>
<div class="card">Paper cash<div class="big" id="cash">—</div><div class="mini">Simulated USD account only</div></div>
<div class="card">Paper equity<div class="big" id="equity">—</div><div class="mini">Indicative mark × 0.98; not an executable exit quote</div></div>
</div>
<section><h2>Execution Diagnostics</h2>
<p>Detected → Consensus → Quoted → Security Checked → Broker Approved → Paper Filled.
Counts are persisted stage events, including retries, not a conversion funnel of unique trades.
Market observations and simulated execution remain distinct; offline fixtures use separate test databases.</p>
<div id="executionSummary"></div>
<table><thead><tr><th>UTC</th><th>Mint</th><th>Stage</th><th>Outcome / stop reason</th><th>Details</th></tr></thead><tbody id="executionRows"></tbody></table></section>
<div class="columns">
<section><h2>Scanner health</h2><table><tbody id="health"></tbody></table></section>
<section><h2>Recent research cycles</h2><table><thead><tr><th>UTC</th><th>Activity</th></tr></thead><tbody id="scans"></tbody></table></section>
</div>
<div class="columns">
<section><h2>Newest Pump.fun tokens</h2><table><thead><tr><th>UTC</th><th>Mint</th><th>Source</th></tr></thead><tbody id="tokens"></tbody></table></section>
<section><h2>Recent connection / research errors</h2><table><thead><tr><th>UTC</th><th>Subsystem</th><th>Error</th></tr></thead><tbody id="errors"></tbody></table></section>
</div>
<div class="columns">
<section><h2>Paper positions</h2><p>Signal-strength scores are heuristic evidence indices, not calibrated win probabilities.</p><table><thead><tr><th>Mint</th><th>Units</th><th>Entry</th><th>Latest</th><th>Paper allocation</th><th>Signal strength</th><th>Security evidence</th></tr></thead><tbody id="pos"></tbody></table></section>
<section><h2>Latest 250 simulated fills</h2><p><a href="/api/fills.csv" download="solana_hunter_paper_fills.csv">Export full paper fill history (CSV)</a></p><table><thead><tr><th>UTC</th><th>Mint</th><th>Side</th><th>Value</th><th>Net PnL</th></tr></thead><tbody id="fills"></tbody></table></section>
</div>
<section><h2>1,000 synthetic stress scenarios — NOT LIVE PnL</h2>
<p>The offline simulation used scripted price paths, mocked risk clearance, and independent $1,000 paper accounts. Its summed PnL is not an investable return.</p>
<div id="syntheticReport">Loading separate simulation report…</div></section>
<section><h2>Pretrained AI shadow forecasts (research only)</h2><p>Chronos-Bolt Tiny requires local installation and at least 32 actual price observations per token. Forecasts do not approve trades.</p><table><thead><tr><th>UTC</th><th>Mint</th><th>Forecast P10</th><th>Median</th><th>Forecast P90</th></tr></thead><tbody id="aiShadow"></tbody></table></section>
<section><h2>Recent observations</h2><table><thead><tr><th>UTC</th><th>Mint</th><th>Event</th><th>Details</th></tr></thead><tbody id="obs"></tbody></table></section>
<footer>Polling the local SQLite ledger every second. A LIVE badge proves only that the scanner heartbeat is recent, not that a feed is healthy or a strategy is profitable. Pausing prevents new paper entries; it does not disconnect market feeds. Stop scanning with Control+C in Terminal.</footer>
<script>
const $=id=>document.getElementById(id);
const num=v=>Number.isFinite(Number(v))?Number(v).toLocaleString('en-US',{maximumFractionDigits:2}):'—';
const money=v=>Number.isFinite(Number(v))?'$'+Number(v).toFixed(2):'—';
const short=s=>String(s||'').slice(0,9)+(s?'…':'');
const when=t=>t?new Date(t*1000).toISOString().slice(11,19):'—';
const age=(now,ts)=>ts===null||ts===undefined?'No events yet':Math.max(0,Math.floor(now-ts))+'s ago';
function table(id,rows,empty='No data yet'){
  const body=$(id);body.replaceChildren();
  if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.textContent=empty;td.colSpan=6;tr.append(td);body.append(tr);return}
  for(const row of rows){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=String(value??'—');tr.append(td)}body.append(tr)}
}
async function control(paused){
  try{const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','X-Hunter-Control':'loopback'},body:JSON.stringify({paused})});if(!r.ok)throw Error('Denied');}
  catch(e){alert('Paper control failed: '+e.message)}
}
async function refresh(){
 try{
  const res=await fetch('/api/state',{cache:'no-store'});if(!res.ok)throw Error('HTTP '+res.status);
  const s=await res.json(),rt=s.runtime||{},perf=s.performance||{},now=s.server_time||Date.now()/1000;
  const open=Object.values(s.positions||{});
  const diagnostics=s.execution_diagnostics||{};
  $('executionSummary').textContent='Detected mints: '+(diagnostics.detected_mints??0)+' | '+
    (diagnostics.counts||[]).map(x=>x.stage+'/'+x.code+': '+x.count).join(' | ');
  table('executionRows',(diagnostics.recent||[]).map(x=>[when(x.time),short(x.mint),x.stage,x.code,JSON.stringify(x.details)]));

  const eq=Number(s.cash||0)+open.reduce((sum,p)=>sum+p.units*p.last_price*.98,0);
  const health=rt.scanner_online?'SCANNER LIVE':rt.heartbeat_updated_at?'SCANNER STALE':'SCANNER NOT STARTED';
  $('status').textContent=health;$('status').style.color=rt.scanner_online?'#85ebac':'#ffca95';
  for(const [k,v] of Object.entries({
   tradeCounts:(perf.paper_buy_fills??0)+' / '+(perf.paper_sell_fills??0),
   realized:money(perf.realized_pnl_usd),profitLoss:money(perf.gross_profit_usd)+' / '+money(perf.gross_loss_usd),
   winRate:perf.win_rate_sell_fills===null?'—':num(100*perf.win_rate_sell_fills)+'%',
   rejections:(perf.decisions?.reject??0)+' / '+(perf.decisions?.rug_reject??0),
   launchRate:num(rt.launch_events_per_second),launch10:num(s.recent_pump_launches),
   received:num(rt.market_received),queue:(rt.queue_depth??'—')+' / '+(rt.queue_capacity??'—'),drops:num(rt.market_dropped),
   scoutTrades:num(s.scout_trades_count),scoutWallets:num(s.scout_wallets_count),qualified:(s.scouted_wallets||[]).length,
   fomo:num(s.recent_fomo_hints),rpcWallets:num(rt.tracked_rpc_wallets),positions:open.length,cash:money(s.cash),equity:money(eq)
  }))$(k).textContent=v;
  table('health',[
   ['Scanner heartbeat',age(now,rt.heartbeat_updated_at)],
   ['Feed connection (last heartbeat)',rt.feed_connected===undefined?'Unknown':rt.feed_connected?'CONNECTED':'DISCONNECTED'],
   ['Last feed event age (not connection proof)',rt.feed_last_event_age_seconds??'No events'],
   ['Shared RPC requests / errors',(rt.rpc_requests??0)+' / '+(rt.rpc_errors??0)],
   ['RPC method cooldowns',JSON.stringify(rt.rpc_cooldowns||{})],
   ['Modeled execution costs',money(perf.modeled_execution_cost_usd)],
   ['Unrealized paper PnL (indicative × 0.98)',money(perf.unrealized_pnl_usd)],
   ['Pump.fun latest launch',age(now,s.latest_launch_at)],
   ['FOMO latest research hint',age(now,s.latest_fomo_at)],
   ['Paper entries',s.paused?'PAUSED':'ENABLED (subject to risk checks)'],
   ['Trader consensus threshold',(rt.min_distinct_wallets??'?')+' independent RPC wallets'],
   ['Qualified wallet requirement',(rt.scout_min_buys??3)+' buys, '+(rt.scout_min_distinct_mints??2)+' tokens'],
   ['Open position capacity',open.length+' / '+(rt.max_open_positions??3)],
   ['Position checks completed',num(rt.position_checks_completed)],
   ['Position checks per cycle',rt.max_position_checks_per_cycle??'—'],
   ['Bounded quote workers',rt.quote_parallelism??'—'],
   ['Max paper cost-basis exposure',rt.max_total_exposure_pct?num(rt.max_total_exposure_pct*100)+'%':'—'],
   ['Adaptive paper sizing',rt.adaptive_paper_sizing?'ON (heuristic, not predicted profit)':'OFF'],
   ['Configured paper purchase range',money(rt.min_paper_entry_usd)+' – '+money(rt.max_paper_entry_usd)],
   ['Paid token subscriptions',rt.paid_token_stream?'ENABLED':'OFF'],
   ['Scanner uptime',rt.scanner_started_at?age(now,rt.scanner_started_at):'Not running'],
  ]);
  table('scans',(s.latest_scans||[]).map(x=>[when(x.time),x.detail]),'Research runs periodically; no scan completed yet');
  table('tokens',(s.latest_launches||[]).map(x=>[when(x.time),short(x.mint),x.source]),'Awaiting new Pump.fun token announcements');
  table('errors',(s.latest_errors||[]).map(x=>[when(x.time),x.kind,x.detail]),'No recent recorded errors');
  table('pos',open.map(p=>[short(p.mint),num(p.units),money(p.entry_price),money(p.last_price),money(p.basis),p.signal_strength==null?'—':num(p.signal_strength*100)+' / 100',p.security_evidence?.status||'Legacy / unknown']),'No qualified paper entries yet');
  table('fills',(s.fills||[]).map(f=>[when(f.time),short(f.mint),f.side,money(f.net_usd),money(f.pnl_usd)]),'No simulated orders placed yet');
  table('aiShadow',(s.ai_shadow||[]).map(x=>[when(x.time),short(x.mint),num(x.low*100)+'%',num(x.median*100)+'%',num(x.high*100)+'%']),'AI offline or insufficient real price samples');
  table('obs',(s.observations||[]).slice(0,20).map(x=>[when(x.time),short(x.mint),x.kind,x.detail]),'Awaiting scanner observations');
  $('lastRefresh').textContent='Dashboard checked '+new Date().toLocaleTimeString()+' · updates every 1s';
 }catch(e){$('status').textContent='DASHBOARD OFFLINE';$('status').style.color='#ff9898';$('lastRefresh').textContent='Cannot fetch local API: '+e.message}
 finally{setTimeout(refresh,1000)}
}
async function synthetic(){
 try{
  const res=await fetch('/api/stress',{cache:'no-store'});
  if(!res.ok)throw Error('not installed');
  const r=await res.json();
  $('syntheticReport').textContent=`${r.scenarios} scripted scenarios · ${r.winning_scenarios} wins · ${r.losing_scenarios} losses · gross wins ${money(r.gross_wins_usd)} · gross losses ${money(r.gross_losses_usd)} · summed isolated trial PnL ${money(r.net_pnl_usd_sum_normalized_trials)} · NO HISTORICAL TRADES OR MODEL PERFORMANCE`;
 }catch(e){$('syntheticReport').textContent='No standalone synthetic report found';}
}
synthetic();refresh();
</script></body></html>'''

def serve(db_path='data/hunter2.sqlite',port=8765,initial_cash=1000):
    db=Database(db_path,initial_cash)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/':raw=PAGE.encode();kind='text/html; charset=utf-8'
            elif path=='/api/state':raw=json.dumps(db.snapshot(),allow_nan=False).encode();kind='application/json'
            elif path=='/health':raw=json.dumps({'mode':'paper','status':'ok','scanner_online':db.snapshot()['runtime']['scanner_online']}).encode();kind='application/json'
            elif path=='/api/fills.csv':raw=db.export_fills_csv().encode();kind='text/csv; charset=utf-8'
            elif path=='/api/stress':
                source=Path(__file__).resolve().parents[1]/'STRESS_1000_REPORT.json'
                if not source.is_file():self.send_error(404);return
                raw=source.read_bytes();kind='application/json'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind)
            self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'self' 'unsafe-inline'")
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            if urlparse(self.path).path!='/api/control' or self.headers.get('X-Hunter-Control')!='loopback':
                self.send_error(403);return
            expected=f'http://127.0.0.1:{port}'
            if self.headers.get('Origin','') not in ('',expected):self.send_error(403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=200:self.send_error(400);return
                data=json.loads(self.rfile.read(length))
                if type(data.get('paused')) is not bool:self.send_error(400);return
            except (ValueError,KeyError,TypeError):self.send_error(400);return
            db.set_entries_paused(data['paused'])
            raw=json.dumps({'paused':db.is_entries_paused()}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'PAPER LIVE Dashboard: http://127.0.0.1:{port} (Ctrl+C to stop)',flush=True)
    try:server.serve_forever()
    finally:server.server_close();db.close()
