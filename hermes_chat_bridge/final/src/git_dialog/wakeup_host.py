"""Offline-reviewed host composition; no listener, thread, CLI or secrets IO.

One loop.tick() path serializes periodic and authenticated wakeup refreshes.
The runner must use this path exclusively, not also call reader.refresh/drain.
No background work starts at construction or during a WSGI request.
"""
import math
import threading
import time

from . import DialogError
from .service import PanelApplication, SnapshotReader
from .wakeup import WakeupApplication, WakeupGate


class SerialReaderLoop:
    def __init__(self, reader, gate=None, *, interval=60):
        if type(reader) is not SnapshotReader or (gate is not None and type(gate) is not WakeupGate):
            raise DialogError("host_binding_rejected")
        if type(interval) is not int or not 30 <= interval <= 3600:
            raise DialogError("host_interval_rejected")
        self._reader, self._gate, self._interval = reader, gate, interval
        self._next, self._clock = None, None
        self._lock = threading.Lock()
        self._stopped = False

    def tick(self, *, monotonic=None):
        """Trusted runner call. Zero or one GET refresh per tick, even if due
        and pending together. Results describe attempted reads, not Git writes
        or a successful source update (inspect the reader's error separately).
        """
        with self._lock:
            if self._stopped:
                return {"refreshed": False, "reason": "stopped"}
            now = time.monotonic() if monotonic is None else monotonic
            if (type(now) not in (int, float) or not math.isfinite(now) or now < 0 or
                    (self._clock is not None and now < self._clock)):
                raise DialogError("host_clock_rejected")
            self._clock = now
            due = self._next is None or now >= self._next
            drained = self._gate.drain(self._reader) if self._gate is not None else False
            if due and not drained:
                self._reader.refresh()
            if due or drained:
                # Completion-based spacing; a slow GET cannot cause catch-up storms.
                finish = time.monotonic() if monotonic is None else now
                self._next = max(now, finish) + self._interval
                return {"refreshed": True, "reason": "wakeup" if drained else "periodic"}
            return {"refreshed": False, "reason": "idle"}

    def stop(self):
        """No new reads after return. Waits for an in-flight GET; it does not
        kill sockets/threads/processes. The network transport supplies timeouts.
        """
        self._reader.cancel()  # Signal before waiting on the in-flight reader lock.
        with self._lock:
            self._stopped = True


class WakeupHost:
    def __init__(self, panel, *, key=None, interval=60):
        if type(panel) is not PanelApplication or type(panel.reader) is not SnapshotReader:
            raise DialogError("host_panel_rejected")
        # key=None is default-deny. No automatic registration, key generation,
        # DB migration or reuse of the owner's password/PAT/subscription secret.
        gate = None if key is None else WakeupGate(key)
        self.loop = SerialReaderLoop(panel.reader, gate, interval=interval)
        self._application = WakeupApplication(panel, gate)

    def __call__(self, env, start_response):
        return self._application(env, start_response)
