"""Read-only preflight acceptance checks, no external Git/LLM operations."""
import pytest
from scripts.preflight_chatgpt_append import PreflightError, preflight
from git_dialog import DialogError

HEAD = "a" * 40
TASK = "task_preflight_test"

def sample():
    return {
        "task": {"protocol": "git-dialog", "version": "1", "task_id": TASK,
                 "title": "Preflight", "initiator": "chatgpt"},
        "state": {"protocol": "git-dialog", "version": "1", "task_id": TASK,
                  "revision": 0, "seq": 0, "last_message_id": None,
                  "next_actor": "chatgpt", "status": "waiting"},
        "messages": [], "head": HEAD,
        "append": {"task_id": TASK, "head": HEAD, "message_id": "first_v1",
                   "kind": "task", "body": "Read only",
                   "created_at_utc": "2026-11-06T12:43:18Z"},
    }

def test_preflight_success():
    result = preflight(sample())
    assert result["ok"] and result["commit_parent"] == HEAD
    assert result["message_path"] == "messages/1.chatgpt.json"
    assert result["state"]["next_actor"] == "mt"
    assert result["state"]["seq"] == 1
    assert result["message"]["reply_to"] is None

@pytest.mark.parametrize("stamp", ["2026-11-06T12:43:18.562Z",
                                  "2026-11-06T12:43:18+00:00",
                                  "2026-13-06T12:43:18Z"])
def test_bad_timestamp_rejected(stamp):
    data = sample()
    data["append"]["created_at_utc"] = stamp
    with pytest.raises(DialogError, match="invalid_timestamp"):
        preflight(data)

def test_oversize_rejected():
    data = sample()
    data["append"]["body"] = "х" * 33000
    with pytest.raises(DialogError, match="body_too_large"):
        preflight(data)

def test_stale_head_rejected():
    data = sample()
    data["append"]["head"] = "b" * 40
    with pytest.raises(PreflightError, match="stale_head"):
        preflight(data)

def test_wrong_turn_rejected():
    data = sample()
    data["state"]["next_actor"] = "mt"
    with pytest.raises(DialogError):
        preflight(data)

def test_foreign_task_rejected():
    data = sample()
    data["append"]["task_id"] = "task_other"
    with pytest.raises(PreflightError, match="foreign_task"):
        preflight(data)

def test_forbidden_kind_and_actor_override():
    data = sample()
    data["append"]["kind"] = "return"
    with pytest.raises(PreflightError, match="unsafe_chatgpt_kind"):
        preflight(data)
    data = sample()
    data["append"]["actor"] = "mt"
    with pytest.raises(PreflightError, match="invalid_preflight_append"):
        preflight(data)

def test_invalid_history_rejected():
    data = sample()
    data["state"]["seq"] = 1
    with pytest.raises(DialogError):
        preflight(data)
