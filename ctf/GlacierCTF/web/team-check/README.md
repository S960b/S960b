# Team-Check — GlacierCTF 2026

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

**Author:** LosFuzzys · **Points:** 50 · **Category:** web · **Tag:** Beginnerfriendly

> Lately, a lot of teams have started to use AI. We built a small webapplication to check if people use it for CTFs.

**Historical service:** https://teamcheck.web.glacierctf.com/

**Status: Solved — flag accepted by platform (participant confirmation).**

## Overview

The Flask application reflects the supplied `teamname` inside `flask.render_template_string(f"...{team_name}...")`. A naive blacklist rejects only the Jinja expression delimiters `{{` and `}}`, but overlooks the statement tags `{% ... %}`. In Jinja2, the `print` statement evaluates and emits expressions:

```jinja2
{%print(7*7)%}                    → 49
{%print(lipsum.__globals__['os'].popen('id').read())%} → uid=0(root) ...
```

Using the same primitive with `cat /flag.txt` reveals the full competition flag. The source file `/app/app.py` was then read back via RCE and confirms the vulnerable logic.

The recovered flag is masked as `gctf{...}` in this public repository.

## Files

- [solve.md](solve.md) — English technical write-up.
- [solve.ru.md](solve.ru.md) — Russian technical write-up.
- [scripts/ssti.py](scripts/ssti.py) — original stdlib-only exploit helper.
- [assets/README.md](assets/README.md) — CTFd metadata and missing original handout.
- [evidence/README.md](evidence/README.md) — saved original HTTP response and RCE-recovered source.

**Evidence limitation:** The organizer's `team-check.tar.gz` could not be downloaded at the time (server returned HTTP 502). The retrieved `app.py` is an RCE transcript, **not** an original handout source file. The historical flag command succeeded according to the investigation report; its raw response was not saved as a separate file.
