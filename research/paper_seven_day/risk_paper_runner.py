#!/usr/bin/env python3
"""risk_paper_runner.py — каркас ChatGPT (job_8b0c6aab / job_ce33523a / job_3c800e68).

NO-ORDER paper plumbing runner: SOLUSDT/BTCUSDT 60m Donchian.
- только PUBLIC GET к Bybit (без API key, без POST/order);
- signal на close завершённого бара, entry = open следующего (next-bar, БЕЗ lookahead);
- ATR stop, timeout, противосигнал;
- SQLite-ledger: processed-idempotency, bars, signals, virtual_orders, positions,
  trades, funding, equity (две кривые: 1% и 0.5% risk/trade), errors;
- crash-safe restart: открытые позиции восстанавливаются, processed не дублирует;
- actual funding ledger по funding history;
- baseline cost 5.5 bps fee + 1 bps slip НА СТОРОНУ.
"""
import argparse, json, sqlite3, time, urllib.parse, urllib.request, traceback
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

BASE="https://api.bybit.com"
CFG={
    "SOLUSDT":{"N":20,"timeout":48,"stop_k":1.5},
    "BTCUSDT":{"N":20,"timeout":96,"stop_k":1.5},
}
HOUR=3600_000
FEE_SIDE_BPS=5.5
MODEL_SLIP_SIDE_BPS=1.0       # baseline backtest: 2 bps total
COST_SIDE=(FEE_SIDE_BPS+MODEL_SLIP_SIDE_BPS)/10000.0

def iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

_MONO = time.monotonic
API_STATS = {
    "http_429": 0,
    "http_403": 0,
    "ret_10006": 0,
    "http_5xx": 0,
    "network_errors": 0,
}
_SERVER_SYNC = {"server_ms": None, "mono": None}
_CACHE_FUNDING_LAST = {}   # sym -> monotonic timestamp

def api(path, **params):
    # Deliberate no-order guarantee: this runner contains GET only.
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        BASE + path + ("?" + q if q else ""),
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            j = json.loads(raw)
    except HTTPError as e:
        if e.code == 429:
            API_STATS["http_429"] += 1
        elif e.code == 403:
            API_STATS["http_403"] += 1
        elif 500 <= e.code < 600:
            API_STATS["http_5xx"] += 1
        raise
    except (URLError, TimeoutError):
        API_STATS["network_errors"] += 1
        raise

    if int(j.get("retCode", 0)) == 10006:
        API_STATS["ret_10006"] += 1
        raise RuntimeError(f"Bybit rate limit retCode=10006: {j}")
    if j.get("retCode") != 0:
        raise RuntimeError(j)
    return j

_CACHE = {"book10": {}}

def server_ms():
    """Серверное время: sync /v5/market/time раз в 60с, дальше экстраполяция
    через monotonic (устойчиво к NTP/ручной коррекции wall-clock)."""
    now_m = _MONO()
    if (_SERVER_SYNC["server_ms"] is None or
        now_m - _SERVER_SYNC["mono"] >= 60.0):
        j = api("/v5/market/time")
        _SERVER_SYNC["server_ms"] = int(j["time"])
        _SERVER_SYNC["mono"] = now_m
        return _SERVER_SYNC["server_ms"]
    return _SERVER_SYNC["server_ms"] + int(
        (now_m - _SERVER_SYNC["mono"]) * 1000.0
    )

def klines(sym, limit=100):
    xs=api("/v5/market/kline",category="linear",symbol=sym,
           interval="60",limit=limit)["result"]["list"]
    out=[]
    for x in xs:
        out.append({"ts":int(x[0]),"o":float(x[1]),"h":float(x[2]),
                    "l":float(x[3]),"c":float(x[4]),"v":float(x[5]),
                    "turn":float(x[6])})
    return sorted(out,key=lambda z:z["ts"])

