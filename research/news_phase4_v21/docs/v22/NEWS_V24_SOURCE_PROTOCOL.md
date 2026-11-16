# NEWS v2.4 — source-broadening prospective protocol (design only)

**Dated:** 2026-11-17. **Status:** design + fixture-tested contract.
**No live feed, no code path into v2.1 or v2.2, no model call.**

## 1. Why a separate cohort

v2.1 and v2.2 select on `btc_relevance()`, a keyword counter. That gate cannot
see a sanctions package or an oil shock, so widening sources inside v2.1 would
change the meaning of its existing records. v2.4 is therefore a **new cohort**
with its own counter, its own prompt version and its own rows.

## 2. Categories (declared, not inferred)

`crypto · macro · monetary_policy · geopolitics · sanctions · energy · regulatory`

A record without a category is **not eligible**. An "other" bucket is how a
source regime drifts unnoticed.

**Relevance is category-based, not keyword-based.** This is the whole point: an
oil inventory surprise with no BTC keyword must be eligible, otherwise the
coverage gap is reproduced inside the new protocol.

## 3. Source classes and provenance

| class | example shape | provenance | note |
|---|---|---|---|
| statistical API | release calendar + JSON payload | `primary` | machine-readable only |
| official press feed | regulator/gazette bulletin | `primary` | |
| central-bank communication | press release | `primary` | |
| exchange/market operator | announcement | `official_secondary` | |
| wire service | machine feed | `media_wire` | down-weighted in the prompt |

A source that is not machine-readable is excluded: a scraper is a silent
correctness risk (layout changes produce plausible nonsense).

## 4. Freshness budget (per category, in seconds)

```
crypto 120 · macro 300 · monetary_policy 300
geopolitics 600 · sanctions 900 · energy 900 · regulatory 900
```

Measured as `observed_ms - published_ms`. Over budget → the record is
**rejected**, not downgraded: a forty-minute-old "shock" is not a shock.

## 5. Contract (implemented, fixture-tested)

`v24_sources.validate_record()` refuses, with a named reason:
- wrong or unknown category, wrong source id, unknown provenance;
- non-integer timestamps, `observed_ms < published_ms` (clock bug or forged
  feed), observation in the future;
- lag beyond the category budget;
- empty title, non-http(s) origin URL;
- a content digest that does not match `sha256(title|published_ms)`.

`v24_sources.select()` applies: age window, dedupe by normalised title key,
optional per-category cap, and a **deterministic** ordering
`(category order, published_ms, content_sha256)`. Two stories in the same
millisecond still order the same way on every run.

## 6. Estimation and bias

- The estimand is the same paired, selection-conditioned difference as v2.2,
  but computed **within category**, never pooled across categories.
- Selection is not randomised. The selector's ordering is itself a bias source,
  so the protocol requires reporting the **selection rate per category** and the
  **rejection reasons distribution** alongside any result.
- Dedupe is crude and semantic-free by design: a model call inside ingestion is
  a leak vector, and an LLM judge would make the sample depend on the model
  under test.

## 7. Outages and partial coverage

A category with no records in a window is **missing**, not zero. The protocol
requires a per-category coverage timeline so a silent feed death cannot look
like a quiet market. A category below 50 % uptime in a block is excluded from
that block with the reason recorded.

## 8. Privacy and security

- Only published, public items; no paywalled or credentialed sources.
- No personal data beyond what the publisher already publishes.
- Origin URLs stored for auditability; never sent to the model in v2.1-style
  prompts (the M arm has no URLs at all).
- No credentials in the repo: adapters read a path from the environment and
  fail closed when it is absent.

## 9. What would need owner approval before any live run

1. A concrete source list with licence check.
2. A dated freeze of the collector version.
3. A separate counter and a separate export.
4. Confirmation that v2.1 and v2.2 remain untouched.

Until then: **design and fixtures only.** `v24_sources.py` contains no network
code, and a test asserts that no `urlopen`/`requests`/socket call exists in it.
