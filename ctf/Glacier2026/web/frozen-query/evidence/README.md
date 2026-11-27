# Frozen Query — historical solution and verification

**Status:** solved, flag accepted by GlacierCTF platform (participant confirmation).

## Evidence supplied by the participant

The first uploaded `frozen-query.tar.gz` is a historical research bundle, not merely an organizer package. It contains:

- `solve.md`, `solve.ru.md` — the participant's detailed solving chronology.
- `README.md`, `README.ru.md` — shorter summary.
- `scripts/exploit_frozen_query.py` — **original** single-query script using double gzip.
- `assets/app.py`, `assets/backend.py` — organizer source extracted into the research bundle.
- `assets/frozen-query.tar.gz` — original organizer archive, byte-identical to the second upload.

The second uploaded `frozen-query (1).tar.gz` contains the original challenge source and deployment files.

## Recorded live test

The participant's write-up reports these historically observed results:

```text
GET /api/bulletin          → 405 Allow: QUERY
QUERY /api/bulletin valley → normal public bulletin
QUERY with clear summit/flag → 403 blocked by glacier-edge
QUERY with one gzip         → 403 blocked
QUERY with wrong window     → X-Cache: MISS
QUERY with double gzip,
  gzip,gzip and matching window → HTTP 200, X-Cache: HIT,
  summit bulletin, flag present
```

No standalone raw HTTP transcript or packet capture was present in the research bundle. The archived report masks the real flag as `gctf{QU3RY_...r3qu35t}`; **the complete flag value does not occur in the two uploaded archives**. This is a limitation of the retained text, not a question about the accepted status.

## Verification in the current turn

- The two original `app.py` files have matching SHA-256 values.
- The two original `backend.py` files have matching SHA-256 values.
- The embedded original organizer archive in the research bundle matches the separately uploaded organizer tarball.
- The exploit script's headers and request-body construction match the source-level WAF and cache logic.

No request was issued to the competition server during this publication update.
