# Uroki (Электронный журнал)

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 394
- **Category:** web
- **Task:** https://game.ctfcup.online/tasks/uroki
- **Attachment:** `uroki.zip`, with the Flask app and the supervisor config
- **Flag:** `ctfcup{...}` (masked)

A school journal: create a lesson, attach a file, download it. Two flaws chain together.

`storage.clean_name` rejects `..`, backslashes and control characters but not a leading slash, and `os.path.join` drops the upload directory for an absolute name, so uploading a file called `/data/uroki.db` replaces the application's database. The download route then passes `file_path` from that database straight to `send_file`, which yields arbitrary file read.

That is not enough for the flag: `entrypoint.sh` does `chown app:app /flag.txt; chmod 0000 /flag.txt`, so `send_file` raises and the endpoint answers 500. The owner can still `chmod`, so the second stage needs code execution as `app`. The Celery worker runs with `-B`, keeps its schedule in a `shelve` database at `/data/celerybeat-schedule`, and any lesson creation triggers `supervisorctl restart celery`. Dropping a pickled payload there and creating a lesson executes it, the payload chmods the flag, and the normal download route returns it.

`scripts/uroki_exploit.py <url>` runs the whole chain; the transcript is in `evidence/run.log`.
