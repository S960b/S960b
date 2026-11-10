#!/usr/bin/env python3
"""Validate raw fills/funding and compute aggregates for the audit report."""
import json
import re
from collections import Counter, defaultdict

RAW = "raw_fills.json"
OUT = "aggregates.json"

def d(x):
    return 0.0 if x is None else float(x)

def fill_compact(f):
    return {
        "time": f["time"], "tid": f["tid"], "oid": f["oid"],
        "coin": f["coin"], "side": f.get("side"), "dir": f.get("dir"),
        "px": f.get("px"), "sz": f.get("sz"), "startPosition": f.get("startPosition"),
        "closedPnl": f.get("closedPnl"), "fee": f.get("fee"),
        "crossed": f.get("crossed"),
    }

def main():
    raw = json.load(open(RAW))
    report = {"validation": {}, "wallets": {}}
    for wk, w in raw["wallets"].items():
        addr = w["address"]
        fills = w["fills"]
        funding = w["funding"]

        v = {}
        v["address_len_ok"] = isinstance(addr, str) and bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", addr))
        missing = set()
        bad_px_sz_fee = 0
        bad_time = 0
        bad_oid = 0
        bad_crossed = 0
        dup_counter = Counter()
        for f in fills:
            for req in ("time", "tid", "oid", "px", "sz", "dir", "side"):
                if req not in f:
                    missing.add(req)
            dup_counter[(f.get("tid"), f.get("oid"), f.get("time"))] += 1
            if not isinstance(f.get("time"), int) or f.get("time") < 1_700_000_000_000:
                bad_time += 1
            if not re.fullmatch(r"\d{6,20}", str(f.get("oid", ""))):
                bad_oid += 1
            try:
                float(f.get("px")); float(f.get("sz")); float(f.get("fee"))
            except (TypeError, ValueError):
                bad_px_sz_fee += 1
            if not isinstance(f.get("crossed"), bool):
                bad_crossed += 1
        n_dup = sum(1 for c in dup_counter.values() if c > 1)
        v["n_fills"] = len(fills)
        v["missing_fields"] = sorted(missing)
        v["bad_px_sz_fee"] = bad_px_sz_fee
        v["bad_time"] = bad_time
        v["bad_oid"] = bad_oid
        v["bad_crossed"] = bad_crossed
        v["duplicate_tid_oid_time"] = n_dup
        v["negative_sz"] = sum(1 for f in fills if d(f.get("sz")) < 0)
        v["negative_px"] = sum(1 for f in fills if d(f.get("px")) < 0)
        v["positive_fee"] = sum(1 for f in fills if d(f.get("fee")) > 0)
        present = set()
        for f in fills:
            present.update(f.keys())
        v["present_fields"] = sorted(present)
        v["has_liquidation_field"] = "liquidation" in present
        v["liquidated_fills"] = sum(1 for f in fills
                                    if "liquidat" in str(f.get("dir", "")).lower()
                                    or str(f.get("liquidation", "")).lower() == "true")

        fills_sorted = sorted(fills, key=lambda f: (f["time"], f["tid"]))
        opens = [f for f in fills_sorted if "Open" in f.get("dir", "")]
        closes = [f for f in fills_sorted if "Close" in f.get("dir", "")]
        close_long = [f for f in closes if f.get("dir") == "Close Long"]
        close_short = [f for f in closes if f.get("dir") == "Close Short"]

        # flip: a closing fill that reverses position sign. Using startPosition
        # (pre-fill position). Close Long flips net position from + to - only
        # if |sz|>startPosition; rough proxy by dir not possible from single fill.
        flips = []
        for f in closes:
            sp = d(f.get("startPosition"))
            sz = d(f.get("sz"))
            if f.get("dir") == "Close Long" and sp > 0 and sz > sp:
                flips.append(f)
            elif f.get("dir") == "Close Short" and sp < 0 and sz > -sp:
                flips.append(f)

        sum_closed = sum(d(f.get("closedPnl")) for f in closes)
        sum_fee_all = sum(d(f.get("fee")) for f in fills)
        sum_fee_open = sum(d(f.get("fee")) for f in opens)
        sum_fee_close = sum(d(f.get("fee")) for f in closes)

        wins = [f for f in closes if d(f.get("closedPnl")) > 0]
        losses = [f for f in closes if d(f.get("closedPnl")) < 0]
        zeros = [f for f in closes if d(f.get("closedPnl")) == 0]

        by_coin_closed = defaultdict(float)
        by_coin_fee = defaultdict(float)
        by_coin_nclose = Counter()
        for f in closes:
            by_coin_closed[f["coin"]] += d(f["closedPnl"])
            by_coin_nclose[f["coin"]] += 1
        for f in fills:
            by_coin_fee[f["coin"]] += d(f["fee"])

        top_pos = sorted(closes, key=lambda f: d(f["closedPnl"]), reverse=True)[:5]
        top_neg = sorted(closes, key=lambda f: d(f["closedPnl"]))[:5]
        recent20 = closes[-20:] if closes else []

        fund_by_coin = defaultdict(float)
        for fd in funding or []:
            fund_by_coin[fd["delta"].get("coin", "?")] += d(fd["delta"].get("usdc"))
        funding_delta = sum(d(fd.get("delta", {}).get("usdc")) for fd in funding) if funding else None

        report["wallets"][wk] = {
            "address": addr,
            "address_len_ok": v["address_len_ok"],
            "validation": v,
            "window": {
                "n_fills_total": len(fills),
                "n_open": len(opens),
                "n_closing": len(closes),
                "n_close_long": len(close_long),
                "n_close_short": len(close_short),
                "n_flip_candidates": len(flips),
            },
            "pnl": {
                "sum_closedPnl_closing_fills": round(sum_closed, 8),
                "n_wins": len(wins), "n_losses": len(losses), "n_zero": len(zeros),
                "top5_positive": [fill_compact(f) for f in top_pos],
                "top5_negative": [fill_compact(f) for f in top_neg],
                "recent20_closing": [fill_compact(f) for f in recent20 if f in closes],
            },
            "fees": {
                "sum_fee_all": round(sum_fee_all, 8),
                "sum_fee_opens": round(sum_fee_open, 8),
                "sum_fee_closes": round(sum_fee_close, 8),
                "fee_sign_convention": "signed: positive fee = maker rebate credited (observed 1:1 with crossed=true on wallet A); negative fee = taker cost debited. Per HL fees docs, maker rebates are negative add rates.",
            },
            "funding": {
                "n": len(funding) if funding else 0,
                "sum_delta_usdc": round(funding_delta, 8) if funding_delta is not None else None,
                "by_coin": {k: round(x, 8) for k, x in fund_by_coin.items()},
            },
            "by_coin_closedPnl": {k: round(x, 8) for k, x in by_coin_closed.items()},
            "by_coin_fee": {k: round(x, 8) for k, x in by_coin_fee.items()},
            "by_coin_nclose": dict(by_coin_nclose),
        }
    json.dump(report, open(OUT, "w"), indent=2)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()