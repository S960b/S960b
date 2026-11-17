#!/usr/bin/env python3
"""NEWS v2.4 — heartbeat producer contract (PURE, offline, no network).

This module is the interface a future probe would implement, written down now so
that the coverage maths can be tested against a real contract instead of
hand-made lists. It contains NO socket code and NO request function.

TRUST BOUNDARY (read this before believing anything the probe says):

    The probe records what an observer claims it saw. It does not prove the
    source answered. `Heartbeat.ok` is an assertion by the probe process, and
    `probe_sha256` is an integrity checksum over the observation, NOT a
    signature. A compromised or forged producer can publish a perfectly valid
    heartbeat stream, exactly as it can publish a valid story record.

    Real authenticity needs: the probe running locally against an allowlisted
    origin, a signed attestation from that origin, raw response retention, and
    a digest chain anchored outside the producer. None of that exists here.
"""
import hashlib
import json
from dataclasses import dataclass

import v24_sources as S

# Outcome taxonomy. Anything not listed is a refusal, not an "other".
OUTCOMES = ("ok", "timeout", "rate_limited", "dns_failure", "tls_failure",
            "http_error", "auth_failure")

RETENTION_DAYS = 30
MAX_PROBE_ATTEMPTS = 3


@dataclass(frozen=True)
class ProbeAttempt:
    """One recorded probe attempt. Immutable, append-only by contract."""
    source_id: str
    attempt_id: int              # monotonic per source, never reused
    started_ms: int
    finished_ms: int
    outcome: str
    http_status: int = None
    error_code: str = None
    clock: str = "system"
    probe_sha256: str = None

    def to_json(self):
        return json.dumps({
            "source_id": self.source_id, "attempt_id": self.attempt_id,
            "started_ms": self.started_ms, "finished_ms": self.finished_ms,
            "outcome": self.outcome, "http_status": self.http_status,
            "error_code": self.error_code, "clock": self.clock,
            "probe_sha256": self.probe_sha256}, sort_keys=True)


def probe_digest(attempt):
    """Integrity checksum over the observation. Not a signature."""
    payload = "|".join(str(x) for x in (attempt.source_id, attempt.attempt_id,
                                        attempt.started_ms, attempt.finished_ms,
                                        attempt.outcome, attempt.http_status,
                                        attempt.error_code, attempt.clock))
    return hashlib.sha256(payload.encode()).hexdigest()


def make_attempt(*, source_id, attempt_id, started_ms, finished_ms, outcome,
                 http_status=None, error_code=None, clock="system"):
    """Validate an attempt at creation, then seal its digest."""
    if not isinstance(source_id, str) or not source_id:
        raise S.AdapterError("attempt_bad_source_id")
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) \
            or attempt_id < 1:
        raise S.AdapterError("attempt_bad_id")
    S._strict_int(started_ms, "started_ms")
    S._strict_int(finished_ms, "finished_ms")
    if finished_ms < started_ms:
        raise S.AdapterError("attempt_negative_duration")
    if outcome not in OUTCOMES:
        raise S.AdapterError("attempt_unknown_outcome:" + str(outcome))
    if not isinstance(clock, str) or not clock:
        raise S.AdapterError("attempt_bad_clock")
    draft = ProbeAttempt(source_id=source_id, attempt_id=attempt_id,
                         started_ms=started_ms, finished_ms=finished_ms,
                         outcome=outcome, http_status=http_status,
                         error_code=error_code, clock=clock)
    return ProbeAttempt(**{**draft.__dict__,
                           "probe_sha256": probe_digest(draft)})


def to_heartbeat(attempt):
    """Project a recorded attempt onto the coverage input.

    Only `ok` counts as a successful probe. Rate limiting and auth failures are
    failures of observation, not proof of health.
    """
    if not isinstance(attempt, ProbeAttempt):
        raise S.AdapterError("not_a_probe_attempt")
    if attempt.probe_sha256 != probe_digest(
            ProbeAttempt(**{**attempt.__dict__, "probe_sha256": None})):
        raise S.AdapterError("attempt_digest_mismatch")
    return S.Heartbeat(source_id=attempt.source_id,
                       observed_ms=attempt.finished_ms,
                       ok=attempt.outcome == "ok",
                       clock=attempt.clock)


class AttemptStore:
    """Append-only in-memory store with a monotonic per-source counter.

    Deliberately not a database: the point of this stage is the contract, and a
    store that cannot forget cannot silently rewrite history.
    """

    def __init__(self, *, retention_days=RETENTION_DAYS):
        self._rows = []
        self._next = {}
        self._retention_ms = retention_days * 86_400_000

    def record(self, *, source_id, started_ms, finished_ms, outcome,
               http_status=None, error_code=None, clock="system"):
        attempt_id = self._next.get(source_id, 0) + 1
        attempt = make_attempt(source_id=source_id, attempt_id=attempt_id,
                               started_ms=started_ms, finished_ms=finished_ms,
                               outcome=outcome, http_status=http_status,
                               error_code=error_code, clock=clock)
        self._next[source_id] = attempt_id
        self._rows.append(attempt)
        return attempt

    def rows(self, *, now_ms=None):
        """Read the log. Old attempts outside retention are reported, not hidden.

        Pruning is a decision, so it is returned explicitly instead of silently
        dropping evidence the coverage maths might still need.
        """
        if now_ms is None:
            return list(self._rows)
        kept = [r for r in self._rows
                if now_ms - r.finished_ms <= self._retention_ms]
        return kept

    def heartbeats(self):
        return [to_heartbeat(r) for r in self._rows]

    def retry_budget(self, source_id):
        """Attempts allowed before backing off. Never unbounded."""
        used = sum(1 for r in self._rows if r.source_id == source_id)
        return max(0, MAX_PROBE_ATTEMPTS - used)
