"""Регрессии side_semantics_check (шаг 7 ревью: дедуп, BBO, tie, знаменатель).

Offline, без сети. Кейсы:
- классификация по BBO (best_bid=max(bids), best_ask=min(asks)), независимо от side;
- 1-тиковый спред: buy@ask / sell@bid => aggr, buy@bid / sell@ask => passive;
- tie (равное расстояние / двойное совпадение внутри тика) => ambiguous;
- вне допуска => indet; глубокий уровень не является границей => indet;
- side='unverified' сохраняется;
- дедуп (pair, id): дубли (в т.ч. между poll-записями) не классифицируются,
  missing_id учитывается отдельно;
- знаменатель процентов = classified (aggr_ok+passive_ok);
- параметры tick и max_age меняют результат.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from side_semantics_check import analyze, classify_trade, nearest_boundary  # noqa: E402

TICK_01 = Decimal('0.01')

# 1-тиковый стакан: bid 100.00 / ask 100.01
BIDS = [['100.00', '2.0']]
ASKS = [['100.01', '2.0']]
# симметричный стакан со спредом 2 тика: 100.00 / 100.02
BIDS2 = [['100.00', '2.0']]
ASKS2 = [['100.02', '2.0']]

# снимок: 2023-11-14T22:13:00Z = epoch 1699999980, t в наносекундах
DEPTH = [{'pair': 'PRLUSDT', 'bids': [['100.00', '2.0']],
          'asks': [['100.01', '2.0']], 't': 1_699_999_980_000_000_000}]


# ---------- nearest_boundary / classify_trade ----------

def test_buy_at_ask_aggr():
    assert nearest_boundary(Decimal('100.01'), BIDS, ASKS, TICK_01) == 'ask'
    assert classify_trade(Decimal('100.01'), 'buy', BIDS, ASKS, TICK_01) == 'aggr'


def test_sell_at_bid_aggr():
    assert nearest_boundary(Decimal('100.00'), BIDS, ASKS, TICK_01) == 'bid'
    assert classify_trade(Decimal('100.00'), 'sell', BIDS, ASKS, TICK_01) == 'aggr'


def test_buy_at_bid_passive():
    assert classify_trade(Decimal('100.00'), 'buy', BIDS, ASKS, TICK_01) == 'passive'


def test_sell_at_ask_passive():
    assert classify_trade(Decimal('100.01'), 'sell', BIDS, ASKS, TICK_01) == 'passive'


def test_tie_mid_spread_ambiguous():
    # ровно между bid/ask в 1-тиковом стакане: равные расстояния
    assert nearest_boundary(Decimal('100.005'), BIDS, ASKS, TICK_01) == 'ambiguous'
    assert classify_trade(Decimal('100.005'), 'buy', BIDS, ASKS, TICK_01) == 'ambiguous'
    # симметричный спред 2 тика, сделка ровно посередине
    assert nearest_boundary(Decimal('100.01'), BIDS2, ASKS2, TICK_01) == 'ambiguous'


def test_far_from_book_indet():
    assert nearest_boundary(Decimal('100.10'), BIDS, ASKS, TICK_01) == 'indet'
    assert classify_trade(Decimal('100.10'), 'buy', BIDS, ASKS, TICK_01) == 'indet'


def test_deep_level_not_boundary_indet():
    # BBO 100.00/100.01, глубокий bid 99.99. Цена 99.985 ближе к глубокому
    # уровню (0.005), но вне допуска от best_bid (0.015) и best_ask (0.025).
    # Глубокий уровень — не граница спреда => indet, не classified.
    deep_bids = [['100.00', '2.0'], ['99.99', '5.0']]
    px = Decimal('99.985')
    assert nearest_boundary(px, deep_bids, ASKS, TICK_01) == 'indet'
    assert classify_trade(px, 'sell', deep_bids, ASKS, TICK_01) == 'indet'
    assert classify_trade(px, 'buy', deep_bids, ASKS, TICK_01) == 'indet'


def test_unverified_side_preserved():
    assert classify_trade(Decimal('100.01'), 'unverified', BIDS, ASKS, TICK_01) == 'unverified'
    assert classify_trade(Decimal('100.00'), 'unverified', BIDS, ASKS, TICK_01) == 'unverified'


# ---------- analyze(): 1-тиковый спред + дубли (регрессия) ----------

# 8 сделок = 4 уникальные комбинации price×side (спред 1 tick), каждая
# повторена тем же id: buy@ask / sell@bid => aggr; buy@bid / sell@ask => passive.
POLL = {'pair': 'PRLUSDT', 'trades': [
    {'id': 't1', 'price': '100.01', 'side': 'buy', 'created_at': 1699999980},    # aggr
    {'id': 't1', 'price': '100.01', 'side': 'buy', 'created_at': 1699999981},    # дубль t1
    {'id': 't2', 'price': '100.00', 'side': 'sell', 'created_at': 1699999980},   # aggr
    {'id': 't2', 'price': '100.00', 'side': 'sell', 'created_at': 1699999981},   # дубль t2
]}
POLL2 = {'pair': 'PRLUSDT', 'trades': [
    {'id': 't3', 'price': '100.00', 'side': 'buy', 'created_at': 1699999982},    # passive
    {'id': 't3', 'price': '100.00', 'side': 'buy', 'created_at': 1699999983},    # дубль t3
    {'id': 't4', 'price': '100.01', 'side': 'sell', 'created_at': 1699999982},   # passive
    {'id': 't4', 'price': '100.01', 'side': 'sell', 'created_at': 1699999983},   # дубль t4
]}


def test_analyze_1tick_spread_with_duplicates():
    a = analyze(DEPTH, [POLL, POLL2])['PRLUSDT']
    assert a['total'] == 8
    assert a['unique'] == 4          # t1..t4
    assert a['duplicates'] == 4      # каждый id повторён один раз
    assert a['missing_id'] == 0
    assert a['aggr_ok'] == 2         # buy@ask, sell@bid
    assert a['passive_ok'] == 2      # buy@bid, sell@ask
    assert a['ambiguous'] == 0
    assert a['indet'] == 0
    assert a['no_snap'] == 0
    assert a['unverified'] == 0
    assert a['classified'] == 4
    assert a['aggr_pct'] == 50.0
    assert a['passive_pct'] == 50.0
    assert a['verdict'] == 'AMBIGUOUS'
    # консистентность подсчётов
    assert a['unique'] + a['missing_id'] + a['duplicates'] == a['total']
    assert (a['aggr_ok'] + a['passive_ok'] + a['ambiguous'] + a['indet']
            + a['no_snap'] + a['unverified']) == a['unique'] + a['missing_id']


def test_analyze_midpoint_ambiguous():
    # сделка с id ровно на середине 1-тикового спреда: равные расстояния
    # до BBO => ambiguous, не классифицируется
    poll = {'pair': 'PRLUSDT', 'trades': [
        {'id': 'm1', 'price': '100.005', 'side': 'buy', 'created_at': 1699999980},
    ]}
    a = analyze(DEPTH, [poll])['PRLUSDT']
    assert a['ambiguous'] == 1
    assert a['aggr_ok'] == 0
    assert a['passive_ok'] == 0
    assert a['classified'] == 0
    assert a['indet'] == 0
    assert a['no_snap'] == 0


def test_analyze_unambiguous_passive_scenario():
    poll = {'pair': 'PRLUSDT', 'trades': [
        {'id': 'p1', 'price': '100.00', 'side': 'buy', 'created_at': 1699999980},
        {'id': 'p2', 'price': '100.01', 'side': 'sell', 'created_at': 1699999981},
    ]}
    a = analyze(DEPTH, [poll])['PRLUSDT']
    assert a['passive_ok'] == 2
    assert a['aggr_ok'] == 0
    assert a['classified'] == 2
    assert a['passive_pct'] == 100.0
    assert a['verdict'] == 'passive-consistent'


def test_analyze_tick_override():
    # без допуска сделка на 100.05 (d_ask=0.04, d_bid=0.05) вне тика
    poll = {'pair': 'PRLUSDT', 'trades': [
        {'id': 'x1', 'price': '100.05', 'side': 'buy', 'created_at': 1699999980},
    ]}
    a_def = analyze(DEPTH, [poll])['PRLUSDT']
    assert a_def['indet'] == 1 and a_def['aggr_ok'] == 0
    # с увеличенным tick: ближайший ask (0.04 < 0.05), buy => aggr
    a_wide = analyze(DEPTH, [poll], ticks={'PRLUSDT': Decimal('0.1')})['PRLUSDT']
    assert a_wide['aggr_ok'] == 1 and a_wide['indet'] == 0 and a_wide['classified'] == 1


def test_analyze_max_age_param():
    # сделка через 40с после снимка: default max_age 30 => no_snap
    poll = {'pair': 'PRLUSDT', 'trades': [
        {'id': 'm1', 'price': '100.01', 'side': 'buy', 'created_at': 1700000020},
    ]}
    a_def = analyze(DEPTH, [poll])['PRLUSDT']
    assert a_def['no_snap'] == 1 and a_def['aggr_ok'] == 0
    # с max_age=60 та же сделка классифицируется (ask, buy => aggr)
    a_wide = analyze(DEPTH, [poll], max_age_s=60.0)['PRLUSDT']
    assert a_wide['no_snap'] == 0 and a_wide['aggr_ok'] == 1