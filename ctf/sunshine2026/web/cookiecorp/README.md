# CookieCorp - Cookie Jar Overflow

- **Platform:** SunshineCTF 2026
- **Category:** Web
- **Date:** 2026
- **Link:** `<web-challenge-url>` (challenge instance, closed after the event)
- **Flag:** `sun{...}` (masked)

CookieCorp lets a baker create a recipe and submit it to an automated Quality Inspector. The interesting part is that the Inspector loads the recipe in a real browser and executes JavaScript which turns every ingredient into a cookie.

The goal is to abuse that cookie-writing primitive so that the Inspector's request to `/api/seal` is accepted as coming from the Chief and returns a **Golden Seal**.

Files:
- `solve.md` - the full writeup, including the failed approaches, the precise eviction mechanism, and what was verified how
- `scripts/exploit.py` - the working exploit (281-cookie recipe)
- `scripts/repro_eviction.py` - minimal local reproduction of the browser-side cookie eviction (tiny local server + headless Chromium via Playwright)
