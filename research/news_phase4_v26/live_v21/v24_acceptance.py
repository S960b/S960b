#!/usr/bin/env python3
"""NEWS v2.4 — offline integration acceptance: fixtures -> ledger -> report.

Offline end to end with a FIXED clock. No network, no model call, no live feed,
no production database. The point is to prove the pipeline hangs together and to
produce a reconciliation an auditor can check line by line:

    input -> invalid / duplicate / accepted -> recorded -> labelled / unlabelled

Every bucket also reports STORY coverage and AVAILABILITY separately, because a
story is not a heartbeat and an empty bucket is not an outage.
"""
import json

import v21_paper_prep as P
import v21_pilot_cycle as PC
import v22_ledger as L
import v24_conformance as K
import v24_sources as S

FIXED_CLOCK_MS = None  # derived from the newest fixture
BUCKET_MS = 3_600_000
HORIZON_MS = 4 * 3_600_000


def _hearts(registry, now_ms):
    """Synthetic probe stream: every declared source answered 60 s ago, and one
    source is deliberately silent so the report has something to distinguish."""
    beats = []
    for spec in K.SPECS:
        if spec.source_id == "eia":
            continue                       # silent source: expect `missing`
        beats.append(S.Heartbeat(spec.source_id, now_ms - 60_000, True))
    beats.append(S.Heartbeat("sec", now_ms - 30_000, False))
    return beats


def _candles(due_ms):
    """One well-formed closed 1m candle covering the due instant."""
    open_ms = due_ms // 60_000 * 60_000
    return [(open_ms, open_ms + 59_999, 64_100.0)]


