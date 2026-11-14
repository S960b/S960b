# Fishies: solving notes

[По-русски](solve.ru.md)

## 1. Task

A fish-sharing application with an administrator Puppeteer bot chained reflected/stored script injection, an unsafe deep merge and inherited browser launch options.

Materials: `server.js`, package files, Handlebars template; saved later successful download evidence and earlier Git-bridge investigations.

## 2. Analysis

The custom `escape()` walks a mutable string, replaces `<`/`>` with longer entities, then increments `index` by the replacement length and also advances the loop index. Adjacent angle brackets can leave an exploitable HTML tag. `POST /api/fish/:id/report` launches Puppeteer with an HttpOnly admin cookie. From the bot, attacker-controlled JavaScript can call the admin-only `PATCH /api/admin/fish/:id/fishData`. Its recursive `combine(target,source)` does not reject `__proto__`, permitting pollution of `Object.prototype`. Puppeteer 25.16.0 reads inherited options including `env`; the separate audit confirmed the downstream Chromium process may receive polluted launch environment.

## 3. Solving procedure

1. Add a fish with a crafted `ascii` value containing adjacent brackets and an `img onerror` handler. Report the fish to trigger the authenticated review bot.
2. Use the bot's same-origin privileges to send the admin PATCH request with a nested `__proto__` object. This is the route into global prototype pollution, not a direct cookie theft; the admin cookie is HttpOnly.
3. The investigation established an environment-option route for Puppeteer and explored `downloadBehavior` for writing a library to a path used at the next Chromium launch. The final archive shows a later successful run that exposed a copy at `/static/dl/flag.txt` and recovered the accepted flag.
4. Read the stored output through the application static route. Stop temporary servers used during the run.

## 4. Evidence

`server.js` proves the faulty escape, admin review, privileged PATCH and vulnerable recursive merge. Previous Git-bridge reports prove bot XSS execution and polluted `env` inheritance locally, while the later archive's `evidence/solve_2.txt` records an actual download of the flag from `/static/dl/flag.txt`. The earlier task's final 'no flag' conclusion predates this later evidence.

**Result:** 1 accepted flag(s), masked in the public repository (`PP{...}`).

## 5. Limits

The final in-browser payload, shared-object source, intermediate download settings and complete successful stdout were not included. We separate source-proven primitives, local tests and the later success record rather than claim each intermediate was fully logged.

## 6. Mitigation

Use a correct context-aware HTML escaping library, avoid rendering untrusted markup, reject `__proto__`/`constructor`/`prototype` in recursive merges, use null-prototype dictionaries and isolate the privileged bot.
