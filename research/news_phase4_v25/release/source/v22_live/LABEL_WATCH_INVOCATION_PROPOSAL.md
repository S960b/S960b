# Tier-2 label watcher — invocation proposal (NOT an activation)

Status: **proposal only.** Nothing here is scheduled, installed, or running.
There is no cron entry, no systemd unit, no loop. The watcher is one-shot by
design; this document describes how a human or an approved scheduler *may*
invoke it once per period, and what must be true before that is allowed.

## 1. What the watcher is

`source/v22_live/tier2_label_watch.py`

```
default        dry, read-only, writes NOTHING (not even an attempts table)
--settle       writes outcomes for sealed triples whose due time has passed
input          only rows already in tier2_triples
due            entry_observed_ms + horizon_ms, both immutable
candle         taken from the exchange payload (open_time, close_time, close),
               never synthesized; source = binance_public_1m_kline
failure        classified transient vs bug
                  transient -> attempt +1 -> pending -> censored at 3 attempts
                               or 6 h past due, price 0.0, source 'unavailable'
                  bug       -> status 'error', exit 3, row untouched
```

## 2. Proposed schedule

| item | value |
|---|---|
| cadence | once per hour (`--settle`), manual or approved scheduler |
| duration | one pass, seconds; hard stop after processing all sealed rows |
| overlap | a second concurrent run is harmless (BEGIN IMMEDIATE + one outcome per row), but the *proposal* is one instance |
| earliest run | only after `entry + 4 h`; the watcher itself returns `pending` before due, so an early run cannot settle |
| latest useful run | before `due + 6 h`; after that a transient failure censors |

A single sealed triple means the hourly cadence gives at most ~6 attempts inside
the 6 h window, of which 3 are needed to censor. That is deliberate: one short
outage should not destroy the row, and a lasting one should.

## 3. Preconditions before any scheduled invocation

1. **A sealed triple exists.** Today the count is **0** — the prospective
   namespace has never been created. There is nothing to settle.
2. **Copy test passed**: the watcher was run against a `VACUUM INTO` snapshot of
   the real database with `--settle` disabled, and the dry output matched
   expectations.
3. **Backup taken**: `VACUUM INTO` of the Tier-2 database before the first real
   `--settle`, with the digest recorded.
4. **Rollback understood**: `--settle` only INSERTs into `tier2_outcomes` and
   `tier2_label_attempts`. A wrong row is removed by deleting that row from a
   *restored copy* and re-sealing — never by editing the original in place.
5. **Code fingerprint recorded**: sha256 of the running file, matching the
   published manifest entry.

## 4. Logging and alerting (proposed, not built)

The watcher prints one JSON object per run. A wrapper would need:

- append that JSON to a log file with the wall clock at start and end;
- alert on `{"status": "error"}` (a bug, not an outage) — immediate;
- alert on `censored` — expected occasionally, but it permanently removes a row
  from the labelled set, so it should be seen;
- alert on `refused` — means the DB path or sealed entry is wrong.

No such wrapper exists. Building it is a separate, small task.

## 5. Restart recovery

There is nothing to recover: the watcher is stateless between runs. Attempt
counters live in `tier2_label_attempts`, terminal outcomes in `tier2_outcomes`,
and both are consulted at the start of each run. A crash mid-run leaves at most
one row in `pending` with its attempt counted, which is the correct state.

## 6. Owner gates

Activation of *inference* and activation of *label settlement* are separate.

| gate | file | state |
|---|---|---|
| Tier-2 inference (`--execute`) | `TIER2_ACTIVE` + `TIER2_ACTIVATION.json` + `TRIGGER_ENABLED` | **absent** |
| Label settlement (this watcher) | none — it needs only a sealed triple | n/a |

The watcher deliberately has no activation flag of its own: it cannot create
evidence, only read evidence that already exists. A sealed triple is itself the
gate.

## 7. What this proposal does not do

- It does not schedule anything.
- It does not create a lock file, cron entry, systemd unit or background job.
- It does not permit settlements before a triple exists.
- It does not touch the strict v2.2 lane, which remains blocked on
  provider-side tool isolation.
