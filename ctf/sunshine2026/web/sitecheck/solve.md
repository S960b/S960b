# SiteCheck — writeup (web, SSRF deny-list bypass)

## What the task was

SiteCheck is a web-diagnostics service. The landing page is a login form: you enlist with a callsign + passphrase and get a personal console. The console has exactly one feature:

    POST /scan   url=<any http(s) address>

The server-side "drone" (a headless browser) opens that URL, measures the load time, counts the fetched files and returns a screenshot of the viewport (1280×800), stored under `/screenshots/<uuid>.png`.

The task text ends with the important sentence:

> The drone politely refuses to inspect internal or local addresses. Safety first!

So this is an SSRF: the question is not *whether* the server can be told to fetch an internal address, but whether the deny-list can be walked around. The interesting stuff lives inside.

---

## Step 1 — enlist and look at the console

Registration is a plain HTML form:

```bash
curl -s -c jar.txt --data 'username=sc1&password=pw12345678' <challenge-url>/register
curl -s -b jar.txt <challenge-url>/dashboard
```

The session is a signed cookie (`sc_session=<uuid>.<hmac>`, `HttpOnly`). The dashboard shows a single form posting to `/scan`, and the report page shows `Status`, `Load time`, `Files fetched`, `Result` and the snapshot.

Two useful early observations:

- the report page only renders the **stats and the image** — never the fetched HTML. So whatever the drone finds inside has to come back as a picture;
- headers say `nginx` → `X-Powered-By: Express` — the app is Express.

---

## Step 2 — confirm the filter and find out what it actually matches

The filter answers with the console page plus this line:

> For safety, SiteCheck will not inspect internal or local addresses.

I threw a matrix of "this is localhost, really" spellings at it (`scripts/filter_probe.py`):

```text
http://127.0.0.1:3000/profile              -> blocked by the filter
http://localhost:3000/profile              -> blocked by the filter
http://0.0.0.0:3000/profile                -> blocked by the filter
http://2130706433:3000/profile             -> blocked by the filter   (decimal IP)
http://017700000001:3000/profile           -> blocked by the filter   (octal IP)
http://127.1:3000/profile                  -> blocked by the filter   (short form)
http://[::1]:3000/profile                  -> ACCEPTED
http://[0:0:0:0:0:0:0:1]:3000/profile      -> ACCEPTED
http://[::ffff:127.0.0.1]:3000/profile     -> ACCEPTED
http://127.0.0.1.nip.io:3000/profile       -> ACCEPTED
http://localtest.me:3000/profile           -> ACCEPTED
```

So the deny-list is a *literal / IPv4-normalised* check. Every IPv4 spelling of `127.0.0.1` is caught, but:

- IPv6 loopback (`[::1]`, the expanded form, and the IPv4-mapped `::ffff:127.0.0.1`) is not in the list;
- a hostname that simply *resolves* to loopback (`127.0.0.1.nip.io`, `localtest.me`) is not in the list either — the filter never resolves DNS at all.

The first real signal: with `http://[::1]/` (port 80) the drone answered `ERR_CONNECTION_REFUSED` in the report — a *connection error*, not the filter message. That is proof the request was actually attempted: the filter was already bypassed and only the port was wrong.

### How you get to that list yourself (no scanner needed)

Nothing in that matrix is a lucky guess — it is a checklist that follows from one sentence in the task, and it takes about a dozen requests:

1. **Classify.** The task says the drone "refuses to inspect internal or local addresses". That is not a hint to hunt for SSRF — it *is* an SSRF, and the real target is whatever performs the address check.
2. **Guess how the check is written.** Address filters are hand-written lists of strings (`127.`, `localhost`, `0.0.0.0`, sometimes `::1`). So the check compares *text*, while the network works with *bytes and IPs*. Every bypass below comes from that mismatch.
3. **Walk the four categories of the same address.** One happy path per category is enough:
   - other notations of IPv4: `127.1`, `127.0.0.1.` (trailing dot), `2130706433` (integer), `017700000001` (octal), `0x7f000001` / `0x7f.1` (hex);
   - IPv6: `[::1]`, the expanded `[0:0:0:0:0:0:0:1]`, and the IPv4-mapped `[::ffff:127.0.0.1]` / `[::ffff:7f00:1]`;
   - DNS that resolves to loopback: `localtest.me`, `127.0.0.1.nip.io`, `sslip.io` — public wildcard services that exist exactly for this;
   - URL-logic tricks: a public URL that redirects inward, or `http://public.com@127.0.0.1/` (filter reads the part before `@`, the network dials the part after it).
4. **Read the answer, don't brute-force.** The report echoes the outcome, so every attempt tells you something: the filter message means *text* matched, `ERR_CONNECTION_REFUSED` means the filter is *already bypassed* and only the port is wrong. The moment you see that second kind of answer, the challenge is reduced to guessing a port — and the stack (`X-Powered-By: Express`) points at 3000.
5. **Why `[::ffff:127.0.0.1]` is knowledge, not luck.** IPv4-in-IPv6 mapping is a standard (RFC 4291) that resolvers and browsers implement; learn it once and it applies to every address filter you meet.

This is checklist work, not scanner work — there is no wordlist to throw at it, and the interesting part is noticing *which* answer changed. PayloadsAllTheThings (SSRF) and HackTricks carry the same short list. A fuzzer is for parameter/path enumeration; this is 15 lines typed by hand.

---

## Step 3 — find the internal service

`ERR_CONNECTION_REFUSED` meant "filter bypassed, but nothing on port 80". Since the public app is Express, the natural guess is the Express default port:

