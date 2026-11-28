from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from git_dialog import DialogError
from git_dialog.service import SnapshotReader
from git_dialog.wakeup import WakeupGate
from git_dialog.wakeup_host import SerialReaderLoop, WakeupHost
from test_service import setup, call, auth_header, fake_poll
from test_wakeup import event, wire, accept, request, KEY, NOW
from test_github import writes


def test_periodic_schedule_completion_and_no_catchup(monkeypatch):
    reader = SnapshotReader(); seen = []
    monkeypatch.setattr(reader, "refresh", lambda: seen.append(1))
    loop = SerialReaderLoop(reader)
    assert loop.tick(monotonic=0) == {"refreshed": True, "reason": "periodic"}
    assert not loop.tick(monotonic=59)["refreshed"]
    assert loop.tick(monotonic=60)["refreshed"]
    assert loop.tick(monotonic=1000)["refreshed"] and len(seen) == 3
    assert not loop.tick(monotonic=1000)["refreshed"]


def test_wakeup_and_due_coalesce_once(monkeypatch):
    reader = SnapshotReader(); gate = WakeupGate(KEY); seen = []
    monkeypatch.setattr(reader, "refresh", lambda: seen.append(1))
    loop = SerialReaderLoop(reader, gate)
    assert accept(gate)[0] == 202
    assert loop.tick(monotonic=0) == {"refreshed": True, "reason": "wakeup"}
    assert not loop.tick(monotonic=1)["refreshed"] and len(seen) == 1
    assert loop.tick(monotonic=60)["reason"] == "periodic"


def test_early_wakeup_then_fallback_and_duplicate(monkeypatch):
    reader = SnapshotReader(); gate = WakeupGate(KEY)
    loop = SerialReaderLoop(reader, gate)
    loop.tick(monotonic=0)
    assert accept(gate)[0] == 202 and loop.tick(monotonic=20)["reason"] == "wakeup"
    assert accept(gate)[1] == "duplicate"
    assert not loop.tick(monotonic=60)["refreshed"]
    assert loop.tick(monotonic=80)["reason"] == "periodic"


def test_no_concurrent_refresh_and_stop(monkeypatch):
    reader = SnapshotReader(); gate = WakeupGate(KEY)
    loop = SerialReaderLoop(reader, gate); entered = threading.Event(); release = threading.Event()
    seen = []
    def refresh():
        seen.append(1); entered.set()
        assert release.wait(timeout=2)
    monkeypatch.setattr(reader, "refresh", refresh)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(loop.tick, monotonic=0)
        assert entered.wait(timeout=2)
        second = pool.submit(loop.tick, monotonic=0)
        stopped = pool.submit(loop.stop)
        assert not second.done() and not stopped.done() and seen == [1]
        release.set(); assert first.result(timeout=2)["refreshed"]
        second.result(timeout=2); stopped.result(timeout=2)
    assert accept(gate)[0] == 202
    assert loop.tick(monotonic=1000) == {"refreshed": False, "reason": "stopped"}
    assert seen == [1]


@pytest.mark.parametrize("value", [0, 29, 3601, True, 30.0])
def test_invalid_interval(value):
    with pytest.raises(DialogError): SerialReaderLoop(SnapshotReader(), interval=value)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "0"])
def test_invalid_clock(value):
    with pytest.raises(DialogError): SerialReaderLoop(SnapshotReader()).tick(monotonic=value)


def test_clock_rewind_denied():
    loop = SerialReaderLoop(SnapshotReader()); loop.tick(monotonic=10)
    with pytest.raises(DialogError): loop.tick(monotonic=9)


def test_slow_read_uses_completion_deadline(monkeypatch):
    values = iter([0, 50, 60, 110, 160])
    monkeypatch.setattr("git_dialog.wakeup_host.time.monotonic", lambda: next(values))
    loop = SerialReaderLoop(SnapshotReader())
    assert loop.tick()["refreshed"]
    assert not loop.tick()["refreshed"]  # Next due110, not60 after slow first read.
    assert loop.tick()["refreshed"]


def test_untrusted_binding_denied():
    with pytest.raises(DialogError): SerialReaderLoop(lambda: None)
    with pytest.raises(DialogError): SerialReaderLoop(SnapshotReader(), lambda: None)
    with pytest.raises(DialogError): WakeupHost(object())


def test_host_disabled_and_construct_no_io(tmp_path, monkeypatch):
    _, panel = setup(tmp_path); seen = []
    monkeypatch.setattr(panel.reader, "refresh", lambda: seen.append(1))
    host = WakeupHost(panel)
    assert request(host, *wire(event()))[0] == 404 and not seen
    assert call(host)[0] == 401
    assert call(host, auth=auth_header())[0] == 200 and not seen


def test_wsgi_to_serial_reader_get_chain(tmp_path, monkeypatch):
    _, panel = setup(tmp_path); api = fake_poll(monkeypatch)
    panel.reader = SnapshotReader(api)
    host = WakeupHost(panel, key=KEY)
    monkeypatch.setattr("git_dialog.wakeup.time.time", lambda: NOW)
    assert not api.calls
    assert request(host, *wire(event()))[0] == 202 and not api.calls
    assert host.loop.tick(monotonic=0)["reason"] == "wakeup"
    assert panel.reader.current() and not writes(api)
    api.calls.clear()
    assert request(host, *wire(event()))[1]["status"] == "duplicate"
    assert not host.loop.tick(monotonic=1)["refreshed"] and not api.calls
    assert call(host, auth=auth_header())[0] == 200


def test_source_failure_waits_until_periodic_deadline(tmp_path, monkeypatch):
    _, panel = setup(tmp_path); api = fake_poll(monkeypatch); api.refs.clear()
    panel.reader = SnapshotReader(api); host = WakeupHost(panel)
    assert host.loop.tick(monotonic=0)["refreshed"]
    assert panel.reader.status()["error"] and call(host, auth=auth_header())[0] == 503
    api.calls.clear()
    assert not host.loop.tick(monotonic=59)["refreshed"] and not api.calls
    assert host.loop.tick(monotonic=60)["refreshed"] and api.calls
