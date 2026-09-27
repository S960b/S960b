# CookieCorp - Cookie Jar Overflow

- **Platform:** SunshineCTF 2026
- **Category:** Web
- **Date:** 2026
- **Link:** `<web-challenge-url>` (challenge instance, closed after the event)
- **Flag:** `sun{...}` (masked)

CookieCorp lets a baker create a recipe and submit it to an automated Quality Inspector. The interesting part is that the Inspector loads the recipe in a real browser and executes JavaScript which turns every ingredient into a cookie.

The goal is to abuse that cookie-writing primitive so that the Inspector's request to `/api/seal` is accepted as coming from the Chief and returns a **Golden Seal**.

Files:
- `solve.md` - the full writeup, including the failed approaches and the final idea
- `scripts/exploit.py` - the working exploit
