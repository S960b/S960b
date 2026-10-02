"""Streamlit-панель (ТЗ п.11): русские подписи, localhost-only, обновление ~1s,
метка «виртуальная торговля», вкладки: Сейчас / Графики / Достижение / Сделки / Качество."""
import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE = os.environ.get("SAFETRADE_BASE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

CFG_PATH = os.environ.get("SAFETRADE_CONFIG") or os.path.join(BASE, "config", "config.yaml")
import yaml
with open(CFG_PATH) as f:
    CFG = yaml.safe_load(f)

st.set_page_config(page_title="SafeTrade Research", layout="wide")

META = {
    "mode": "демонстрация" if os.environ.get("SAFETRADE_DEMO") else "реальные данные",
    "started": None,
    "virtual": True,
}
st.sidebar.markdown("## SafeTrade Research")
st.sidebar.markdown(f"**Режим:** {META['mode']}")
st.sidebar.markdown("**⚠ ВИРТУАЛЬНАЯ ТОРГОВЛЯ** — реальных ордеров не отправляется")
st.sidebar.markdown("**Источники:** " + ", ".join(CFG["sources"] + [CFG["target"]]))


def load_state_db():
    import sqlite3
    path = os.path.join(BASE, CFG["sqlite_path"])
    if not os.path.exists(path):
        return None, None, None
    conn = sqlite3.connect(path)
    try:
        last_run = pd.read_sql("SELECT * FROM runs ORDER BY started_utc DESC LIMIT 1", conn)
        health = pd.read_sql("SELECT * FROM health ORDER BY id DESC LIMIT 200", conn)
        trades = pd.read_sql("SELECT * FROM paper_trades ORDER BY signal_utc_ns DESC LIMIT 500", conn)
        return last_run, health, trades
    except Exception:
        return None, None, None
    finally:
        conn.close()


def load_parquet_paths():
    pq_dir = os.path.join(BASE, CFG["storage"]["parquet_dir"])
    files = []
    for root, _, fs in os.walk(pq_dir):
        for f in fs:
            if f.endswith(".parquet") and not f.startswith("."):
                files.append(os.path.join(root, f))
    return files


def read_recent(files, limit=200000):
    """Читаем последние N событий из последних файлов (для графиков: агрегация не меняет replay)."""
    if not files:
        return pd.DataFrame()
    import duckdb
    recent = sorted(files)[-6:]
    gl = [f for f in recent] + [f for f in files if f not in recent]
    df = duckdb.query(f"""
        SELECT recv_utc_ns, exchange, canonical_symbol, event_type,
               price, qty, sequence, bids, asks, quality_flags
        FROM read_parquet({gl!r})
        ORDER BY recv_utc_ns DESC LIMIT {limit}
    """).df()
    return df


last_run, health_df, trades_df = load_state_db()
files = load_parquet_paths()
df = read_recent(files) if files else pd.DataFrame()

if df.empty:
    st.warning("Данных пока нет. Запустите сбор: python cli.py collect --minutes 60 (vs. real data)")
    st.stop()

st.title("SafeTrade Research — панель")
if last_run is not None and not last_run.empty:
    r = last_run.iloc[0]
    st.caption(f"Запись: run {r['run_id']} · старт {r['started_utc']} UTC · "
               f"источники {r['sources']} · рынки {r['markets']}")

tabs = st.tabs(["Сейчас", "Графики", "Достижение", "Сделки", "Качество"])

symbols = sorted(df.canonical_symbol.unique())
sel_sym = st.sidebar.selectbox("Рынок", symbols) if symbols else "BTCUSDT"


def mid_of(ev):
    if ev.get("bids") and ev.get("asks"):
        b = float(json.loads(ev["bids"])[0][0]) if isinstance(ev["bids"], str) else float(ev["bids"][0][0])
        a = float(json.loads(ev["asks"])[0][0]) if isinstance(ev["asks"], str) else float(ev["asks"][0][0])
        return (b + a) / 2
    if ev.get("price"):
        return float(ev["price"])
    return None


with tabs[0]:
    st.subheader("Сейчас — живой монитор")
    sub = df[df.canonical_symbol == sel_sym]
    if sub.empty:
        st.info("Нет событий по этому рынку")
    else:
        latest = sub.sort_values("recv_utc_ns").iloc[-1]
        cols = st.columns(5)
        cols[0].metric("SafeTrade bid", f"{float(json.loads(latest['bids'])[0][0]):.2f}" if latest.get("bids") else "—")
        cols[1].metric("SafeTrade ask", f"{float(json.loads(latest['asks'])[0][0]):.2f}" if latest.get("asks") else "—")
        cols[2].metric("Посл. событие", f"{datetime.fromtimestamp(latest['recv_utc_ns']/1e9, tz=timezone.utc):%H:%M:%S}")
        mid_vals = []
        safe_mid = None
        for ex in CFG["sources"] + [CFG["target"]]:
            exl = sub[sub.exchange == ex]
            if exl.empty:
                continue
            e = exl.sort_values("recv_utc_ns").iloc[-1]
            m = mid_of(e)
            if m is not None:
                mid_vals.append(m)
                if ex == CFG["target"]:
                    safe_mid = m
        if mid_vals:
            extr = [m for m in mid_vals if m]
            cols[3].metric("Внешний mid (медиана)", f"{sorted(extr)[len(extr)//2]:.2f}" if extr else "—")
        st.markdown("**Состояние:** «наблюдение». Сигналы и paper-входы — на вкладке «Сделки» (виртуально).")
        st.info("Текущий разрыв mid ≠ прибыль. Прибыль считается только после комиссий, "
                "спреда, глубины и задержки исполнения (см. вкладки «Достижение»/«Сделки»).")

with tabs[1]:
    st.subheader("Графики")
    sub = df[df.canonical_symbol == sel_sym].sort_values("recv_utc_ns")
    if len(sub) < 10:
        st.info("Недостаточно точек")
    else:
        fig = go.Figure()
        for ex in CFG["sources"] + [CFG["target"]]:
            exd = sub[sub.exchange == ex].copy()
            exd["mid"] = exd.apply(mid_of, axis=1)
            exd = exd.dropna(subset=["mid"])
            t = pd.to_datetime(exd["recv_utc_ns"], unit="ns")
            fig.add_trace(go.Scatter(x=t, y=exd["mid"], mode="lines", name=ex, line=dict(width=1)))
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10),
                          title=f"mid по источникам — {sel_sym}")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Ось времени UTC. Агрегация графика не меняет underlying execution replay.")