def book1(sym):
    r=api("/v5/market/orderbook",category="linear",symbol=sym,
          limit=1)["result"]
    return float(r["b"][0][0]),float(r["a"][0][0]),int(r.get("ts") or 0),float(r["b"][0][1]),float(r["a"][0][1])

def book10(sym):
    """Полная книга: bids/asks по 10 уровней [(px,qty),...] + ts. Кэш 2с (ключ=symbol)."""
    now = time.time()
    cached = _CACHE["book10"].get(sym)
    if cached and now - cached[0] < 2:
        return cached[1]
    r=api("/v5/market/orderbook",category="linear",symbol=sym,
          limit=10)["result"]
    bids=[(float(x[0]),float(x[1])) for x in r["b"]]
    asks=[(float(x[0]),float(x[1])) for x in r["a"]]
    out=(bids,asks,int(r.get("ts") or 0))
    _CACHE["book10"][sym] = (now, out)
    return out

def executable_vwap(side, levels, notional_usd):
    """VWAP-исполнение notional_usd по книге.
    side=1 (long entry): покупаем по asks; side=-1 (short entry): продаём по bids.
    Возвращает (ok_full, vwap_px, filled_notional_usd)."""
    spent_usd = 0.0      # USD отданных (buy) / полученных (sell)
    qty_filled = 0.0     # базового актива
    for px, qty in levels:
        if px <= 0 or qty <= 0:
            continue
        lv_usd = px * qty
        if spent_usd + lv_usd >= notional_usd:
            need_usd = notional_usd - spent_usd
            need_qty = need_usd / px
            spent_usd += need_usd
            qty_filled += need_qty
            return True, (spent_usd / qty_filled if qty_filled > 0 else 0.0), spent_usd
        spent_usd += lv_usd
        qty_filled += qty
    if qty_filled <= 0:
        return False, 0.0, 0.0
    return False, (spent_usd / qty_filled), spent_usd

def funding_rows(sym,start_ms,end_ms):
    xs=api("/v5/market/funding/history",category="linear",symbol=sym,
           startTime=start_ms,endTime=end_ms,limit=50)["result"]["list"]
    return sorted(
        [{"ts":int(x["fundingRateTimestamp"]),"rate":float(x["fundingRate"])}
         for x in xs], key=lambda z:z["ts"])

def atr(rows,n=14):
    if len(rows)<n+1: return None
    tr=[]
    for i in range(1,len(rows)):
        p=rows[i-1]["c"]; x=rows[i]
        tr.append(max(x["h"]-x["l"],abs(x["h"]-p),abs(x["l"]-p)))
    return sum(tr[-n:])/n

def donchian(rows,N):
    # rows[-1] = fully CLOSED signal bar. Current bar is never used.
    if len(rows)<N+1: return 0,None,None
    x=rows[-1]; prev=rows[-N-1:-1]
    hi=max(z["h"] for z in prev); lo=min(z["l"] for z in prev)
    s=1 if x["c"]>hi else (-1 if x["c"]<lo else 0)
    return s,hi,lo

