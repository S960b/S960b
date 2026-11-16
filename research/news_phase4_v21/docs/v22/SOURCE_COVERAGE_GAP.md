# Source coverage gap — NEWS v2.1 selector and collector

**Dated:** 2026-11-16. **Scope:** audit note. **No code change, no new feed.**

## 1. What the current pipeline actually covers

v2.1 ingests free, unauthenticated sources and classifies each event into a
cohort. In the live ledger the distribution is dominated by crypto venues and a
handful of macro releases:

- primary/machine-readable: BLS, SEC, Fed-style releases (the `PRIMARY_SOURCES`
  set in `v21_selector.py`);
- secondary media: crypto outlets, which dominate by volume;
- media provenance is down-weighted in the prompt (`secondary_media`).

## 2. The gap, stated plainly

`btc_relevance()` scores an event by counting BTC-flavoured keywords. A story
that moves BTC without ever saying "bitcoin" — a sanctions package, an oil
supply shock, a surprise rate path shift — scores **zero** and is dropped before
ranking. That is not a neutral filter: it removes exactly the regime where a
news arm would be most interesting, and it leaves the experiment measuring
crypto-venue chatter.

Consequences worth naming:
- **Regime concentration.** The sample is crypto-heavy, so an H1 result would be
  a statement about crypto news, not about news.
- **Selection interacts with the estimand.** The selector's keyword gate is part
  of what "selected event" means; widening coverage changes the estimand, not
  just the sample size.
- **No geopolitical/macro taxonomy exists.** There is no category field, so a
  future analysis cannot even stratify by topic after the fact.

## 3. What must NOT be done

- Do not add sources to v2.1. Its six records and its pre-registered header
  would silently change meaning mid-stream.
- Do not retro-apply new categories to old rows.
- Do not mix cohorts or source regimes in one pooled estimate.

## 4. Proposed v2.4 broadening protocol (design only)

A separate prospective version, `prospective-v2.4-sources` (numbering leaves room
for the three-arm v2.2 and any v2.3 correction), with its own counter
and its own rows:

| category | candidate sources (free, machine-readable) | freshness target | provenance class |
|---|---|---|---|
| macro releases | statistical agencies' release calendars and JSON API | ≤ 5 min | primary |
| central-bank communication | official press-release feeds | ≤ 5 min | primary |
| sanctions / regulatory | official gazettes and regulator bulletins | ≤ 15 min | primary |
| energy / oil | public price and inventory releases | ≤ 15 min | primary |
| geopolitical wire | news wires with a machine feed | ≤ 10 min | secondary_media |

Requirements before any code:
1. **A category field is part of the schema**, not a post-hoc label.
2. **Relevance is category-aware**: a macro/oil event must not be scored by BTC
   keywords at all, or the same gate that excludes it now would exclude it again.
3. **A new prompt version** with the category list in the frozen input, so the
   model knows what it is being given.
4. **Separate reporting**: v2.4 results are reported per category and never
   pooled with v2.1 or v2.2.
5. **Freshness is measured, not assumed**: the `observed_ms − published_ms`
   distribution per source is reported before the first record is sealed.

Until this exists, the honest description of v2.1/v2.2 is: *a crypto-news
experiment with a macro drip*, not a general news experiment.
