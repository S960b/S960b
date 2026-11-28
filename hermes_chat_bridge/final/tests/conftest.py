"""All tests are offline and may access only temporary SQLite databases."""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import socket
import sqlite3
import subprocess
import pytest


@pytest.fixture(autouse=True)
def isolate_io(tmp_path, monkeypatch):
    original = sqlite3.connect

    def temporary_database(path, *args, **kwargs):
        # Preserve the offline guard while permitting real SQLite read-only
        # file: URI syntax. A URI is NOT a bypass: parse, resolve, and check
        # its actual filesystem path against the test's temporary root.
        target = path
        if isinstance(path, str) and path.startswith("file:"):
            if kwargs.get("uri") is not True:
                raise AssertionError("non-temporary SQLite database")
            parsed = urlsplit(path)
            if (parsed.scheme != "file" or parsed.netloc not in ("", "localhost") or
                    parsed.query != "mode=ro" or parsed.fragment or
                    not parsed.path.startswith("/")):
                raise AssertionError("non-temporary SQLite database")
            target = unquote(parsed.path)
        if not Path(target).resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError("non-temporary SQLite database")
        return original(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("network and subprocess forbidden in isolated tests")

    monkeypatch.setattr(sqlite3, "connect", temporary_database)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