SCHEMA="""
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT);
CREATE TABLE IF NOT EXISTS processed(
 symbol TEXT,bar_ts INTEGER,PRIMARY KEY(symbol,bar_ts));
CREATE TABLE IF NOT EXISTS bars(
 symbol TEXT,ts INTEGER,o REAL,h REAL,l REAL,c REAL,server_ms INTEGER,
 local_wall TEXT,local_mono REAL,PRIMARY KEY(symbol,ts));
CREATE TABLE IF NOT EXISTS signals(
 symbol TEXT,bar_ts INTEGER,side INTEGER,n INTEGER,channel_hi REAL,
 channel_lo REAL,atr REAL,created TEXT);
CREATE TABLE IF NOT EXISTS virtual_orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,action TEXT,side INTEGER,
 signal_bar_ts INTEGER,model_px REAL,bid REAL,ask REAL,book_ts INTEGER,
 skew_bps REAL,created TEXT);
CREATE TABLE IF NOT EXISTS positions(
 symbol TEXT PRIMARY KEY,side INTEGER,entry_ms INTEGER,entry_px REAL,
 stop_px REAL,bars_held INTEGER,n1 REAL,n05 REAL,entry_cost1 REAL,
 entry_cost05 REAL,last_funding_ms INTEGER,
 pending_exit INTEGER DEFAULT 0,pending_reason TEXT,pending_since_ms INTEGER);
CREATE TABLE IF NOT EXISTS trades(
 id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,side INTEGER,
 entry_ms INTEGER,exit_ms INTEGER,entry_px REAL,exit_px REAL,reason TEXT,
 n1 REAL,n05 REAL,gross_pnl1 REAL,gross_pnl05 REAL,
 net_pnl1 REAL,net_pnl05 REAL,funding1 REAL,funding05 REAL,
 cost1 REAL,cost05 REAL,created TEXT);
CREATE TABLE IF NOT EXISTS funding(
 symbol TEXT,funding_ts INTEGER,entry_ms INTEGER,rate REAL,side INTEGER,
 pnl1 REAL,pnl05 REAL,PRIMARY KEY(symbol,funding_ts,entry_ms));
CREATE TABLE IF NOT EXISTS equity(ts INTEGER,eq1 REAL,eq05 REAL,note TEXT);
CREATE TABLE IF NOT EXISTS errors(ts TEXT,where_ TEXT,msg TEXT);
CREATE TABLE IF NOT EXISTS diagnostics(ts TEXT,where_ TEXT,msg TEXT);
"""

def dbopen(path):
    db=sqlite3.connect(path,timeout=30)
    db.row_factory=sqlite3.Row
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")
    # миграция старых БД: добавить pending-колонки если их нет
    cols=[r[1] for r in db.execute("PRAGMA table_info(positions)")]
    if "pending_exit" not in cols:
        db.execute("ALTER TABLE positions ADD COLUMN pending_exit INTEGER DEFAULT 0")
        db.execute("ALTER TABLE positions ADD COLUMN pending_reason TEXT")
        db.execute("ALTER TABLE positions ADD COLUMN pending_since_ms INTEGER")
    for k in ("eq1","eq05","eq1_day","eq05_day","eq1_peak","eq05_peak","day_ts"):
        db.execute("INSERT OR IGNORE INTO meta VALUES(?,?)",(k,"100.0" if k in ("eq1","eq05","eq1_day","eq05_day","eq1_peak","eq05_peak") else "0"))
    db.commit()
    return db

def mget(db,k):
    return float(db.execute("SELECT v FROM meta WHERE k=?",(k,)).fetchone()[0])

def mset(db,k,v):
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",(k,str(v)))

def logeq(db,note):
    db.execute("INSERT INTO equity VALUES(?,?,?,?)",
        (int(time.time()*1000),mget(db,"eq1"),mget(db,"eq05"),note))

def size(eq,risk_frac,entry,stop):
    dist=abs(entry-stop)/entry
    return 0.0 if dist<=0 else min(eq*risk_frac/dist,2.0*eq)

