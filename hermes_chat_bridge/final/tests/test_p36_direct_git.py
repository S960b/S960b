"""P36 offline tests. No network, real service, Git mutations or credentials."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from git_dialog import DialogError
from git_dialog.direct_git import append_mt, describe, read_body, _reconcile_mt
from git_dialog.direct_events import (
    DirectGitJournal, DirectGitNotificationPump, newly_published,
)

T = "task_" + "d" * 32
H0, H1, H2, H3 = ("0" * 40, "1" * 40, "2" * 40, "3" * 40)


def message(seq, mid, sender):
    return NS(task_id=T, seq=seq, message_id=mid, sender=sender,
              recipient="chatgpt" if sender == "mt" else "mt", kind="report",
              body="opaque sample text")


def snapshot(head=H2):
    ms = [message(1, "m1", "chatgpt"), message(2, "m2", "mt")]
    return NS(head=head, state=NS(task_id=T, seq=len(ms), status="working",
                                 next_actor="chatgpt"), messages=ms,
              last=ms[-1])


class FakeDialog:
    def __init__(self):
        self.snap = snapshot()
        self.commits = {
            H2: {"message": f"git-dialog {T} m2", "parents": [{"sha": H1}]},
            H1: {"message": f"git-dialog {T} m1", "parents": [{"sha": H0}]},
            H0: {"message": "git-dialog-create", "parents": []},
        }
        self.at_commit = {H2: self.snap}

    def _commit(self, h):
        return self.commits[h]

    def _snapshot(self, t, h):
        assert t == T
        return (self.at_commit[h], None, None)

    def load(self, t):
        assert t == T
        return self.snap

    def _head(self, t):
        assert t == T
        return self.snap.head


class FakeRegistry:
    def page(self, *, offset=0, limit=20):
        if offset:
            return [], None
        return [{"task_id": T, "state": "active"}], None

    @contextmanager
    def writing(self, task_id):
        assert task_id == T
        yield


def test_git_scan_emits_only_mt_and_exact_committed_head():
    events = newly_published(FakeDialog(), snapshot(), (H0, 0))
    assert len(events) == 1
    e = events[0]
    assert e == {"source": "git-dialog", "queue": "test", "task_id": T,
                 "head": H2, "message_id": "m2", "seq": 2,
                 "from": "mt", "to": "chatgpt"}
    assert "body" not in e


def test_git_scan_accepts_existing_cursor_and_skips_duplicates():
    assert newly_published(FakeDialog(), snapshot(), (H2, 2)) == []
    assert newly_published(FakeDialog(), snapshot(), (H1, 1))[0]["head"] == H2


def test_git_scan_refuses_missing_ancestor():
    with pytest.raises(DialogError, match="direct_event_commit_rejected"):
        newly_published(FakeDialog(), snapshot(), (H3, 1))


def test_journal_atomic_commit_and_idempotent_delivery(tmp_path):
    db = tmp_path / "direct.sqlite"
    f = FakeDialog()
    j = DirectGitJournal(db, FakeRegistry(), f, bootstrap=False)
    try:
        event = newly_published(f, f.snap, (H0, 0))[0]
        j.commit_batch(T, H2, 2, [event])
        j.commit_batch(T, H2, 2, [event])
        assert j.cursor(T) == (H2, 2)
        pending = j.pending()
        assert pending[1] == event
        assert "body" not in pending[1]
        j.acknowledge(pending[0])
        assert j.pending() is None
    finally:
        j.close()
    k = DirectGitJournal(db, FakeRegistry(), f, bootstrap=False)
    assert k.cursor(T) == (H2, 2)
    assert k.pending() is None
    k.close()


def test_bootstrap_does_not_replay_legacy_messages(tmp_path):
    f = FakeDialog()
    j = DirectGitJournal(tmp_path / "direct.sqlite", FakeRegistry(), f)
    assert j.cursor(T) == (H2, 2)
    assert j.pending() is None
    j.close()


def test_pump_publishes_metadata_only(tmp_path):
    f = FakeDialog()
    j = DirectGitJournal(tmp_path / "direct.sqlite", FakeRegistry(), f,
                         bootstrap=False)
    j.commit_batch(T, H0, 0, [])
    j.bind("a" * 64)

    class Done:
        def done(self): return True
        def result(self): return True

    class Delivery:
        def __init__(self): self.items = []
        def submit(self, eid, data, digest):
            self.items.append((eid, data, digest))
            return Done()

    delivery = Delivery()
    clock = iter([1, 2, 3]).__next__
    p = DirectGitNotificationPump(j, f, FakeRegistry(), delivery, clock=clock)
    p.tick()
    assert len(delivery.items) == 1
    assert set(delivery.items[0][1]) == {
        "source", "queue", "task_id", "head", "message_id", "seq", "from", "to"
    }
    p.tick()
    assert j.pending() is None
    p.close()
    j.close()


def test_direct_mt_writer_uses_verified_adapter_no_mcp():
    class Dialog:
        def __init__(self): self.calls = []
        def load(self, t): return NS(head=H1)
        def append(self, *a, **kw):
            self.calls.append((a, kw))
            return NS(message=NS(message_id="mt_report", seq=1),
                      commit_sha=H2, created=True)
    d = Dialog()
    out = append_mt(d, FakeRegistry(), T, H1, "mt_report", "report", "data")
    assert out == {"message_id": "mt_report", "seq": 1,
                   "head": H2, "created": True}
    assert d.calls[0][1]["body"] == "data"
    assert d.calls[0][1]["actor"] == "mt"


def test_direct_mt_writer_refuses_stale_head():
    d = NS(load=lambda _: NS(head=H2))
    with pytest.raises(DialogError, match="stale_head"):
        append_mt(d, FakeRegistry(), T, H1, "mt_report", "report", "data")


def test_body_file_rejects_symlinks_and_oversize(tmp_path):
    p = tmp_path / "body.txt"
    p.write_text("plain text", encoding="utf8")
    assert read_body(str(p)) == "plain text"
    link = tmp_path / "link.txt"
    link.symlink_to(p)
    with pytest.raises(DialogError, match="direct_body_file_rejected"):
        read_body(str(link))
    p.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(DialogError, match="direct_body_file_rejected"):
        read_body(str(p))


def test_metadata_index_contains_no_body():
    x = describe(snapshot())
    assert x["head"] == H2
    assert x["messages"][-1]["message_id"] == "m2"
    assert "body" not in repr(x)

def test_new_journal_recovers_an_owned_zero_byte_aborted_startup(tmp_path):
    path = tmp_path / "p36.sqlite"
    path.touch(mode=0o600)
    assert path.stat().st_size == 0
    journal = DirectGitJournal(path, FakeRegistry(), FakeDialog(), bootstrap=False)
    try:
        assert journal.cursor(T) is None
    finally:
        journal.close()


def test_missing_active_branch_does_not_break_bootstrap_or_scan(tmp_path):
    class GoneDialog(FakeDialog):
        def load(self, task_id):
            raise DialogError("branch_missing_or_inaccessible")
        def _head(self, task_id):
            raise DialogError("branch_missing_or_inaccessible")

    gone = GoneDialog()
    journal = DirectGitJournal(tmp_path / "p36.sqlite", FakeRegistry(), gone)
    try:
        assert journal.cursor(T) is None
        pump = DirectGitNotificationPump(journal, gone, FakeRegistry(), None)
        pump.scan()
        assert journal.cursor(T) is None
        assert journal.pending() is None
    finally:
        journal.close()

def test_excluded_p26_pilot_never_reads_deleted_git_ref(tmp_path):
    class MustNotReadGit:
        def load(self, task_id):
            raise AssertionError("no GET ref allowed for legacy pilot")

    journal = DirectGitJournal(tmp_path / "p36.sqlite", FakeRegistry(),
                               MustNotReadGit(), exclude_task_id=T)
    try:
        assert journal.cursor(T) is None
        assert journal.pending() is None
    finally:
        journal.close()


def test_migrated_routing_alias_not_probed_even_if_registry_says_active(tmp_path):
    from git_dialog.direct_events import RETIRED_ROUTING_ANCHOR

    class AliasRegistry:
        def page(self, *, offset=0, limit=20):
            if offset:
                return [], None
            return [{"task_id": RETIRED_ROUTING_ANCHOR,
                     "state": "active", "remote_absent": 0}], None

    class NoGit:
        def load(self, task_id):
            raise AssertionError("retired alias must never cause Git GET")

        def _head(self, task_id):
            raise AssertionError("retired alias must never cause Git GET")

    journal = DirectGitJournal(tmp_path / "p36.sqlite", AliasRegistry(), NoGit())
    try:
        pump = DirectGitNotificationPump(journal, NoGit(), AliasRegistry(), None)
        pump.scan()
        assert journal.cursor(RETIRED_ROUTING_ANCHOR) is None
        assert journal.pending() is None
    finally:
        journal.close()


def test_remote_absent_registry_entry_never_probes_git(tmp_path):
    class DeletedRegistry:
        def page(self, *, offset=0, limit=20):
            return ([{"task_id": T, "state": "active", "remote_absent": 1}], None)

    class NoGit:
        def load(self, task_id):
            raise AssertionError("remote_absent must never cause Git GET")
        def _head(self, task_id):
            raise AssertionError("remote_absent must never cause Git GET")

    registry = DeletedRegistry()
    journal = DirectGitJournal(tmp_path / "p36.sqlite", registry, NoGit())
    try:
        DirectGitNotificationPump(journal, NoGit(), registry, None).scan()
        assert journal.cursor(T) is None
    finally:
        journal.close()


def test_new_dialog_creation_root_is_not_treated_as_a_message():
    dlg = FakeDialog()
    dlg.commits[H0]["message"] = f"git-dialog create {T}"
    events = newly_published(dlg, snapshot(), None)
    assert len(events) == 1
    assert events[0]["head"] == H2
    assert events[0]["message_id"] == "m2"


def test_new_dialog_root_must_match_task_id():
    dlg = FakeDialog()
    dlg.commits[H0]["message"] = "git-dialog create task_" + "f" * 32
    with pytest.raises(DialogError, match="direct_event_commit_rejected"):
        newly_published(dlg, snapshot(), None)
