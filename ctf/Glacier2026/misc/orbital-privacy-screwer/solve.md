# Orbital Privacy Screwer — Morse code hidden in SSE timing

[Русская версия](solve.ru.md)

## 1. The satellite dashboard was a decoy

The TCP launcher at `challs.glacierctf.com:13381` started a temporary web instance (about **600 seconds**) on a unique `https://<id>.orbital-privacy-screwer.web.glacierctf.com` hostname. Its frontend showed an orbital target-acquisition dashboard.

The browser opened an `EventSource` at

```text
GET /api/acquire?q=<query>&reqId=<id>&sessionId=<id>
```

A complete stream yielded one `cycle_start`, **67** `step` events, and one `complete`. The stream lasted about **7.936 seconds**, and each step contained a progress percentage, a phase name, and a `carrier` boolean. The apparent surveillance query was a distraction.

The organizer's `src/server.py` is pure Python (ThreadingHTTPServer). Its module docstring explicitly names a **Morse steganographic encoder with flag count verification**. The relevant constants are:

```python
DOT_DURATION_MS = 67
DASH_DURATION_MS = 133
SEPARATOR_DURATION_MS = 200

TARGET_DOTS = 30
TARGET_DASHES = 22
TARGET_SEPARATORS = 15
EXPECTED_TOTAL_EVENTS = 67
EXPECTED_DURATION_MS = 7936
```

The real flag file is loaded by the server and converted to uppercase before transmission. The organizer's packaged `flag.txt` is only a fake placeholder.

## 2. Source-to-wire path

`load_and_verify_gctf()` reads and uppercases the configured flag, looks up every character in `MORSE_CODE`, and checks the number of dot, dash and separator symbols.

`build_morse_event_sequence()` converts those symbols to entries with associated `duration_ms`. The request handler sends each `step` SSE event and then sleeps for its duration using `precise_sleep()`. It does **not** put the Morse letter or duration directly into the JSON sent to the client. The **time until the next step** (or the final `complete`) carries the symbol.

Example source logic:

```python
for event_data in PRECOMPUTED_TRANSMISSION:
    payload = {"type": "step", "seq": event_data["seq"],
               "phase": event_data["phase"], "carrier": event_data["carrier_active"]}
    send_sse_message(payload)
    precise_sleep(event_data["duration_ms"] / 1000.0)
```

`carrier` is true for dots and dashes and false for separators, but cannot distinguish dot from dash. The query's SHA-256 hash, `sector_code`, `telemetry_variance`, percentages and six stage names are noise, not the encoding.

For ordinary queries the code computes:

```python
normalized = " ".join(query.strip().lower().split())
digest = sha256(normalized.encode()).hexdigest().upper()
hash = digest[:8]
sector_code = f"SEC-{int(digest[8:12],16) % 99 + 1:02d}"
variance = int(digest[12:14],16)/255*0.08 + 0.01
```

The source also contains special commands (`status`, `help`, `ping`, `assets`, `sensors`, `grid`, `ephemeris`), which did not provide the secret.

## 3. Why conventional probing failed

Before consulting the local handout, the investigation tested directory traversal, command/SQL/template format injection in `q`, hidden URL parameters, predictable `sessionId`, altered headers, session races, and an assumed special `SEC-7E` sector code. None yielded the flag.

It also inspected `background-image.png` for LSB channels, extra PNG bytes, and hidden writing near the `targetPixel` at (853,280). No useful payload was recovered from that image.

The server behaved consistently: `q` is normalized and hashed; empty queries and input longer than 128 characters are rejected; concurrent unique queries retain their own results; `POST /api/cancel` aborts active sessions.

These negative tests are useful context: the exploit path was **decoding an intentional timing covert channel**, not bypassing a web security control.

## 4. Capture 67 inter-arrival intervals

The historical final script is `scripts/morse4.py` in the participant's evidence archive. It makes six independent HTTPS requests, with different `reqId` and `sessionId` values, and timestamps every `step` plus `complete` on arrival using `time.perf_counter()`.

There are **68 arrival timestamps**, yielding exactly **67 differences**. The last `step → complete` interval matters: dropping it would lose the final Morse symbol.

For each run it rejects incomplete or heavily jittered captures, then takes the **median at each of the 67 positions**. This is important because the reverse proxy can coalesce SSE frames: one early run produced spurious 0 and 484.8 ms gaps.

The six successful runs had 67 deltas each, with median gaps between 131.6 and 132.7 ms. The source durations are far enough apart to use simple thresholds:

| Inter-arrival time | Morse |
| --- | --- |
| less than 100 ms | `.` |
| 100–170 ms | `-` |
| above 170 ms | letter separator |

The exact captured median sequence is preserved in the original [scripts/verify_decode.py](scripts/verify_decode.py). The portable [scripts/capture_morse.py](scripts/capture_morse.py) uses the same algorithm but accepts a URL argument instead of reading a private file under `/tmp/gigatool`.

## 5. Decode and verify

Classifying the 67 median intervals gives:

```text
--. -.-. - ..-. ----- -... .-.. .. ...- - -.. .-. ...-- ... --... -
```

Inverting the server's Morse alphabet yields:

```text
G C T F 0 B L I V T D R 3 S 7 T
GCTF0BLIVTDR3S7T
```

The counts match the original source exactly:

| Metric | Expected | Observed |
| --- | ---: | ---: |
| Dots | 30 | 30 |
| Dashes | 22 | 22 |
| Separators | 15 | 15 |
| Steps | 67 | 67 |
| Ideal duration | 7936 ms | 7936 ms after re-encoding |
| Sum of observed median intervals | ~7936 ms | ~7947 ms in archived verification |

The archived `verify_decode.py` re-encodes `GCTF0BLIVTDR3S7T` and checks each of the 67 measured values: **no classified mismatches**. The reported timing sum differs by a few milliseconds due to network/scheduling jitter, not a symbol error.

The raw secret is a *16-character uppercase plaintext*, not a ready-made brace-wrapped `gctf{...}` string. Extracting the `GCTF` prefix, lowercasing the remainder and wrapping it with the platform's CTF syntax produces the submitted flag. The participant has **explicitly confirmed** acceptance of that exact assembled value by the GlacierCTF platform.

According to the public write-up convention, the accepted full value is represented as `gctf{...}`; the transmission string above is left intact to make the decoding reproducible.

## 6. Evidence limitations and one historical discrepancy

The agent archive `glacierctf_orbital-privacy-screwer_materials.zip` includes `REPORT.md`, the original handout, scripts, static files, excerpts of command output, and verification results.

The report predates the user's explicit confirmation of platform acceptance. It also mentions an unrelated, longer candidate flag that appeared in an earlier exchange but **does not match the 67-event Morse transmission**. We do not treat that candidate as the decoded remote output. The participant's later statement resolves the status of the short Morse-derived submission.

The live instance's `pct` values were evenly spaced, while the archived source rounds `pct` according to accumulated durations. This could indicate a different deployed build; no source of that exact deployed build was retained. It did **not** affect the 67 measured time intervals or the decoding.

The measured output was transcribed into `evidence/*.txt` after the original session; raw packet captures, server-side stdout and a screenshot of the submission response were not preserved. Platform acceptance is therefore attributed to the participant. No challenge server was contacted for this write-up.

## 7. Lessons

- SSE can carry secrets in timing even when all JSON fields look like telemetry.
- Measure **step-to-step and final step-to-complete** gaps, not only fields in each event.
- Repeat the experiment and aggregate per-position medians to defeat proxy jitter.
- Verify the recovered message against the server's symbol counts and time budget.
- Keep raw transmitted text distinct from the submitted flag-format transformation.
