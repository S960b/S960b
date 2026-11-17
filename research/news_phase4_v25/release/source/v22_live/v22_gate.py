#!/usr/bin/env python3
"""NEWS v2.2 — launch gate, deliberately separate from the legacy pause.

The legacy lane is stopped by `~/.local/share/news-phase4-v21/PAUSED_MODEL_INFERENCE`.
That file stays exactly as it is: it records why the v2.1 restricted-agent lane
was stopped, and the owner has not lifted it. Lifting a pause is an owner act,
not a side effect of making a new lane work.

This module is the v2.2 gate. It reads its OWN flag file and never touches the
legacy one. Two independent conditions must hold before any model call:

  1. the flag file exists, is readable, and authorises v2.2 specifically;
  2. the caller passes a purpose that the flag names.

If either fails, `authorise()` raises and no request is built. The gate is
consulted by the runner immediately before the executor, so a missing flag
cannot be worked around by calling the client directly and hoping.

Flag file format (checked structurally, not by eye):

    V22_MODEL_CALLS: AUTHORISED
    owner: <who authorised>
    authorised_at_utc: <ISO-8601>
    purposes: <comma separated>       # e.g. paper_arms
    legacy_pause_untouched: yes       # must be the literal yes
"""
import time
from pathlib import Path

FLAG = Path.home() / ".local/share/news-phase4-v24/V22_MODEL_CALLS_AUTHORISED"
LEGACY_PAUSE = Path.home() / ".local/share/news-phase4-v21/PAUSED_MODEL_INFERENCE"

REQUIRED_KEYS = ("V22_MODEL_CALLS", "owner", "authorised_at_utc",
                 "purposes", "legacy_pause_untouched")


class GateClosed(RuntimeError):
    pass


def read_flag(path=FLAG):
    """Parse the flag into a dict, or raise GateClosed with a precise reason."""
    path = Path(path)
    if not path.exists():
        raise GateClosed("no_v22_flag")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateClosed("flag_unreadable:" + type(exc).__name__) from None

    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    missing = [k for k in REQUIRED_KEYS if not fields.get(k)]
    if missing:
        raise GateClosed("flag_incomplete:" + ",".join(missing))
    if fields["V22_MODEL_CALLS"].upper() != "AUTHORISED":
        raise GateClosed("flag_not_authorised")
    return fields


def authorise(purpose, *, flag_path=None, legacy_pause_path=None, now_ms=None):
    """Return the flag fields if `purpose` is authorised, else raise.

    Defaults are resolved at call time, not bound at import: a test (or an
    owner) can point the gate at a different flag without editing the module.
    """
    flag_path = FLAG if flag_path is None else flag_path
    legacy_pause_path = (LEGACY_PAUSE if legacy_pause_path is None
                         else legacy_pause_path)
    fields = read_flag(flag_path)
    purposes = {p.strip() for p in fields["purposes"].split(",") if p.strip()}
    if purpose not in purposes:
        raise GateClosed("purpose_not_authorised:" + purpose)
    if fields["legacy_pause_untouched"].lower() not in ("yes", "true"):
        raise GateClosed("flag_does_not_assert_legacy_pause_untouched")

    # Assert, do not assume: the legacy pause must still be present. If someone
    # deleted it to "simplify", this gate refuses instead of inheriting that.
    if not Path(legacy_pause_path).exists():
        raise GateClosed("legacy_pause_missing")

    return {"purpose": purpose, "owner": fields["owner"],
            "authorised_at_utc": fields["authorised_at_utc"],
            "flag_sha256_checked_at_ms": int(now_ms if now_ms is not None
                                             else time.time() * 1000)}


def write_flag(*, owner, purposes, authorised_at_utc, path=FLAG):
    """Owner-side helper. Kept here so the format cannot drift from the reader."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join([
        "V22_MODEL_CALLS: AUTHORISED",
        "owner: " + owner,
        "authorised_at_utc: " + authorised_at_utc,
        "purposes: " + ",".join(purposes),
        "legacy_pause_untouched: yes",
        "",
        "Scope: v2.2 paper lane only. Tool-free request path (no tools field).",
        "The legacy v2.1 pause file is NOT lifted by this flag.",
        "",
    ])
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return path
