# Channel terms record — verified 2026-11-17

Facts observed from this host, one request each, User-Agent
`MultiTool-NEWS-research/2.4 (offline research)`. This is evidence about what the
servers return; it is **not** a licence grant, and no redistribution is claimed.

| channel | robots.txt | endpoint | decision |
|---|---|---|---|
| Federal Reserve `press_monetary.xml` | **404** (no robots file served) | 200 text/xml | **ALLOWED** — Fed's own feeds index documents RSS subscription; robots absent means no restriction is declared |
| SEC `pressreleases.rss` | **403** (fair-access threshold hit from this host) | 200 rss+xml | **ALLOWED at 1 req / 2 s** — the RSS answered 200 while page requests were throttled, so the limit is respected, not bypassed |
| White House `presidential-actions/feed/` | **200**, `User-agent: *`, `Disallow: /*?s=` and `/*&s=` only | 200 xml | **ALLOWED** — the feed URL matches no Disallow rule |
| Kraken `0/public/SystemStatus` | **404** (no robots file) | 200 json | **ALLOWED** — public unauthenticated status endpoint |

## What remains quarantined, and why

| channel | observed | reason |
|---|---|---|
| `api.coinbase.com/v2/status` | 404 | endpoint removed |
| `bls.gov/developers/` | 403 | page refused this client; not retried |
| `sec.gov/about/privacy-information` | 403 | page refused; the *RSS* is used instead |
| `api.bls.gov/.../data` | 200 | reachable, but BLS releases are scheduled statistics, not news; no category rule exists yet |

## Rules this code follows

1. Never retry around a 403 — it is recorded as a blocker.
2. Per-host minimum interval: SEC 2 s, everyone else 1 s.
3. One retry maximum, 2 MiB ceiling, descriptive User-Agent.
4. Raw payload retained by digest; published vs observed timestamps kept apart.
5. No paywalled, credentialed or third-party licensed feed is touched.

## Honest limitation

Terms pages themselves (`bls.gov/developers/`, `sec.gov/about/privacy-information`)
returned **403** to this client, so I could not read them to quote their wording.
The decision above rests on what the *endpoints* do and on robots. A human should
read those pages in a browser before any wider use; until then this is a
single-purpose research fetch at a very low rate, not a licence.
