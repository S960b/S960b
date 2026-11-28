import json
import pytest
from waitress.adjustments import Adjustments

from git_dialog import DialogError
from git_dialog import wakeup_smoke as smoke


@pytest.mark.parametrize("header", [b"Content-Length: 2\r\nContent-Length: 3",
    b"Host: x\r\nHost: y", b"Transfer-Encoding: chunked", b"Expect: 100-continue",
    b"X_Hermes_Signature: x", b"Forwarded: host=evil", b"X-Forwarded-Host: evil"])
def test_strict_parser_rejects_before_wsgi(header):
    parser = smoke.StrictParser(Adjustments())
    parser.received(b"POST /bridge/wakeup HTTP/1.1\r\n" + header + b"\r\n\r\n{}")
    assert parser.error is not None
    parser.close()


def test_normal_parser_accepts_and_forces_close():
    parser = smoke.StrictParser(Adjustments())
    request = b"POST /bridge/wakeup HTTP/1.1\r\nHost: 127.0.0.1:9999\r\nContent-Length: 2\r\n\r\n{}"
    consumed = parser.received(request)
    parser.received(request[consumed:])
    assert parser.error is None and parser.completed and parser.connection_close
    parser.close()


def test_watchdog_closes_only_own_channels():
    class Channel:
        closed = False
        def close(self): self.closed = True
    owned, other = Channel(), Channel()
    smoke.close_owned({"owned": owned})
    assert owned.closed and not other.closed


def test_absolute_uri_rejected():
    parser = smoke.StrictParser(Adjustments())
    parser.received(b"GET http://evil.example/ HTTP/1.1\r\nHost: 127.0.0.1:9999\r\n\r\n")
    assert parser.error is not None
    parser.close()


def test_fixture_is_metadata_only():
    obj = smoke.fixture_event()
    assert set(obj) == {"name", "eventId", "timestamp", "data"}
    assert "body" not in obj["data"] and obj["data"]["job_id"] == "synthetic_wakeup_probe.job"


@pytest.mark.parametrize("args", [[], ["--self-test", "--port", "8789"], ["--self-test", "--credential", "x"]])
def test_cli_no_paths_or_ports(args):
    with pytest.raises(SystemExit): smoke.main(args)


def test_cli_safe_output(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "run", lambda: {"status": "passed"})
    assert smoke.main(["--self-test"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "passed"}


def test_cli_redacts_exception(monkeypatch, capsys):
    def fail(): raise RuntimeError("secret-path-must-not-echo")
    monkeypatch.setattr(smoke, "run", fail)
    assert smoke.main(["--self-test"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out.strip() == '{"error": "wakeup_smoke_failed"}'
