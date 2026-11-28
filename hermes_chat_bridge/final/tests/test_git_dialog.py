"""Изолированные тесты git-dialog.v1: только tmpdir и fake-ref-store."""

import json
import threading

import pytest


@pytest.fixture(autouse=True)
def block_network_and_subprocess(monkeypatch):
    """Autouse: весь набор не имеет права ходить в сеть или запускать процессы."""
    import socket
    import subprocess

    def boom(*a, **k):
        raise AssertionError("сетевой/процессный вызов запрещён в изолированном тесте")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    yield

from git_dialog import (
    MAX_BODY_BYTES,
    DialogError,
    FakeRefStore,
    Message,
    Snapshot,
    TaskState,
    auto_retry_allowed,
    message_path,
    prepare_append,
    validate_rel_path,
    validate_snapshot,
    validate_task_id,
)

TASK = "task_demo_1"
REF = f"dialogs/{TASK}"
TASK_OBJ = {"protocol": "git-dialog", "version": "1", "task_id": TASK, "title": "demo"}


def initial_snapshot(head="h0"):
    state = TaskState(task_id=TASK, revision=0, seq=0, last_message_id=None,
                      next_actor="chatgpt", status="waiting")
    return Snapshot(task=dict(TASK_OBJ), state=state, messages=[], head=head)


def append(snap, head, mid, actor, kind="task", body="hello", **kw):
    return prepare_append(snap, head, task_id=TASK, message_id=mid, actor=actor,
                          kind=kind, body=body, **kw)


def with_message(snap, prepared):
    """Применить Prepared к snapshot (как после успешного commit)."""
    return Snapshot(task=snap.task, state=prepared.state,
                    messages=snap.messages + [prepared.message],
                    head=f"h{prepared.state.revision}")


# ---------- базовый обмен и ход ----------

def test_normal_exchange_chatgpt_mt_chatgpt():
    s = initial_snapshot()
    p1 = append(s, "h0", "m1", "chatgpt", body="задача")
    assert p1.created and p1.message.seq == 1 and p1.message.reply_to is None
    assert p1.state.next_actor == "mt"
    s = with_message(s, p1)

    p2 = append(s, "h1", "m2", "mt", kind="report", body="отчёт")
    assert p2.message.reply_to == "m1" and p2.message.seq == 2
    assert p2.state.next_actor == "chatgpt"
    s = with_message(s, p2)

    assert [m.message_id for m in s.messages] == ["m1", "m2"]
    assert s.state.revision == 2


def test_wrong_turn_rejected():
    s = initial_snapshot()
    with pytest.raises(DialogError) as e:
        append(s, "h0", "m1", "mt")
    assert e.value.code == "wrong_turn"


# ---------- идемпотентность и конфликты ----------

def test_idempotent_retry_returns_same_message():
    s = initial_snapshot()
    s = with_message(s, append(s, "h0", "m1", "chatgpt", body="текст"))
    again = append(s, s.head, "m1", "chatgpt", body="текст")
    assert again.created is False
    assert again.message.message_id == "m1"
    assert len(s.messages) == 1


def test_delayed_redelivery_of_older_message_is_noop():
    """Повтор m1 после m2 — no-op, не откатывает состояние (замечание ревью 1)."""
    s = initial_snapshot()
    s = with_message(s, append(s, "h0", "m1", "chatgpt", body="A"))
    s = with_message(s, append(s, "h1", "m2", "mt", kind="report", body="B"))

    repeat = append(s, s.head, "m1", "chatgpt", body="A")
    assert repeat.created is False
    assert repeat.state == s.state          # состояние не откатилось
    assert repeat.message.message_id == "m1"
    assert repeat.message.seq == 1


def test_message_id_conflict_with_other_payload():
    s = initial_snapshot()
    s = with_message(s, append(s, "h0", "m1", "chatgpt", body="A"))
    s = with_message(s, append(s, "h1", "m2", "mt", kind="report", body="B"))
    with pytest.raises(DialogError) as e:
        append(s, s.head, "m2", "mt", kind="report", body="ДРУГОЕ")
    assert e.value.code == "message_id_conflict"


