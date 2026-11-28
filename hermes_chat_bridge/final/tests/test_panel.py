from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest

from git_dialog import DialogError
from git_dialog.github import GitHubDialog
from git_dialog.panel import CSS, render_panel, preview_response, security_headers, canary_snapshot
from test_github import FakeApi, TASK, STAMP


def snapshot(body="test", count=2):
    api = FakeApi(); dialog = GitHubDialog(api); head = api.initial
    for index in range(count):
        actor = "mt" if index % 2 else "chatgpt"
        head = dialog.append(TASK, head, message_id="m" + str(index), actor=actor,
                             kind="report" if index % 2 else "task", body=body, created_at_utc=STAMP).commit_sha
    return dialog.load(TASK)


def test_one_task_direction_history_and_disabled_delete_below_history():
    html = render_panel(snapshot(), now=datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)).decode()
    assert html.count("<tbody><tr>") == 1 and "MT → ChatGPT" in html
    assert "История (2 сообщений)" in html and "Ожидается ход" in html
    assert html.index("<summary>История") < html.index(">Удалить задачу</button>")
    assert 'type="button" disabled' in html and "queue" not in html
    assert "Изолированный просмотр" in html


class Tags(HTMLParser):
    def __init__(self): super().__init__(); self.tags = []
    def handle_starttag(self, tag, attrs): self.tags.append((tag, dict(attrs)))


def test_script_and_title_rendered_as_text_no_active_content():
    snap = snapshot('</pre><script>alert(1)</script><img src=x onerror=alert(2)>')
    snap.task["title"] = '<svg onload="alert(3)">'
    html = render_panel(snap).decode()
    tags = Tags(); tags.feed(html)
    assert all(tag not in ("script", "img", "svg", "iframe", "form") for tag, _ in tags.tags)
    assert not any(key.startswith("on") for _, attrs in tags.tags for key in attrs)
    assert "&lt;script&gt;" in html and "&lt;svg" in html


def test_pagination_only_requested_page_and_fixed_links():
    html = render_panel(snapshot(count=4), offset=1, limit=1).decode()
    assert html.count("<article>") == 1
    assert "<code>m1</code>" in html and "<code>m0</code>" not in html
    assert '/history?offset=2&amp;limit=1' in html and '/history?offset=0&amp;limit=1' in html


def test_long_body_preview_bounded_and_truncation_explicit():
    html = render_panel(snapshot("x" * 60000, count=2))
    assert len(html) < 20000 and "Показаны первые 2048 символов" in html.decode()


def test_security_headers_match_static_style_hash():
    import base64, hashlib
    headers = security_headers()
    digest = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    assert "'sha256-" + digest + "'" in headers["Content-Security-Policy"]
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store" and headers["X-Frame-Options"] == "DENY"
    assert "unsafe-inline" not in headers["Content-Security-Policy"]


@pytest.mark.parametrize("method,target,status", [("GET","/",200),("GET","/history?offset=0&limit=1",200),
    ("POST","/",405),("DELETE","/",405),("GET","/../secret",404),("GET","https://evil.example/",404),
    ("GET","/api/task",404),("GET","/?limit=21",400),("GET","/?offset=-1",400),
    ("GET","/?limit=1&limit=2",400),("GET","/?nonce=secret",400),("GET","/?offset=NaN",400)])
def test_pure_read_only_routes(method, target, status):
    actual, headers, body = preview_response(snapshot(), method, target, host="127.0.0.1:18878", expected_host="127.0.0.1:18878")
    assert actual == status and headers["Cache-Control"] == "no-store"
    assert b"secret" not in body


def test_host_mismatch_denied():
    assert preview_response(snapshot(), "GET", "/", host="evil.example", expected_host="127.0.0.1:18878")[0] == 403


def test_timeout_label_does_not_claim_worker_dead():
    now = datetime(2026, 10, 26, 15, 20, tzinfo=timezone.utc)
    html = render_panel(snapshot(), now=now).decode()
    assert 'class="red"' in html and "не доказательство падения" in html


@pytest.mark.parametrize("offset,limit", [(0,21),(0,True),(-1,20),(True,20)])
def test_render_bounds(offset, limit):
    with pytest.raises(DialogError): render_panel(snapshot(), offset=offset, limit=limit)


def test_synthetic_fixture_is_self_contained_and_not_live_task():
    snap = canary_snapshot()
    assert snap.state.task_id == "task_preview_fixture" and snap.head == "f" * 40
    html = render_panel(snap).decode()
    assert "FIXTURE_PONG_NO_EXECUTION" in html and "синтетические данные" in html
    assert "task_3c2d37a2485b" not in html
