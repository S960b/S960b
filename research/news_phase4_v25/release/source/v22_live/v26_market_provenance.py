#!/usr/bin/env python3
"""NEWS v2.6: immutable local evidence for public Binance PAPER observations.

This is an additive OFFLINE-TESTABLE adapter. Nothing is activated or deployed.
Binance klines omit the trading symbol in their JSON rows, so the recorded
request URL is the only local binding to the requested ticker. A response hash
proves local byte integrity, NOT exchange-authenticated asset identity.
No trading endpoints, API keys, model calls or automatic retries.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import v26_symbol_market as M

SCHEMA = "news_v26_binance_request_witness_v1"
ALLOWED_OPERATIONS = frozenset(("hourly", "entry", "exit"))


class WitnessError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(obj):
    return (json.dumps(obj, sort_keys=True, ensure_ascii=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def validate_request(url, *, symbol, operation):
    """Prove the recorded URL is the one selected BEFORE network access."""
    M.checked_symbol(symbol)
    if operation not in ALLOWED_OPERATIONS:
        raise WitnessError("invalid_operation")
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or p.netloc != "api.binance.com"
            or p.username or p.password or p.fragment):
        raise WitnessError("invalid_exchange_url")
    pairs = urllib.parse.parse_qsl(p.query, keep_blank_values=True,
                                  strict_parsing=True)
    params = dict(pairs)
    if len(params) != len(pairs) or params.get("symbol") != symbol:
        raise WitnessError("wrong_or_duplicate_symbol_param")
    if operation == "entry":
        if p.path != "/api/v3/ticker/price" or set(params) != {"symbol"}:
            raise WitnessError("invalid_entry_query")
    else:
        if p.path != "/api/v3/klines":
            raise WitnessError("invalid_kline_path")
        expected_interval = "1h" if operation == "hourly" else "1m"
        expected_keys = ({"symbol", "interval", "limit"} if operation == "hourly"
                         else {"symbol", "interval", "startTime", "limit"})
        if set(params) != expected_keys or params["interval"] != expected_interval:
            raise WitnessError("invalid_kline_query")
        if params["limit"] != ("30" if operation == "hourly" else "5"):
            raise WitnessError("invalid_kline_limit")
        if operation == "exit":
            if not params["startTime"].isdigit():
                raise WitnessError("invalid_start_time")
    return True


def _write_once(directory, filename, body):
    """Content addressed, never overwrites, rejects symlink destinations."""
    directory = Path(directory)
    if directory.is_symlink():
        raise WitnessError("evidence_directory_symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise WitnessError("evidence_directory_symlink")
    dest = directory / filename
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(dest), flags, 0o600)
    except FileExistsError:
        if dest.is_symlink() or not dest.is_file() or dest.read_bytes() != body:
            raise WitnessError("evidence_collision")
        return str(dest)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    return str(dest)


def observe(operation, symbol, *, cutoff_ms=None, after_ms=None,
            due_ms=None, now_ms=None, opener=None, evidence_dir=None,
            clock=None):
    """One public GET. Return (validated market value, exact local witness).

    A mock opener and clock make offline tests deterministic. Real callers must
    use a private evidence_dir if durable raw reconstruction is required.
    """
    M.checked_symbol(symbol)
    if operation not in ALLOWED_OPERATIONS:
        raise WitnessError("invalid_operation")
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    captured = []
    started_ms = clock()

    def tracing_opener(request, timeout):
        if captured:
            raise WitnessError("multiple_requests_in_one_observation")
        url = request.full_url
        validate_request(url, symbol=symbol, operation=operation)
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as resp:
            observed_status = getattr(resp, "status", 200)
            actual_url = resp.geturl() if hasattr(resp, "geturl") else url
            if observed_status != 200 or actual_url != url:
                raise WitnessError("unexpected_status_or_redirect")
            raw = resp.read(M.MAX_BYTES + 1)
        captured.append({"url": url, "actual_url": actual_url, "status": observed_status,
                         "raw": raw, "request_started_ms": started_ms,
                         "response_received_ms": clock()})
        return io.BytesIO(raw)

    if operation == "hourly":
        result = M.hourly_market(symbol, cutoff_ms, opener=tracing_opener)
    elif operation == "entry":
        result = M.quote_after(symbol, after_ms=after_ms, opener=tracing_opener,
                               clock_ms=clock)
    else:
        result = M.exit_candle(symbol, due_ms=due_ms, now_ms=now_ms,
                               opener=tracing_opener)
    if len(captured) != 1:
        raise WitnessError("missing_http_capture")
    item = captured[0]
    raw = item["raw"]
    if len(raw) > M.MAX_BYTES:
        raise WitnessError("excessive_response")
    manifest = {
        "schema": SCHEMA, "operation": operation, "symbol": symbol,
        "request_url": item["url"], "final_url": item["actual_url"],
        "status": item["status"], "request_started_ms": item["request_started_ms"],
        "response_received_ms": item["response_received_ms"],
        "raw_sha256": sha(raw), "raw_bytes": len(raw),
        "result_sha256": sha(canonical(result))
    }
    if evidence_dir is not None:
        # Raw payload kept alongside manifest for exact replay, not just hash.
        raw_path = _write_once(evidence_dir, sha(raw) + ".bin", raw)
        manifest["raw_path"] = raw_path
        payload = canonical(manifest)
        manifest_path = _write_once(evidence_dir, sha(payload) + ".json", payload)
        manifest["manifest_path"] = manifest_path
    return result, manifest


def verify_manifest(manifest, raw):
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise WitnessError("invalid_manifest_schema")
    validate_request(manifest.get("request_url", ""), symbol=manifest["symbol"],
                     operation=manifest["operation"])
    if (manifest.get("status") != 200
            or manifest["request_url"] != manifest.get("final_url")
            or type(raw) is not bytes or len(raw) != manifest.get("raw_bytes")
            or sha(raw) != manifest.get("raw_sha256")):
        raise WitnessError("witness_integrity_mismatch")
    return True


def selftest():
    import tempfile
    cutoff = 1_800_000_000_000
    assert cutoff % M.HOUR_MS == 0
    clock_now = cutoff + 5000
    klines = []
    for i in range(30):
        start = cutoff - (30-i)*M.HOUR_MS
        klines.append([start, "1", "1", "1", str(100+i), "1",
                       start+M.HOUR_MS-1])

    class Fake:
        def __init__(self, payload, req):
            self.data = json.dumps(payload).encode("utf-8")
            self.url = req.full_url
            self.status = 200
        def geturl(self):
            return self.url
        def read(self, limit=-1):
            return self.data[:limit] if limit >= 0 else self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    requests = []
    def hour_opener(req, timeout):
        requests.append(req.full_url)
        return Fake(klines, req)
    with tempfile.TemporaryDirectory() as tmp:
        market, proof = observe("hourly", "ETHUSDT", cutoff_ms=cutoff,
                                opener=hour_opener, clock=lambda: clock_now,
                                evidence_dir=tmp)
        assert market["symbol"] == proof["symbol"] == "ETHUSDT"
        assert market["features"]["candle_count"] == 26
        assert proof["request_url"] == requests[0]
        assert verify_manifest(proof, Path(proof["raw_path"]).read_bytes())
        assert Path(proof["manifest_path"]).exists()
        try:
            verify_manifest(proof, b"tampered")
            raise AssertionError("tampered raw accepted")
        except WitnessError:
            pass

        q, qp = observe("entry", "SOLUSDT", after_ms=cutoff,
                        opener=lambda req, timeout: Fake(
                            {"symbol":"SOLUSDT","price":"120.25"}, req),
                        clock=lambda: clock_now, evidence_dir=tmp)
        assert q["symbol"] == qp["symbol"] == "SOLUSDT"
        assert verify_manifest(qp, Path(qp["raw_path"]).read_bytes())

        due = cutoff + 4*M.HOUR_MS + 10_000
        minute = due // M.MIN_MS * M.MIN_MS
        candle, xp = observe("exit", "BTCUSDT", due_ms=due,
                             now_ms=minute+M.MIN_MS+100,
                             opener=lambda req, timeout: Fake(
                                 [[minute,"1","1","1","30000","1",
                                   minute+M.MIN_MS-1]], req),
                             clock=lambda: clock_now, evidence_dir=tmp)
        assert candle["symbol"] == xp["symbol"] == "BTCUSDT"
        assert verify_manifest(xp, Path(xp["raw_path"]).read_bytes())
        assert len(list(Path(tmp).glob("*.bin"))) == 3

    for invalid in (
        "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&symbol=SOLUSDT&interval=1m&startTime=10&limit=5",
        "https://evil.example/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime=10&limit=5",
        "https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=1m&startTime=10&limit=5",
    ):
        try:
            validate_request(invalid, symbol="BTCUSDT", operation="exit")
            raise AssertionError("unsafe URL accepted")
        except WitnessError:
            pass

    print("NEWS V26 BINANCE WITNESS SELFTEST OK: URL, raw SHA, replay, 3 symbols")


if __name__ == "__main__":
    selftest()
