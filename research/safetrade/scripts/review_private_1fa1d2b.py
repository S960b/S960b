"""Offline private-client contracts. Fake keys and mocked transport only."""
import json
import sys
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import private_api_probe as probe
from scripts import private_scan as scan


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return b"[]"


def test_entire_scan_never_bypasses_read_path_allowlist(monkeypatch):
    calls = []

    def transport(req, **kwargs):
        path = urlsplit(req.full_url).path.removeprefix("/api/v2")
        calls.append(path)
        return Response()

    class Opener:
        def open(self, req, **kwargs):
            return transport(req, **kwargs)

    monkeypatch.setattr(scan, "load_key", lambda: ("dummy-key", "dummy-secret"))
    monkeypatch.setattr(probe.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(probe.urllib.request, "urlopen", transport)
    scan.main()
    outside = [path for path in calls if path not in probe.READ_ONLY_WHITELIST]
    assert outside == [], f"scan reached forbidden transport paths: {outside}"


@pytest.mark.parametrize("obj,value", [
    ({"currency": "usdt", "balance": "123.456", "locked": "7.89"}, "123.456"),
    ({"id": 456789, "price": "234.567", "amount": "8.765"}, "234.567"),
])
def test_anonymized_dictionary_does_not_print_private_values(capsys, obj, value):
    probe.show_anonymized("synthetic response", 200, json.dumps(obj))
    assert value not in capsys.readouterr().out


def test_unknown_plaintext_response_is_not_echoed(capsys):
    probe.show_anonymized("synthetic failure", 500, "private value: dummy-sensitive-token")
    assert "dummy-sensitive-token" not in capsys.readouterr().out


@pytest.mark.parametrize("path", [
    "/orders/123/cancel", "/trade/orders", "/trade/market/orders/123",
    "/trade/withdraws", "/trade/deposits",
])
def test_api_get_rejects_unlisted_path_before_transport(monkeypatch, path):
    def forbidden_transport(*args, **kwargs):
        pytest.fail("transport must not be called")

    monkeypatch.setattr(probe.urllib.request, "build_opener", forbidden_transport)
    with pytest.raises(ValueError):
        probe.api_get(path, "dummy-key", "dummy-secret")


def test_hmac_matches_independent_fixed_vector(monkeypatch):
    monkeypatch.setattr(probe.time, "time", lambda: 1584087661.035)
    monkeypatch.setattr(probe, "_last_nonce_ms", [0])
    signed = probe.sign_headers("/trade/market/orders", "changeme", "changeme")
    assert signed["X-Auth-Nonce"] == "1584087661035"
    assert signed["X-Auth-Signature"] == (
        "8e8b02ca60cf30590d9b46587074c86f8cdcb72e4ee05eb58aca05bcbd5ffd17")


def test_protected_redirect_handler_rejects_cross_host_redirect():
    req = probe.urllib.request.Request(
        "https://safetrade.com/api/v2/trade/market/orders",
        headers={"X-Auth-Apikey": "dummy-key"})
    with pytest.raises(urllib.error.HTTPError) as err:
        probe.NoRedirect().redirect_request(req, None, 302, "found", {},
                                             "https://other.example/")
    assert err.value.code == 302
