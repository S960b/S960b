#!/usr/bin/env python3
"""NEWS v2.1 — event selection for paper inference (read-only).

Chooses WHICH ingested event would be handed to the paper model, and records
why every other candidate was rejected. It never calls a model and never writes
to the store.

Selection policy, in strict priority order:
  1. cohort fast_verified (trusted issuer, <= 5 min old at decision time)
  2. cohort context_news (real news, 5 min .. 2 h old at decision time)
  Within a cohort: primary issuers first, then relevance score, then recency.
  FIFO on RSS arrival order is explicitly NOT used.

Relevance: a story must plausibly move BTC. Stories that are unrelated to BTC,
market structure or macro releases are declined and logged as `declined`, not
silently dropped. BTC relevance is a keyword score; the paper model still makes
the actual forecast.
"""
import argparse
import json
import re
import sqlite3
import sys
import time

COHORTS = ("fast_verified", "context_news")
RECENCY_WINDOW_MS = 2 * 3_600_000
FAST_WINDOW_MS = 5 * 60_000
CONTEXT_WINDOW_MS = 2 * 3_600_000
RELEVANT_COHORTS = frozenset(COHORTS)

# Terms that make a story plausibly BTC-relevant. Deliberately broad: this is a
# filter against obvious noise (sports, unrelated equities), not an editor.
BTC_TERMS = ("bitcoin", "btc", "crypto", "etf", "stablecoin", "binance",
             "coinbase", "kraken", "exchange", "blockchain", "mining",
             "halving", "custody", "sec", "cftc", "fed", "fomc", "rate",
             "inflation", "cpi", "payroll", "treasury", "tariff", "ethereum")
NOISE_TERMS = ("sports", "football", "celebrity", "movie", "recipe")

PRIMARY_SOURCES = frozenset(("fed", "sec", "whitehouse", "bls", "binance",
                             "coinbase_status", "kraken_status",
                             "bluesky_verified"))


def load_events(con, now_ms, cohorts=COHORTS, window_ms=RECENCY_WINDOW_MS):
    """Candidates only: recent enough to be a decision, in an eligible cohort."""
    rows = con.execute(
        "SELECT event_id,source,origin_id,published_ms,observed_ms,"
        "source_kind,cohort,payload_json FROM events "
        "WHERE cohort IN (%s) AND published_ms IS NOT NULL "
        "AND (? - published_ms) <= ? "
        "AND published_ms <= ? ORDER BY observed_ms DESC"
        % ",".join("?" * len(cohorts)),
        (*cohorts, now_ms, window_ms, now_ms)).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r[7])
        except Exception:
            payload = {}
        out.append({"event_id": r[0], "source": r[1], "origin_id": r[2],
                    "published_ms": r[3], "observed_ms": r[4],
                    "source_kind": r[5], "cohort": r[6],
                    "title": payload.get("title") or "",
                    "summary": payload.get("summary") or ""})
    return out


def btc_relevance(ev):
    """0..1 rough score. Never used as a signal, only for ordering/filtering."""
    text = f"{ev.get('title','')} {ev.get('summary','')}".lower()
    if any(t in text for t in NOISE_TERMS):
        return 0.0
    hits = sum(1 for t in BTC_TERMS if t in text)
    if "bitcoin" in text or "btc" in text:
        hits += 2
    return min(1.0, hits / 5.0)


def dedupe_key(ev):
    """Collapse the same story republished by several outlets."""
    norm = re.sub(r"[^a-z0-9 ]+", " ", (ev.get("title") or "").lower())
    words = [w for w in norm.split() if len(w) > 2]
    return " ".join(words[:8])


def reclassify_at_decision(ev, now_ms):
    """Ingestion cohort is a claim about the past; the decision needs the truth.

    An item that was fast_verified when ingested can be well past 5 minutes by
    the time a model would see it. Such an item is DEMOTED to context_news (and
    then to nothing if it is older than the context window) — it is never
    "promoted" back to fast, and a stale fast item is never dispatched as fast.
    """
    pub = ev.get("published_ms")
    if not isinstance(pub, int):
        return None, "no_publish_time"
    age = now_ms - pub
    if age < 0:
        return None, "future_publish_time"
    declared = ev.get("cohort")
    if declared == "fast_verified" and age <= FAST_WINDOW_MS:
        return "fast_verified", None
    if age <= CONTEXT_WINDOW_MS:
        return "context_news", ("demoted_from_fast" if
                                declared == "fast_verified" else None)
    return None, "outside_all_windows"


