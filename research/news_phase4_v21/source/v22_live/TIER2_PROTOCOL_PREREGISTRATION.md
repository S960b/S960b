# NEWS Tier-2 observational protocol — PRE-REGISTRATION (before any data)

```
protocol_id      prospective-v2.5-tier2
registered_utc   2026-11-17T09:22:39Z   (SUPERSEDED — see TIER2_ERRATUM_1.md)
corrected_note   The value originally written here, 2026-11-17T09:52:00Z, was a
                 hand-typed error and is LATER than commit 5b26ce3 that contains
                 this file (12:22:39+03:00 = 09:22:39Z). The authoritative
                 registration time is the git commit timestamp above. The erratum
                 records the discrepancy in full; nothing else in this protocol
                 changes. Zero Tier-2 observations existed at either moment.
registered_by    MT, on instruction of ChatGPT seq 155
status           DRAFTED AND OFFLINE-TESTED ONLY — NOT ACTIVE
data_at_registration   ZERO Tier-2 rows exist. Not one.
prompts_version  v2.5-tier2-arms3 (separate from prospective-v2.2-arms3)
namespace        ~/.local/share/news-phase4-v25/tier2.sqlite
```

**Pre-registration means this file was written before any Tier-2 observation.**
If a Tier-2 row ever exists before this file's commit, the protocol is void.
The commit that introduced this file is the registration timestamp; the hash of
this file is recorded in every Tier-2 triple's provenance.

## 1. What this protocol can and cannot claim

**Weaker evidence, stated first.** Tier-2 rests entirely on what our own process
can see:

| surface | what is verified | how it is verified |
|---|---|---|
| client | our outbound body carried no tool definitions | `tool_surface()` walks the whole request body |
| gateway | the body forwarded upstream carried no tool keys, byte-for-byte digest compared | `forwarded_body()` + sha256 at ingress and egress |
| response | the COMPLETE response was parsed and inspected for tool-call artifacts | `response_facts()` over `output[]`, message items, tool_calls |
| upstream | **unknown** | nothing. There is no server-side tool history. |

**Forbidden language.** A Tier-2 row may never say "fully isolated",
"known_zero", "provider proof", "certified", or "no tools possible". The only
permitted overall label is `observational_tier2`. Any export, table or sentence
that upgrades this is a defect.

**What is specifically NOT established:** that the upstream provider did not
expose a tool interface it chose not to use. Absence of a call is not absence of
capability. This protocol measures *absence of tool activity in the observed
transcript*, which is weaker than *absence of tool capability on the server*.

## 2. Separation from the strict cohort — non-negotiable

| | strict v2.2 | Tier-2 (this) |
|---|---|---|
| evidence needed to seal | `known_zero` (server-auditable) | `observational_tier2` |
| namespace | `news-phase4-v24/v22.sqlite` | `news-phase4-v25/tier2.sqlite` |
| prompts version | `prospective-v2.2-arms3` | `v2.5-tier2-arms3` |
| cohort tag | declared cohort as-is | suffixed `|tier2` |
| counters | separate | separate, never summed |
| analysis | v2.2 analyzer | separate analysis, pre-registered below |

A Tier-2 row is **never** merged into, promoted to, or reported alongside the
strict cohort. The strict v2.2 promise stays unmet; this is an additional, weaker
promise recorded separately. The two excluded exploratory triples
(`exploratory2`, `exploratory3`) are **not** retroactively reclassified into
Tier-2 — they predate this registration and remain excluded in
`EXCLUSIONS.jsonl`.

## 3. What a Tier-2 triple is

Same experimental shape as v2.2, because the arms are not what is being weakened:

- three arms on ONE frozen snapshot: N (news only), NM (news + market vector),
  M (market vector only, constructed from a separate dict, leak-audited);
- reservation before any invocation; states `prepared -> started -> completed`;
- entry quote sampled strictly AFTER the last arm completes;
- exit from the first CLOSED 1m candle at/after due, drift in `[0, 60000)`;
- 4 h horizon; 6 bps round-trip cost applied in analysis;
- append-only ledger, immutable rows, seal hash over the full record.

Only the *attestation tier* differs, and only in the direction of claiming less.

## 4. Storage and evidence

```
tier2.sqlite          triples, arms, outcomes — same schema shape as v22.sqlite
                      plus tier TEXT NOT NULL CHECK (tier='observational_tier2')
attestation.jsonl     SHA-linked entries: prev_sha256 chain over
                      (request_sha256, response_sha256, model, status, verdicts)
exclusions.jsonl      any row ever removed from analysis, appended, never edited
```

Every Tier-2 row carries: `prompts_sha256`, `attestation_record_sha256`, the
attestation chain position, `protocol_id`, and `protocol_file_sha256`. A row
whose attestation record cannot be found in the log, or whose chain position does
not match, is **invalid** and must be excluded rather than repaired.

## 5. Analysis, fixed before data

- Primary: M-vs-NM delta in Brier score over resolved rows; N-vs-NM as secondary.
- Resolved only when the 4 h label is written by the loaded hook from a closed
  candle; a manual or reconstructed label is `label_invalid` and excluded.
- Sign-flip permutation test on the paired delta, Holm correction across the
  three comparisons, walk-forward climatology baseline — identical to v2.2.
- Stopping rule: the trial stops at **one** qualifying triple, or 14 days from
  activation, whichever first. No optional continuation.
- Any Tier-2 row is labelled `observational_tier2` in every table, chart and
  sentence. A combined v2.2-vs-Tier-2 comparison is prohibited until both
  cohorts reach n ≥ 10, and even then must report them side by side, never pooled.

## 6. Activation requires an explicit owner decision

Nothing in this file activates anything. Activation is a separate, dated,
owner-authorized record, and the gate file for Tier-2 does not exist yet. Until
then `tier2.py` can only be exercised on offline fixtures with the model gate
closed.

## 7. Failure modes that void the protocol

- a Tier-2 row sealed without its attestation record in the chain;
- a response containing any tool artifact;
- a model identity different from the one in the attestation record;
- a missing terminal response or an ambiguous retry;
- an event older than its category freshness budget, or synthesized;
- an entry quote not strictly after the last arm;
- a candle that was not closed, or drift outside `[0, 60000)`;
- any edit to a sealed row, or to the chain, however small;
- any wording that upgrades `observational_tier2` to a stronger claim.
