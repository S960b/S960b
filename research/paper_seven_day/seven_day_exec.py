#!/usr/bin/env python3
"""Persistent absolute 7-day deadline wrapper, no trading logic changes.

Runner is executed via os.execv, so systemd tracks its PID and signals.
The state file must be on persistent storage and never deleted between restarts.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import time

SECONDS = 7 * 24 * 3600

def atomic_create(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        dfd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        raise

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--python", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--poll", type=float, default=10.0)
    args = parser.parse_args()
    state = args.state.expanduser().resolve()
    now = time.time()
    try:
        atomic_create(state, {"schema": 1, "started_at_unix": now,
                              "deadline_unix": now + SECONDS,
                              "duration_seconds": SECONDS})
    except FileExistsError:
        pass
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("duration_seconds") != SECONDS:
            raise ValueError("unexpected state schema/duration")
        started = float(data["started_at_unix"])
        deadline = float(data["deadline_unix"])
        if not 0 < started <= deadline or abs(deadline-started-SECONDS) > 0.01:
            raise ValueError("invalid deadline state")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("FAIL_CLOSED invalid seven-day state: " + str(exc), file=sys.stderr)
        return 78
    remaining = deadline - time.time()
    if remaining <= 0:
        print("SEVEN_DAY_COMPLETED deadline=" +
              dt.datetime.fromtimestamp(deadline, dt.timezone.utc).isoformat(),
              flush=True)
        return 0
    print("SEVEN_DAY_RESUME remaining_seconds=%.2f deadline=%s" % (
        remaining, dt.datetime.fromtimestamp(deadline, dt.timezone.utc).isoformat()),
        flush=True)
    # --hours accepts a float in argparse of current runner: Hermes must verify.
    argv = [args.python, args.runner, "--db", args.db, "--hours",
            repr(remaining / 3600.0), "--poll", repr(args.poll)]
    os.execv(args.python, argv)

if __name__ == "__main__":
    sys.exit(main())
