# Uroki: write, read, then ask the scheduler for a favour

[По-русски](solve.ru.md)

## 0. The application

A school journal: create a lesson, attach a file, open the lesson card, download the attachment. The archive has a Flask app, a SQLite database in `/data`, an upload directory in `/data/uploads`, and a supervisor config that runs gunicorn plus a Celery worker with beat.

The flag is at `/flag.txt`, and `entrypoint.sh` ends with two lines that matter:

```sh
chown app:app /flag.txt
chmod 0000 /flag.txt
```

So the file exists, is owned by the user the app runs as, and cannot be read by anyone until the mode changes. That single detail decides the shape of the whole exploit.

Why this step: reading the entrypoint first tells us that "read the flag" is not one step but two, and that the second step needs code execution as `app`.

## 1. Absolute filename breaks the upload directory

`storage.clean_name` strips path separators only if `..`, a backslash or a control character is present:

```python
UNSAFE = re.compile(r"\.\.|\\|[\x00-\x1f]")
def clean_name(name):
    name = (name or "").strip()
    if UNSAFE.search(name):
        name = os.path.basename(name.replace("\\", "/"))
    return name.strip() or FALLBACK_NAME

def save_attachment(folder, file):
    path = os.path.join(folder, clean_name(file.filename))
    file.save(path)
```

A name like `/data/uroki.db` contains none of the forbidden patterns, and `os.path.join` drops the first argument when the second is absolute. The upload therefore writes anywhere the process can write.

The handler then stores the returned path in the database and calls `db.set_attachment(id, f.filename, path)`.

Why this step: this is the first primitive, arbitrary file write, and it is enough to replace the application's own database.

## 2. Reading arbitrary files

The download handler takes the path from the database and passes it straight to `send_file`:

```python
@app.get("/lesson/<int:id>/file")
def download_attachment(id):
    lesson = db.get_lesson(id)
    if lesson is None or not lesson["file_path"]:
        abort(404)
    return send_file(lesson["file_path"], as_attachment=True, download_name=lesson["file_name"])
```

There is no check that the path stays inside the upload directory. So the chain is:

1. Create a lesson to get an id the upload route accepts.
2. Build a local SQLite file with the same schema, containing that id plus extra rows whose `file_path` points at the files we want.
3. Upload it as `/data/uroki.db`, replacing the live database.
4. Read `/lesson/<extra id>/file` for each row.

This gives `/etc/passwd`, `/entrypoint.sh`, the app sources, anything readable by `app`. It also gives a reliable signal for what is *not* readable: a missing file answers 500 (the `send_file` exception is not turned into 404), so a 500 on `/flag.txt` means "present but denied", not "absent".

Why this step: the database swap turns a write primitive into an arbitrary read, and the read is what reveals the `chmod 0000`.

## 3. Why the flag stays out of reach

`/flag.txt` is owned by `app` with mode `0000`. Opening it fails with `EACCES` even for the owner, because ownership gives the right to change the mode, not to bypass it. `send_file` raises, the endpoint answers 500.

Two consequences follow. The published one-stage exploit, which only replaces the database and requests `/flag.txt`, ends with three 500s and no flag. And the second stage cannot be "read harder": it has to change the mode, which requires code running as `app`.

Why this step: recognising the ownership detail converts a failed read into a clear next objective.

## 4. Code execution through the beat schedule

`supervisord.conf` runs the worker as `app` from `directory=/data`:

```text
command=celery -A app.tasks worker -B --concurrency=2 -l info
```

`tasks.py` sets `beat_scheduler="app.scheduler:JournalScheduler"`, and `JournalScheduler` extends Celery's `PersistentScheduler`, which keeps its schedule in a `shelve` database named `celerybeat-schedule` in the working directory, that is in `/data`. `shelve` stores pickles.

`web.py` calls `ops.apply_schedule_changes()` on every lesson create, edit and delete, and that function runs `supervisorctl restart celery`. So a restart can be triggered from the web UI at will.

The plan writes itself:

1. Build a `dbm.dumb` database holding one key whose pickle calls `os.system`.
2. Upload it with the absolute-path primitive as `/data/celerybeat-schedule.dir` and `/data/celerybeat-schedule.dat`.
3. Create any lesson, which restarts the worker.
4. Beat opens the shelf on startup, unpickles the value, and the command runs as `app`.

The payload does two things, so the result survives even if one read path fails:

```sh
chmod 0644 /flag.txt
cp /flag.txt /data/uploads/flagcopy.txt
```

Why this step: the owner of a `0000` file may still `chmod` it, so the RCE only has to run as the right user, not as root.

## 5. Result

After the restart the flag is readable through the same download route, and the copy in the upload directory is a fallback.

```bash
python3 scripts/uroki_exploit.py http://<host> --timeout 20
```

Recorded run against the live instance:

```text
[+] carrier lesson id = 1906314037
[+] built replacement DB (20480 bytes)
[+] database replaced
[+] entrypoint: chown app:app /flag.txt
[+] entrypoint: chmod 0000 /flag.txt
[+] uploaded /data/celerybeat-schedule.dir: HTTP 303
[+] uploaded /data/celerybeat-schedule.dat: HTTP 303
[+] celery restarted, waiting for the scheduler to load the payload
[FLAG] ctfcup{...}
```

The flag is masked here; the raw transcript is in `evidence/run.log`.

## 6. Dead ends

- Trying to read `/flag.txt` after the database swap only: 500, because of the mode.
- Guessing other flag paths: `/flag`, `/app/flag.txt`, `/data/flag.txt` all answer 500, and `/entrypoint.sh` shows there is only one file.
- Writing into `/app`: the image makes it mode `0555` and owned by root.
- Uploading a Python module into the import path: `celery` is started from `/usr/local/bin`, so the working directory is not on `sys.path`, and `/app` is read-only anyway.

## 7. Lessons

- A file with mode `0000` is not protected from its owner: ownership is the permission to change permissions, and that is enough to ask for code execution instead of a read.
- Message brokers and schedulers that persist state with pickle are a code execution surface; here the schedule file sits in a directory the web app can write.
- A 500 instead of 404 on a download route is a signal, not noise: it separated "absent" from "denied" and pointed at the mode.
- Fixing only the upload path would still leave the scheduler writable; both the write and the read paths need bounds, and the data directory should not be writable by the web process.

One paragraph: an absolute filename passes `clean_name` and `os.path.join` writes the uploaded file over `/data/uroki.db`, so a crafted database turns the download route into arbitrary file read; that reveals `/flag.txt` is owned by `app` with mode `0000`, so the same write primitive is used to drop a pickled payload into the Celery beat schedule in `/data`, a lesson creation restarts the worker, and the payload chmods the flag, which is then downloaded normally.
