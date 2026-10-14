#!/usr/bin/env python3
"""Read-only, crash-safe daily archive of a SQLite paper-trading experiment.

Usage:
  python3 paper_checkpoint.py --db ~/safetrade-research/data/risk_paper.db \
      --out ~/safetrade-research/data/checkpoints --tag daily
No Bybit calls. Does not change the source DB.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
UTC = dt.timezone.utc

def sha256_file(path):
    dig = hashlib.sha256()
    with open(path, "rb") as fh:
        for part in iter(lambda: fh.read(1024 * 1024), b""):
            dig.update(part)
    return dig.hexdigest()

def check_sqlite(db_path):
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    source = sqlite3.connect("file:" + db_path.as_posix() + "?mode=ro", uri=True, timeout=30)
    try:
        source.execute("PRAGMA busy_timeout=30000")
        return source
    except BaseException:
        source.close()
        raise

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=pathlib.Path)
    p.add_argument("--out", required=True, type=pathlib.Path)
    p.add_argument("--tag", default="daily")
    p.add_argument("--log", type=pathlib.Path, help="optional runner log for last lines")
    args = p.parse_args()

    now = dt.datetime.now(UTC)
    stamp = now.astimezone(MSK).strftime("%Y%m%d_%H%M%S")
    root = args.out.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / (stamp + "_" + args.tag)
    if final.exists():
        raise FileExistsError(str(final))
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=".checkpoint_", dir=str(root)))
    db_copy = tmp / "snapshot.sqlite3"
    summary = {
        "captured_at_utc": now.isoformat(),
        "captured_at_msk": now.astimezone(MSK).isoformat(),
        "source_db": str(args.db.expanduser().resolve()),
        "tag": args.tag,
        "tables": {},
        "warnings": [],
    }
    try:
        source = check_sqlite(args.db.expanduser().resolve())
        try:
            target = sqlite3.connect(str(db_copy), timeout=30)
            try:
                source.backup(target, pages=256, sleep=0.2)
            finally:
                target.close()
        finally:
            source.close()

        snap = sqlite3.connect(str(db_copy))
        snap.row_factory = sqlite3.Row
        try:
            integrity = snap.execute("PRAGMA integrity_check").fetchone()[0]
            summary["integrity_check"] = integrity
            if integrity != "ok":
                raise RuntimeError("snapshot integrity check: " + str(integrity))
            table_names = [r[0] for r in snap.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for name in table_names:
                # SQL identifier must come from sqlite_master; quote defensively.
                quoted = '"' + name.replace('"', '""') + '"'
                count = snap.execute("SELECT count(*) FROM " + quoted).fetchone()[0]
                outfile = tmp / ("table_" + hashlib.sha256(name.encode()).hexdigest()[:12] + ".jsonl")
                with outfile.open("w", encoding="utf-8") as fh:
                    for row in snap.execute("SELECT * FROM " + quoted):
                        record = {}
                        for key in row.keys():
                            val = row[key]
                            if isinstance(val, bytes):
                                import base64
                                val = {"base64": base64.b64encode(val).decode("ascii")}
                            record[key] = val
                        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                summary["tables"][name] = {"rows": count, "export": outfile.name}
        finally:
            snap.close()

        if args.log:
            log = args.log.expanduser()
            if log.exists():
                with log.open("rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    fh.seek(max(0, fh.tell() - 65536), os.SEEK_SET)
                    tail = fh.read().decode("utf-8", "replace")
                (tmp / "runner_log_tail.txt").write_text(tail, encoding="utf-8")
            else:
                summary["warnings"].append("log_path_missing: " + str(log))

        summary["files"] = {}
        for f in sorted(tmp.iterdir()):
            if f.is_file():
                summary["files"][f.name] = {"bytes": f.stat().st_size, "sha256": sha256_file(f)}
        (tmp / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.rename(final)
        print(json.dumps({"status": "ok", "directory": str(final),
                          "summary": summary}, ensure_ascii=False))
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

if __name__ == "__main__":
    main()