def funding_apply(db,sym,pos,now_ms):
    # РЕВИЗИЯ 4 п.2 + финальная проверка: не поллить /funding/history каждые 10с;
    # TTL 60с на monotonic (устойчиво к NTP/correction wall-clock); кэш после успеха.
    now_m = _MONO()
    last = _CACHE_FUNDING_LAST.get(sym)
    if last is not None and now_m - last < 60.0:
        return
    start=max(int(pos["last_funding_ms"] or pos["entry_ms"]),
              int(pos["entry_ms"]))+1
    rows = funding_rows(sym,start,now_ms)   # HTTP here
    _CACHE_FUNDING_LAST[sym] = now_m        # set ONLY after success
    for f in rows:
        if f["ts"]<=pos["entry_ms"] or f["ts"]>now_ms:
            continue
        # positive funding: longs pay, shorts receive
        p1=-pos["side"]*pos["n1"]*f["rate"]
        p05=-pos["side"]*pos["n05"]*f["rate"]
        try:
            db.execute("INSERT INTO funding VALUES(?,?,?,?,?,?,?)",
              (sym,f["ts"],pos["entry_ms"],f["rate"],pos["side"],p1,p05))
        except sqlite3.IntegrityError:
            continue
        mset(db,"eq1",mget(db,"eq1")+p1)
        mset(db,"eq05",mget(db,"eq05")+p05)
        db.execute("UPDATE positions SET last_funding_ms=? WHERE symbol=?",
                   (f["ts"],sym))
        logeq(db,f"funding {sym} {f['rate']}")

def pos_get(db,sym):
    return db.execute("SELECT * FROM positions WHERE symbol=?",(sym,)).fetchone()

def orderlog(db,sym,action,side,bar_ts,model,bid,ask,bts):
    if side>0:
        live=ask if action=="enter" else bid
        skew=(live/model-1)*10000 if action=="enter" else (model/live-1)*10000
    else:
        live=bid if action=="enter" else ask
        skew=(model/live-1)*10000 if action=="enter" else (live/model-1)*10000
    db.execute("""INSERT INTO virtual_orders
      (symbol,action,side,signal_bar_ts,model_px,bid,ask,book_ts,skew_bps,created)
      VALUES(?,?,?,?,?,?,?,?,?,?)""",
      (sym,action,side,bar_ts,model,bid,ask,bts,skew,iso()))

def portfolio_open_notional(db):
    """Суммарный открытый gross notional по всем позициям, РАЗДЕЛЬНО по кривым.
    Возвращает (tot1, tot05): для 1% и 0.5% shadow curves. Cap применяется к каждой кривой отдельно."""
    tot1 = tot05 = 0.0
    for p in db.execute("SELECT n1,n05 FROM positions"):
        tot1 += abs(float(p["n1"]))
        tot05 += abs(float(p["n05"]))
    return tot1, tot05

def mtm_equity(db):
    """TRUE MTM: realized equity (meta eq1/eq05, funding уже начислен) + unrealized
    PnL открытых позиций по последнему рыночному mid. Вызывается в risk_state
    для daily loss / DD / leverage cap, чтобы halt срабатывал ДО закрытия."""
    eq1, eq05 = mget(db, "eq1"), mget(db, "eq05")
    for p in db.execute("SELECT symbol, side, entry_px, n1, n05 FROM positions"):
        try:
            bids, asks, _ = book10(p["symbol"])
            mid = (bids[0][0] + asks[0][0]) / 2.0 if bids and asks else p["entry_px"]
        except Exception:
            mid = p["entry_px"]
        ep = float(p["entry_px"])
        if ep > 0:
            eq1 += float(p["side"]) * float(p["n1"]) * (mid / ep - 1.0)
            eq05 += float(p["side"]) * float(p["n05"]) * (mid / ep - 1.0)
    return eq1, eq05

