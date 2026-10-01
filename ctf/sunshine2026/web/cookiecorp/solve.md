# CookieCorp - writeup (web, cookie jar overflow)

## What the task was

CookieCorp is a small web application where a normal baker can create a recipe and submit it for review.

The interesting endpoint is:

    POST /api/seal

A normal baker cannot call it directly:

    {"error":"inspector authorization required"}

The site says that ordinary inspectors can only apply the standard seal, while the CookieCorp Chief can award the Golden Seal.

The important part is the automated Inspector. When it opens a submitted recipe, the page contains `mixer.js`, which does this for every ingredient:

```js
document.cookie = ing.name + '=' + (ing.value || '') + '; path=/';
```

So the attacker controls both the **cookie name** and the **cookie value** that are written by the Inspector's browser.

The authentication cookies are initially protected with `HttpOnly`, so a simple `role=chief` ingredient is not enough from a normal baker session.

## What I tried first (and why each failed)

1. **Direct `/api/seal` with `role=chief`** - dead. The request came from my baker session and the server answered `403 inspector authorization required`.

2. **Mass assignment** - I added fields such as `role`, `isChief`, `admin`, `approved`, `goldenSeal`, `status`, etc. to the recipe request. The account still remained a baker and the batch received the standard seal.

3. **Stored XSS** - I tried to break out of the `window.__recipe` script with `</script>...`. The application escapes `<` in the JSON, so the payload is rendered as text and does not become executable HTML.

4. **Guessing individual cookie names and values** - common names such as `chief`, `inspector`, `isChief`, `goldenSeal`, `authorization`, and similar values all produced the standard seal.

So the problem was not simply finding the magic cookie name.

## The break: evicting the Inspector's HttpOnly role cookie

The recipe builder allows up to **300 ingredients**, and every ingredient
becomes a separate cookie in the Inspector's browser: `mixer.js` executes
`document.cookie = name + "=" + value + "; path=/"` for each one. So a single
review visit gives us up to ~300 cookie writes inside a privileged browser
context.

That matters because of a specific cookie-store rule: **a page script cannot
overwrite an HttpOnly cookie** with the same name/domain/path. The Inspector's
cookie jar contains two login cookies:

```text
session = <opaque id>   HttpOnly, Priority=High
role    = baker         HttpOnly, default priority
```

So a plain `role=chief` ingredient is *silently ignored* - the server still
sees `role=baker` and awards the STANDARD seal. This is the trap behind dead
ends 1 and 4: the decisive state lives in the Inspector's jar, not in my baker
session.

An HttpOnly cookie cannot be overwritten, but it **can be evicted**. Chromium
enforces a limit of 180 cookies per domain and, when the limit is crossed,
evicts cookies least-recently-used first, with the lowest-priority cookies
removed first (RFC 6265 eviction). `session` is protected by `Priority=High`;
`role` is not. The attack:

1. 275 filler ingredients push the jar past the 180-cookie limit.
2. Chromium evicts the oldest default-priority cookies - the HttpOnly
   `role=baker` is gone.
3. The trailing `role=chief` ingredient is no longer shadowed, so it **is**
   stored as the effective `role` cookie.
4. `mixer.js` then calls `/api/seal` with the still-valid `session`
   (Priority=High survived the eviction).
5. The server reads `role=chief` and awards the **GOLDEN SEAL**.

The winning payload used **281 ingredients**: 275 generated filler names
(`inspector_*`, `quality_*`, `chief_*`, `seal_*`, `auth_*`, `role_*`, ...)
followed by the privileged-looking cookies:

```text
role=chief
inspector=1
chief=1
quality_inspector=1
isChief=true
goldenSeal=true
```

Only `role=chief` drives the seal decision; the other trailing names were
extra guesses and are harmless. The ordering is the important part: the
privileged names must come *after* the filler, once the jar is full.

### What was verified live vs. reconstructed after the event

- **Verified live on the challenge instance:** the 281-cookie recipe made the
  Inspector return `GOLDEN SEAL` (the poll loop in `exploit.py`); direct
  `role=chief` from my baker session and single-cookie guesses from my own
  context always ended in STANDARD / `403 inspector authorization required`.
