#!/usr/bin/env python3
"""NEWS v2.6: instrument-aware PAPER market adapter for BTC/ETH/SOL.

Additive stage: no network or model calls on import, no DB writes or trades.
Uses the same public Binance spot endpoints and audited Tier-2 validators.
Legacy v2.5 functions and the running series remain unchanged until E2E tests.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request

import tier2 as T2
import v21_paper_prep as P
import v22_arms as A

SYMBOLS = frozenset(("BTCUSDT", "ETHUSDT", "SOLUSDT"))
API = "https://api.binance.com"
MAX_BYTES = 1_000_000
HOUR_MS = 3_600_000
MIN_MS = 60_000
VERSION = "news-v26-multiasset-paper-v1"


class MarketError(ValueError):
    pass


def checked_symbol(symbol):
    if type(symbol) is not str or symbol not in SYMBOLS:
        raise MarketError("symbol_not_allowlisted")
    return symbol


def _get_json(path, *, opener=None):
    if not path.startswith("/api/v3/") or "://" in path:
        raise MarketError("invalid_api_path")
    url = API + path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NEWS-paper-v26/1.0",
                                                   "Accept": "application/json"})
        fn = opener or urllib.request.urlopen
        with fn(req, timeout=15) as response:
            body = response.read(MAX_BYTES + 1)
    except Exception as exc:
        raise MarketError("binance_fetch_failed:" + type(exc).__name__) from None
    if len(body) > MAX_BYTES:
        raise MarketError("binance_response_too_large")
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise MarketError("binance_invalid_json") from None


def _candles(payload, *, interval_ms):
    if type(payload) is not list or not 0 < len(payload) <= 1000:
        raise MarketError("invalid_kline_payload")
    parsed = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 7:
            raise MarketError("invalid_kline_row")
        try:
            start, end = int(row[0]), int(row[6])
            price = float(row[4])
        except (ValueError, TypeError, OverflowError):
            raise MarketError("invalid_kline_fields") from None
        if (start <= 0 or end - start != interval_ms - 1
                or start % interval_ms != 0
                or not math.isfinite(price) or price <= 0):
            raise MarketError("invalid_kline_bounds_or_price")
        parsed.append((start, end, price))
    if len({x[0] for x in parsed}) != len(parsed):
        raise MarketError("duplicate_kline_open")
    return parsed


def hourly_market(symbol, cutoff_ms, *, opener=None):
    """Exactly the legacy 26 fully closed 1h features, for selected instrument."""
    symbol = checked_symbol(symbol)
    if type(cutoff_ms) is not int or cutoff_ms <= 0 or cutoff_ms % HOUR_MS:
        raise MarketError("invalid_market_cutoff")
    data = _get_json("/api/v3/klines?" + urllib.parse.urlencode(
        {"symbol": symbol, "interval": "1h", "limit": 30}), opener=opener)
    candles = _candles(data, interval_ms=HOUR_MS)
    # P.market_features itself checks continuity, >=26 closed hours, and
    # that the latest candle is recent and strictly pre-cutoff.
    features = P.market_features(candles, cutoff_ms, require_cutoff_on_hour=True)
    return {"symbol": symbol, "features": features, "cutoff_ms": cutoff_ms}


def quote_after(symbol, *, after_ms, opener=None, clock_ms=None):
    """Sample public spot price AFTER all three model arms, never before."""
    symbol = checked_symbol(symbol)
    if type(after_ms) is not int or after_ms <= 0:
        raise MarketError("invalid_last_arm_ms")
    data = _get_json("/api/v3/ticker/price?" + urllib.parse.urlencode(
        {"symbol": symbol}), opener=opener)
    if type(data) is not dict or data.get("symbol") != symbol:
        raise MarketError("quote_symbol_mismatch")
    try:
        price = float(data["price"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise MarketError("quote_price_invalid") from None
    observed_ms = time.time_ns() // 1_000_000 if clock_ms is None else clock_ms()
    quote = {"symbol": symbol, "source": "binance_public_ticker",
             "price": price, "observed_ms": observed_ms}
    T2.validate_quote(quote, after_ms, now_ms=observed_ms, symbol=symbol)
    return quote


def exit_candle(symbol, *, due_ms, now_ms, opener=None):
    """First genuinely closed minute containing the exact entry+4h due time."""
    symbol = checked_symbol(symbol)
    if type(due_ms) is not int or type(now_ms) is not int or due_ms <= 0:
        raise MarketError("invalid_exit_timestamp")
    if now_ms < due_ms:
        raise MarketError("label_not_due")
    start_ms = (due_ms // MIN_MS) * MIN_MS
    if start_ms + MIN_MS - 1 >= now_ms:
        raise MarketError("label_pending_candle_open")
    data = _get_json("/api/v3/klines?" + urllib.parse.urlencode(
        {"symbol": symbol, "interval": "1m",
         "startTime": start_ms, "limit": 5}), opener=opener)
    candles = sorted(_candles(data, interval_ms=MIN_MS))
    for start, close, price in candles:
        if close < due_ms:
            continue
        if start > due_ms or close >= now_ms:
            raise MarketError("label_candle_unavailable")
        candle = {"symbol": symbol, "source": "binance_public_1m_kline",
                  "open_ms": start, "close_ms": close,
                  "close": price, "closed": True}
        T2.validate_candle(candle, due_ms, symbol=symbol)
        return candle
    raise MarketError("label_candle_unavailable")


def _replace_header(text, symbol):
    header, sep, body = text.partition("\nARM:")
    if not sep:
        raise MarketError("prompt_arm_delimiter_absent")
    if "BTCUSDT" not in header:
        raise MarketError("prompt_header_missing_baseline_symbol")
    # Only trusted header is rewritten. Never substitute inside news text.
    return header.replace("BTCUSDT", symbol) + sep + body


def system_prompt(symbol):
    symbol = checked_symbol(symbol)
    baseline = T2.ARM_SYSTEM_T2
    if "BTCUSDT" not in baseline:
        raise MarketError("system_prompt_missing_baseline_symbol")
    return baseline.replace("BTCUSDT", symbol)


def symbol_prompts(symbol, event, market_features, *, max_abs_bps=10000):
    """Rebuild all 3 arms with a single instrument; preserve untrusted news."""
    symbol = checked_symbol(symbol)
    prompts = A.build_prompts_3(event, market_features, max_abs_bps=max_abs_bps)
    converted = {}
    for arm in A.ARM_NAMES:
        with_symbol = _replace_header(prompts[arm], symbol)
        prefix, separator, payload = with_symbol.partition("FROZEN_INPUT_JSON:\n")
        if not separator:
            raise MarketError("prompt_json_missing")
        obj = json.loads(payload)
        if obj.get("symbol") != "BTCUSDT":
            raise MarketError("baseline_symbol_mismatch")
        obj["symbol"] = symbol
        converted[arm] = (prefix + separator + json.dumps(
            obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    if any(json.loads(p.split("FROZEN_INPUT_JSON:\n", 1)[1])["symbol"] != symbol
           for p in converted.values()):
        raise MarketError("cross_asset_prompt")
    A.audit_market_arm_isolation(converted, {
        arm: A.execution_metadata(arm, event_ms=market_features["candle_closes_ms"],
                                  model_id="deepseek-v4-flash")
        for arm in A.ARM_NAMES}, event)
    return converted


class _Response:
    def __init__(self, obj):
        self.data = json.dumps(obj).encode()
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, _limit=-1):
        return self.data


def selftest():
    from unittest.mock import Mock

    assert tuple(sorted(SYMBOLS)) == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    for malformed in ("BTC", "XRPUSDT", "SOLUSDT&interval=1d", "", None, 12):
        try:
            checked_symbol(malformed)
            raise AssertionError("bad symbol accepted")
        except MarketError:
            pass

    cutoff = 1_800_000_000_000
    assert cutoff % HOUR_MS == 0
    klines = []
    for i in range(30):
        start = cutoff - (30-i)*HOUR_MS
        klines.append([start, "1", "1", "1", str(100+i), "1",
                       start+HOUR_MS-1])
    def open_hour(request, timeout):
        assert request.full_url.startswith(API + "/api/v3/klines?")
        assert "symbol=ETHUSDT" in request.full_url
        return _Response(klines)
    market = hourly_market("ETHUSDT", cutoff, opener=open_hour)
    assert market["symbol"] == "ETHUSDT"
    assert market["features"]["candle_count"] == 26
    assert market["features"]["candle_closes_ms"] < cutoff

    seen_quote = []
    def open_quote(request, timeout):
        seen_quote.append(request.full_url)
        return _Response({"symbol":"SOLUSDT", "price":"150.25"})
    quote = quote_after("SOLUSDT", after_ms=cutoff, opener=open_quote,
                        clock_ms=lambda: cutoff+1000)
    assert quote["symbol"] == "SOLUSDT" and quote["price"] == 150.25
    assert "symbol=SOLUSDT" in seen_quote[0]
    try:
        quote_after("BTCUSDT", after_ms=cutoff, opener=open_quote,
                    clock_ms=lambda: cutoff+1000)
        raise AssertionError("wrong symbol quote accepted")
    except MarketError:
        pass
    try:
        quote_after("SOLUSDT", after_ms=cutoff+2000, opener=open_quote,
                    clock_ms=lambda: cutoff+1000)
        raise AssertionError("pre-response quote accepted")
    except T2.Tier2Error:
        pass

    due = cutoff + 4*HOUR_MS + 10_000
    minute = (due // MIN_MS)*MIN_MS
    def open_minute(request, timeout):
        assert "symbol=BTCUSDT" in request.full_url
        return _Response([[minute, "1", "1", "1", "25000", "1",
                           minute+MIN_MS-1]])
    candle = exit_candle("BTCUSDT", due_ms=due, now_ms=minute+MIN_MS+100,
                         opener=open_minute)
    assert candle["close_ms"] >= due and candle["symbol"] == "BTCUSDT"

    event = {"event_id": "e"*64, "cohort": "crypto", "title": "BTCUSDT headline untouched",
             "source": "test_source", "source_kind": "media",
             "published_ms": cutoff-HOUR_MS, "observed_ms": cutoff-HOUR_MS}
    p = symbol_prompts("ETHUSDT", event, market["features"])
    for arm in A.ARM_NAMES:
        hdr = p[arm].split("\nARM:", 1)[0]
        assert "ETHUSDT" in hdr and "BTCUSDT" not in hdr
        payload = json.loads(p[arm].split("FROZEN_INPUT_JSON:\n", 1)[1])
        assert payload["symbol"] == "ETHUSDT"
    assert "BTCUSDT headline untouched" in p[A.ARM_NEWS]
    assert "BTCUSDT headline untouched" not in p[A.ARM_MARKET]
    assert "SOLUSDT" in system_prompt("SOLUSDT")

    print("NEWS V26 MULTI-SYMBOL MARKET SELFTEST OK: 3 symbols, 1h, 1m, quote, arms")

if __name__ == "__main__":
    selftest()