def risk_state(db):
    """Возвращает (daily_halted, dd_halted, day_ts) по MTM (true mark-to-market)."""
    import datetime as _dt
    day_ts = int(mget(db, "day_ts") or 0)
    today_s = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    day_s = _dt.datetime.fromtimestamp(day_ts / 1000, _dt.timezone.utc).strftime("%Y%m%d") if day_ts else "0"
    if day_ts == 0 or day_s != today_s:
        # смена UTC-суток: фиксируем day-start equity (по MTM на данный момент)
        mset(db, "eq1_day", mget(db, "eq1"))
        mset(db, "eq05_day", mget(db, "eq05"))
        mset(db, "day_ts", str(int(time.time() * 1000)))
        day_ts = int(time.time() * 1000)
    eq1, eq05 = mtm_equity(db)
    d1 = (eq1 / mget(db, "eq1_day") - 1.0)
    d05 = (eq05 / mget(db, "eq05_day") - 1.0)
    # peak тоже на MTM базе — обновляем из realized+unrealized
    if eq1 > mget(db, "eq1_peak"): mset(db, "eq1_peak", eq1)
    if eq05 > mget(db, "eq05_peak"): mset(db, "eq05_peak", eq05)
    p1, p05 = mget(db, "eq1_peak"), mget(db, "eq05_peak")
    dd1 = (p1 - eq1) / p1 if p1 > 0 else 0.0
    dd05 = (p05 - eq05) / p05 if p05 > 0 else 0.0
    daily_halt = (d1 <= -0.05) or (d05 <= -0.05)
    dd_halt = (dd1 >= 0.20) or (dd05 >= 0.20)
    return daily_halt, dd_halt, day_ts

def refresh_peaks(db):
    """Обновить peak equity (для DD halt)."""
    eq1,eq05=mget(db,"eq1"),mget(db,"eq05")
    if eq1>mget(db,"eq1_peak"): mset(db,"eq1_peak",eq1)
    if eq05>mget(db,"eq05_peak"): mset(db,"eq05_peak",eq05)

