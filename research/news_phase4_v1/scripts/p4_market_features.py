"""Pure BTCUSDT hourly OHLCV feature builder for Phase 4.

Input: Binance /api/v3/klines interval=1h, already retrieved by a caller.
No network, no filesystem, no external imports; only fully closed pre-cutoff candles.
"""
import hashlib
import json
import math
import statistics

HOUR_MS = 3_600_000
MIN_CLOSED = 26
MARKET_FEATURE_VERSION = 'p4-market-1h-v1'

class MarketFeatureError(ValueError):
    pass


def _is_int(x):
    return type(x) is int and x >= 0


def _number(x, positive=True):
    try:
        v = float(x)
    except (ValueError, TypeError, OverflowError):
        raise MarketFeatureError('invalid_numeric_value') from None
    if not math.isfinite(v) or (positive and v <= 0) or (not positive and v < 0):
        raise MarketFeatureError('invalid_numeric_value')
    return v


def _read(row):
    if not isinstance(row, (list, tuple)) or len(row) < 7:
        raise MarketFeatureError('invalid_kline_shape')
    try:
        opening = int(row[0]) if type(row[0]) is not bool else -1
        closing = int(row[6]) if type(row[6]) is not bool else -1
    except (ValueError, TypeError, OverflowError):
        raise MarketFeatureError('invalid_candle_clock') from None
    if not _is_int(opening) or not _is_int(closing):
        raise MarketFeatureError('invalid_candle_clock')
    if opening % HOUR_MS or closing != opening + HOUR_MS - 1:
        raise MarketFeatureError('invalid_candle_boundaries')
    price, volume = _number(row[4]), _number(row[5], positive=False)
    return (opening, closing, price, volume)


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))


def closed_precutoff_features(candles, cutoff_ms, skew_ms=3000):
    """Return immutable feature digest/values or raise; never forward-fill missing rows.

    The caller MUST capture observed_at and verify Binance serverTime drift.
    This function excludes every incomplete/near-cutoff candle, then requires
    26 contiguous, complete 1h candles. No 24h backfill after cutoff.
    """
    if not _is_int(cutoff_ms) or not _is_int(skew_ms) or skew_ms > 60_000:
        raise MarketFeatureError('invalid_cutoff')
    if not isinstance(candles, (list, tuple)) or len(candles) > 1000:
        raise MarketFeatureError('invalid_input_size')
    parsed = [_read(row) for row in candles]
    if len({r[0] for r in parsed}) != len(parsed):
        raise MarketFeatureError('duplicate_candle')
    closed = sorted((r for r in parsed if r[1] + skew_ms <= cutoff_ms), key=lambda r: r[0])
    if len(closed) < MIN_CLOSED:
        raise MarketFeatureError('insufficient_closed_candles')
    sample = closed[-MIN_CLOSED:]
    if any(b[0] - a[0] != HOUR_MS for a, b in zip(sample, sample[1:])):
        raise MarketFeatureError('gapped_candles')
    # Refuse stale market data; a cut-off must be close to the last completed hour.
    if cutoff_ms - (sample[-1][1] + skew_ms) > HOUR_MS + skew_ms:
        raise MarketFeatureError('stale_candles')
    closes = [r[2] for r in sample]
    volumes = [r[3] for r in sample]
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    trailing = volumes[-25:-1]  # previous 24 hourly bars, excluding current
    mean_vol = statistics.mean(trailing)
    vol_std = statistics.pstdev(trailing)
    recent4 = statistics.mean(volumes[-4:])
    prev4 = statistics.mean(volumes[-8:-4])
    values = {
        'return_1h': closes[-1] / closes[-2] - 1.0,
        'return_4h': closes[-1] / closes[-5] - 1.0,
        'return_24h': closes[-1] / closes[-25] - 1.0,
        'realized_vol_4h': statistics.pstdev(returns[-4:]),
        'realized_vol_24h': statistics.pstdev(returns[-24:]),
        'volume_ratio_1h_to_prev24h': volumes[-1] / mean_vol if mean_vol else None,
        'volume_z_1h': (volumes[-1] - mean_vol) / vol_std if vol_std else None,
        'volume_change_4h': recent4 / prev4 - 1.0 if prev4 else None,
    }
    if any(v is not None and not math.isfinite(v) for v in values.values()):
        raise MarketFeatureError('nonfinite_feature')
    evidence = {'version': MARKET_FEATURE_VERSION, 'symbol': 'BTCUSDT',
                'interval': '1h', 'closed_candles': sample}
    payload = _canonical(evidence).encode('utf-8')
    return {'available_ms': sample[-1][1] + skew_ms,
            'cutoff_ms': cutoff_ms, 'digest': hashlib.sha256(payload).hexdigest(),
            'values': values, 'candle_count': len(sample),
            'last_closed_open_ms': sample[-1][0],
            'last_closed_close_ms': sample[-1][1],
            'evidence_bytes': len(payload), 'feature_version': MARKET_FEATURE_VERSION}

