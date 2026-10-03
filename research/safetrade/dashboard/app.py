"""Local research dashboard. Quotes use shared normalisation and separate sources."""
import json
import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

BASE = os.environ.get('SAFETRADE_BASE', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_ROOT)
from dashboard.model import read_state, recent_history, quote_table
from analysis.oracle import prepare_mid_column

with open(os.environ.get('SAFETRADE_CONFIG') or os.path.join(CODE_ROOT, 'config', 'config.yaml')) as f:
    CFG = yaml.safe_load(f)

st.set_page_config(page_title='SafeTrade Research', layout='wide')
st.title('SafeTrade — исследование задержки цены')
st.caption('Виртуальные сделки. Реальные ордера не отправляются.')
st.sidebar.write('Оракул: '+', '.join(CFG['sources']))
markets = [m['canonical'] for m in CFG['markets'] if m['enabled']]
symbol = st.sidebar.selectbox('Рынок', markets or ['BTCUSDT'])
st.sidebar.caption('Данные обновляются каждые 5 секунд. Время UTC.')


@st.cache_data(ttl=5)
def history(symbol, run_id):
    return recent_history(os.path.join(BASE, CFG['storage']['parquet_dir']), symbol, run_id)


def read_report(kind, run_id):
    path = os.path.join(BASE, 'reports', f'{kind}_{symbol}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return None
    return rep if rep.get('run_id') == run_id else None


@st.fragment(run_every=5)
def panel():
    state, health, run = read_state(os.path.join(BASE, CFG['sqlite_path']))
    run_id = run[0] if run else None
    df = history(symbol, run_id)
    if run_id is None and not df.empty:
        run_id = df.iloc[0].run_id
    latest = pd.DataFrame(state.get('latest_events', []))
    if not latest.empty:
        latest = latest[(latest.canonical_symbol==symbol) & (latest.run_id==run_id)]
    current = latest if not latest.empty else df
    if run:
        st.write(f'Запись {run_id} · состояние: {run[1]} · старт: {run[2]} UTC')
    else:
        st.info('Нет активной записи. Запуск: python cli.py collect --minutes 60')
    if not current.empty and any(isinstance(p, dict) and p.get('fixture') for p in current.payload):
        st.warning('Искусственные данные для проверки программы. Их результат ничего не говорит о доходности на бирже.')
    tabs = st.tabs(['Сейчас', 'Графики', 'Достижение', 'Виртуальные счета', 'Качество', 'Пары'])
    with tabs[0]:
        quotes, r, n = quote_table(current, CFG)
        st.metric('Медиана трёх внешних бирж R', f'{r:.6f}' if r is not None else 'недоступна')
        st.caption(f'Свежих внешних источников: {n}/3. SafeTrade в медиану не входит.')
        if not quotes.empty:
            st.dataframe(quotes, hide_index=True, width='stretch')
        st.info('REST-снимки SafeTrade пригодны для наблюдения. Их связь с WS-дельтами пока не подтверждена, поэтому paper-входы на них запрещены.')
    with tabs[1]:
        if df.empty:
            st.info('Закрытых файлов с данными пока нет.')
        else:
            frame = prepare_mid_column(df.copy())
            fig = go.Figure()
            for ex in CFG['sources']+[CFG['target']]:
                s = frame[(frame.exchange==ex) & frame._selected]
                fig.add_trace(go.Scatter(x=pd.to_datetime(s.recv_utc_ns, unit='ns', utc=True),
                                        y=s._mid, mode='markers' if ex==CFG['target'] else 'lines',
                                        connectgaps=False, name=ex))
            fig.update_layout(height=400, xaxis_title='UTC', yaxis_title='Цена USDT')
            st.plotly_chart(fig, width='stretch')
            st.caption('Последние 10 минут записи, до 50 000 событий. Между редкими снимками SafeTrade линия не дорисовывается.')
    with tabs[2]:
        rep = read_report('analysis', run_id)
        if rep is None:
            st.info('Для этой записи нет отчёта. Запуск: python cli.py analyze --symbol '+symbol)
        else:
            rows = []
            for name, key in [('R0 — сырая', 'summary'), ('F0 — с премией', 'summary_adj')]:
                for h, value in rep.get('reach_A', {}).get(key, {}).items():
                    rows.append({'Цель': name, 'Горизонт, с': h, 'Всего': value['n_events'],
                                 'Достигла, % всех': value['reached_pct'], 'Медиана первого наблюдения, с': value['median_s'],
                                 'Статусы': json.dumps(value['by_status'], ensure_ascii=False)})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
            st.json({'премия': rep.get('premium'), 'воронка': rep.get('funnel')}, expanded=False)
            st.caption('unknown — наблюдений не хватает; already_at_target — цель достигнута до сигнала; unavailable — премия ещё не готова.')
    with tabs[3]:
        rep = read_report('paper', run_id)
        if rep is None:
            st.info('Запуск: python cli.py paper --symbol '+symbol)
        else:
            rows = []
            for name, hypo in rep['hypotheses'].items():
                for scenario, value in hypo['scenarios'].items():
                    rows.append({'Гипотеза': name, 'Задержка, мс': value['L_ms'], 'Бюджет, USDT': value['budget_usdt'],
                                 'Входов': value['n_entries'], 'Закрыто': value['n_closed'],
                                 'Неизвестных заявок': value.get('n_unknown_entries', 0),
                                 'Неизвестных позиций': value['n_open_unknown'],
                                 'Деньги, USDT': value['cash_usdt'], 'Доступно, USDT': value.get('available_cash_usdt', value['cash_usdt']),
                                 'Стоимость счёта, USDT': value['equity_usdt'], 'Закрытая прибыль, USDT': value['realized_pnl_usdt'],
                                 'Комиссии, USDT': value['fees_paid_usdt']})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
            st.caption('Каждая строка — отдельный счёт со стартовыми $100. Результаты строк не складываются. Выход проверяется через H секунд плюс задержка заявки.')
            st.caption('Если данные об исполнении пропали, заявка или позиция остаётся неизвестной, а счёт прекращает новые входы. Пустая стоимость счёта означает, что её нельзя достоверно оценить.')
            st.json({n:{k:v['rejections'] for k,v in h['scenarios'].items()} for n,h in rep['hypotheses'].items()}, expanded=False)
            path = os.path.join(BASE, 'reports', f'paper_trades_{symbol}_{run_id}.csv')
            if os.path.exists(path):
                trades = pd.read_csv(path)
                if not trades.empty:
                    st.dataframe(trades, hide_index=True, width='stretch')
                st.download_button('Скачать сделки CSV', trades.to_csv(index=False), 'paper_trades.csv', 'text/csv')
    with tabs[4]:
        if not health.empty:
            st.dataframe(health[['ts_utc', 'exchange', 'symbol', 'connected', 'last_msg_age_s', 'msgs', 'reconnects']], hide_index=True, width='stretch')
        st.json(state.get('counters', state.get('last_run', {})), expanded=False)
        st.caption('Возраст котировки и работоспособность соединения — разные показатели. events_flushed — строки уже в закрытых Parquet-файлах.')
    with tabs[5]:
        # Пары: скрининг (lag) / maker-отбор / детальный сбор (поправка пользователя:
        # расширить вкладку, добавить выбор режима исследования)
        mode = st.radio('Режим исследования', ['Maker-отбор (этап 1)', 'Lag-скрининг (закрыт)', 'Детальный сбор'],
                        horizontal=True, label_visibility='collapsed')
        if mode == 'Maker-отбор (этап 1)':
            maker_path = os.path.join(BASE, 'reports', 'pair_screen_maker.csv')
            if os.path.exists(maker_path):
                maker = pd.read_csv(maker_path)
                st.dataframe(maker, hide_index=True, width='stretch')
                st.caption('Широкий отбор всех USDT-пар SafeTrade (100). Широкий спред НЕ исключает пару '
                           '(поправка 1: для maker это повод проверить встречную торговлю). '
                           'Активность — по временному покрытию сделок (trades_per_hour, last_trade_age), '
                           'а не по числу прочитанных страниц. Широкий спред — повод проверить встречную торговлю, '
                           'а не исключить пару (поправки 2-6).')
            else:
                st.info('reports/pair_screen_maker.csv не найден. Запуск: cli.py maker-screen')
        elif mode == 'Lag-скрининг (закрыт)':
            screen_path = os.path.join(BASE, 'reports', 'pair_screen.csv')
            if os.path.exists(screen_path):
                screen = pd.read_csv(screen_path, comment='#')
                st.dataframe(screen, hide_index=True, width='stretch')
                st.caption('Lag-скрининг закрыт (2026-10-03): гипотеза запаздывания не подтвердилась. '
                           'Таблица оставлена как референс.')
            else:
                st.info('reports/pair_screen.csv не найден.')
        else:
            # детальный сбор: самые свежие данные data/maker/*_summary.json
            import glob as _glob
            sums = sorted(_glob.glob(os.path.join(BASE, 'data', 'maker', '*_summary.json')))
            if sums:
                latest = json.load(open(sums[-1]))
                st.json(latest, expanded=False)
                run_id = latest.get('run_id')
                dpath = os.path.join(BASE, 'data', 'maker', f'{run_id}_depth.jsonl')
                if os.path.exists(dpath):
                    import collections
                    rows = [json.loads(l) for l in open(dpath) if l.strip()]
                    per = collections.defaultdict(list)
                    for r in rows:
                        if r.get('ok') and r.get('bids') and r.get('asks'):
                            bb = max(float(p) for p, _ in r['bids'])
                            ba = min(float(p) for p, _ in r['asks'])
                            per[r['pair']].append((ba - bb) / ((ba + bb) / 2) * 1e4)
                    out = []
                    for pair, spreads in sorted(per.items()):
                        if spreads:
                            s = sorted(spreads)
                            out.append({'pair': pair, 'n_snaps': len(s),
                                        'spread_min_bps': round(s[0], 1),
                                        'spread_p50_bps': round(s[len(s)//2], 1),
                                        'spread_p90_bps': round(s[int(len(s)*.9)], 1)})
                    if out:
                        st.dataframe(pd.DataFrame(out), hide_index=True, width='stretch')
                        st.caption(f'Детальный сбор {run_id}: распределение спреда по снимкам depth. '
                                   'REST 20с, trades 60с, oracle LTC каждые 5 тиков. БЕЗ ордеров.')
            else:
                st.info('Детального сбора ещё нет. Запуск: cli.py maker-collect --pairs PRLUSDT,... --minutes 360')
        diag_path = os.path.join(BASE, 'reports', f'signals_{symbol}.csv')
        if os.path.exists(diag_path):
            with st.expander(f'Диагностика сигналов ({symbol})'):
                sig = pd.read_csv(diag_path)
                st.dataframe(sig, hide_index=True, width='stretch')
                st.caption('Для каждого сигнала (lag-стратегия, закрыта): возраст предыдущего снимка SafeTrade, '
                           'число будущих снимков на горизонтах, ask_R_ratio/ask_F0_ratio, spread, '
                           'VWAP-статусы бюджетов 10/25/50 USDT.')


panel()
