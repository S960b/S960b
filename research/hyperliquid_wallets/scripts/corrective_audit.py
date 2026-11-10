#!/usr/bin/env python3
"""Corrective OFFLINE audit of the already-saved Hyperliquid fills.

IMPORTANT: reads ONLY /home/kali/research/hyperliquid-public-wallet-audit-v1/raw_fills.json
(sha256-verified: 7512e9e3426d7080096a0ee83e0821dde0a1f74df7260dc4d4971accde6d302c).
NO network / NO new API requests. NO rewriting of prior artifacts.

Fee convention (CORRECTED per ChatGPT + sqd.dev + HL docs):
  fee > 0  = COST charged (taker usually, but not guaranteed by crossed)
  fee < 0  = MAKER REBATE / credit
  crossed=true  typically taker -> fee >= 0 (cost)
  crossed=false typically maker -> fee <= 0 (credit), BUT not guaranteed:
              if crossed=false and fee>0 it is STILL a cost, not a rebate.
  So we NEVER assume sign from crossed; we take the actual fee sign.

fill-only net formula:  net_fill_only = sum(closedPnl) - sum(fee)
  (per sqd.dev: net_realized = sum(closedPnl) - sum(fee))
"""
import json
import hashlib
from collections import defaultdict
from decimal import Decimal, getcontext

getcontext().prec = 40

RAW = "/home/kali/research/hyperliquid-public-wallet-audit-v1/raw_fills.json"
OUT = "corrective_offline_audit.json"

WINDOW_START = 1793664000000  # 2026-11-03T00:00:00Z
WINDOW_END = 1794268799999    # 2026-11-09T23:59:59.999Z


def D(x):
    if x is None:
        return Decimal("0")
    return Decimal(str(x))


def rnd(x, n=6):
    return x.quantize(Decimal(1).scaleb(-n)) if x is not None else None