def enter(db,sym,side,bar_ts,px,a,bids,asks,bts):
    eq1,eq05=mget(db,"eq1"),mget(db,"eq05")
    daily_halt, dd_halt, _ = risk_state(db)
    if daily_halt or dd_halt:
        # no new entries при daily loss stop / DD halt
        db.execute("INSERT INTO errors VALUES(?,?,?)",(iso(),sym,
            f"enter_blocked daily_halt={daily_halt} dd_halt={dd_halt}"))
        db.commit()
        return
    stop=px-side*CFG[sym]["stop_k"]*a
    # live-fill proxy: long entry=ask, short entry=bid (исполнимая цена против спреда)
    if side>0:
        fill_px = asks[0][0] if asks else px
    else:
        fill_px = bids[0][0] if bids else px
    if fill_px<=0: fill_px=px
    n1=size(eq1,0.01,fill_px,stop); n05=size(eq05,0.005,fill_px,stop)
    # ПОРЯДОК (ревизия 3, п.4): risk-based -> portfolio cap -> depth check
    tot1, tot05 = portfolio_open_notional(db)
    rem1 = max(0.0, 2.0 * eq1 - tot1)
    rem05 = max(0.0, 2.0 * eq05 - tot05)
    n1 = min(n1, rem1); n05 = min(n05, rem05)
    if n1<=0 and n05<=0:
        db.execute("INSERT INTO errors VALUES(?,?,?)",(iso(),sym,
            "enter_blocked portfolio_cap_full"))
        db.commit()
        return
    # depth check: VWAP на max(n1,n05) — консервативная общая цена для обеих кривых
    levels = asks if side>0 else bids
    n_max = max(n1, n05)
    ok, vwap, filled = executable_vwap(side, levels, n_max)
    if not ok:
        db.execute("INSERT INTO errors VALUES(?,?,?)",(iso(),sym,
            f"l1_insufficient side={side} n_max={n_max:.2f} filled={filled:.2f}"))
        db.commit()
        return
    exec_px = vwap if vwap>0 else fill_px
    c1=n1*COST_SIDE; c05=n05*COST_SIDE
    mset(db,"eq1",eq1-c1); mset(db,"eq05",eq05-c05)
    db.execute("""INSERT OR REPLACE INTO positions
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (sym,side,bar_ts+HOUR,exec_px,stop,0,n1,n05,c1,c05,bar_ts+HOUR,0,None,None))
    orderlog(db,sym,"enter",side,bar_ts,exec_px,bids[0][0] if bids else 0,asks[0][0] if asks else 0,bts)
    logeq(db,f"enter {sym} {side} fx={exec_px} n1={n1:.2f} n05={n05:.2f}")
    refresh_peaks(db)

def try_pending_exit(db,sym,bids,asks,bts):
    """Retry pending exit: если позиция open c pending_exit=1 — пробуем закрыть по свежей книге.
    Возвращает True если позиция закрыта, False если всё ещё pending/нет позиции."""
    pos=pos_get(db,sym)
    if not pos or not pos["pending_exit"]:
        return False
    levels = bids if pos["side"]>0 else asks
    n_max = max(abs(pos["n1"]), abs(pos["n05"]))
    if not levels:
        return False
    ok, vwap, filled = executable_vwap(pos["side"], levels, n_max)
    if not ok:
        db.execute("INSERT INTO diagnostics VALUES(?,?,?)",(iso(),sym,
            f"exit_retry_illiquid side={pos['side']} notional={n_max:.2f} filled={filled:.2f}"))
        db.commit()
        return False
    exitpos(db,sym,pos,int(time.time()*1000),vwap,"exit_retry_full",int(time.time()*1000),bids,asks,bts)
    return True

def exitpos(db,sym,pos,exit_ms,px,reason,bar_ts,bids=None,asks=None,bts=0):
    side=pos["side"]
    # РЕВИЗИЯ 4/5: при недостаточной глубине НЕ закрываем позицию фиктивно,
    # а переводим в persistent pending_exit состояние (переживает restart, блокирует новые входы).
    levels = bids if side>0 else asks
    n_max = max(abs(pos["n1"]), abs(pos["n05"]))  # USD-notional
    if levels:
        ok, vwap, filled = executable_vwap(side, levels, n_max)
        if ok:
            fill_px = vwap
        else:
            db.execute("""UPDATE positions SET pending_exit=1, pending_reason=?, pending_since_ms=?
                          WHERE symbol=?""",(reason,int(time.time()*1000),sym))
            db.execute("INSERT INTO diagnostics VALUES(?,?,?)",(iso(),sym,
                f"exit_illiquid side={side} notional={n_max:.2f} filled={filled:.2f} reason={reason}"))
            db.commit()
            return  # позиция НЕ закрыта, pending_exit=1, retry на следующем poll
    else:
        # recovery/modell-proxy (нет книги): conservative use model px
        fill_px = px
    if fill_px is None or fill_px<=0: fill_px=px
    g1=side*pos["n1"]*(fill_px/pos["entry_px"]-1)
    g05=side*pos["n05"]*(fill_px/pos["entry_px"]-1)
    xc1=pos["n1"]*COST_SIDE; xc05=pos["n05"]*COST_SIDE
    f1=f05=0.0
    # funding уже начислен в eq; для net pnl соберём из ledger
    for f in db.execute("SELECT pnl1,pnl05 FROM funding WHERE symbol=? AND entry_ms=?",
                        (sym,pos["entry_ms"])):
        f1+=float(f["pnl1"]); f05+=float(f["pnl05"])
    net1=g1+f1-xc1-pos["entry_cost1"]; net05=g05+f05-xc05-pos["entry_cost05"]
    mset(db,"eq1",mget(db,"eq1")+g1-xc1)
    mset(db,"eq05",mget(db,"eq05")+g05-xc05)
    db.execute("""INSERT INTO trades
      (symbol,side,entry_ms,exit_ms,entry_px,exit_px,reason,n1,n05,
       gross_pnl1,gross_pnl05,net_pnl1,net_pnl05,funding1,funding05,
       cost1,cost05,created)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (sym,side,pos["entry_ms"],exit_ms,pos["entry_px"],fill_px,reason,
       pos["n1"],pos["n05"],g1,g05,net1,net05,f1,f05,
       pos["entry_cost1"]+xc1,pos["entry_cost05"]+xc05,iso()))
    if bids is not None:
        orderlog(db,sym,"exit",side,bar_ts,fill_px,bids[0][0],asks[0][0] if asks else 0,bts)
    db.execute("DELETE FROM positions WHERE symbol=?",(sym,))
    logeq(db,f"exit {sym} {reason}")
    refresh_peaks(db)

