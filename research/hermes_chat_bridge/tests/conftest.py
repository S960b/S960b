"""Fail-closed test isolation: no live SQLite paths and no real sockets."""
from pathlib import Path
from urllib.parse import urlparse, unquote
import socket
import sqlite3
import pytest


@pytest.fixture(autouse=True)
def isolated_io(tmp_path, monkeypatch):
    connect = sqlite3.connect
    def temporary_database(database, *args, **kwargs):
        name = str(database)
        if name.startswith('file:'):
            name = unquote(urlparse(name).path)
        if name != ':memory:' and not Path(name).resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError('test attempted a non-temporary SQLite database')
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', temporary_database)
    def no_network(*args, **kwargs):
        raise AssertionError('real network connections forbidden in test suite')
    monkeypatch.setattr(socket.socket, 'connect', no_network)

    monkeypatch.setattr(socket.socket, 'connect_ex', no_network)