def select(con, now_ms=None, limit=1, min_relevance=0.2):
    """Return (chosen, decision_log). Deterministic given the same inputs."""
    now_ms = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
    cands = load_events(con, now_ms)
    decisions, ranked = [], []
    seen = {}
    for ev in cands:
        # Age at DECISION time, not delivery lag: a story published 4 h ago is
        # stale even if we only polled it a minute later.
        age_ms = now_ms - ev["published_ms"]
        ev["age_ms"] = age_ms
        ev["relevance"] = btc_relevance(ev)
        reasons = []
        effective, note = reclassify_at_decision(ev, now_ms)
        ev["declared_cohort"] = ev["cohort"]
        ev["cohort"] = effective
        if note:
            ev["cohort_note"] = note
        if effective is None:
            reasons.append(note or "cohort_not_eligible")
        if age_ms > RECENCY_WINDOW_MS:
            reasons.append("older_than_2h")
        if ev["relevance"] < min_relevance:
            reasons.append("not_btc_relevant")
        # D09: duplicates are no longer resolved at this point. Declining here
        # happened BEFORE ranking, so the first variant seen won and a secondary
        # mirror could suppress a primary source. The key travels with the
        # candidate and the choice is made after ranking.
        key = dedupe_key(ev)
        ev["dedupe_key"] = key
        if reasons:
            decisions.append({"event_id": ev["event_id"],
                              "source": ev["source"],
                              "cohort": ev["cohort"],
                              "age_ms": age_ms,
                              "relevance": round(ev["relevance"], 2),
                              "decision": "declined",
                              "reasons": reasons})
            continue
        rank = (
            0 if ev["cohort"] == "fast_verified" else 1,
            0 if ev["source"] in PRIMARY_SOURCES else 1,
            -ev["relevance"],
            age_ms,
            # Explicit, deterministic tie breaker: the event id. Two variants
            # that agree on every ranking component must still order the same
            # way on every run and on every row source.
            ev["event_id"],
        )
        ranked.append((rank, ev))
        decisions.append({"event_id": ev["event_id"], "source": ev["source"],
                          "cohort": ev["cohort"], "age_ms": age_ms,
                          "relevance": round(ev["relevance"], 2),
                          "dedupe_key": (key[:16] if key else None),
                          "decision": "candidate"})

    ranked.sort(key=lambda x: x[0])
    # One event, one pair: the highest-ranked variant of each story is the only
    # one eligible for selection; the rest are recorded as duplicates of it.
    winners, kept = {}, []
    for rank, ev in ranked:
        key = ev.get("dedupe_key")
        if key:
            if key in winners:
                winners[key]["duplicates"].append(ev["event_id"])
                continue
            winners[key] = {"event_id": ev["event_id"], "duplicates": []}
        kept.append(ev)

    chosen = kept[:max(0, limit)]
    primary_ids = {e["event_id"] for e in kept}
    for d in decisions:
        eid = d["event_id"]
        own = next((ev.get("dedupe_key") for _, ev in ranked
                    if ev["event_id"] == eid), None)
        if eid not in primary_ids and own in winners:
            d["decision"] = "declined"
            d["reasons"] = ["duplicate_of_" + winners[own]["event_id"][:12]]
            d["superseded_by"] = winners[own]["event_id"]
        elif eid in {c["event_id"] for c in chosen}:
            d["decision"] = "selected"
    return chosen, decisions


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--now-ms", type=int, default=None)
    a = p.parse_args(argv)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    try:
        chosen, decisions = select(con, a.now_ms, a.limit)
        print(json.dumps({
            "status": "selected" if chosen else "no_eligible_event",
            "chosen": [{"event_id": c["event_id"], "source": c["source"],
                        "cohort": c["cohort"],
                        "age_ms": c["age_ms"],
                        "relevance": round(c["relevance"], 2),
                        "title": c["title"][:80]} for c in chosen],
            "considered": len(decisions),
            "declined": sum(1 for d in decisions if d["decision"] == "declined"),
            "decisions": decisions[:20],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
