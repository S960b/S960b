"""Fail-closed test isolation: no live SQLite paths and no real sockets."""
from pathlib import Path
import socket
import sqlite3
import pytest


@pytest.fixture(autouse=True)
def isolated_io(tmp_path, monkeypatch):
    connect = sqlite3.connect
    def temporary_database(database, *args, **kwargs):
        if str(database) != ':memory:' and not Path(database).resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError('test attempted a non-temporary SQLite database')
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', temporary_database)
    def no_network(*args, **kwargs):
        raise AssertionError('real network connections forbidden in test suite')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