def test_foreign_task_rejected():
    s = initial_snapshot()
    with pytest.raises(DialogError) as e:
        prepare_append(s, "h0", task_id="task_other_2", message_id="m1",
                       actor="chatgpt", kind="task", body="x")
    assert e.value.code == "foreign_task"


# ---------- валидация входных данных (замечания ревью 2-4) ----------

def test_snapshot_with_invalid_status_rejected():
    s = initial_snapshot()
    bad = TaskState(TASK, 0, 0, None, "chatgpt", "nonsense")
    with pytest.raises(DialogError) as e:
        append(Snapshot(dict(TASK_OBJ), bad, [], "h0"), "h0", "m1", "chatgpt")
    assert e.value.code == "invalid_status"


def test_snapshot_with_unknown_protocol_or_version_rejected():
    s = initial_snapshot()
    for task in ({**TASK_OBJ, "protocol": "other"}, {**TASK_OBJ, "version": "9"}):
        with pytest.raises(DialogError) as e:
            append(Snapshot(task, s.state, [], "h0"), "h0", "m1", "chatgpt")
        assert e.value.code in ("unknown_protocol", "unknown_version")


def test_task_id_with_newline_rejected():
    for bad in ("task_demo_1\n", "task_demo_1 ", "Task_Demo", "ab", "task demo"):
        with pytest.raises(DialogError) as e:
            validate_task_id(bad)
        assert e.value.code == "invalid_task_id"


def test_empty_and_invalid_message_id_rejected():
    s = initial_snapshot()
    for bad in ("", " ", "-x", "a" * 65, "m\n1"):
        with pytest.raises(DialogError) as e:
            append(s, "h0", bad, "chatgpt")
        assert e.value.code == "invalid_message_id"


def test_invalid_timestamp_rejected():
    s = initial_snapshot()
    for bad in ("not-a-date", "2026-13-45T99:99:99Z", "2026-10-26 12:00:00"):
        with pytest.raises(DialogError) as e:
            append(s, "h0", "m1", "chatgpt", created_at_utc=bad)
        assert e.value.code == "invalid_timestamp"


def test_corrupted_history_rejected():
    """Порванная цепочка reply_to / неверный seq / дубликаты отвергаются."""
    s = initial_snapshot()
    p1 = append(s, "h0", "m1", "chatgpt")
    msg1 = p1.message

    broken_reply = Message(TASK, "m2", 2, "mt", "chatgpt", "mX", "report",
                           "2026-10-26T00:00:00Z", "B")
    with pytest.raises(DialogError) as e:
        append(Snapshot(dict(TASK_OBJ), TaskState(TASK, 2, 2, "m2", "mt", "working"),
                        [msg1, broken_reply], "h2"), "h2", "m3", "chatgpt")
    assert e.value.code == "bad_reply_to"

    wrong_seq = Message(TASK, "m2", 5, "mt", "chatgpt", "m1", "report",
                        "2026-10-26T00:00:00Z", "B")
    with pytest.raises(DialogError) as e:
        append(Snapshot(dict(TASK_OBJ), TaskState(TASK, 2, 2, "m2", "mt", "working"),
                        [msg1, wrong_seq], "h2"), "h2", "m3", "chatgpt")
    assert e.value.code == "invalid_seq"

    dup = Message(TASK, "m1", 2, "mt", "chatgpt", "m1", "report",
                  "2026-10-26T00:00:00Z", "B")
    with pytest.raises(DialogError) as e:
        append(Snapshot(dict(TASK_OBJ), TaskState(TASK, 2, 2, "m1", "mt", "working"),
                        [msg1, dup], "h2"), "h2", "m3", "chatgpt")
    assert e.value.code in ("invalid_state", "bad_reply_to")


# ---------- пути, лимиты, неизменность ----------

