# VecNet - embedding inversion

- **Platform:** SunshineCTF 2026
- **Category:** Misc
- **Difficulty:** 500 points
- **Date:** 2026
- **Flag:** `sun{k33p...secur3!}` (masked)
- **Link:** https://2026.sunshinectf.org (challenge instances are time-limited; the original host is not included here)

VecNet was a small "semantic mail defense" site. The public page looked like a static landing page, but the deployed `.git` directory was exposed. The repository history leaked an internal archive download endpoint, a reverted config file, and credentials for a webmail interface. The webmail then pointed to a ChromaDB-backed vector store where one important document was stored only as an embedding.

The main trick was that embeddings are not safe as secrets. ChromaDB exposed three records: a plaintext SHA256 hash, a plaintext magic string, and one embedding-only password requirement. I inverted the requirement embedding with `vec2text`, used the recovered sentence plus the hash to brute-force the exact archive password, and opened the encrypted 7z archive.

Files:

- `solve.md` - the full story, written for beginners, including dead ends and why they failed
- `scripts/git_recover.py` - helper for recovering files from the exposed `.git` directory (host placeholder)
- `scripts/chroma_dump.py` - helper for dumping the ChromaDB collection by UUID (host/secret placeholders)
- `scripts/brute_password.py` - local SHA256 brute-force for the final archive password pattern