def process(db,sym):
    cfg=CFG[sym]; sm=server_ms(); rows=klines(sym,100)
    closed=[x for x in rows if x["ts"]+HOUR<=sm]
    current=[x for x in rows if x["ts"]<=sm<x["ts"]+HOUR]
    if len(closed)<max(cfg["N"]+2,20) or not current: return
    x=closed[-1]; cur=current[-1]

    if db.execute("SELECT 1 FROM processed WHERE symbol=? AND bar_ts=?",
                  (sym,x["ts"])).fetchone():
        # even if bar processed — retry pending exit on every poll (state D)
        pe=pos_get(db,sym)
        if pe and pe["pending_exit"]:
            try:
                bids,asks,bts=book10(sym)
                try_pending_exit(db,sym,bids,asks,bts)
            except Exception:
                pass
        return

    any_seen=db.execute("SELECT 1 FROM processed WHERE symbol=? LIMIT 1",
                        (sym,)).fetchone()
    lateness=sm-cur["ts"]

    # --- КРИТИЧНО #1: stale restart / outage guard ---
    # Если процесс уже работал (any_seen) и мы «проснулись» позже 120с от начала
    # текущего бара — НЕ создаём новые виртуальные записи по уже прошедшему open.
    missed = (not any_seen and lateness>120000) or lateness>120000
    if missed:
        # recovery открытой позиции: проверить ПРОПУЩЕННЫЕ закрытые бары
        pos=pos_get(db,sym)
        if pos:
            # последовательно по закрытым барам после entry: stop/timeout/opposite
            for b in closed:
                if b["ts"]<=pos["entry_ms"]: continue
                stopped=(pos["side"]>0 and b["l"]<=pos["stop_px"]) or                 (pos["side"]<0 and b["h"]>=pos["stop_px"])
                if stopped:
                    fx=min(pos["stop_px"],b["o"]) if pos["side"]>0 else                max(pos["stop_px"],b["o"])
                    exitpos(db,sym,pos,b["ts"]+HOUR,fx,"stop_recovery",b["ts"])
                    pos=None
                    break
                held=pos["bars_held"]+1
                if held>=cfg["timeout"]:
                    exitpos(db,sym,pos,b["ts"]+HOUR,b["o"],"timeout_recovery",b["ts"])
                    pos=None
                    break
                # opposite-сигнал на пропущенном баре
                idx=closed.index(b)
                if idx>=cfg["N"]:
                    hi=max(z["h"] for z in closed[idx-cfg["N"]:idx])
                    lo=min(z["l"] for z in closed[idx-cfg["N"]:idx])
                    s2=1 if b["c"]>hi else (-1 if b["c"]<lo else 0)
                    if s2!=0 and s2==-pos["side"]:
                        exitpos(db,sym,pos,b["ts"]+HOUR,b["o"],"opposite_recovery",b["ts"])
                        pos=None
                        break
        db.execute("INSERT INTO diagnostics VALUES(?,?,?)",(iso(),sym,
            f"missed_boundary lateness_ms={lateness} any_seen={bool(any_seen)}"))
        db.execute("INSERT OR IGNORE INTO processed VALUES(?,?)",(sym,x["ts"]))
        db.commit(); return

    db.execute("""INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)""",
      (sym,x["ts"],x["o"],x["h"],x["l"],x["c"],sm,iso(),time.monotonic()))

    s,hi,lo=donchian(closed,cfg["N"]); a=atr(closed,14)
    if a is None: return
    db.execute("INSERT INTO signals VALUES(?,?,?,?,?,?,?,?)",
      (sym,x["ts"],s,cfg["N"],hi,lo,a,iso()))
    bids,asks,bts=book10(sym)

    pos=pos_get(db,sym)
    if pos:
        funding_apply(db,sym,pos,sm)
        pos=pos_get(db,sym)
        stopped=(pos["side"]>0 and x["l"]<=pos["stop_px"]) or                 (pos["side"]<0 and x["h"]>=pos["stop_px"])
        if stopped:
            px=min(pos["stop_px"],x["o"]) if pos["side"]>0 else                max(pos["stop_px"],x["o"])
            exitpos(db,sym,pos,x["ts"]+HOUR,px,"stop",x["ts"],bids,asks,bts)
            pos=None
        else:
            held=pos["bars_held"]+1
            db.execute("UPDATE positions SET bars_held=? WHERE symbol=?",(held,sym))
            opposite=(s!=0 and s==-pos["side"]); timed=held>=cfg["timeout"]
            if opposite or timed:
                exitpos(db,sym,pos,cur["ts"],cur["o"],
                        "opposite" if opposite else "timeout",
                        x["ts"],bids,asks,bts)
                pos=None

    # signal at close(t), entry at open(t+1) — только если мы своевременно увидели границу
    if pos is None and s!=0:
        enter(db,sym,s,x["ts"],cur["o"],a,bids,asks,bts)

    # pending_exit: явная диагностика блокировки новых входов (state C/D)
    cur_pos=pos_get(db,sym)
    if cur_pos and cur_pos["pending_exit"] and s!=0:
        db.execute("INSERT INTO diagnostics VALUES(?,?,?)",(iso(),sym,
            "enter_blocked_pending_exit"))
        db.commit()

    db.execute("INSERT OR IGNORE INTO processed VALUES(?,?)",(sym,x["ts"]))
    # MTM-equity snapshot каждый закрытый час
    refresh_peaks(db)
    db.commit()

