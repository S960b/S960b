# NEWS v2.4 — candidate OFFICIAL feeds (code/doc only, live set unchanged)

Purpose: answer "can a qualifying story (≤300/600/900 s old) ever be caught by
polling these sources?" without changing the live source set.

Checked 2026-11-17 ~07:45 UTC, one GET per URL, UA `news-research/1.0`,
no scraping beyond the published feed/RSS document itself.
`robots.txt` behaviour for the already-wired four is recorded in
`SOURCE_TERMS_RECORD.md`.

## 1. Current sources — the measured problem is cadence, not polling

| source | items | span | items/day |
|---|---|---|---|
| fed_press_monetary | 15 | 170 d | ~0.09 |
| sec_press | 25 | 57 d | ~0.44 |
| whitehouse_actions | 30 | 60 d | ~0.50 |

Combined ≈ 1 publication per day across the three dated feeds. At a 600 s
budget the probability of catching one during a given 10-minute window is
roughly `1 / 144`. That is why 11 polls in stage 16 yielded zero new items —
not a bug, arithmetic.

## 2. Candidates probed (reachability only)

| candidate | HTTP | notes |
|---|---|---|
| fed `feeds/press_all.xml` | 200 | superset of the monetary feed already wired |
| ECB `rss/press.html` | 200 | 15 items, latest 2026-11-16 14:00 +01 |
| Bank of England `rss/news` | 200 | 50 items, latest 2026-11-13 09:00 UTC |
| BIS `doclist/all_pressrels.rss` | 200 | 1 item, no `pubDate` — same defect as Kraken status |
| IMF `external/rss/pressreleases.aspx` | **403** | not usable; not retried around |
| SEC EDGAR `browse-edgar?…output=atom` | **403** | fair-access refused; not retried around |
| ECB `rss/fmo.html` | 404 | does not exist |

Accessibility is **not** a licence decision. A 200 means the document exists and
was served; it does not grant redistribution rights, and none of the candidates
above has been checked against a stated licence. That check must happen before
any of them is wired.

## 3. Cadence estimate — the honest arithmetic

| candidate | observed cadence | P(catch inside 600 s at 180 s polling) |
|---|---|---|
| ECB press | a few per day, clustered | low single-digit % |
| BoE news | ~daily | ~1 % |
| Fed press_all | daily-ish | ~1 % |
| BIS | ~1 item, no dates | unusable as an event stream |

**Conclusion to state plainly:** no combination of the checked *official* feeds
produces an intraday stream. Adding them changes the expected wait from roughly
one story per day to a handful per day. A first qualifying prospective row would
still arrive on the order of days, not minutes, and only if the collector is
running at the right moment.

The only official sources that genuinely publish intraday are statistical
release calendars and exchange/market operations notices, and those were already
quarantined in `SOURCE_TERMS_RECORD.md` for good reasons (BLS = scheduled
statistics, not news; exchange status = state, not event).

## 4. Recommendation

1. Keep the live set at four sources.
2. If a broader set is wanted, wire ECB and BoE **only after** a licence check,
   with their own freshness promises, and expect the wait to stay in days.
3. If the experiment needs intraday candidates, the honest options are
   * a different criterion for what counts as "fresh" (owner decision, not a
     collector tweak), or
   * a paid/licensed feed — out of scope for paper mode here.

Nothing in this document changes the live source set.
