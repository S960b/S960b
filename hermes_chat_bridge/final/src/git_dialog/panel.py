"""Bounded read-only HTML projection. No HTTP listener, scripts or live data IO."""
import base64
import hashlib
from html import escape
import re
from urllib.parse import parse_qsl, urlsplit

from . import DialogError
from .gateway import panel_view


def canary_snapshot():
    """Synthetic public test markers only; never loads files, Git, DB or PAT."""
    from . import Message, TaskState, parse_snapshot
    task_id = "task_preview_fixture"
    messages = [Message(task_id, "fixture_request", 1, "chatgpt", "mt", None, "ack",
                        "2026-10-26T15:00:00Z", "FIXTURE_PING_NO_EXECUTION"),
                Message(task_id, "fixture_report", 2, "mt", "chatgpt", "fixture_request", "report",
                        "2026-10-26T15:00:01Z", "FIXTURE_PONG_NO_EXECUTION")]
    task = {"protocol": "git-dialog", "version": "1", "task_id": task_id,
            "title": "Изолированный canary · синтетические данные"}
    state = TaskState(task_id, 2, 2, "fixture_report", "chatgpt", "working")
    return parse_snapshot(task, state.to_dict(), [m.to_dict() for m in messages], "f" * 40)

CSS = """body{margin:2rem auto;padding:0 1rem;max-width:1100px;font:16px system-ui;background:#101722;color:#e6edf5}
h1{font-size:1.4rem}table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;padding:.8rem;border-bottom:1px solid #334155}
small,.muted{color:#a7b5c8}code,pre{font-family:ui-monospace,monospace;overflow-wrap:anywhere}pre{white-space:pre-wrap;max-height:18rem;overflow:auto}
.green{color:#69d6a0}.yellow{color:#f8d66d}.red{color:#ff9292}.gray{color:#9ca3af}.notice,article{padding:1rem;background:#182335;border:1px solid #334155;border-radius:.6rem;margin:1rem 0}
a{color:#8ecbff}button{display:block;margin:.6rem 0;padding:.6rem}summary{cursor:pointer}nav{display:flex;gap:1rem}button:disabled{opacity:.55}"""


def security_headers():
    digest = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode("ascii")
    return {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'sha256-" + digest +
                "'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}


def render_panel(snapshot, *, offset=0, limit=20, now=None):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise DialogError("invalid_render_page")
    view = panel_view(snapshot, now=now, offset=offset, limit=limit)
    return render_view(view, offset=offset, limit=limit)


def render_view(view, *, offset=0, limit=20):
    """Render a trusted gateway projection; every data string is escaped."""
    esc = lambda value: escape(str(value), quote=True)
    names = {"chatgpt": "ChatGPT", "mt": "MT"}
    labels = {"green": "Ответил", "yellow": "Ожидается ход", "red": "Ошибка или задержка", "gray": "—"}
    direction = view["direction"]
    arrow = (names[direction["from"]] + " → " + names[direction["to"]]) if direction else "Обмен ещё не начат"
    badges = []
    for actor in ("chatgpt", "mt"):
        color = view["actor_colors"][actor]
        badges.append('<td class="' + color + '">● ' + labels[color] + '</td>')
    waiting = names[view["waiting_for"]] if view["waiting_for"] else "Никто"
    articles = []
    for message in view["history"]:
        body = message["body"]
        preview = body[:2048]
        truncated = '<p class="muted">Показаны первые 2048 символов. Полный текст остаётся в хранилище.</p>' if len(body) > 2048 else ''
        articles.append('<article><strong>#' + str(message["seq"]) + ' ' + esc(names[message["from"]]) +
            ' → ' + esc(names[message["to"]]) + '</strong> <small>' + esc(message["created_at_utc"]) +
            ' · ' + esc(message["kind"]) + '</small><p><code>' + esc(message["message_id"]) +
            '</code></p><pre>' + esc(preview) + '</pre>' + truncated + '</article>')
    links = []
    if offset:
        links.append('<a href="/history?offset=' + str(max(0, offset - limit)) + '&amp;limit=' + str(limit) + '">← Ранее</a>')
    if view["next_offset"] is not None:
        links.append('<a href="/history?offset=' + str(view["next_offset"]) + '&amp;limit=' + str(limit) + '">Далее →</a>')
    note = '10 минут без передачи хода. Это не доказательство падения исполнителя.' if view["stalled"] else 'Цвет показывает передачу хода, а не heartbeat процесса.'
    document = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Git Dialog · просмотр</title><style>' + CSS + '</style><body>'
        '<h1>Git Dialog · одна беседа</h1><p class="notice">Изолированный просмотр. Запись, закрытие и удаление отключены. Это не рабочая панель моста.</p>'
        '<table><thead><tr><th>Задача</th><th>Направление</th><th>ChatGPT</th><th>MT</th><th>Статус</th><th>Следующий ход</th></tr></thead><tbody><tr>'
        '<td>' + esc(view["title"]) + '<br><code>' + esc(view["task_id"]) + '</code></td><td>' + esc(arrow) + '</td>' +
        ''.join(badges) + '<td>' + esc(view["status"]) + '</td><td>' + esc(waiting) + '</td></tr></tbody></table>'
        '<p class="muted">' + esc(note) + '</p><details open><summary>История (' + str(view["total_messages"]) +
        ' сообщений)</summary>' + ''.join(articles) + '<nav>' + ''.join(links) + '</nav></details>'
        '<button type="button" disabled title="Нужны проверенная аутентификация и отдельное подтверждение">Удалить задачу</button>'
        '<p class="muted">Удаление ветки не гарантирует физического стирания Git-объектов.</p>'
        '<small>Snapshot: <code>' + esc(view["head"]) + '</code></small></body></html>')
    result = document.encode("utf-8")
    if len(result) > 256 * 1024:
        raise DialogError("render_too_large")
    return result


def preview_response(snapshot, method, target, *, host, expected_host, now=None):
    """Pure GET router for a future isolated preview adapter, not authentication.

    Test canary data only until a reviewed authenticated HTTP adapter exists.
    No filesystem routes, redirects, external links, POST, delete or RPC access.
    """
    if host != expected_host or not isinstance(host, str) or not host:
        return 403, security_headers(), b"Forbidden"
    if method != "GET":
        return 405, {**security_headers(), "Allow": "GET"}, b"Read-only"
    if not isinstance(target, str) or len(target) > 128 or any(ord(c) < 33 for c in target):
        return 400, security_headers(), b"Invalid request"
    try:
        url = urlsplit(target)
        if url.scheme or url.netloc or url.fragment or url.path not in ("/", "/history"):
            return 404, security_headers(), b"Not found"
        pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        if len(values) != len(pairs) or set(values) - {"offset", "limit"}:
            raise ValueError()
        if any(not re.fullmatch(r"[0-9]{1,6}", value) for value in values.values()):
            raise ValueError()
        body = render_panel(snapshot, offset=int(values.get("offset", "0")),
                            limit=int(values.get("limit", "20")), now=now)
        return 200, security_headers(), body
    except (ValueError, DialogError):
        return 400, security_headers(), b"Invalid request"
