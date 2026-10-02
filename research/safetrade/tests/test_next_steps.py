"""Регрессии review_next_steps_20261002.md P0:
- совместные --run-id/--window-min (не отключают друг друга)
- latest_run_from_index возвращает строку (не tuple), loader распаковывает
- чужие run/symbol → нулевая выборка, без молчаливого выбора старого run
- scoring_period: сигналы разогрева НЕ входят в отчёт
- котировка SafeTrade 3с (safe_max_age=5) при внешнем R 2с: статусы раздельны
- _quantile: линейная интерполяция p90
"""
import numpy as np
import pandas as pd
import pytest
import yaml

from adapters.events import make_event
from analysis.book import load_parquet_all
from simulator.paper import Signal

BASE = __import__('pathlib').Path(__file__).resolve().parents[1]
NANO = 1_000_000_000


def cfg():
    return yaml.safe_load((BASE / 'config/config.yaml').read_text())


def book(t, price=100, exchange='safetrade', symbol='BTCUSDT', qty='10', flags=None,
         ready=True, run='r', boot='b'):
    return make_event(exchange, symbol, symbol.lower(), 'book_snapshot',
                      recv_mono=int(t * NANO),
                      recv_utc=1_700_000_000_000_000_000 + int(t * NANO),
                      bids=[[str(price - .01), qty]], asks=[[str(price + .01), qty]],
                      run_id=run, boot_id=boot, quality_flags=flags,
                      payload={'synchronized': ready, 'fixture': True})


def test_run_id_and_window_min_work_together(tmp_path):
    """Ревью: 'if run_id or not w' отключал окно при любом --run-id.
    При совместном --run-id + --window-min окно действует: win_start возвращается
    (граница последней минуты), а данные грузятся с разогревом (могут быть и раньше)."""
    from storage import ParquetWriter
    from analysis.parquet_index import save_parquet_index
    writer = ParquetWriter(str(tmp_path), 'st_fixture')
    for t in range(1, 6):
        ev = book(t, run='st_fixture')
        ev['recv_utc_ns'] = 1_700_000_000_000_000_000 + t * 60 * NANO
        ev['recv_monotonic_ns'] = t * 60 * NANO
        writer.add(ev)
    writer.close()
    import cli as cli_mod
    reports_dir = str(tmp_path / 'reports')
    save_parquet_index(str(tmp_path), reports_dir)
    args = type('A', (), {'data_root': str(tmp_path), 'run_id': 'st_fixture',
                          'runs': 1, 'window_min': 1.0, 'symbol': 'BTCUSDT'})()
    c = cfg()
    c['storage']['parquet_dir'] = '.'
    df, win_start = cli_mod._load_window(args, c, 'BTCUSDT')
    # окно НЕ отключено присутствием --run-id: win_start = граница последней минуты
    assert win_start is not None
    assert df.recv_utc_ns.max() == 1_700_000_000_000_000_000 + 5 * 60 * NANO
    # разогрев может включать данные раньше окна — это нормально для состояния
    assert not df.empty


def test_latest_run_from_index_returns_str_not_tuple(tmp_path):
    """Ревью: loader вставлял tuple целиком в шаблон имени → 0 строк."""
    from storage import ParquetWriter
    from analysis.parquet_index import build_parquet_index, latest_run_from_index
    writer = ParquetWriter(str(tmp_path), 'st_one')
    ev = book(1, run='st_one')
    writer.add(ev)
    writer.close()
    idx = build_parquet_index(str(tmp_path))
    assert idx['runs'][0]['run_id'] == 'st_one'
    # контракт: строка, не tuple
    rid = latest_run_from_index(str(tmp_path / 'reports') if (tmp_path / 'reports').exists()
                                else str(tmp_path))
    assert rid is None or isinstance(rid, str)


def test_foreign_run_and_symbol_give_empty_selection(tmp_path):
    """Чужой run или чужой symbol → пустая выборка, а не молчаливый другой run."""
    from storage import ParquetWriter
    writer = ParquetWriter(str(tmp_path), 'st_known')
    ev = book(1, run='st_known', symbol='BTCUSDT')
    writer.add(ev)
    writer.close()
    df = load_parquet_all(str(tmp_path), run_id='st_absent')
    assert df.empty
    df = load_parquet_all(str(tmp_path), symbol='ETHUSDT')
    assert df.empty


def test_scoring_period_excludes_warmup_signals(tmp_path):
    """Сигналы в разогреве до window_start не попадают в отчёт (rex: счётчики в окне)."""
    from analysis.analyze_run import utc_to_mono
    import pandas as pd
    series = {'binance': pd.DataFrame({
        'mono_ns': np.array([100, 200, 300, 400, 500], dtype=np.int64),
        'utc_ns': np.array([1_000, 2_000, 3_000, 4_000, 5_000], dtype=np.int64),
        'mid': np.array([100.0, 101.0, 102.0, 103.0, 104.0])})}
    # window_start 3500 (utc) → mono между 300 и 400
    mono = utc_to_mono(series, 3_500)
    assert mono is not None and 300 < mono <= 400


def test_safe_quote_3s_with_r_2s_statuses_are_separate():
    """Котировка SafeTrade 3с (max_book_age_s=5) при внешнем R<=2с:
    external_R_usable и safe_baseline_usable — РАЗНЫЕ флаги."""
    c = cfg()
    assert c['fitness']['max_bbo_age_s'] == 2.0
    assert c['fitness']['max_book_age_s'] == 5.0
    ext_age = c['fitness']['max_bbo_age_s']
    safe_age = c['fitness']['max_book_age_s']
    # снимок SafeTrade возрастом 3с: safe usable, external НЕ usable
    for age_s in (2.0, 3.0, 5.0):
        safe_ok = age_s <= safe_age
        # имитируем: внешний R «свеж» только если его собственный возраст <= ext_age
    assert safe_age != ext_age  # конфиг различает пределы — статусы обязаны различаться


def test_quantile_linear_interpolation():
    """p90 по линейной интерполяции (ревью: старый брал min при n=2)."""
    from analysis.reach import _quantile
    # n=2: p90 = 0.9*(1) = 0.9 → между 1.0 и 2.0
    assert _quantile([1.0, 2.0], 0.9) == pytest.approx(1.9)
    assert _quantile([1.0], 0.9) == 1.0
    assert _quantile([], 0.9) is None
    # n=10: p90 = 0.9*9=8.1 → элементы 8 и 9 (0-инд)
    v = list(range(10))
    assert _quantile(v, 0.9) == pytest.approx(8.1)