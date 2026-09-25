# PHault - solving the blind SQL injection

## The target

A single PHP endpoint: `/?id=`. The index page renders its own source with `highlight_file()`, which immediately showed the query:

```php
$sql = "SELECT username FROM users WHERE id = " . $_GET["id"];  // bare concatenation
$res = $db->query($sql);
if (!$res) die("ill try to tell him, dw");
$row = $res->fetch_row();
```

A textbook SQL injection point, and the flag sat in a `flag` table. The hard part was that the app never shows anything: on success it prints the same phrase as on failure.

## Dead ends (in the order I hit them)

**1. In-band / UNION - dead.** Every response was byte-identical: same 4557 bytes, same md5 for `1`, `1'`, `1 OR 1=1`, `-1`, `0x1` and UNION variants. There is no echo channel, so nothing to read in the body.

**2. Time-based - dead.** `SLEEP(8)`, `BENCHMARK`, heavy cartesian JOINs - all returned in 2.59-2.64s. `SLEEP(1)` and `SLEEP(0)` were indistinguishable. I burned a few requests on timing variants before reading the source properly: a shutdown function pads every fast response up to 2.0 seconds (`usleep(2.0 - elapsed)` in `register_shutdown_function`, with the author's comment `// no timing attack!!`), and anything that would actually take longer never finishes. The timing channel is simply not there.

**3. File write - dead.** `INTO OUTFILE` silently did nothing: no FILE privilege (or an empty `secure_file_priv`).

**4. Classic error-based (extractvalue / updatexml) - dead.** `mysqli_report(MYSQLI_REPORT_OFF)` means failed `query()` calls return `false` without emitting warnings - the usual error channel is switched off.

## First foothold: an array warning as an information channel

`/?id[]=1` returned:

```
Warning: Array to string conversion in /var/www/html/index.php on line 14
```

Three facts from one request: the parameter reaches PHP as intended, `display_errors` is `On`, and the webroot is `/var/www/html`. With errors rendered into the body, the hunt for an error-based oracle was on.

## The channel that worked: PHP 8 mysqli `true` vs result set

The clue was in the source again. On PHP 8, `mysqli::query()` returns:

- a `mysqli_result` for a SELECT with rows,
- `true` when the statement executed but produced **no result set** (e.g. `SELECT ... INTO @var`),
- `false` on error.

The app does `$res->fetch_row()` unconditionally after the error check. `fetch_row()` on the boolean `true` is a hard Fatal error - and `display_errors=On` puts that fatal right into the body.

So I appended `INTO @a` to my probe query. Now the body became a two-state oracle:

- body length 4744, contains `Fatal error` (query OK, `fetch_row()` on bool) = condition TRUE
- body length 4557, the die phrase = query FALSE

Sanity probes:

```
1 INTO @a                                     -> Fatal (oracle alive)
1 AND (SELECT 1 FROM flag LIMIT 1) INTO @a     -> Fatal (table exists)
1 AND (SELECT 1 FROM no_such_table LIMIT 1) INTO @a -> die
```

One pitfall on the way: `1 AND (SELECT 1 FROM users) INTO @a` (no LIMIT) came back as die and briefly looked like the table did not exist. With `LIMIT 1` it was Fatal. Use `LIMIT 1` in existence probes, always.

## Building the conditional: a runtime error, not a prepare-time one

Natural idea: `IF(<cond>, 1, (SELECT 1 FROM no_such_table))`. It died in **both** branches - because a missing table is resolved at prepare time, before the condition ever runs. Constant errors (`POW(10,400)`, bad geometry literals) behave the same way: never a runtime decision. That was another dead end, useful to know.

The working false branch is a true runtime error - a duplicate entry, which only fires if the query is actually executed:

```sql
(SELECT 1 FROM (SELECT COUNT(*),FLOOR(RAND(0)*2)x FROM information_schema.columns GROUP BY x)t)
```

Final probe shape:

```sql
1 AND IF(<cond>, 1, <RTE>) INTO @a
```

- `<cond>` true -> branch `1` -> query OK -> Fatal body (4744)
- `<cond>` false -> RTE executes -> duplicate entry -> die body (4557)

Verified with trivial conditions (`1=1`, `1=2`) before touching the flag query.

## Exfiltrating the flag

With a reliable boolean oracle, extraction is mechanical:

- flag length: `LENGTH((SELECT flag FROM flag LIMIT 1)) > mid`
- each character: `ASCII(SUBSTRING((SELECT flag FROM flag LIMIT 1), N, 1)) > mid`

Binary search over ASCII [32,126], about 7 requests per character. I read the source to confirm the `flag` table had a `flag` column (same LIMIT 1 existence probe over `information_schema.columns`).

For pacing I kept it slow and single-threaded: Firefox User-Agent, a 1.3s pause between probes, one GET per probe, every response logged to a file. The full run was ~480 requests over ~14 minutes without tripping anything.

The script is attached: `scripts/exfil.py`. It first searches the length, then walks the flag character by character. The instance host is redacted (`HOST = "<CHALLENGE-INSTANCE-HOST>"`) - the original challenge instance is long expired, so set it to your own instance before running. The search logic itself was verified offline first (stub `urllib.request.urlopen` and `time.sleep`, run the unchanged script against a known flag) before pointing it at the live endpoint.

## Result

```
FLAG: pwnsec{728c...d674}
```

(24 characters, extracted one by one - the full value is masked here.)

## What this one taught me

1. Identical responses do not mean the injection point is useless - find an observable difference somewhere else (a warning, a fatal, a header, timing).
2. Anti-timing padding (`usleep` to a fixed response time) is recognizable: everything comes back at the same wall time, and long queries never return at all. Don't fight it - switch channels.
3. PHP 8's `mysqli::query()` returning `true` vs a result set vs `false` is a clean boolean oracle whenever `display_errors` is on.
4. Conditional payloads must branch on a **runtime** error; prepare-time errors kill both branches.
5. Automate the repetitive part, but verify the automation offline against a known answer first.
6. `highlight_file()` on an index page is a gift: read the source before brute-forcing anything.