def test_unsafe_paths_rejected():
    for bad in ["../etc/passwd", "dialogs/../x", "/abs/path", "a//b", "a/./b", "a\\b"]:
        with pytest.raises(DialogError):
            validate_rel_path(bad)
    with pytest.raises(DialogError):
        validate_task_id("../bad")
    assert message_path(TASK, 1, "chatgpt") == f"dialogs/{TASK}/messages/1.chatgpt.json"


def test_body_limit_enforced():
    s = initial_snapshot()
    big = "x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(DialogError) as e:
        append(s, "h0", "m1", "chatgpt", body=big)
    assert e.value.code == "body_too_large"


def test_snapshot_and_old_messages_immutable(tmp_path):
    s = initial_snapshot()
    p1 = append(s, "h0", "m1", "chatgpt", body="original")
    s = with_message(s, p1)
    snapshot_json = json.dumps([m.to_dict() for m in s.messages], sort_keys=True)
    before_state = s.state.to_dict()

    p2 = append(s, s.head, "m2", "mt", kind="report", body="reply")

    assert json.dumps([m.to_dict() for m in s.messages], sort_keys=True) == snapshot_json
    assert s.state.to_dict() == before_state
    assert p2.message.body == "reply"

    path = tmp_path / "1.chatgpt.json"
    path.write_text(p1.message.to_json(), encoding="utf-8")
    assert json.loads(path.read_bytes())["body"] == "original"


def test_message_and_state_are_one_transaction():
    s = initial_snapshot()
    p = append(s, "h0", "m1", "chatgpt")
    assert p.commit_parent == "h0"
    assert p.state.last_message_id == p.message.message_id
    assert p.state.seq == p.message.seq


# ---------- конкурентность ----------

def test_two_concurrent_writes_one_winner():
    store = FakeRefStore()
    store.open_branch(REF, "h0")
    s = initial_snapshot()
    a = append(s, "h0", "m1a", "chatgpt")
    b = append(s, "h0", "m1b", "chatgpt")

    r1 = store.publish_cas(REF, a.commit_parent, "h1a")
    r2 = store.publish_cas(REF, b.commit_parent, "h1b")
    assert r1["status"] == "ok"
    assert r2["status"] == "conflict"
    assert store.head(REF) == "h1a"


def test_threaded_concurrent_writes_exactly_one_winner():
    """Настоящая потоковая конкуренция (замечание ревью по тесту)."""
    store = FakeRefStore()
    store.open_branch(REF, "h0")
    results: list[dict] = []
    barrier = threading.Barrier(8)

    def writer(i: int):
        barrier.wait()
        results.append(store.publish_cas(REF, "h0", f"h{i}"))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert sum(1 for r in results if r["status"] == "ok") == 1
    assert sum(1 for r in results if r["status"] == "conflict") == 7
    winner = next(r["head"] for r in results if r["status"] == "ok")
    assert store.head(REF) == winner


def test_append_after_done_or_closing_rejected():
    s = initial_snapshot()
    s = with_message(s, append(s, "h0", "m1", "chatgpt", kind="close", body="закрываю"))
    assert s.state.status == "done"
    with pytest.raises(DialogError) as e:
        append(s, s.head, "m2", "mt", kind="report")
    assert e.value.code == "closed_task"

    st = TaskState(TASK, 1, 1, "m1", "mt", "closing")
    s2 = Snapshot(dict(TASK_OBJ), st, s.messages, "h9")
    with pytest.raises(DialogError):
        append(s2, "h9", "m2", "mt")


def test_deleted_branch_not_restored_by_late_writer():
    store = FakeRefStore()
    store.open_branch(REF, "h0")
    s = initial_snapshot()
    late = append(s, "h0", "m1", "chatgpt")

    assert store.delete_branch(REF) is True
    r = store.publish_cas(REF, late.commit_parent, "h1")
    assert r["status"] == "branch_missing"
    assert store.head(REF) is None


def test_same_result_recovered_without_duplication():
    s = initial_snapshot()
    p = append(s, "h0", "m1", "chatgpt", body="итог")
    s = with_message(s, p)
    repeat = append(s, s.head, "m1", "chatgpt", body="итог")
    assert repeat.created is False
    assert repeat.state == s.state


