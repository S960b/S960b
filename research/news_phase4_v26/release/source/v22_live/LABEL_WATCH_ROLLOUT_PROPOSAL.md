# Label watcher — bounded, reversible rollout proposal (LABEL ONLY)

Status: **proposal.** Nothing is scheduled, installed or running. There is no
cron entry, no systemd unit, no daemon, no lock file, and no activation flag for
this component.

## Scope

This covers **only** +4 h settlement of already-sealed triples:

```
source/v22_live/tier2_label_watch.py
```

It does **not** cover, and cannot trigger, prospective inference.

## What it can and cannot do

| can | cannot |
|---|---|
| read rows already in `tier2_triples` | create a triple |
| write `tier2_outcomes` / `tier2_label_attempts` | call a model |
| refuse when the exchange candle is absent | touch the feed database |
| censor after a bounded number of transient failures | edit a sealed row |

A sealed triple is itself the gate: with zero triples, the watcher returns
`no_triples` and writes nothing.

## Rollout steps, in order

1. **Fingerprint the code.** `sha256sum source/v22_live/tier2_label_watch.py` and
   confirm it equals the entry in `MANIFEST.sha256` of the commit being deployed.
   Record commit SHA.
2. **Backup.** `VACUUM INTO` the Tier-2 database before the first real run,
   record the digest, keep the file until at least one labelled row is verified.
3. **Copy test.** Run the watcher **dry** against the backup copy:
   ```
   python3 tier2_label_watch.py --db <copy> --now-ms <t>
   ```
   Dry writes nothing at all — not even the attempts table — so the copy must be
   byte-identical afterwards. Assert that.
4. **One-shot dry-run on the real file.** Same command against the live database.
   Still dry, so still no writes. Confirm `no_triples` (current state) or a list
   of `would_label` / `would_pending` entries.
5. **First real settlement — only if a triple exists.** Run once with `--settle`.
   Inspect the single JSON line. Expected `labelled` with a price and a `drift_ms`
   under 60 000, or `censored`/`pending` with a reason.
6. **Verify, then repeat hourly** if approved. The watcher is stateless between
   runs; attempt counters and terminal outcomes live in the database.

## Rollback

- Nothing is scheduled, so "rollback" is simply "stop invoking it".
- A wrong outcome row is removed by restoring the backup copy and re-running;
  the original database is never edited in place.
- Censored rows are terminal by design. Undoing one means restoring the copy
  from step 2 and re-settling with a working candle source.

## Monitoring

Per run the watcher prints one JSON object. A wrapper should alert on:

```
status "error"    -> a BUG, not an outage. Investigate before the next run.
status "refused"  -> wrong DB path or invalid sealed entry. Stop and report.
outcome "censored"-> the row left the labelled set permanently. Review.
```

No wrapper exists yet; building it is a separate small task.

## Activation dependencies

| dependency | state today |
|---|---|
| at least one sealed triple | **none — 0 prospecting triples ever created** |
| Tier-2 inference gate (`TIER2_ACTIVE`) | absent |
| activation record (`TIER2_ACTIVATION.json`) | absent |
| trigger flag (`TRIGGER_ENABLED`) | absent |
| provider-side tool isolation (strict v2.2) | **unresolved** |

## The unresolved blocker, stated once

Prospective inference needs evidence that the upstream provider never exposed a
tool interface. No such artifact exists for this gateway: there is no tap inside
it, and the provider publishes no tool history. Tier-2 sidesteps this by claiming
less — `observational_tier2`, never `known_zero` — and the strict v2.2 lane stays
blocked. Nothing in this proposal changes that.
