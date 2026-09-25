# PHault - writeup (blind SQL injection, beginner level)

## What the task was

A page with an `id` parameter. The page does this with your input:

    SELECT username FROM users WHERE id = <your input>

Your input goes straight into the SQL query, without filtering. That is an SQL injection point: instead of a number, I can send SQL code. The catch: the page never shows the query result. Whatever happens, it answers with the same message - success and failure look identical. The flag is somewhere in the database.

"Blind" SQL injection means exactly this: the input reaches the query, but the answer is invisible, so the whole task is finding **any** way to tell "yes" from "no".

Nice bonus: the page displays its own source code (`highlight_file()`), so the exact query and the error handling were visible from the start.

## What I tried first (and why each failed)

1. **UNION SELECT** - asking the query to return the flag as a normal-looking row. Dead: the page never prints rows anyway. The response was byte-identical every time (same length, same md5) for valid, invalid and injected inputs.

2. **Time-based** - "if my condition is true, make the query wait 8 seconds" (`SLEEP(8)`). Dead: every request came back in about the same time (~2.6s). The reason was in the source: the app pads every response to at least 2 seconds (`usleep()`), so short sleeps are hidden and long queries never finish. The author even wrote `// no timing attack!!` in the code. No timing channel.

3. **Writing the flag to a file** (`INTO OUTFILE`) - dead: no FILE privilege, the write silently does nothing.

4. **Error-based via XML functions** (`extractvalue` / `updatexml`) - dead: MySQL errors are silenced (`mysqli_report(MYSQLI_REPORT_OFF)`), so nothing appears in the error text.

So: no visible output, no timing, no file writes, no errors. Four standard approaches, all gone.

## The break: PHP printed a warning

I sent `id[]=1` - an array instead of a number. PHP replied:

    Warning: Array to string conversion in /var/www/html/index.php on line 14

One request, three facts: the input reaches PHP, PHP shows errors in the response (`display_errors = On`), and the webroot path leaked. If PHP errors are visible, there may be a way to make them answer my questions.

## The oracle: "Fatal error" vs the normal answer

The source shows what happens after the query:

    $res = $db->query($sql);
    if (!$res) die("ill try to tell him, dw");
    $row = $res->fetch_row();

In PHP 8, `mysqli::query()` can return `true` instead of a result set - for example, if the query is `SELECT ... INTO @a` (picking a value into a variable, no result rows). Then the code calls `fetch_row()` on that `true`. PHP 8 throws a **Fatal error**: "Call to a member function fetch_row() on bool". And since errors are visible, that text lands in the response body.

Result: two distinguishable responses.

- body length 4744, contains "Fatal error" -> query executed fine -> my condition is TRUE
- body length 4557, the usual die message -> query failed -> FALSE

This is an oracle: I can ask "is statement X true?" and read yes/no from the page.

## Asking questions: the IF + runtime error trick

First idea: `IF(condition, 1, (SELECT 1 FROM nonexistent_table))`. It failed in **both** branches - MySQL notices the missing table when it prepares the query, before the condition is even evaluated. Constant errors behave the same way.

What works is an error that only happens when the query actually runs. Classic one - a duplicate entry:

    SELECT 1 FROM (SELECT COUNT(*),FLOOR(RAND(0)*2)x FROM information_schema.columns GROUP BY x)t

It fires "Duplicate entry ... for key" only at execution time. Final payload:

    ?id=1 AND IF(<condition>, 1, <that-error>) INTO @a

- condition TRUE -> the `1` branch runs -> no error -> Fatal body
- condition FALSE -> the error branch runs -> duplicate entry -> die body

## Extracting the flag

With yes/no questions working, extraction is mechanical:

- flag length: `LENGTH((SELECT flag FROM flag LIMIT 1)) > mid`
- each character: `ASCII(SUBSTRING((SELECT flag FROM flag LIMIT 1), N, 1)) > mid`

Binary search over printable characters - about 7 questions per character. I wrote a small Python script (`scripts/exfil.py`). To stay under the radar I paced it: Firefox User-Agent, a 1.3s pause between requests, one request at a time, everything logged to a file. ~480 requests over ~14 minutes, and the flag came back character by character.

Result (masked): `pwnsec{728c...d674}` - 24 characters.

## One-paragraph version (for interviews)

The site puts user input straight into an SQL query but shows no results - a blind SQL injection. UNION, timing, file write and error-based approaches were all blocked (identical output, ~2 second response padding, no privileges, silenced errors). A malformed input (`id[]=1`) revealed that PHP errors are visible in responses. PHP 8 returns `true` from a query without a result set, and the script then crashes on `fetch_row()` - the Fatal error text became my "yes" signal. Combining that with `IF(condition, 1, runtime_error)` gave a yes/no oracle, and a paced Python script extracted the flag via binary search, character by character.

## Lessons learned

1. "Blind" means no output - look for any other observable difference: an error page, a warning, a header.
2. If all timing requests return the same time, the app is padding responses - stop probing timing.
3. PHP errors leaking into responses (`display_errors`) are a legitimate information channel.
4. Database questions (`IF`) need a runtime error in the false branch; prepare-time errors kill both branches.
5. Automate the boring part, but stay polite: slow single requests beat fast bursts.
6. The page showed its own source - reading it first saved hours. Read the source before guessing.