```text
http://[::1]:3000/         -> Status 200, 9-10 files fetched
```

Same app, but this time the drone was talking to the internal instance, and the snapshot showed a completely different header than our freshly registered bronze inspector:

```text
Console   Personnel File   ◈ admin   Disembark
SITECHECK PERSONNEL FILE
Inspector: admin                    OMEGA
Chief Inspection Drone – Clearance OMEGA
◇ Overview
Automated SiteCheck agent. Executes every diagnostic on behalf of the fleet.
```

That is the built-in admin inspector. The internal instance treats a caller coming from loopback as **its own drone/admin**, not as an anonymous visitor. (Our own account is `Junior Inspector · Clearance BRONZE`, and its clearance section reads `REDACTED · insufficient clearance`.)

Worth being explicit about the trust model: the drone does **not** forward the requesting inspector's session — scanning the public `<challenge-url>/profile` renders the *login page*, not a profile. The `admin` view was not "our session"; it is simply what the internal instance shows to a loopback caller.

---

## Step 4 — read the flag

The personnel file is long: the `#clearance` section sits below a ~1400px filler block, and the drone only captures a 1280×800 viewport. The page's own CSS comments that anchor jumps are instant "so the drone snapshot stays deterministic" — so the fragment decides what lands in the picture:

```text
http://[::1]:3000/profile#clearance
```

The snapshot is the whole deliverable of this step (see `assets/sitecheck-clearance-plate-redacted.png`; the flag below is masked, a few characters in the screenshot are blanked too):

> ◈ Clearance Data — CLASSIFIED
> Restricted personnel token — visible only to holders of this file:
> `sun{fr4gm3nt3d_r3fl3ct10ns...futur3}`

The plate is large and legible, so there is nothing clever to extract — you just open the picture and read it by eye. `scripts/solve.py` automates only the part up to this point: it registers an account, gets the drone to the internal dossier and **saves the snapshot**, then stops and lets a human read the flag.

```bash
python3 scripts/solve.py        # set BASE first; prints the path to the PNG
```

Flag (masked): `sun{fr4gm3nt3d_r3fl3ct10ns...futur3}`

---

## What I tried first (and why each failed)

1. **Direct IPv4 loopback** (`http://127.0.0.1:3000/profile`) — dead, the filter message appears instead of a report.

2. **Decimal / octal / short IP encodings** (`2130706433`, `017700000001`, `127.1`) — dead. This is the classic bypass against naive `url.startsWith("127.")`-style checks, but SiteCheck normalises IPs, so all of them are refused.

3. **Redirect-based bypass** — give the drone a public URL that 302s to the internal one. Partially dead:
   - `httpbingo.org/redirect-to?...` refuses to redirect anywhere outside its own allow-list (`403 Forbidden redirect URL`);
   - `httpbin.org/redirect-to?...` does follow the redirect, but renders the **login page** of the internal app, not the admin dossier.
   Practically this was a rabbit hole — the direct IPv6 URL already works and is the intended path.

4. **Looking for the flag in the public app** — `/flag`, `/flag.txt`, `/admin`, `/.env`, `/package.json`, `/.git/config`, `/robots.txt` all `404`. Everything sensitive lives inside, behind loopback.

5. **Automated flag extraction from the snapshot** — a mistake in the other direction. I originally wrote a "solver" that OCR'd the screenshot to auto-print the flag. After fighting the stylised font for a while I realised no script is needed: the plate is meant to be read by eye, so the tool should simply stop at the picture. Keep the finished product simple.

The bug is a **deny-list that does not cover IPv6 (or DNS) forms of loopback**, plus an internal instance that grants elevated clearance to loopback callers.

---

## Result

The internal instance's admin personnel file leaks the flag plate; it is read straight off the drone snapshot by eye.

Flag (masked): `sun{fr4gm3nt3d_r3fl3ct10ns...futur3}`

---

## One-paragraph version (for interviews)

SiteCheck is an authenticated URL-fetch service ("diagnostics drone") with a deny-list that is supposed to stop internal and local addresses. The list normalises and blocks every IPv4 spelling of loopback (`127.0.0.1`, `localhost`, `0.0.0.0`, decimal `2130706433`, octal, short `127.1`) but misses IPv6 loopback, the IPv4-mapped `::ffff:127.0.0.1`, and hostnames that resolve to loopback (`127.0.0.1.nip.io`). Using `http://[::1]:3000/profile#clearance` I reached the internal copy of the app on the Express port, which serves loopback callers as its built-in `admin` / `OMEGA` inspector; the `#clearance` anchor puts the restricted section inside the 1280×800 drone snapshot, and the flag is read straight from the picture.

---

## Lessons learned

1. **"Internal addresses are refused" is a promise about a string filter, not about the network.** Always enumerate encodings: decimal, octal, short form, IPv6, IPv6-mapped IPv4, and DNS names that resolve to loopback.
2. Distinguish *filter message* from *connection error*: `ERR_CONNECTION_REFUSED` on a loopback URL is already a bypass, with the wrong port.
3. Look at the whole deny-list behaviour, not just one case — `nip.io`/`localtest.me` passing means the check never resolves the hostname.
4. When the SSRF output is a screenshot, fragments (`#clearance`) are a legitimate way to control what lands in the viewport.
5. Trust on the *source address* is a real pattern: the same app behaved as `BRONZE` on the public interface and `OMEGA` on loopback.
6. Know where the "solve" actually ends: sometimes the deliverable is a picture, and the human step (read it) is the point — don't over-build.
