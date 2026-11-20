#!/usr/bin/env python3
"""Install and arm the owner-approved NEWS paper series100 on Linux systemd --user.

No development, no changing model prompts. Must be run explicitly with --activate.
All artifacts are separate from the historical one-shot trial. Stdlib only.
"""
import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "series_runner.py"
PROTOCOL = HERE / "TIER2_SERIES_PROTOCOL.md"
DATA = Path.home() / ".local/share/news-phase4-v25-series100"
SYSTEMD = Path.home() / ".config/systemd/user"
ACT = DATA / "TIER2_SERIES_ACTIVATION.json"
GATE = DATA / "SERIES_TRIGGER_ENABLED"
COLLECTOR_OVERRIDE = SYSTEMD / "news-phase4-collector.timer.d/override.conf"
SERIES_ID = "tier2-series100-2026-11"
TRIAL_UNIT = "news-series100-trial"
LABEL_UNIT = "news-series100-label"

def fail(s):
    raise RuntimeError(s)

def run(*args):
    cmd = ("systemctl", "--user") + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    if p.returncode != 0:
        fail("%s: %s" % (" ".join(cmd), p.stderr.strip()))
    return p.stdout.strip()

def new_file(path, raw):
    path = Path(path)
    if path.is_symlink():
        fail("symlink_refused:" + str(path))
    # Existing files must NEVER be overwritten during a second activation.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())

def write_unit(path, text):
    if path.is_symlink():
        fail("unit_symlink_refused:" + str(path))
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)

