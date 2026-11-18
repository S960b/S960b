#!/usr/bin/env python3
"""Immutable private Tier-2 wire evidence, written BEFORE parsing or attestation.

Each response is stored under:
    <attestation.parent>/raw_model_calls/<trial_id>/<arm>.request.bin
    <attestation.parent>/raw_model_calls/<trial_id>/<arm>.response.bin
    <attestation.parent>/raw_model_calls/<trial_id>/<arm>.response.meta.json

The artifacts are local/private, never included in the public source release.
An interrupted request is retained without a response. No retry or overwrite.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

ARM_NAMES = frozenset(("news", "news_market", "market"))
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class RawEvidenceError(RuntimeError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe_label(s, kind):
    if not isinstance(s, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", s):
        raise RawEvidenceError("invalid_" + kind)
    return s


def _private_dir(base):
    base = Path(base)
    if base.is_symlink():
        raise RawEvidenceError("evidence_dir_symlink")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if base.is_symlink() or not base.is_dir():
        raise RawEvidenceError("evidence_dir_invalid")
    if base.stat().st_mode & 0o077:
        raise RawEvidenceError("evidence_dir_permissions")
    return base


def _write_once(path, raw, max_bytes):
    if not isinstance(raw, bytes):
        raise RawEvidenceError("wire_payload_not_bytes")
    if not raw or len(raw) > max_bytes:
        raise RawEvidenceError("wire_payload_size_out_of_bounds")
    path = Path(path)
    if path.is_symlink():
        raise RawEvidenceError("evidence_symlink_refused")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        # Do not erase partial/incomplete evidence after a failure. It is an
        # incident for the verifier, NOT grounds for trying the model again.
        raise
    return {"sha256": digest(raw), "bytes": len(raw), "path": str(path)}


def raw_capture(*, attestation_path, trial_id, arm):
    """Return the capture hook for exactly one invocation; no network calls."""
    _safe_label(trial_id, "trial_id")
    if arm not in ARM_NAMES:
        raise RawEvidenceError("invalid_arm")
    base = _private_dir(Path(attestation_path).parent)
    base = _private_dir(base / "raw_model_calls")
    base = _private_dir(base / trial_id)

    def capture(kind, payload, meta):
        if kind not in ("request", "response"):
            raise RawEvidenceError("invalid_capture_kind")
        if not isinstance(meta, dict):
            raise RawEvidenceError("capture_metadata_missing")
        artifact = base / (arm + "." + kind + ".bin")
        info = _write_once(artifact, payload, MAX_REQUEST_BYTES if kind == "request"
                           else MAX_RESPONSE_BYTES)
        if kind == "response":
            record = {
                "trial_id": trial_id,
                "arm": arm,
                "captured_ms": int(time.time() * 1000),
                "raw_sha256": info["sha256"],
                "bytes": info["bytes"],
                "http_status": meta.get("status"),
                "provider_request_id": meta.get("provider_request_id"),
                "transport_error": bool(meta.get("transport_error", False)),
            }
            body = (json.dumps(record, sort_keys=True, separators=(",", ":"))
                    + "\n").encode("utf-8")
            _write_once(base / (arm + ".response.meta.json"),
                        body, MAX_REQUEST_BYTES)
        return info

    return capture


def verify_wire(*, attestation_path, trial_id, arm, request_sha256,
                response_sha256):
    """Verify immutable raw files and response metadata for one completed arm."""
    _safe_label(trial_id, "trial_id")
    if arm not in ARM_NAMES:
        raise RawEvidenceError("invalid_arm")
    base = Path(attestation_path).parent / "raw_model_calls" / trial_id
    if base.is_symlink() or not base.is_dir():
        raise RawEvidenceError("raw_evidence_missing")
    found = {}
    for kind, expected in (("request", request_sha256),
                           ("response", response_sha256)):
        path = base / (arm + "." + kind + ".bin")
        if path.is_symlink() or not path.is_file():
            raise RawEvidenceError("raw_" + kind + "_missing")
        if path.stat().st_mode & 0o077:
            raise RawEvidenceError("raw_permissions")
        actual = path.read_bytes()
        if digest(actual) != expected:
            raise RawEvidenceError("raw_" + kind + "_sha_mismatch")
        found[kind] = actual
    meta_path = base / (arm + ".response.meta.json")
    if meta_path.is_symlink() or not meta_path.is_file():
        raise RawEvidenceError("raw_response_metadata_missing")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if (meta.get("arm") != arm or meta.get("trial_id") != trial_id
            or meta.get("raw_sha256") != response_sha256
            or meta.get("bytes") != len(found["response"])):
        raise RawEvidenceError("raw_response_metadata_mismatch")
    return found