- **Verified locally, same browser engine:** `scripts/repro_eviction.py`
  replays the whole browser-side primitive. A headless Chromium (the same
  engine family as the Inspector bot) logs in against a tiny local server that
  sets the same cookies (`session` Priority=High + `role=baker`, both
  HttpOnly), runs the mixer, and calls `/api/seal` with the challenge's rule
  (GOLDEN iff `role == chief`). Chromium 136 output:

  ```text
  filler | seal    | role seen by server | session
  0      | STANDARD| baker               | present
  178    | STANDARD| baker               | present
  180    | GOLDEN  | chief               | present
  ```

  The flip happens exactly at Chromium's documented 180-cookie-per-domain
  limit, and the High-priority `session` cookie survives; the default-priority
  `role=baker` does not.
- **Reconstructed after the event:** the challenge shipped no server source,
  so the exact server-side rule (GOLDEN iff `role == chief`, `session`
  Priority=High) was pinned down after the event by cross-checking other
  public writeups. The browser-side mechanism above does not depend on that
  detail and is reproducible locally.

## Exploitation

The exploit is completely automatic:

1. Register a normal baker account.
2. Build a recipe with 281 cookie-producing ingredients: 275 filler cookies,
   then `role=chief` (and the other guesses) at the very end.
3. Submit the recipe for inspection.
4. Poll `/recipe/<id>` until the Inspector finishes.
5. Check for `GOLDEN SEAL`.

The relevant part of the script is simply:

```python
for i, name in enumerate(names[:275]):
    # generate many distinct cookies
    ingredients.append({"name": name[:48], "value": value[:64]})

for name, value in [
    ("role", "chief"),
    ("inspector", "1"),
    ("chief", "1"),
    ("quality_inspector", "1"),
    ("isChief", "true"),
    ("goldenSeal", "true"),
]:
    ingredients.append({"name": name, "value": value})
```

The resulting server-side state is enough for the Inspector's `/api/seal` request to produce:

```text
GOLDEN SEAL
```

## Result

The batch was accepted with the Chief's Golden Seal and the challenge was solved.

Flag: `sun{...}` (masked)

## One-paragraph version (for interviews)

CookieCorp lets you submit a recipe that an automated headless-Chromium
Inspector loads in its own browser, turning every ingredient into a cookie
write (`document.cookie = name=value`). The Inspector already holds an
HttpOnly `role=baker` cookie, and JavaScript cannot overwrite an HttpOnly
cookie - so a single `role=chief` ingredient never sticks. The trick is to
evict that cookie instead: the recipe builder allows 300 ingredients, and
Chromium evicts least-recently-used cookies past its 180-cookie-per-domain
limit. Flooding the jar with 275 filler cookies evicts the HttpOnly
`role=baker` (default priority) while the `session` cookie (Priority=High)
survives; the trailing `role=chief` ingredient is then stored as the real
role. The Inspector's own `/api/seal` call now carries `role=chief` and the
server returns the Chief's Golden Seal. The browser-side mechanism is
reproduced locally with a tiny server plus headless Chromium
(`scripts/repro_eviction.py`): the seal flips from STANDARD to GOLDEN exactly
at 180 cookies.

## Lessons learned

1. HttpOnly does not make a cookie unremovable - it only blocks JS from
   reading or overwriting it. A cookie-jar overflow (RFC 6265 eviction,
   Chromium's 180-per-domain limit) can evict it anyway.
2. Watch cookie priorities: in this challenge `session` was `Priority=High`
   and survived the eviction; the default-priority `role` was the eviction
   victim. Protection attributes on cookies are part of the security model.
3. A client-side primitive becomes powerful when it runs in a privileged
   automated browser - the attacker controls persistent state (cookies) in a
   context that then performs an authorized action.
4. Testing from the wrong session is misleading: the relevant request is the
   Inspector's `/api/seal`, not the attacker's baker requests.
5. When a feature has a large bounded list (300 ingredients), check whether
   its boundary interacts with browser limits - here it exceeded the cookie
   jar capacity.
6. Pin the mechanism down with a local reproduction using the same engine
   (Playwright + Chromium) instead of leaving the explanation at "it worked":
   the flip point (180 cookies) is reproducible and checkable.
