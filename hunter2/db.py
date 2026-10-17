"""SQLite WAL ledger with atomic fills, deduplication, and snapshots.
No external API credentials or private keys ever persisted.
"""
from __future__ import annotations
import sqlite3,json,time,threading
from pathlib import Path

class Database:
    def __init__(self,path='data/hunter2.sqlite',initial_cash=1000.):
        self.path=str(path);Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(self.path,check_same_thread=False,timeout=12)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA busy_timeout=12000')
        self.conn.executescript('''
CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY AUTOINCREMENT,time REAL NOT NULL,mint TEXT,side TEXT,units REAL,price REAL,net_usd REAL,pnl_usd REAL,reason TEXT,wallets TEXT,liquidity REAL);
CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY AUTOINCREMENT,time REAL,mint TEXT,kind TEXT,detail TEXT);
CREATE TABLE IF NOT EXISTS seen(wallet TEXT NOT NULL,signature TEXT NOT NULL, PRIMARY KEY(wallet,signature));
CREATE TABLE IF NOT EXISTS wallet_checkpoints(wallet TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_mints(mint TEXT PRIMARY KEY,observed REAL,curve TEXT,source TEXT);
CREATE INDEX IF NOT EXISTS idx_fills_time ON fills(time);
CREATE INDEX IF NOT EXISTS idx_observations_time ON observations(time);
CREATE TABLE IF NOT EXISTS scout_targets(mint TEXT PRIMARY KEY, last_scanned REAL NOT NULL DEFAULT 0, creator TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS scout_signatures(mint TEXT NOT NULL, signature TEXT NOT NULL, PRIMARY KEY(mint,signature));
CREATE TABLE IF NOT EXISTS scout_trades(wallet TEXT NOT NULL, mint TEXT NOT NULL, side TEXT NOT NULL,
 signature TEXT NOT NULL, observed REAL NOT NULL, PRIMARY KEY(wallet,mint,signature));
CREATE INDEX IF NOT EXISTS idx_scout_wallet ON scout_trades(wallet,side);
-- These research checkpoints are separate from copy-trader checkpoints (`seen`).
-- Existing user data is never reset by this additive schema extension.
CREATE TABLE IF NOT EXISTS scout_wallet_checks(wallet TEXT PRIMARY KEY, last_checked REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scout_wallet_signatures(wallet TEXT NOT NULL,signature TEXT NOT NULL,
 PRIMARY KEY(wallet,signature));
-- Research hints do not count toward independent wallet consensus.
CREATE TABLE IF NOT EXISTS fomo_research_hints(event_id TEXT PRIMARY KEY,mint TEXT NOT NULL,
 side TEXT NOT NULL,event_time REAL NOT NULL,seen_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_fomo_hints_mint ON fomo_research_hints(mint,event_time);
CREATE INDEX IF NOT EXISTS idx_fomo_hints_recent ON fomo_research_hints(seen_at);
CREATE INDEX IF NOT EXISTS idx_candidate_recent ON candidate_mints(observed);
-- Disposable operational heartbeat. Does not contain secrets or order state.
CREATE TABLE IF NOT EXISTS runtime_status(
 id INTEGER PRIMARY KEY CHECK(id=1), updated_at REAL NOT NULL, payload TEXT NOT NULL
);
-- Operator pause is stored apart from the paper ledger state so a dashboard
-- request cannot clobber concurrent simulated fill or mark-to-market writes.
CREATE TABLE IF NOT EXISTS paper_controls(id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS price_samples(id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL,observed REAL NOT NULL, price REAL NOT NULL,liquidity REAL NOT NULL,market_cap REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_price_samples_mint ON price_samples(mint,id);
CREATE TABLE IF NOT EXISTS ai_shadow(id INTEGER PRIMARY KEY AUTOINCREMENT,mint TEXT NOT NULL,observed REAL NOT NULL,lower_return REAL,median_return REAL,upper_return REAL,samples INTEGER NOT NULL,model TEXT NOT NULL);



''')
        if self.conn.execute('SELECT 1 FROM state WHERE id=1').fetchone() is None:
            st=dict(cash=float(initial_cash),positions={},day='',day_start=float(initial_cash),paused=False,peak_equity=float(initial_cash))
            self.conn.execute('INSERT INTO state(id,json) VALUES(1,?)',(json.dumps(st),));self.conn.commit()
        # One-time additive migration preserves pre-existing pause preference.
        legacy=json.loads(self.conn.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
        self.conn.execute('INSERT OR IGNORE INTO paper_controls(id,paused) VALUES(1,?)',
                          (int(bool(legacy.get('paused',False))),))
        self.conn.commit()
    def is_entries_paused(self):
        with self.lock:
            row=self.conn.execute('SELECT paused FROM paper_controls WHERE id=1').fetchone()
            return bool(row[0]) if row else True  # Fail closed on missing control state.
    def set_entries_paused(self, flag):
        if type(flag) is not bool:raise ValueError('pause flag must be boolean')
        with self.lock,self.conn:
            self.conn.execute('UPDATE paper_controls SET paused=? WHERE id=1',(int(flag),))
    def state(self):
        with self.lock:return json.loads(self.conn.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
    def save_state(self,st,fill=None):
        with self.lock,self.conn:
            self.conn.execute('UPDATE state SET json=? WHERE id=1',(json.dumps(st,allow_nan=False),))
            if fill:
                self.conn.execute('''INSERT INTO fills(time,mint,side,units,price,net_usd,pnl_usd,reason,wallets,liquidity) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                                  (fill['time'],fill['mint'],fill['side'],fill['units'],fill['price'],fill['net_usd'],fill['pnl_usd'],fill['reason'],json.dumps(fill.get('wallets',[])),fill.get('liquidity',0)))
    def observe(self,mint,kind,detail):
        with self.lock,self.conn:
            self.conn.execute('INSERT INTO observations(time,mint,kind,detail) VALUES(?,?,?,?)',(time.time(),mint,kind,str(detail)[:500]))
    def record_error(self,kind,detail):self.observe('',kind,detail)
    def record_runtime(self, values, now=None):
        """Persist one scanner heartbeat without changing any trading history."""
        stamp=time.time() if now is None else float(now)
        raw=json.dumps(values,allow_nan=False,separators=(',',':'))
        with self.lock,self.conn:
            self.conn.execute('''INSERT INTO runtime_status(id,updated_at,payload) VALUES(1,?,?)
                ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,
                    payload=excluded.payload''',(stamp,raw))
    def has_wallet_checkpoint(self,wallet):
        with self.lock:return self.conn.execute('SELECT 1 FROM wallet_checkpoints WHERE wallet=?',(wallet,)).fetchone() is not None
    def has_seen(self,wallet,signature):
        with self.lock:return self.conn.execute('SELECT 1 FROM seen WHERE wallet=? AND signature=?',(wallet,signature)).fetchone() is not None
    def mark_seen(self,wallet,signatures):
        with self.lock,self.conn:
            self.conn.execute('INSERT OR IGNORE INTO wallet_checkpoints(wallet,created) VALUES(?,?)',(wallet,time.time()))
            self.conn.executemany('INSERT OR IGNORE INTO seen(wallet,signature) VALUES(?,?)',[(wallet,s) for s in signatures])
    def remember_mint(self,mint,curve='',source='pumpportal'):
        with self.lock,self.conn:
            self.conn.execute('''INSERT INTO candidate_mints VALUES(?,?,?,?) ON CONFLICT(mint) DO UPDATE SET curve=CASE WHEN excluded.curve<>'' THEN excluded.curve ELSE candidate_mints.curve END''',(mint,time.time(),curve,source))
    def remember_mints_batch(self, launches):
        """Batch launch ingestion: one short transaction, no history resets."""
        if not launches:return 0
        with self.lock,self.conn:
            self.conn.executemany('''INSERT OR IGNORE INTO candidate_mints(mint,observed,curve,source)
                 VALUES(?,?,?,?)''',[(x.mint,time.time(),x.curve,x.source) for x in launches])
            # Never overwrite a known curve or creator with an empty string.
            self.conn.executemany('''UPDATE candidate_mints SET curve=?
                 WHERE mint=? AND curve='' AND ?<>'' ''',[(x.curve,x.mint,x.curve) for x in launches if x.curve])
            self.conn.executemany('''INSERT OR IGNORE INTO scout_targets(mint,creator)
                 VALUES(?,?)''',[(x.mint,x.creator) for x in launches if x.creator])
            self.conn.executemany('''UPDATE scout_targets SET creator=? WHERE mint=? AND creator='' ''',
                                  [(x.creator,x.mint) for x in launches if x.creator])
        return len(launches)

    def paid_token_targets(self, limit=32, now=None):
        """If explicitly enabled, prefer current FOMO-buys then recent launches.

        This does NOT assert profitability or put any orders into the market.
        """
        now=time.time() if now is None else now
        with self.lock:
            return [row[0] for row in self.conn.execute('''SELECT c.mint FROM candidate_mints AS c
                WHERE c.observed>=?
                ORDER BY CASE WHEN EXISTS (
                    SELECT 1 FROM fomo_research_hints h
                    WHERE h.mint=c.mint AND h.side='buy' AND h.event_time>=?
                ) THEN 0 ELSE 1 END,c.observed DESC LIMIT ?''',
                (now-300,now-120,limit)).fetchall()]

    def remember_fomo_hint(self, hint):
        with self.lock,self.conn:
            cur=self.conn.execute('INSERT OR IGNORE INTO fomo_research_hints(event_id,mint,side,event_time,seen_at) VALUES(?,?,?,?,?)',
                                 (hint.event_id,hint.mint,hint.side,hint.event_time,time.time()))
            return bool(cur.rowcount)

    def remember_creator(self,mint,creator):
        with self.lock,self.conn:
            self.conn.execute('INSERT OR IGNORE INTO scout_targets(mint,creator) VALUES(?,?)',(mint,creator))
            self.conn.execute('UPDATE scout_targets SET creator=? WHERE mint=? AND creator=?', (creator,mint,''))

    def is_known_creator(self,wallet):
        with self.lock:
            return self.conn.execute('SELECT 1 FROM scout_targets WHERE creator=? LIMIT 1',(wallet,)).fetchone() is not None

    def scout_targets(self,limit,now,min_rescan,window,min_age=75):
        # Avoid sampling tokens just seconds after launch, when their only
        # transaction may be the creator's initial purchase. This is a
        # read-only scheduling filter and does not delete or alter launches.
        with self.lock:
            return self.conn.execute('''SELECT c.mint,c.curve,COALESCE(s.creator,'')
                FROM candidate_mints c LEFT JOIN scout_targets s ON s.mint=c.mint
                WHERE c.observed>=? AND c.observed<=?
                  AND COALESCE(s.last_scanned,0)<=?
                ORDER BY CASE WHEN c.observed>=? THEN 0 ELSE 1 END,
                CASE WHEN EXISTS (
                  SELECT 1 FROM fomo_research_hints h
                  WHERE h.mint=c.mint AND h.side='buy' AND h.event_time>=?
                ) THEN 0 ELSE 1 END,
                COALESCE(s.last_scanned,0), c.observed DESC LIMIT ?''',
                (now-window,now-min_age,now-min_rescan,now-110,now-120,limit)).fetchall()

    def scout_mark_mint(self,mint,now):
        with self.lock,self.conn:
            self.conn.execute('INSERT OR IGNORE INTO scout_targets(mint,last_scanned) VALUES(?,?)',(mint,now))
            self.conn.execute('UPDATE scout_targets SET last_scanned=? WHERE mint=?',(now,mint))

    def scout_signature_seen(self,mint,signature):
        with self.lock:
            return self.conn.execute('SELECT 1 FROM scout_signatures WHERE mint=? AND signature=?',
                                     (mint,signature)).fetchone() is not None

    def scout_mark_signature(self,mint,signature):
        with self.lock,self.conn:
            self.conn.execute('INSERT OR IGNORE INTO scout_signatures(mint,signature) VALUES(?,?)',
                              (mint,signature))

    def record_scout_trade(self,wallet,mint,side,signature,now):
        with self.lock,self.conn:
            cur=self.conn.execute('''INSERT OR IGNORE INTO scout_trades(wallet,mint,side,signature,observed)
                VALUES(?,?,?,?,?)''',(wallet,mint,side,signature,now))
            return bool(cur.rowcount)

    def scout_candidates(self,min_buys,min_mints,limit):
        """Activity-qualified candidates, NOT independently verified profitable traders."""
        with self.lock:
            return [r[0] for r in self.conn.execute('''
                SELECT t.wallet FROM scout_trades t
                WHERE t.side='buy'
                  AND NOT EXISTS(SELECT 1 FROM scout_targets x WHERE x.creator=t.wallet)
                GROUP BY t.wallet HAVING COUNT(*)>=? AND COUNT(DISTINCT t.mint)>=?
                ORDER BY COUNT(DISTINCT t.mint) DESC,COUNT(*) DESC LIMIT ?''',
                (min_buys,min_mints,limit)).fetchall()]

    def scout_wallet_targets(self,limit,now,cooldown_seconds=1200):
        """Seed follow-up from observed buyers, excluding every known creator.

        The last-check time is research-only and must not influence the wallet
        poller's `seen` table, wallet checkpoints, or simulated positions.
        """
        with self.lock:
            return [row[0] for row in self.conn.execute("""
                SELECT t.wallet FROM scout_trades AS t
                LEFT JOIN scout_wallet_checks AS wc ON wc.wallet=t.wallet
                WHERE t.side='buy'
                  AND NOT EXISTS(SELECT 1 FROM scout_targets AS c WHERE c.creator=t.wallet)
                  AND COALESCE(wc.last_checked,0) <= ?
                GROUP BY t.wallet, wc.last_checked
                ORDER BY COALESCE(wc.last_checked,0),COUNT(*) DESC,t.wallet
                LIMIT ?""", (now-cooldown_seconds,limit)).fetchall()]

    def scout_wallet_signature_seen(self,wallet,signature):
        with self.lock:
            return self.conn.execute('SELECT 1 FROM scout_wallet_signatures WHERE wallet=? AND signature=?',
                                     (wallet,signature)).fetchone() is not None

    def scout_wallet_mark_signature(self,wallet,signature):
        with self.lock,self.conn:
            self.conn.execute('INSERT OR IGNORE INTO scout_wallet_signatures(wallet,signature) VALUES(?,?)',
                              (wallet,signature))

    def scout_wallet_mark_checked(self,wallet,now):
        with self.lock,self.conn:
            self.conn.execute('INSERT INTO scout_wallet_checks(wallet,last_checked) VALUES(?,?) '
                              'ON CONFLICT(wallet) DO UPDATE SET last_checked=excluded.last_checked',(wallet,now))

    def close(self):
        with self.lock:
            if self.conn is not None:
                self.conn.close()
                self.conn=None

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def __del__(self):
        try:
            if getattr(self,'conn',None) is not None:self.close()
        except Exception:pass

    def curve_owner(self,mint):
        with self.lock:
            r=self.conn.execute('SELECT curve FROM candidate_mints WHERE mint=?',(mint,)).fetchone()
            return (r[0] or None) if r else None
    def recent_fills(self,limit=100):
        with self.lock:
            cur=self.conn.execute('SELECT time,mint,side,units,price,net_usd,pnl_usd,reason,wallets,liquidity FROM fills ORDER BY id DESC LIMIT ?', (limit,))
            return [dict(zip([c[0] for c in cur.description],row)) for row in cur.fetchall()]
    def leaderboard(self,limit=30,lookback_days=30):
        data={}
        cutoff=time.time()-lookback_days*86400
        for fill in self.recent_fills(10000):
            if fill['side']!='sell' or fill['time']<cutoff:continue
            for w in json.loads(fill['wallets'] or '[]'):
                s=data.setdefault(w,dict(wallet=w,trades=0,wins=0,pnl=0))
                s['trades']+=1;s['wins']+=int(fill['pnl_usd']>0);s['pnl']+=fill['pnl_usd']/max(1,len(json.loads(fill['wallets'] or '[]')))
        for s in data.values():
            n=s['trades'];s['score']=round((s['wins']+2)/(n+4)-0.5+max(-0.15,min(0.15,s['pnl']/1000)),4)
        return sorted(data.values(),key=lambda x:(-x['score'],-x['trades']))[:limit]
    def performance(self):
        """Real simulated ledger metrics only (never synthetic stress figures).

        A partial sell counts as a fill, not an independent winning position.
        Gross profits/losses refer to realized simulated sell fill PnL.
        """
        with self.lock:
            rows=self.conn.execute("SELECT side,COALESCE(pnl_usd,0),COALESCE(net_usd,0),COALESCE(reason,'') FROM fills").fetchall()
            rejected=self.conn.execute("SELECT kind, COUNT(*) FROM observations WHERE kind IN ('reject','rug_reject','scout_consensus','paper_buy','exit_pending','unfilled_exit') GROUP BY kind").fetchall()
        buys=sum(side=='buy' for side,_,_,_ in rows)
        sells=[float(pnl) for side,pnl,_,_ in rows if side=='sell']
        wins=[x for x in sells if x>0]
        losses=[x for x in sells if x<0]
        return {
          'paper_buy_fills':buys,'paper_sell_fills':len(sells),
          'realized_pnl_usd':round(sum(sells),4),
          'gross_profit_usd':round(sum(wins),4),
          'gross_loss_usd':round(sum(losses),4),
          'winning_sell_fills':len(wins),'losing_sell_fills':len(losses),
          'win_rate_sell_fills':round(len(wins)/len(sells),4) if sells else None,
          'profit_factor':round(sum(wins)/-sum(losses),4) if losses else None,
          'simulated_buy_spend_usd':round(-sum(net for side,_,net,_ in rows if side=='buy'),4),
          'simulated_sell_proceeds_usd':round(sum(net for side,_,net,_ in rows if side=='sell'),4),
          'decisions':dict(rejected),
        }

    def record_price_sample(self,quote,now=None):
        """Keep bounded, real observed quote tape for optional AI shadow research."""
        import math
        ts=time.time() if now is None else now
        if not (all(math.isfinite(x) and x>0 for x in (quote.price_usd,quote.liquidity_usd,quote.market_cap_usd))
                and abs(time.time()-quote.timestamp)<3600):return False
        with self.lock,self.conn:
            self.conn.execute('INSERT INTO price_samples(mint,observed,price,liquidity,market_cap) VALUES(?,?,?,?,?)',
                (quote.mint,ts,quote.price_usd,quote.liquidity_usd,quote.market_cap_usd))
            # Each mint is bounded; no unbounded tape growth across cycles.
            self.conn.execute('DELETE FROM price_samples WHERE mint=? AND id NOT IN '
                '(SELECT id FROM price_samples WHERE mint=? ORDER BY id DESC LIMIT 256)',(quote.mint,quote.mint))
        return True

    def recent_price_series(self,mint,limit=128):
        with self.lock:
            return list(reversed([r[0] for r in self.conn.execute(
                'SELECT price FROM price_samples WHERE mint=? ORDER BY id DESC LIMIT ?',
                (mint,max(1,min(limit,256)))).fetchall()]))

    def record_ai_shadow(self,mint,lower,median,upper,sample_count,model_id):
        with self.lock,self.conn:
            self.conn.execute('INSERT INTO ai_shadow(mint,observed,lower_return,median_return,upper_return,samples,model) VALUES(?,?,?,?,?,?,?)',
                (mint,time.time(),lower,median,upper,sample_count,model_id))

    def ai_shadow_recent(self,limit=10):
        with self.lock:
            return [dict(zip(('mint','time','low','median','high','samples','model'),r))
                    for r in self.conn.execute('SELECT mint,observed,lower_return,median_return,upper_return,samples,model FROM ai_shadow ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]

    def export_fills_csv(self):
        """Read-only, complete simulated-fill export for dashboard auditing."""
        import csv,io
        out=io.StringIO()
        with self.lock:
            cur=self.conn.execute("SELECT id,time,mint,side,units,price,net_usd,pnl_usd,reason,liquidity FROM fills ORDER BY id")
            writer=csv.writer(out)
            writer.writerow([column[0] for column in cur.description])
            writer.writerows(cur.fetchall())
        return out.getvalue()

    def snapshot(self):
        """Read-only status snapshot: missing heartbeats are clearly identified."""
        st=self.state();fills=self.recent_fills(250);now=time.time()
        with self.lock:
            ob=self.conn.execute('SELECT time,mint,kind,detail FROM observations ORDER BY id DESC LIMIT 70').fetchall()
            new_launches=self.conn.execute('SELECT COUNT(*) FROM candidate_mints WHERE observed>=?',(now-600,)).fetchone()[0]
            fomo_count=self.conn.execute('SELECT COUNT(*) FROM fomo_research_hints WHERE seen_at>=?',(now-600,)).fetchone()[0]
            scout_trades=self.conn.execute('SELECT COUNT(*) FROM scout_trades').fetchone()[0]
            wallets_observed=self.conn.execute('SELECT COUNT(DISTINCT wallet) FROM scout_trades').fetchone()[0]
            latest_launches=self.conn.execute('SELECT observed,mint,source FROM candidate_mints ORDER BY observed DESC LIMIT 20').fetchall()
            latest_scans=self.conn.execute("SELECT time,detail FROM observations WHERE kind='scout_scan' ORDER BY id DESC LIMIT 5").fetchall()
            latest_errors=self.conn.execute('''SELECT time,kind,detail FROM observations WHERE
                kind IN ('scout_rpc','wallet_rpc','pumpportal_feed','scout_cycle','fomo_research_feed','market_ingest_error','price_cycle','entry','ai_shadow')
                ORDER BY id DESC LIMIT 5''').fetchall()
            heartbeat=self.conn.execute('SELECT updated_at,payload FROM runtime_status WHERE id=1').fetchone()
            latest_launch_time=self.conn.execute('SELECT MAX(observed) FROM candidate_mints').fetchone()[0]
            latest_fomo_time=self.conn.execute('SELECT MAX(seen_at) FROM fomo_research_hints').fetchone()[0]
        runtime={}
        if heartbeat:
            try:runtime=json.loads(heartbeat[1])
            except (ValueError,TypeError):runtime={}
        age=round(max(0,now-heartbeat[0]),1) if heartbeat else None
        runtime['heartbeat_age_seconds']=age
        runtime['scanner_online']=age is not None and age<10
        runtime['heartbeat_updated_at']=heartbeat[0] if heartbeat else None
        min_buys=int(runtime.get('scout_min_buys',3))
        min_mints=int(runtime.get('scout_min_distinct_mints',2))
        st['paused']=self.is_entries_paused()
        st.update(scouted_wallets=self.scout_candidates(min_buys,min_mints,100),
                  recent_pump_launches=new_launches,recent_fomo_hints=fomo_count,
                  scout_trades_count=scout_trades,scout_wallets_count=wallets_observed,
                  latest_launch_at=latest_launch_time,latest_fomo_at=latest_fomo_time,
                  latest_launches=[dict(zip(('time','mint','source'),x)) for x in latest_launches],
                  latest_scans=[dict(zip(('time','detail'),x)) for x in latest_scans],
                  latest_errors=[dict(zip(('time','kind','detail'),x)) for x in latest_errors],
                  runtime=runtime,server_time=now,
                  fills=fills,performance=self.performance(),ai_shadow=self.ai_shadow_recent(),leaderboard=self.leaderboard(),
                  observations=[dict(zip(('time','mint','kind','detail'),r)) for r in ob])
        return st
