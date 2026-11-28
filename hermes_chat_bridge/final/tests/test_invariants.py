from dataclasses import replace
import pytest
from git_dialog import (DialogError, Message, Snapshot, TaskState, parse_snapshot,
                        parse_task, prepare_append, validate_snapshot, message_file_path)

TASK = "task_demo_1"
META = {"protocol": "git-dialog", "version": "1", "task_id": TASK, "title": "demo"}
M1 = Message(TASK, "m1", 1, "chatgpt", "mt", None, "task", "2026-10-26T00:00:00Z", "hi")
STATE = TaskState(TASK, 1, 1, "m1", "mt", "working")


@pytest.mark.parametrize("state", [replace(STATE, next_actor="chatgpt"),
                                     replace(STATE, revision=0)])
def test_tail_and_state_agree(state):
    with pytest.raises(DialogError, match="invalid_state"):
        validate_snapshot(Snapshot(dict(META), state, [M1], "h1"))


def test_history_actor_must_alternate():
    m2 = Message(TASK, "m2", 2, "chatgpt", "mt", "m1", "task", M1.created_at_utc, "wrong")
    with pytest.raises(DialogError, match="bad_history_turn"):
        validate_snapshot(Snapshot(dict(META), TaskState(TASK, 2, 2, "m2", "mt", "working"), [M1, m2], "h2"))


def test_direct_dataclass_bool_seq_is_rejected():
    with pytest.raises(DialogError, match="invalid_seq"):
        validate_snapshot(Snapshot(dict(META), STATE, [replace(M1, seq=True)], "h1"))


@pytest.mark.parametrize("messages", [None, {}, "", 0])
def test_invalid_history_container_is_not_silently_empty(messages):
    with pytest.raises(DialogError, match="invalid_snapshot"):
        parse_snapshot(META, TaskState(TASK, 0, 0, None, "chatgpt", "waiting").to_dict(), messages, "h0")


def test_snapshot_requires_title_even_without_json_parser():
    meta = {k: v for k, v in META.items() if k != "title"}
    with pytest.raises(DialogError, match="invalid_task"):
        validate_snapshot(Snapshot(meta, STATE, [M1], "h1"))


def test_version_is_not_coerced():
    with pytest.raises(DialogError, match="unknown_version"):
        parse_task({**META, "version": 1})


def test_close_cannot_be_followed_or_reopened_in_snapshot():
    close = replace(M1, kind="close")
    with pytest.raises(DialogError):
        validate_snapshot(Snapshot(dict(META), STATE, [close], "h1"))
    m2 = Message(TASK, "m2", 2, "mt", "chatgpt", "m1", "report", M1.created_at_utc, "late")
    with pytest.raises(DialogError, match="closed_task"):
        validate_snapshot(Snapshot(dict(META), TaskState(TASK, 2, 2, "m2", "chatgpt", "done"), [close, m2], "h2"))


def test_branch_relative_file_path():
    assert message_file_path(2, "mt") == "messages/2.mt.json"
    with pytest.raises(DialogError):
        message_file_path(True, "mt")


def test_invalid_unicode_body_is_protocol_error():
    snapshot = Snapshot(dict(META), TaskState(TASK, 0, 0, None, "chatgpt", "waiting"), [], "h0")
    with pytest.raises(DialogError, match="invalid_body"):
        prepare_append(snapshot, "h0", task_id=TASK, message_id="m1",
                       actor="chatgpt", kind="task", body="\ud800")
