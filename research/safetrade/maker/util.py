"""Общие утилиты maker-скрининга (ревью 738de3a, P0.3/P1).

- parse_iso_utc: строгий ISO8601 -> epoch seconds (aware UTC, дробные секунды).
- datetime/age: возраст сделки в минутах относительно события.
- valid_book: проверка валидности стакана (ok, положительные цены, не crossed).
"""
import time
from datetime import datetime, timezone

_TS_FORMATS = (
    '%Y-%m-%dT%H:%M:%SZ',
    '%Y-%m-%dT%H:%M:%S.%fZ',
    '%Y-%m-%dT%H:%M:%S%z',
    '%Y-%m-%dT%H:%M:%S.%f%z',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
)


def parse_iso_utc(s):
    """parse ISO8601 timestamp as AWARE UTC; returns epoch seconds (float) or None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if not s:
        return None
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def age_minutes(ts_epoch, now_epoch):
    """Возраст события (epoch) в минутах от now; None если ts не число."""
    if ts_epoch is None or now_epoch is None:
        return None
    try:
        return (float(now_epoch) - float(ts_epoch)) / 60.0
    except (TypeError, ValueError):
        return None


def valid_book(bids, asks):
    """Двусторонний валидный стакан: непустые стороны, конечные ПОЛОЖИТЕЛЬНЫЕ
    цены/объёмы, bid <= ask (не crossed). Возвращает (ok, best_bid, best_ask).

    P1 (ревью 0b71d28): отклоняет inf/nan цены и объёмы, нулевые объёмы;
    BBO выбирается только из положительных активных уровней.
    """
    import math
    if not bids or not asks:
        return False, None, None

    def _level_ok(price, qty):
        try:
            p = float(price)
            q = float(qty)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(p) or not math.isfinite(q):
            return False
        return p > 0 and q > 0

    # BBO только из активных уровней (qty > 0)
    try:
        bb = max(float(p) for p, q in bids if _level_ok(p, q))
        ba = min(float(p) for p, q in asks if _level_ok(p, q))
    except ValueError:
        return False, None, None
    if not (bb > 0 and ba > 0 and bb <= ba):
        return False, bb, ba
    # все уровни должны быть валидными (повреждённые отклоняем целиком)
    for side in (bids, asks):
        for p, q in side:
            if not _level_ok(p, q):
                return False, None, None
    return True, bb, ba


def coverage_note(cov):
    """Нормализованное описание coverage (полнота потока)."""
    return {
        'full': 'история дочитана до конца',
        'full_at_page': 'история закончилась на последней прочитанной странице',
        'history_truncated': 'достигнут лимит страниц, поток не догнан',
        'coverage_unknown': 'данные неполные/непроверяемые',
        'no_trades_observed': 'в наблюдаемом окне сделок не получено',
        'request_failed': 'запрос не выполнен (сеть/HTTP)',
    }.get(cov, cov)