def iso(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def verify(raw_bytes):
    return hashlib.sha256(raw_bytes).hexdigest()


def main():
    with open(RAW, "rb") as f:
        raw_bytes = f.read()
    sha = verify(raw_bytes)

    report = {
        "_disclaimer": "OFFLINE corrective audit. No network requests made.",
        "source_raw_fills_json_sha256": sha,
        "source_raw_fills_json_expected_sha256": "7512e9e3426d7080096a0ee83e0821dde0a1f74df7260dc4d4971accde6d302c",
        "fe": {
            "convention": "fee>0 = COST charged; fee<0 = MAKER REBATE/credit. net_fill_only = sum(closedPnl) - sum(fee). We never infer sign from crossed.",
            "crossed_is_not_guarantee": True,
        },
        "window": {"start_ms": WINDOW_START, "end_ms": WINDOW_END,
                   "start_iso": "2026-11-03T00:00:00Z", "end_iso": "2026-11-09T23:59:59.999Z"},
        "query_metadata": {"note": "No per-request metadata (intervals/counts/headers) was durably saved in this environment; -> coverage of the extract can be characterised only from the raw payloads themselves. Status UNPROVEN for anything not directly derivable."},
        "wallets": {},
    }

    raw = json.loads(raw_bytes.decode("utf-8"))
    for wk, w in raw.get("wallets", {}).items():
        fills = w["fills"]
        funding = w["funding"]

        # ---- stable sort ----
        fills_sorted = sorted(fills, key=lambda f: (f["time"], f["tid"]))

        # distinct keys
        dkeys = [(f["time"], f["tid"], f["oid"]) for f in fills_sorted]
        distinct_keys = set(dkeys)
        distinct_tid_oid = set((f["tid"], f["oid"]) for f in fills_sorted)
        distinct_oid = set(f["oid"] for f in fills_sorted)

        # query coverage evidence derivable from payload
        ts = [f["time"] for f in fills_sorted]
        tmin, tmax = min(ts), max(ts)

        # ---- fee matrices ----
        fee_pos = [f for f in fills if D(f.get("fee")) > 0]
        fee_neg = [f for f in fills if D(f.get("fee")) < 0]
        fee_zero = [f for f in fills if D(f.get("fee")) == 0]
        sum_fee = sum((D(f.get("fee")) for f in fills), Decimal("0"))
        sum_fee_pos = sum((D(f.get("fee")) for f in fee_pos), Decimal("0"))
        sum_fee_neg = sum((D(f.get("fee")) for f in fee_neg), Decimal("0"))

        crossed_fee = defaultdict(lambda: {"pos": 0, "neg": 0, "zero": 0, "sum": Decimal("0")})
        for f in fills:
            key = "crossed=true" if f.get("crossed") else "crossed=false"
            fee = D(f.get("fee"))
            if fee > 0:
                crossed_fee[key]["pos"] += 1
            elif fee < 0:
                crossed_fee[key]["neg"] += 1
            else:
                crossed_fee[key]["zero"] += 1
            crossed_fee[key]["sum"] += fee
        fee_matrix = {}
        for k, v in crossed_fee.items():
            fee_matrix[k] = {"pos_count": v["pos"], "neg_count": v["neg"],
                             "zero_count": v["zero"], "sum_fee": rnd(v["sum"])}

        # open vs close fees
        opens = [f for f in fills if "Open" in f.get("dir", "")]
        closes = [f for f in fills if "Close" in f.get("dir", "")]
        sum_fee_opens = sum((D(f.get("fee")) for f in opens), Decimal("0"))
        sum_fee_closes = sum((D(f.get("fee")) for f in closes), Decimal("0"))

        # closedPnl
        sum_closedPnl = sum((D(f.get("closedPnl")) for f in fills), Decimal("0"))
        sum_closedPnl_closes = sum((D(f.get("closedPnl")) for f in closes), Decimal("0"))

        # fill-only net: sum(closedPnl) - sum(fee)
        net_fill_only = sum_closedPnl - sum_fee
        net_fill_only_closes = sum_closedPnl_closes - sum_fee

        # by asset adjusted fill-only net
        per_asset = defaultdict(lambda: {"closedPnl": Decimal("0"), "fee": Decimal("0"), "n": 0})
        for f in fills:
            c = f["coin"]
            per_asset[c]["closedPnl"] += D(f.get("closedPnl"))
            per_asset[c]["fee"] += D(f.get("fee"))
            per_asset[c]["n"] += 1
        per_asset_out = {}
        for c, v in sorted(per_asset.items()):
            per_asset_out[c] = {
                "n_fills": v["n"],
                "sum_closedPnl": rnd(v["closedPnl"]),
                "sum_fee": rnd(v["fee"]),
                "net_fill_only": rnd(v["closedPnl"] - v["fee"]),
            }

        # by (coin, position side) -- dir side
        per_coin_side = defaultdict(lambda: {"closedPnl": Decimal("0"), "fee": Decimal("0"), "n": 0})
        for f in fills:
            c = f["coin"]
            side = f.get("dir", "")
            per_coin_side[(c, side)]["closedPnl"] += D(f.get("closedPnl"))
            per_coin_side[(c, side)]["fee"] += D(f.get("fee"))
            per_coin_side[(c, side)]["n"] += 1
        per_coin_side_out = {}
        for (c, side), v in sorted(per_coin_side.items()):
            per_coin_side_out[f"{c} | {side}"] = {
                "n": v["n"], "sum_closedPnl": rnd(v["closedPnl"]),
                "sum_fee": rnd(v["fee"]), "net_fill_only": rnd(v["closedPnl"] - v["fee"])}

        # top closing fills by closedPnl
        def f_compact(f):
            return {
                "time": f["time"], "time_iso": iso(f["time"]), "coin": f["coin"],
                "side": f.get("side"), "dir": f.get("dir"), "px": f.get("px"),
                "sz": f.get("sz"), "startPosition": f.get("startPosition"),
                "closedPnl": f.get("closedPnl"), "fee": f.get("fee"),
                "fee_signed": str(D(f.get("fee"))), "crossed": f.get("crossed"),
                "tid": f["tid"], "oid": f["oid"],
            }

        top5_pos = sorted(closes, key=lambda f: D(f["closedPnl"]), reverse=True)[:5]
        top5_neg = [x for x in sorted(closes, key=lambda f: D(f["closedPnl"])) if D(f["closedPnl"]) < 0][:5]

        # distinct oid/tid vs fills (many fills share one oid -> NOT independent trades)
        # count trades approximation: distinct oid among closing fills
        close_oids = set(f["oid"] for f in closes)
        close_tids = set(f["tid"] for f in closes)

        funding_report = None
        if funding is not None:
            ftimes = [fd["time"] for fd in funding]
            sum_fund = sum((D(fd.get("delta", {}).get("usdc")) for fd in funding), Decimal("0"))
            funding_report = {
                "n_entries": len(funding),
                "page_cap_likely": len(funding) >= 500,
                "min_time": min(ftimes), "max_time": max(ftimes),
                "min_time_iso": iso(min(ftimes)), "max_time_iso": iso(max(ftimes)),
                "sum_delta_usdc_partial": rnd(sum_fund),
                "partial_note": "Funding entries are capped at 500 per time-range response (documented generic cap). WITHOUT pagination proof this is PARTIAL coverage, NOT full 7d funding. Not added to total net.",
            }

        report["wallets"][wk] = {
            "address": w["address"],
            "fills": {
                "n_fills": len(fills),
                "n_fills_at_2000_page_cap": len(fills) >= 2000,
                "time_min_ms": tmin, "time_max_ms": tmax,
                "time_min_iso": iso(tmin), "time_max_iso": iso(tmax),
                "distinct_time_tid_oid": len(distinct_keys),
                "distinct_tid_oid": len(distinct_tid_oid),
                "distinct_oid": len(distinct_oid),
                "n_fills_with_shared_oid": len(fills) - len(distinct_oid),
                "note_shared_oid": "multiple fills may share one oid (partial fills of one order / one position slice) -> these are NOT independent trades.",
            },
            "coverage": {
                "derivable_time_span": f"{iso(tmin)} .. {iso(tmax)}",
                "starts_at_window_start": tmin <= WINDOW_START,
                "ends_at_window_end": tmax >= WINDOW_END,
                "flag": "PARTIAL/UNPROVEN" if (tmin > WINDOW_START or tmax < WINDOW_END or len(fills) >= 2000) else "derivable-full",
                "note": "Raw payload alone cannot prove full-window coverage. If page cap (2000) hit or span narrower than window => coverage PARTIAL/UNVERIFIED. No new requests allowed by task.",
            },
            "fee": {
                "n_fee_pos": len(fee_pos), "sum_fee_pos": rnd(sum_fee_pos),
                "n_fee_neg": len(fee_neg), "sum_fee_neg": rnd(sum_fee_neg),
                "n_fee_zero": len(fee_zero),
                "sum_fee_all": rnd(sum_fee),
                "sum_fee_opens": rnd(sum_fee_opens), "sum_fee_closes": rnd(sum_fee_closes),
                "matrix_crossed_x_sign": fee_matrix,
                "convention": "fee>0 = cost, fee<0 = maker rebate. net = sum(closedPnl) - sum(fee).",
            },
            "pnl": {
                "sum_closedPnl_all_fills": rnd(sum_closedPnl),
                "n_closing_fills": len(closes), "n_open_fills": len(opens),
                "sum_closedPnl_closing_fills": rnd(sum_closedPnl_closes),
                "n_closing_pos": len([f for f in closes if D(f.get('closedPnl')) > 0]),
                "n_closing_neg": len([f for f in closes if D(f.get('closedPnl')) < 0]),
                "n_closing_zero": len([f for f in closes if D(f.get('closedPnl')) == 0]),
                "win_rate_claim_note": "Win-rate over closedPnl>0/<0 among fills is NOT a position win-rate; do not present as strategy win rate.",
            },
            "net": {
                "net_fill_only_all_fills": rnd(net_fill_only),
                "net_fill_only_closing_fills": rnd(net_fill_only_closes),
                "formula": "net_fill_only = sum(closedPnl) - sum(fee)  [per sqd.dev]",
                "excludes": ["funding (partial)", "open/unrealized position PnL", "whole 7d coverage (see coverage flag)"],
                "not_full_7d": True,
                "not_wallet_total": True,
            },
            "top5_closing_pos": [f_compact(f) for f in top5_pos],
            "top5_closing_neg": [f_compact(f) for f in top5_neg],
            "by_asset_fill_only_net": per_asset_out,
            "by_coin_dir_fill_only_net": per_coin_side_out,
            "trades_distinctness": {
                "n_closing_fills": len(closes),
                "n_distinct_closing_oid": len(close_oids),
                "n_distinct_closing_tid": len(close_tids),
                "note": "Number of fills != number of trades. Group by oid to approximate order-level trades; partial fills share oid.",
            },
            "funding": funding_report,
        }

    def _default(o):
        if isinstance(o, Decimal):
            return str(o)
        raise TypeError(o)
    json.dump(report, open(OUT, "w"), indent=2, default=_default)
    print(json.dumps(report, indent=2, default=_default))


if __name__ == "__main__":
    main()