"""Standalone Taskboard password tests; requires only psycopg import availability."""
import pytest
pytest.importorskip('psycopg')
from bridge_pg.taskboard_v2 import make_credentials,_write_auth,_read_auth


def test_password_file(tmp_path):
    p=tmp_path/'auth.json'
    _write_auth(p,make_credentials('correct-horse-battery'))
    data=_read_auth(p)
    assert len(data['ticket_key'])==64
    assert 'correct-horse-battery' not in p.read_text()
    with pytest.raises(FileExistsError):_write_auth(p,make_credentials('another-secure-value'))