def check():
    if not RUNNER.is_file() or not PROTOCOL.is_file():
        fail("runner_or_protocol_missing")
    if not (Path.home() / ".local/share/news-phase4-v24/v24.sqlite").is_file():
        fail("news_feed_db_missing")
    if DATA.is_symlink() or SYSTEMD.is_symlink():
        fail("directory_symlink_refused")
    if (ACT.exists() or ACT.is_symlink() or GATE.exists() or GATE.is_symlink()):
        fail("already_activated_or_gate_exists_refuse_to_overwrite")
    if not shutil_which("systemctl"):
        fail("systemctl_missing")
    if subprocess.run(["/usr/bin/python3", "-m", "py_compile", str(RUNNER),
                       str(HERE / "series_guard.py")]).returncode != 0:
        fail("runner_python_compile_failed")
    import importlib.util
    spec = importlib.util.spec_from_file_location("series_guard", HERE / "series_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    if guard.CALL_LIMIT != 300 or guard.SERIES_LIMIT != 100:
        fail("series_budget_not_100_300")
    if guard.EVIDENCE_TIER != "observational_tier2":
        fail("tier_invalid")
    return guard

def shutil_which(program):
    import shutil
    return shutil.which(program)

def unit_definitions():
    wd = str(HERE)
    program = "/usr/bin/python3 " + str(RUNNER)
    trial_service = (
        "[Unit]\nDescription=NEWS v2.5 series100 paper only — news prediction\n"
        "After=network-online.target\n"
        "ConditionPathExists=%h/.local/share/news-phase4-v25-series100/SERIES_TRIGGER_ENABLED\n"
        "ConditionPathExists=%h/.local/share/news-phase4-v25-series100/TIER2_SERIES_ACTIVATION.json\n"
        "[Service]\nType=oneshot\nWorkingDirectory=" + wd + "\n"
        "ExecStart=" + program + " --execute\n"
        "TimeoutStartSec=900s\nRestart=no\nUMask=0077\nNoNewPrivileges=yes\n"
    )
    trial_timer = (
        "[Unit]\nDescription=NEWS paper series100 scan fresh events each minute\n"
        "[Timer]\nOnCalendar=*:*:20\nAccuracySec=5s\nPersistent=false\n"
        "Unit=" + TRIAL_UNIT + ".service\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    label_service = (
        "[Unit]\nDescription=NEWS paper series100 settle 4h BTC labels\n"
        "After=network-online.target\n"
        "ConditionPathExists=%h/.local/share/news-phase4-v25-series100/tier2_series.sqlite\n"
        "[Service]\nType=oneshot\nWorkingDirectory=" + wd + "\n"
        "ExecStart=" + program + " --label\n"
        "TimeoutStartSec=180s\nRestart=no\nUMask=0077\nNoNewPrivileges=yes\n"
    )
    label_timer = (
        "[Unit]\nDescription=NEWS paper series100 label check each five minutes\n"
        "[Timer]\nOnCalendar=*:0/5\nAccuracySec=5s\nPersistent=false\n"
        "Unit=" + LABEL_UNIT + ".service\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    collector_timer_override = (
        "[Timer]\nOnCalendar=\nOnCalendar=*:*:00\nAccuracySec=5s\n"
        "Persistent=false\n"
    )
    return {
        SYSTEMD / (TRIAL_UNIT+".service"): trial_service,
        SYSTEMD / (TRIAL_UNIT+".timer"): trial_timer,
        SYSTEMD / (LABEL_UNIT+".service"): label_service,
        SYSTEMD / (LABEL_UNIT+".timer"): label_timer,
        COLLECTOR_OVERRIDE: collector_timer_override
    }

def install_units():
    SYSTEMD.mkdir(parents=True, exist_ok=True, mode=0o700)
    COLLECTOR_OVERRIDE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    units=unit_definitions()
    for path, body in units.items():
        write_unit(path, body)
    run("daemon-reload")
    run("restart", "news-phase4-collector.timer")
    run("enable", "news-phase4-collector.timer")

def arm(owner_seq):
    guard = check()
    if owner_seq < 1:
        fail("owner_seq_invalid")
    # Explicit user approval was relayed in the Hermes task identified
    # by owner_seq. Never infer an owner GO from an old model call.
    import sys as _sys
    source = HERE.parent / "release" / "source"
    live = source / "v22_live"
    _sys.path[:0] = [str(live),str(source)]
    import tier2 as T2
    DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
    if DATA.is_symlink():
        fail("series_data_symlink")
    record = {
        "purpose": guard.SERIES_PURPOSE,
        "series_id": SERIES_ID,
        "paper_only": True, "owner_go": True, "owner_seq": owner_seq,
        "owner_task_id": "task_760cfd0ff32f51a5c99ad18ef08613a5",
        "evidence_tier": "observational_tier2",
        "upstream_tool_isolation": "unverified",
        "max_model_invocations": 300, "max_events": 100,
        "no_calendar_expiry": True, "expires_ms": None,
        "started_ms": time.time_ns() // 1_000_000,
        "model": "deepseek-v4-flash",
        "prompts_version": T2.PROMPTS_VERSION,
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    }
    # Stage activation BEFORE gate. No services are armed prior to gate.
    new_file(ACT, (json.dumps(record,sort_keys=True,indent=2)+"\n").encode())
    install_units()
    # Verify the activation will pass guard (gate currently absent).
    new_file(GATE,b"owner_approved_paper_only\n")
    try:
        guard.validate_activation(activation_path=ACT,gate_path=GATE)
        run("enable","--now",LABEL_UNIT+".timer")
        run("enable","--now",TRIAL_UNIT+".timer")
        print(json.dumps({"armed":True,"series_id":SERIES_ID,
             "trial_timer":run("show",TRIAL_UNIT+".timer","-p","ActiveState","-p","UnitFileState"),
             "label_timer":run("show",LABEL_UNIT+".timer","-p","ActiveState","-p","UnitFileState"),
             "collector_timer":run("show","news-phase4-collector.timer","-p","ActiveState","-p","UnitFileState")},
             sort_keys=True))
    except BaseException:
        # Physical gate revocation stops future calls; preserve immutable record
        # and logs so a failed activation cannot be silently retried.
        GATE.unlink(missing_ok=True)
        raise

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--activate",action="store_true")
    p.add_argument("--owner-hermes-seq",type=int,default=0)
    a=p.parse_args()
    if not a.activate:
        print("Not activated. Supply --activate --owner-hermes-seq N after an explicit GO.")
        return 0
    try:
        arm(a.owner_hermes_seq)
    except Exception as e:
        print("REFUSED",type(e).__name__,str(e),file=sys.stderr)
        return 2
    return 0

if __name__=="__main__":
    raise SystemExit(main())