def run(*, now_ms=FIXED_CLOCK_MS):
    """Replay the recorded fixtures at a FIXED clock.

    The fixtures were recorded at their own instants, so replaying them at an
    arbitrary wall-clock would make every one of them "stale" and the run would
    only prove the freshness rule. The clock is therefore derived from the
    newest fixture plus one bucket: deterministic, documented, and independent
    of the host.
    """
    registry = K.registry()
    fixtures = K.load_fixtures()
    # Each fixture is replayed at ITS OWN observation instant plus one second:
    # that is the only clock at which a recorded payload can be judged without
    # turning the whole run into a freshness test. The clock is still fixed and
    # derived from the data, never from the host.
    # The clock is frozen at the newest fixture's observation so the fixtures
    # themselves are inside their category budgets. Age rejections elsewhere in
    # the run are then a property of the data, not of the clock.
    newest = max(p["observed_ms"] for p in fixtures.values())
    now_ms = now_ms if now_ms is not None else newest + 1_000
    # Records older than their category budget are rejected for age before
    # dedupe is ever consulted, so the only records that reach the dedupe stage
    # are the fresh ones the caller adds on purpose.

    records = []
    for payload in fixtures.values():
        records.append(K.record_from_fixture(payload))

    # Stage-9 correction: the duplicate must be VALID and be caught by the
    # dedupe rule, not masked by a digest failure. It is a second, registered
    # source carrying the same headline with a different publication instant,
    # so its digest differs and validation passes - only dedupe can reject it.
    original = fixtures["crypto"]
    second = dict(original)
    second["source_id"] = "bin"
    second["category"] = "crypto"
    second["provenance"] = "primary"
    # A GENUINE duplicate pair, both FRESH at the run clock: same headline and
    # same source id, two seconds apart, so both pass validation and only the
    # dedupe rule can reject one. This closes the stage-8 gap, where the
    # "duplicate" was really a digest failure and the dedupe path was never
    # exercised.
    fresh = dict(fixtures["crypto"])
    fresh["published_ms"] = now_ms - 120_000
    fresh["observed_ms"] = now_ms - 90_000
    winner = K.record_from_fixture(fresh)
    late = dict(fresh)
    late["observed_ms"] = fresh["observed_ms"] + 2_000
    loser = K.record_from_fixture(late)
    records.extend([winner, loser])

    # And one genuinely invalid record (bad digest) so the invalid channel is
    # populated as well - the two channels must be distinguishable.
    broken = dict(fixtures["sanctions"])
    broken["title"] = "Same source, tampered title"
    broken_record = K.record_from_fixture(broken)
    records.append(S.SourceRecord(
        source_id=broken_record.source_id,
        category=broken_record.category,
        provenance=broken_record.provenance,
        published_ms=broken_record.published_ms,
        observed_ms=broken_record.observed_ms,
        title=broken_record.title,
        origin_url=broken_record.origin_url,
        content_sha256="0" * 64))

    # Shape validation at each record's own clock, then one selection pass at
    # the frozen clock. The two are reported separately so a shape failure is
    # never confused with an age failure.
    shape_failures = []
    for rec in records:
        spec = registry.get(rec.source_id)
        try:
            S.validate_record(rec, spec=spec, now_ms=rec.observed_ms + 1_000)
        except S.AdapterError as exc:
            shape_failures.append({"reason": str(exc),
                                   "source_id": rec.source_id,
                                   "content_sha256": rec.content_sha256})

    selection = S.select(records, now_ms=now_ms, registry=registry,
                         max_age_s=None)
    # Three distinct channels, and they must not be mixed:
    #   selection["invalid"]  - refused at the boundary (digest, source, shape)
    #   selection["rejected"]  - well-formed but lost on a rule (age, duplicate)
    #   shape_failures         - the same records judged at their own clock
    # `invalid` here is the boundary count, because that is what the accounting
    # balances against.
    quarantined = list(selection["invalid"])

    # Stage 9: cadence and its anchor live on the SourceSpec, not in a side
    # map, so the schedule a bucket is judged against is the declared one.
    coverage = S.coverage_timeline(
        selection["accepted"], start_ms=now_ms - BUCKET_MS,
        end_ms=now_ms + 1, bucket_ms=BUCKET_MS, registry=registry,
        heartbeats=_hearts(registry, now_ms))

    # Recorded: every accepted story becomes a sealed triple with mock arms.
    recorded, labelled, unlabelled = 0, 0, 0
    ledger_path = ":memory:"
    con = L.open_v22(ledger_path)
    try:
        for rec in selection["accepted"]:
            event = {"event_id": rec.content_sha256, "source": rec.source_id,
                     "cohort": "context_news",
                     "published_ms": rec.published_ms,
                     "observed_ms": rec.observed_ms,
                     "title": rec.title, "summary": ""}
            prompts = K and None  # placeholder: prompts are per v2.2 builder
            # The v2.2 builder needs a market vector; a fixed one keeps the run
            # deterministic and is not derived from any live data.
            market = {"return_1h": 0.0, "return_4h": 0.0, "return_24h": 0.0,
                      "realized_vol_24h": 0.01, "candle_opens_ms": now_ms - 86_400_000,
                      "candle_closes_ms": now_ms - 3_600_000, "candle_count": 26}
            import v22_arms as A
            prompts = A.build_prompts_3(event, market)
            hashes = A.prompts_sha256(prompts)
            prepared = {"prompts_sha256": hashes, "cutoff_ms": now_ms}
            L.reserve_triple(con, event_id=event["event_id"],
                             prompts_sha=hashes, now_ms=now_ms)
            for arm in A.ARM_NAMES:
                L.mark_arm(con, event_id=event["event_id"], arm=arm,
                           state="completed", now_ms=now_ms + 100,
                           session_id=f"mock-{arm}")
            results = L.mock_arm_results(event["event_id"],
                                         {"news": (0.55, 40, 0.4),
                                          "news_market": (0.54, 30, 0.45),
                                          "market": (0.50, 0, 0.5)},
                                         base_ms=now_ms + 1_000)
            quote = {"price": 64_000.0, "observed_ms": now_ms + 10_000,
                     "source": "binance_public_ticker"}
            out = L.seal_triple(con, event=event, prepared=prepared,
                                arm_results=results, quote=quote,
                                now_ms=now_ms + 11_000)
            if out.get("status") != "sealed":
                continue
            recorded += 1
            due_ms = quote["observed_ms"] + HORIZON_MS
            con.execute("UPDATE v22_outcomes SET due_ms=? WHERE event_id=?",
                        (due_ms, event["event_id"]))
            con.commit()
            label = L.label_triple(con, event["event_id"], due_ms=due_ms,
                                   now_ms=due_ms + 60_000,
                                   candles=_candles(due_ms))
            if label.get("status") == "labelled":
                labelled += 1
            else:
                unlabelled += 1

        export = L.export_v22(con)
    finally:
        con.close()

    gold = {r.content_sha256: r.category for r in records}
    dedupe = S.dedupe_metrics(records, by_expected=gold)

    accounting = {
        "input": len(records),
        "invalid": len(selection["invalid"]),
        "duplicate": sum(1 for r in selection["rejected"]
                         if r["reason"] == "duplicate"),
        # A duplicate is a first-class outcome, not a leftover "rejected".
        "deduplicated": sum(1 for r in selection["rejected"]
                            if r["reason"] == "duplicate"),
        "rejected_stale": sum(1 for r in selection["rejected"]
                              if r["reason"].startswith("too_stale")),
        "rejected_other": len(selection["rejected"])
        - sum(1 for r in selection["rejected"] if r["reason"] == "duplicate")
        - sum(1 for r in selection["rejected"]
              if r["reason"].startswith("too_stale")),
        "accepted": len(selection["accepted"]),
        "recorded": recorded,
        "labelled": labelled,
        "unlabelled": unlabelled,
    }
    # One record is in exactly one bucket: rejected at selection, quarantined
    # as invalid, or accepted. `input` counts every record handed to the run,
    # including the deliberate duplicate and the tampered one.
    accounting["rejected"] = len(selection["rejected"])
    # Two independent identities that must both hold:
    #   input = invalid + deduplicated + rejected + accepted
    #   accepted = sealed + explicit failure
    accounting["seal_failures"] = accounting["accepted"] - accounting["recorded"]
    # `rejected` already contains the duplicates, so the identity counts the
    # duplicate bucket ONCE: invalid + rejected + accepted == input.
    accounting["balances_selection"] = (
        accounting["input"] == accounting["invalid"]
        + accounting["rejected"] + accounting["accepted"])
    accounting["balances_records"] = (
        accounting["accepted"] == accounting["recorded"]
        + accounting["seal_failures"])
    accounting["balances_records"] = (accounting["accepted"]
                                      == accounting["recorded"]
                                      + accounting["rejected_other"])

    return {
        "kind": "news_v24_offline_acceptance",
        "protocol_version": S.PROTOCOL_VERSION,
        "clock_ms": now_ms,
        "accounting": accounting,
        "coverage": coverage,
        "dedupe": dedupe,
        "refusals": selection["rejected"] + quarantined,
        "export_counts": {"triples": len(export["triples"]),
                          "arms": len(export["arms"]),
                          "outcomes": len(export["outcomes"])},
        "claims": {
            "provenance": "NOT established: content_sha256 is an integrity "
                          "checksum, not authentication",
            "availability": "derived from synthetic heartbeat probes only",
            "performance": "none: mock arms, no evidence about markets",
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, sort_keys=True))
