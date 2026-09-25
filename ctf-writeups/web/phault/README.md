# PHault - Blind SQL Injection

- **Platform:** ctf.ae (pwnsec)
- **Category:** Web
- **Difficulty:** Easy (249 points)
- **Date:** 2026
- **Flag:** `pwnsec{728c...d674}` (masked)
- **Link:** https://ctf.ae (challenge instances on the platform are time-limited; the original instance for this solve has expired)

The challenge is a single PHP page that takes an `id` parameter. The page source is displayed right on the index via `highlight_file()` (a gift in itself), and the page never echoes anything useful - success and failure look identical. The task is to extract the flag from the database with no visible output and no usable timing channel: classic blind SQL injection.

Contents of this folder:

- `solve.md` - the full solving path, including dead ends
- `scripts/exfil.py` - the binary-search exfiltration script (set `HOST` to your own instance)