def recover(db):
    for p in db.execute("SELECT * FROM positions"):
        print(iso(),"RECOVER",p["symbol"],"side",p["side"],
              "entry",p["entry_px"],"stop",p["stop_px"],
              "bars",p["bars_held"])

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default="data/risk_paper.db")
    ap.add_argument("--hours",type=float,default=24.0)
    ap.add_argument("--poll",type=float,default=10.0)
    a=ap.parse_args()

    db=dbopen(a.db)
    recover(db)
    end=time.monotonic()+a.hours*3600
    loop_count = 0
    hb_last = time.monotonic()
    print(iso(),"START no-order paper runner",CFG,"db",a.db)

    while time.monotonic()<end:
        loop_count += 1
        for sym in CFG:
            try:
                process(db,sym)
                mset(db,f"last_success_{sym}",iso())
            except Exception as e:
                db.execute("INSERT INTO errors VALUES(?,?,?)",
                    (iso(),sym,repr(e)+"\n"+traceback.format_exc()))
                db.commit()
                print(iso(),"ERR",sym,repr(e))
        # Heartbeat (аудит №5 + финальная проверка): дешёвый state в meta раз в минуту
        if time.monotonic()-hb_last>=60:
            mset(db,"last_heartbeat_utc",iso())
            mset(db,"loop_count",str(loop_count))
            mset(db,"http_429_count",str(API_STATS["http_429"]))
            mset(db,"http_403_count",str(API_STATS["http_403"]))
            mset(db,"ret_10006_count",str(API_STATS["ret_10006"]))
            mset(db,"http_5xx_count",str(API_STATS["http_5xx"]))
            mset(db,"network_errors_count",str(API_STATS["network_errors"]))
            db.commit()
            hb_last=time.monotonic()
        time.sleep(a.poll)

    # Не force-close: открытая paper-позиция должна пережить restart.
    logeq(db,"runner_end")
    db.commit()
    print(iso(),"FINISH",
          "eq1",mget(db,"eq1"),
          "eq05",mget(db,"eq05"),
          "open_positions",
          db.execute("SELECT count(*) FROM positions").fetchone()[0])

if __name__=="__main__":
    main()