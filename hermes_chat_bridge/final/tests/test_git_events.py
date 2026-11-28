import asyncio
from concurrent.futures import Future
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from git_dialog import DialogError
from git_dialog.append_store import AppendStore
from git_dialog.git_events import EventJournal, NativeDelivery, NotificationPump, event_id, owner_digest
from git_dialog.github import ApiError, GitHubDialog
from test_github import FakeApi, TASK, STAMP, writes


def value(seq=2):
    return {'source':'git-dialog', 'queue':'test', 'task_id':TASK, 'head':'b'*40,
            'message_id':'report', 'seq':seq, 'from':'mt', 'to':'chatgpt'}


def journal(path): return EventJournal(path, TASK, 'a'*40, 0)


class NativeStore:
    def __init__(self): self.subs, self.calls = [], []
    def active_subscriptions(self): return self.subs
    def enqueue_delivery(self, sid, eid, payload):
        self.calls.append((sid, eid, payload)); return 1


def sub(owner='owner'):
    return dict(id='subscription', owner=owner, event='hermes.message.created', arguments={'queue':'test'})


@pytest.mark.parametrize('principal', [None, {}, '', 'two owners', '\nowner', 'x'*513])
def test_owner_binding_never_comes_from_ambiguous_values(principal):
    with pytest.raises(DialogError): owner_digest(principal)


def test_journal_private_durable_metadata_only_and_no_rebind(tmp_path):
    path = tmp_path/'events.sqlite'
    j = journal(path); j.bind(owner_digest('principal_alice')); j.record(value()); j.close()
    assert path.stat().st_mode & 0o777 == 0o600
    j = journal(path)
    try:
        assert j.owner_hash == owner_digest('principal_alice')
        assert j.pending() == (event_id(value()), value())
        j.bind(owner_digest('principal_alice'))
        with pytest.raises(DialogError): j.bind(owner_digest('foreign'))
        j.acknowledge(event_id(value())); assert j.pending() is None
        assert b'principal_alice' not in path.read_bytes()
    finally: j.close()


@pytest.mark.parametrize('kind', ['empty','foreign','link','mode','hardlink','checkpoint'])
def test_journal_never_adopts_or_repairs_existing_invalid_file(tmp_path, kind):
    path = tmp_path/'events.sqlite'
    if kind in ('mode','hardlink','checkpoint'):
        j = journal(path); j.close()
        if kind == 'mode': path.chmod(0o644)
        if kind == 'hardlink': (tmp_path/'second').hardlink_to(path)
        if kind == 'checkpoint':
            with sqlite3.connect(path) as c: c.execute('UPDATE route SET head=?', ('c'*40,))
    elif kind == 'link': path.symlink_to(tmp_path/'target')
    else:
        path.touch(mode=0o600)
        if kind == 'foreign':
            with sqlite3.connect(path) as c: c.execute('CREATE TABLE secrets(x)')
    with pytest.raises((DialogError, OSError)): journal(path)


@pytest.mark.parametrize('change', [{'body':'secret'}, {'job_id':'fake'}, {'task_id':'foreign'},
    {'from':'chatgpt'}, {'to':'mt'}, {'source':'queue'}, {'queue':'prod'},
    {'seq':True}, {'seq':0}, {'head':'short'}, {'message_id':'\n'}])
def test_only_bounded_git_metadata_can_be_journalled(tmp_path, change):
    j = journal(tmp_path/'e')
    try:
        with pytest.raises(DialogError): j.record(dict(value(), **change))
        assert j.pending() is None
    finally: j.close()


def test_checkpoint_excludes_historical_messages(tmp_path):
    j = EventJournal(tmp_path/'e', TASK, 'a'*40, 12)
    try:
        with pytest.raises(DialogError): j.record(value(12))
        j.record(value(14)); assert j.pending()[1]['seq'] == 14
    finally: j.close()


def test_native_delivery_explicitly_filters_owner_event_queue_before_enqueue():
    async def run():
        store = NativeStore()
        store.subs = [sub('foreign'), sub(None), dict(sub(), event='other'),
                      dict(sub(), arguments='{"queue":"test"}'),
                      dict(sub(), arguments={'queue':'foreign'}), sub()]
        delivery = NativeDelivery(store, asyncio.get_running_loop(), TASK)
        assert await delivery.deliver(event_id(value()), value(), owner_digest('owner')) is True
        assert store.calls == [('subscription', event_id(value()), value())]
        store.subs = [sub('foreign')]
        assert await delivery.deliver(event_id(value()), value(), owner_digest('owner')) is False
    asyncio.run(run())


def test_native_methods_always_execute_on_original_event_loop_thread():
    async def run():
        original = threading.get_ident()
        class Store(NativeStore):
            def active_subscriptions(self):
                assert threading.get_ident() == original
                return [sub()]
            def enqueue_delivery(self, *args):
                assert threading.get_ident() == original
                return super().enqueue_delivery(*args)
        store = Store(); delivery = NativeDelivery(store, asyncio.get_running_loop(), TASK)
        def gateway_thread():
            assert threading.get_ident() != original
            return delivery.submit(event_id(value()), value(), owner_digest('owner')).result(2)
        assert await asyncio.to_thread(gateway_thread) is True
        assert len(store.calls) == 1
    asyncio.run(run())


