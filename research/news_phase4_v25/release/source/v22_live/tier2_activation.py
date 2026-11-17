#!/usr/bin/env python3
"""Owner-approved Tier-2 PAPER trial activation, without initiating inference.

The operational sequence is:
  --status       read-only, never creates a file
  --stage        create exactly ONE immutable activation JSON (NO model gate)
  --enable-gate  create TIER2_ACTIVE only after stage and safety checks
  --disarm       remove only TIER2_ACTIVE, preserve activation/evidence

This script NEVER writes TRIGGER_ENABLED, runs the collector, creates a trial,
opens a model connection, installs a timer, or edits the strict v2.2 lane.
Even with TIER2_ACTIVE set, the live trigger remains disabled until an owner
explicitly authorises the later controlled paper trial. Tool isolation in
Tier-2 is prompt-only plus client-side no-tools evidence, NOT provider proof.
"""
import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import tier2 as T2

OWNER_TASK_ID = "task_760cfd0ff32f51a5c99ad18ef08613a5"
OWNER_PRIOR_APPROVAL_SEQ = 157
MAX_AGE_DAYS = 14


class ActivationError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paths(root=None):
    base = Path(root) if root is not None else T2.DATA
    return {
        "base": base,
        "activation": base / "TIER2_ACTIVATION.json",
        "gate": base / "TIER2_ACTIVE",
        "trigger": base / "TRIGGER_ENABLED",
        "trial_db": base / "tier2.sqlite",
        "claims": base / "tier2_claims.sqlite",
        "attestation": base / "attestation.jsonl",
    }


def _no_symlink(path):
    if path.is_symlink():
        raise ActivationError("symlink_refused:" + path.name)


def _write_once(path, raw):
    path = Path(path)
    _no_symlink(path.parent)
    if path.parent.exists() and not path.parent.is_dir():
        raise ActivationError("data_directory_invalid")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _no_symlink(path.parent)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _record(*, trial_id, owner_seq, owner_task_id, started=None):
    if (not isinstance(trial_id, str)
            or not trial_id.startswith("tier2-paper-")
            or len(trial_id) > 80
            or any(x not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for x in trial_id)):
        raise ActivationError("bad_trial_id")
    if owner_task_id != OWNER_TASK_ID or owner_seq != OWNER_PRIOR_APPROVAL_SEQ:
        raise ActivationError("owner_approval_reference_mismatch")
    stamp = started if started is not None else utc_now()
    return {
        "trial_id": trial_id,
        "purpose": T2.TRIAL_PURPOSE,
        "owner_seq": owner_seq,
        "owner_task_id": owner_task_id,
        "owner_approval_context": "renewed owner authorization for staged Tier-2 activation on 2026-11-17",
        "started_utc": stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "protocol_sha256": T2.protocol_sha(),
        "erratum_sha256": sha(T2.ERRATUM_FILE),
        "max_model_invocations": 3,
        "max_triples": 1,
        "model": "deepseek-v4-flash",
        "prompts_version": T2.PROMPTS_VERSION,
        "prompt_only_no_web": True,
        "upstream_tool_isolation": "unverified",
        "paper_only": True,
    }


def _check_age(rec, now=None):
    now = now or utc_now()
    try:
        started = datetime.fromisoformat(rec["started_utc"].replace("Z", "+00:00"))
    except (TypeError, ValueError, KeyError) as exc:
        raise ActivationError("invalid_activation_timestamp") from exc
    if started.tzinfo is None:
        raise ActivationError("activation_timestamp_not_utc")
    age = (now - started).total_seconds()
    if age < 0 or age >= MAX_AGE_DAYS * 86400:
        raise ActivationError("activation_outside_14_day_window")
    if rec.get("prompts_version") != T2.PROMPTS_VERSION:
        raise ActivationError("activation_prompt_version_mismatch")
    if rec.get("prompt_only_no_web") is not True or rec.get("upstream_tool_isolation") != "unverified":
        raise ActivationError("activation_wrong_evidence_tier")
    return True


