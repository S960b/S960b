# Frozen Query — original organizer handout

Two distinct files were uploaded:

1. `frozen-query.tar.gz` — participant's research bundle containing EN/RU write-ups, `scripts/exploit_frozen_query.py`, an embedded organizer archive and extracted `app.py`/`backend.py`. SHA-256: `7956dcd59eabe3e0966e1a58abf58d39d44a1920b077d79092a8590e4ee86b25`.
2. `frozen-query (1).tar.gz` — **original organizer handout**. SHA-256: `f0952f989b28c25f4856f4d0c7412b38052c27a5e2df033ef4a62b0314e21c87`.

The original handout is also embedded unchanged in the research bundle at `assets/frozen-query.tar.gz` (same SHA-256 as the second upload).

Organizer files:

```text
frozen-query/requirements.txt
frozen-query/Dockerfile
frozen-query/flag.txt
frozen-query/sha256sum
frozen-query/deploy.sh
frozen-query/entrypoint.sh
frozen-query/deploy.bat
frozen-query/src/backend.py
frozen-query/src/index.html
frozen-query/src/app.py
```

Source files were compared byte-for-byte across the separate organizer handout and extracted research assets:

| Original file | SHA-256 |
| --- | --- |
| `src/app.py` | `bd7bd62881fad5eab780a418a19d84acf5e215153db099b5ee57b39fd0ef108a` |
| `src/backend.py` | `d1712d748746db6b13e3beb20319c283d76d1185f1e05a1a919af5aee0a49f48` |

`app.py` implements the edge WAF, decompression, shared cache, cache-key computation and auditor loop; `backend.py` implements bulletin data and the auditor authorization check.

The organizer's `flag.txt` is a **placeholder**, not the live submitted flag. Both original archives are preserved as conversation attachments; the public repository provides source-level explanations and the original exploit script, not redundant binary archive copies.
