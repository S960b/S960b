"""Единая схема событий (ТЗ п.5): время получения фиксируется ДО разбора JSON."""
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

SCHEMA_VERSION = 1


def monotonic_ns() -> int:
    return time.monotonic_ns()


def utc_ns() -> int:
    return time.time_ns()


def make_event(
    exchange: str,
    canonical_symbol: str,
    native_symbol: str,
    event_type: str,          # bbo | book_snapshot | book_delta | ticker | trade | health
    recv_utc: Optional[int] = None,
    recv_mono: Optional[int] = None,
    exchange_event_ts: Optional[int] = None,
    sequence: Optional[int] = None,
    side: Optional[str] = None,
    price: Optional[str] = None,
    qty: Optional[str] = None,
    bids: Optional[list] = None,
    asks: Optional[list] = None,
    trade_id: Optional[str] = None,
    trade_side: Optional[str] = None,
    quality_flags: Optional[list] = None,
    payload: Optional[dict] = None,
    boot_id: str = "boot",
    run_id: str = "run",
    raw_ref: str = "",
) -> dict:
    """Единый dict-формат события. price/qty — decimal string (ТЗ п.5).

    bids/asks — списки [price_str, qty_str] для snapshot; для delta qty="0" = удаление уровня.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "boot_id": boot_id,
        "exchange": exchange,
        "canonical_symbol": canonical_symbol,
        "native_symbol": native_symbol,
        "market_type": "spot",
        "event_type": event_type,
        "exchange_event_ts": exchange_event_ts,
        "recv_utc_ns": recv_utc if recv_utc is not None else utc_ns(),
        "recv_monotonic_ns": recv_mono if recv_mono is not None else monotonic_ns(),
        "parse_done_monotonic_ns": monotonic_ns(),
        "sequence": sequence,
        "side": side,
        "price": price,
        "qty": qty,
        "bids": bids,
        "asks": asks,
        "trade_id": trade_id,
        "trade_side": trade_side,
        "quality_flags": quality_flags or [],
        "raw_ref": raw_ref,
        "payload": payload,
    }


def new_run_id(prefix: str = "st") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def new_boot_id() -> str:
    return uuid.uuid4().hex[:12]