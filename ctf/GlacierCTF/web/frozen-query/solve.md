# Frozen Query — a double-gzip gap between WAF and cache key

[Русская версия](solve.ru.md)

**Provenance:** The participant's task is confirmed solved by the supplied list, but the exact original conversation payload and flag could not be recovered. The following technical solution was **independently reconstructed** from the published D23R walkthrough (21–22 November 2026): https://dua2z3rr.github.io/posts/Frozen-Query-Walkthrough/ . It documents the same original challenge; it is not falsely presented as a verbatim participant transcript.

## Architecture

The Flask application implements `QUERY /api/bulletin`. The summit entry in `src/backend.py` includes a flag and can normally be read only by the auditor, who possesses `AUDIT_TOKEN`.

A separate background auditor calls the internal endpoint about **every five seconds** with

```json
{"region":"summit","fields":["level","note","flag"],"window":<floor(unix_time/30)>}
```

and an authorization header. It bypasses the cache on its own request and primes a cache entry valid for **30 seconds**. Cache entries store HTTP status, headers and body.

The cache lookup key is SHA-256 of the following four newline-separated strings:

```text
/api/bulletin
application/json
application/json
{"fields":["level","note","flag"],"region":"summit","window":<window>}
```

The components are path, normalized Content-Type (vendor media type is normalized to application/json), Accept and canonical JSON (sorted keys, minimal separators). Crucially **the key lacks auditor authorization**, allowing unprivileged cache hits on privileged response bodies.

## Bypass the WAF

Normal unauthenticated requests are checked before accessing cache. The blocked words include `flag`. With a single gzip layer, the WAF decompresses the data and sees the forbidden key, returning HTTP 403.

However, `waf_allows(raw, encoding)` decompresses **only the last gzip coding**; the later `decode_content(raw, encoding)` loops through **every coding**.

Set `Content-Encoding: gzip, gzip` and send `gzip(gzip(plaintext_json))`. The first decompress leaves still-compressed bytes, which are not valid visible JSON and do not contain the literal word `flag`; the WAF allows them. The handler then performs two decompress operations and recovers the auditor's exact JSON before cache-key creation.

The auditor's cache entry is reused since authorization is absent from the key. Timing matters: the JSON `window` equals `int(time.time()//30)`, and the auditor refreshes the cache about every five seconds. Retry for a few seconds to hit the primed key.

## Reproduction

See [scripts/solve.py](scripts/solve.py), a **newly written adaptation**, not a copy of the other author's source or of the participant's missing original. Requires `requests` and an explicitly specified challenge deployment URL. It uses HTTP method QUERY, vendor Content-Type, Accept application/json, a double compressed body, and bounded retries.

A correct response has HTTP 200, `X-Cache: HIT`, and a summit bulletin with `flag`. A local organizer test may contain the fake `gctf{FAKE_FAKE_FAKE_FAKE_FAKE_FAKE}`; do not confuse this with the accepted competition flag.

## Root cause

The WAF validates a representation different from the normalized representation that the application consumes, while the cache key is shared across principals. Fix by applying all content decoding once **before validation**, and by preventing private responses from being stored in a cross-user cache or by including a validated authorization scope in the cache key.

## Independent source

Technical details of WAF and cache semantics: [D23R, Frozen Query Walkthrough — GlacierCTF 2026](https://dua2z3rr.github.io/posts/Frozen-Query-Walkthrough/), published 21 November 2026 and updated 22 November 2026. The present exposition and solver are independent rewording and implementation; no full article or source code has been copied.
