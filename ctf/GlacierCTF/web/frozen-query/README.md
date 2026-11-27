# Frozen Query — GlacierCTF 2026

[Русская версия](README.ru.md) · [Technical write-up](solve.md)

**Author:** EmberZero · **Category:** web / http / cache / rfc10008 · **Points:** 50

**Status:** Solved — flag accepted by the platform (participant confirmation).

**Challenge URL:** https://frozen-query.web.glacierctf.com/

The Glacier Avalanche Service uses the HTTP `QUERY` method from RFC 10008. Only auditors should be able to read the `summit` bulletin, which contains the flag.

The weakness combines **inconsistent Content-Encoding normalization** and a **shared edge cache that does not partition by authorization**. Non-auditor requests go through a WAF that gunzips at most once, while the cache-key code gunzips every advertised layer. With `Content-Encoding: gzip, gzip`, the WAF sees compressed bytes but the cache receives the exact canonical JSON used by the auditor. The scheduled auditor refreshes its privileged response every five seconds; the attack obtains that response as `X-Cache: HIT`.

## Repository files

- [solve.md](solve.md) — complete source-based English explanation.
- [solve.ru.md](solve.ru.md) — full Russian explanation.
- [scripts/exploit_frozen_query.py](scripts/exploit_frozen_query.py) — original exploit preserved in the uploaded research archive.
- [scripts/solve.py](scripts/solve.py) — configurable bounded-retry adaptation.
- [assets/README.md](assets/README.md) — two checked handouts, source hashes, and organizer file inventory.
- [evidence/README.md](evidence/README.md) — historical confirmation and limits.

The **first uploaded tarball** contains the participant's full EN/RU write-ups, original exploit, and extracted `app.py` and `backend.py`; the **second** is the original organizer handout. Both sets of source files match byte-for-byte. Earlier GitHub documentation relied on a third-party reconstruction; this revision is grounded in the participant's uploaded solution and actual challenge source.

As for other GlacierCTF write-ups here, the literal flag is masked in public documentation.
