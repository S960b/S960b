# PHault - Blind SQL Injection

- **Platform:** ctf.ae (pwnsec)
- **Category:** Web
- **Difficulty:** Easy (249 points)
- **Date:** 2026
- **Flag:** `pwnsec{728c...d674}` (masked)
- **Link:** https://ctf.ae (challenge instances are time-limited; the original instance has expired)

A single page with an `id` parameter. The page puts your input straight into an SQL query and never shows the result - success and failure look exactly the same. The flag is hidden in the database, and extracting it means asking the server yes/no questions and reading the answers from error pages. That is a blind SQL injection.

Files:

- `solve.md` - the full story, written for beginners, including what did not work
- `scripts/exfil.py` - the Python script that pulled the flag character by character (set `HOST` to your own instance)