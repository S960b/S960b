# You Are Kidding Me - JWT `kid` key/path confusion

- **Platform:** SunshineCTF 2026
- **Category:** Web
- **Date:** 2026
- **Link:** `<challenge-url>` (challenge instance, closed after the event)
- **Flag:** `sun{...}` (masked)

A car blog ("Chrome Horizon") where a weekly reader pass is issued as a **JWT** signed with **HS256**. The Editor's Desk at `/admin` is only for JWT's whose `role` claim is `editor`.

The JWT header carries a `kid` ("key ID") field, and the server reads the HMAC secret **from the file path given by `kid`**. That path is never validated, so by pointing `kid` at a public file whose bytes we know, we can recompute a valid signature and forge an `editor` token.

Files:

- `solve.md` - the full write-up, including the dead ends
- `scripts/solve.py` - a working solver