def test_delivery_is_rejected_on_wrong_thread_before_native_sql():
    async def run():
        store = NativeStore(); delivery = NativeDelivery(store, asyncio.get_running_loop(), TASK)
        def wrong():
            with pytest.raises(DialogError):
                asyncio.run(delivery.deliver(event_id(value()), value(), owner_digest('owner')))
        await asyncio.to_thread(wrong)
        assert not store.calls
    asyncio.run(run())


def publish_pair(store, dialog, *, lose=False):
    first = store.publish(dialog, TASK, dialog.load(TASK).head, message_id='request',
        actor='chatgpt', kind='task', body='private request', created_at_utc=STAMP)
    original = dialog.transport.request
    if lose:
        def lost(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'PATCH': raise ApiError()
            return result
        dialog.transport.request = lost
    try:
        return store.publish(dialog, TASK, first.commit_sha, message_id='report', actor='mt',
                             kind='report', body='private report', created_at_utc=STAMP)
    finally: dialog.transport.request = original


def test_scan_derives_only_confirmed_mt_metadata_without_remote_writes(tmp_path):
    api = FakeApi(); dialog = GitHubDialog(api); store = AppendStore(tmp_path/'a')
    try:
        reply = publish_pair(store, dialog); api.calls.clear()
        cursor, page = store.notification_page(dialog, TASK)
        assert cursor == 2 and page == [dict(value(), head=reply.commit_sha)]
        assert not api.calls and 'body' not in page[0] and 'job_id' not in page[0]
        assert store.notification_page(dialog, TASK, after_seq=2)[1] == []
        assert store.notification_page(dialog, TASK, after_row=cursor) == (0, [])
    finally: store.close()


def test_lost_git_response_recovers_exact_saved_message_get_only(tmp_path):
    api = FakeApi(); dialog = GitHubDialog(api); path = tmp_path/'a'
    store = AppendStore(path)
    with pytest.raises(DialogError): publish_pair(store, dialog, lose=True)
    assert store.status(TASK, 'report')['confirmed_commit'] is None
    store.close(); store = AppendStore(path); api.calls.clear()
    try:
        _, page = store.notification_page(dialog, TASK)
        assert len(page) == 1 and page[0]['message_id'] == 'report'
        assert store.status(TASK, 'report')['confirmed_commit'] == page[0]['head']
        assert not writes(api)
    finally: store.close()


def test_unobserved_attempt_is_not_recomputed_or_republished(tmp_path):
    api = FakeApi(); dialog = GitHubDialog(api); store = AppendStore(tmp_path/'a')
    first = store.publish(dialog, TASK, api.initial, message_id='request', actor='chatgpt',
                          kind='task', body='data', created_at_utc=STAMP)
    original = api.request
    def fail(method, path, payload=None):
        if method == 'PATCH': raise ApiError()
        return original(method, path, payload)
    api.request = fail
    with pytest.raises(DialogError): store.publish(dialog, TASK, first.commit_sha,
        message_id='report', actor='mt', kind='report', body='data', created_at_utc=STAMP)
    api.request = original; api.calls.clear()
    try:
        assert store.notification_page(dialog, TASK)[1] == []
        assert not writes(api)
    finally: store.close()


def test_crash_after_native_enqueue_before_ack_replays_same_event_not_git(tmp_path):
    api = FakeApi(); dialog = GitHubDialog(api); store = AppendStore(tmp_path/'a')
    publish_pair(store, dialog); api.calls.clear()
    j = journal(tmp_path/'e'); j.bind(owner_digest('owner'))
    native_unique = set(); submits = []
    class Delivery:
        def submit(self, eid, data, digest):
            native_unique.add(('subscription', eid)); submits.append(eid)
            future = Future(); future.set_result(True); return future
    pump = NotificationPump(j, store, dialog, Delivery(), clock=lambda:0)
    pump.tick(); pump.close(); j.close()  # enqueue completed, local ack not yet performed
    j = journal(tmp_path/'e'); pump = NotificationPump(j, store, dialog, Delivery(), clock=lambda:0)
    try:
        pump.tick(); pump.tick()
        assert j.pending() is None and len(native_unique) == 1
        assert len(submits) == 2 and submits[0] == submits[1]
        assert not writes(api)
    finally: pump.close(); j.close(); store.close()


def test_failed_or_no_subscriber_enqueue_keeps_outbox_for_reconciliation(tmp_path):
    j = journal(tmp_path/'e'); j.bind(owner_digest('owner')); j.record(value())
    clock = [0]; attempts = []
    class Delivery:
        def submit(self, *args):
            future = Future(); attempts.append(args)
            if len(attempts) == 1: future.set_exception(RuntimeError('must not log this'))
            else: future.set_result(False)
            return future
    appends = SimpleNamespace(notification_page=lambda *a, **k:(0, []))
    pump = NotificationPump(j, appends, None, Delivery(), clock=lambda:clock[0])
    try:
        pump.tick(); pump.tick(); assert j.pending() and pump.error == 'git_event_enqueue_unconfirmed'
        clock[0] = 31; pump.tick(); pump.tick()
        assert len(attempts) == 2 and j.pending()
    finally: pump.close(); j.close()
