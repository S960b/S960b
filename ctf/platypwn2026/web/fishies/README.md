# Fishies

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** web
- **Difficulty:** 3
- **Materials:** `server.js`, package files, Handlebars template; saved later successful download evidence and earlier Git-bridge investigations
- **Flags:** 1 accepted (`PP{...}`)

A fish-sharing application with an administrator Puppeteer bot chained reflected/stored script injection, an unsafe deep merge and inherited browser launch options.

**Root cause:** The custom `escape()` walks a mutable string, replaces `<`/`>` with longer entities, then increments `index` by the replacement length and also advances the loop index. Adjacent angle brackets can leave an exploitable HTML tag. `POST /api/fish/:id/report` launches Puppeteer with an HttpOnly admin cookie. From the bot, attacker-controlled JavaScript can call the admin-only `PATCH /api/admin/fish/:id/fishData`. Its recursive `combine(target,source)` does not reject `__proto__`, permitting pollution of `Object.prototype`. Puppeteer 25.16.0 reads inherited options including `env`; the separate audit confirmed the downstream Chromium process may receive polluted launch environment.

See the [solving path](solve.md).