with tabs[2]:
    st.subheader("Достижение цели")
    report_path = os.path.join(BASE, "reports", f"analysis_{sel_sym}.json")
    if os.path.exists(report_path):
        rep = json.load(open(report_path))
        summ = rep.get("reach_A", {}).get("summary", {})
        if summ and "n_signals" in str(summ):
            rows = []
            for h, v in summ.items():
                rows.append({"Горизонт, с": h, "Событий": v.get("n_signals"),
                             "% достиг. сырой": v.get("raw_reached_pct"),
                             "% достиг. скорр.": v.get("adj_reached_pct"),
                             "Медиана (сырая), с": v.get("raw_median_s")})
            st.dataframe(pd.DataFrame(rows))
            st.caption("Сырая цель R0 = медиана внешних mid на момент сигнала; "
                       "скорректированная F0 = R0·exp(b), b — обычная премия SafeTrade за прошлое окно.")
        else:
            st.info("Отчёт анализа ещё не готов или сигналов пока нет.")
    else:
        st.info("Запустите: python cli.py analyze")

with tabs[3]:
    st.subheader("Сделки (виртуальные)")
    if trades_df is not None and not trades_df.empty:
        show = trades_df[["signal_utc", "hypothesis", "L_ms", "size_usdt", "entry_price",
                          "exit_price", "qty", "fee_paid", "pnl", "reason"]]
        st.dataframe(show)
        st.download_button("Экспорт CSV", trades_df.to_csv(index=False), "paper_trades.csv", "text/csv")
    else:
        st.info("Бумажных сделок пока нет. Запустите: python cli.py paper")

with tabs[4]:
    st.subheader("Качество данных")
    if health_df is not None and not health_df.empty:
        h = health_df.copy()
        h["ts"] = pd.to_datetime(h["ts_utc"])
        st.dataframe(h[["ts", "exchange", "connected", "last_msg_age_s", "msgs", "reconnects"]].head(50))
    st.markdown(f"**Parquet-файлов:** {len(files)}")
    st.markdown("**Понятия:** bid — лучшая цена покупки; ask — лучшая цена продажи; "
                "спред = (ask−bid)/mid; 1 bps = 0,01%.")
    free = None
    import sqlite3
    if os.path.exists(os.path.join(BASE, CFG["sqlite_path"])):
        conn = sqlite3.connect(os.path.join(BASE, CFG["sqlite_path"]))
        try:
            row = conn.execute("SELECT value FROM ui_state WHERE key='disk_free'").fetchone()
            if row:
                free = json.loads(row[0])
        except Exception:
            pass
        conn.close()
    if free:
        st.markdown(f"**Свободно на диске:** {free/1e9:.1f} ГБ")

st.sidebar.caption("Обновление ~1 сек · UTC в данных · отображение Europe/Moscow "
                   "(переключение в настройках Streamlit)")