def stage(*, root=None, trial_id="tier2-paper-20261117-first",
          owner_seq=OWNER_PRIOR_APPROVAL_SEQ, owner_task_id=OWNER_TASK_ID):
    p = paths(root)
    for x in p.values():
        if x is not p["base"]:
            _no_symlink(x)
    if any(p[name].exists() for name in ("activation", "gate", "trigger",
                                         "trial_db", "claims", "attestation")):
        raise ActivationError("existing_tier2_state_refused")
    record = _record(trial_id=trial_id, owner_seq=owner_seq, owner_task_id=owner_task_id)
    raw = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _write_once(p["activation"], raw)
    parsed = T2.read_activation(p["activation"])
    _check_age(parsed)
    return {"status": "staged_no_model_gate", "trial_id":trial_id,
            "activation_sha256": hashlib.sha256(raw).hexdigest(),
            "trigger_enabled": False, "model_calls": 0}


def enable_gate(*, root=None):
    p = paths(root)
    for name in ("activation", "gate", "trigger"):
        _no_symlink(p[name])
    if p["trigger"].exists():
        raise ActivationError("trigger_must_remain_disabled")
    if p["gate"].exists():
        raise ActivationError("gate_already_present")
    if not p["activation"].is_file():
        raise ActivationError("activation_not_staged")
    parsed = T2.read_activation(p["activation"])
    _check_age(parsed)
    if any(p[name].exists() for name in ("trial_db", "claims", "attestation")):
        raise ActivationError("trial_already_started")
    _write_once(p["gate"], b"tier2-paper-only:gate-staged-for-future-controlled-trial\n")
    try:
        T2.require_gate(gate=p["gate"], activation=p["activation"])
    except Exception:
        p["gate"].unlink(missing_ok=True)
        raise
    return {"status": "model_gate_enabled_trigger_disabled",
            "trial_id": parsed["trial_id"], "trigger_enabled": False,
            "model_calls": 0}


def disarm(*, root=None):
    p = paths(root)
    _no_symlink(p["gate"])
    p["gate"].unlink(missing_ok=True)
    return {"status": "disarmed", "activation_preserved": p["activation"].exists()}


def status(*, root=None):
    p = paths(root)
    # Pure read-only observation: no new files/directories.
    for name in ("activation", "gate", "trigger"):
        _no_symlink(p[name])
    enabled = p["gate"].exists()
    trigger = p["trigger"].exists()
    result = {"status": "unconfigured", "gate": enabled,
              "trigger": trigger, "model_calls_this_command": 0}
    if p["activation"].exists():
        rec = T2.read_activation(p["activation"])
        result["trial_id"] = rec["trial_id"]
        result["status"] = "gate_enabled" if enabled else "staged"
        try:
            _check_age(rec)
        except ActivationError as exc:
            result["status"] = "expired_or_invalid"
            result["reason"] = str(exc)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stage", action="store_true", help="write signed-by-hash activation JSON only")
    group.add_argument("--enable-gate", action="store_true", help="enable model gate, NEVER trigger")
    group.add_argument("--disarm", action="store_true", help="disable model gate, leave records")
    group.add_argument("--status", action="store_true", help="read-only status (default)")
    parser.add_argument("--trial-id", default="tier2-paper-20261117-first")
    parser.add_argument("--root", default=None, help="fixture root; never use for live trial")
    args = parser.parse_args(argv)
    try:
        if args.stage:
            result = stage(root=args.root, trial_id=args.trial_id)
        elif args.enable_gate:
            result = enable_gate(root=args.root)
        elif args.disarm:
            result = disarm(root=args.root)
        else:
            result = status(root=args.root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ActivationError, T2.Tier2Error, OSError) as exc:
        print(json.dumps({"status": "refused",
                          "reason": type(exc).__name__,
                          "detail": str(exc)[:200]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