def test_unknown_outcome_forbids_automatic_retry():
    assert auto_retry_allowed("not_started") is True
    assert auto_retry_allowed("unknown") is False
    assert auto_retry_allowed("applied") is False


def test_no_network_or_subprocess_in_tests(monkeypatch):
    import subprocess
    import socket

    def boom(*a, **k):
        raise AssertionError("сетевой/процессный вызов запрещён в изолированном тесте")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)

    s = initial_snapshot()
    assert append(s, "h0", "m1", "chatgpt").created is True


# ---------- дополнительные требования поручения ----------

def test_historical_retry_after_close_is_noop():
    """Повтор старого сообщения после закрытия беседы — no-op, без дублей."""
    s = initial_snapshot()
    s = with_message(s, append(s, "h0", "m1", "chatgpt", body="A"))
    s = with_message(s, append(s, "h1", "m2", "mt", kind="report", body="B"))
    s = with_message(s, append(s, "h2", "m3", "chatgpt", kind="close", body="C"))
    assert s.state.status == "done"

    repeat = append(s, s.head, "m1", "chatgpt", body="A")
    assert repeat.created is False
    assert repeat.state == s.state
    assert [m.message_id for m in s.messages] == ["m1", "m2", "m3"]


def test_bool_seq_and_revision_rejected():
    with pytest.raises(DialogError) as e:
        message_path(TASK, True, "chatgpt")
    assert e.value.code == "invalid_seq"

    for st in (TaskState(TASK, True, 0, None, "chatgpt", "waiting"),
               TaskState(TASK, 0, True, None, "chatgpt", "waiting")):
        with pytest.raises(DialogError):
            validate_snapshot(Snapshot(dict(TASK_OBJ), st, [], "h0"))


def test_zero_and_nonstring_ids_rejected():
    for bad in (0, None, b"m1", ["m1"]):
        with pytest.raises(DialogError) as e:
            append(initial_snapshot(), "h0", bad, "chatgpt")
        assert e.value.code == "invalid_message_id"


def test_parse_rejects_corrupt_json():
    from git_dialog import parse_message, parse_state, parse_task

    good_task = dict(TASK_OBJ)
    for bad in ({**good_task, "protocol": "invalid"}, {**good_task, "version": "unknown"},
                {k: v for k, v in good_task.items() if k != "task_id"},
                {**good_task, "title": "  "}):
        with pytest.raises(DialogError):
            parse_task(bad)

    good_state = TaskState(TASK, 0, 0, None, "chatgpt", "waiting").to_dict()
    for bad in ({**good_state, "status": "nonsense"}, {**good_state, "version": "9"},
                {**good_state, "seq": True}, {k: v for k, v in good_state.items() if k != "revision"}):
        with pytest.raises(DialogError):
            parse_state(bad)

    good_msg = Message(TASK, "m1", 1, "chatgpt", "mt", None, "task",
                       "2026-10-26T00:00:00Z", "A").to_dict()
    for bad in ({**good_msg, "created_at_utc": "not-a-date"},
                {**good_msg, "message_id": ""},
                {**good_msg, "protocol": "x"},
                {**good_msg, "seq": True},
                {**good_msg, "from": "someone"}):
        with pytest.raises(DialogError):
            parse_message(bad)


def test_parse_snapshot_roundtrip_and_namespace():
    from git_dialog import branch_ref, parse_snapshot

    s = initial_snapshot()
    s = with_message(s, append(s, "h0", "m1", "chatgpt", body="A"))

    rebuilt = parse_snapshot(s.task, s.state.to_dict(),
                            [m.to_dict() for m in s.messages], s.head)
    assert rebuilt.state == s.state
    assert [m.message_id for m in rebuilt.messages] == ["m1"]

    assert branch_ref(TASK) == f"dialogs/{TASK}"
    assert message_path(TASK, 1, "chatgpt") == f"dialogs/{TASK}/messages/1.chatgpt.json"
