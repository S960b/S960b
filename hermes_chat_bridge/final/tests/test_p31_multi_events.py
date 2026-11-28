"""P31 multi-dialog push tests: fake Git, real private SQLite, no network."""
import asyncio
from concurrent.futures import Future
import json

import pytest

from git_dialog import DialogError
from git_dialog.git_events import event_id, owner_digest
from git_dialog.multi_git_events import (
    MultiDialogNativeDelivery, MultiDialogNotificationPump, MultiEventJournal,
)
from test_dialog_catalog import catalog
from test_github import TASK, writes


def test_only_new_confirmed_mt_reports_become_events(catalog, tmp_path):
    api, service = catalog
    # Existing messages at activation are *not* replayed to a new subscriber.
    old = service.create('mt', 'old_dialog', 'Old')
    service.append('mt', old['task_id'], head=old['head'],
                   message_id='old_report', kind='report', body='historical')
    checkpoint = service.appends.notification_checkpoint()
    journal = MultiEventJournal(tmp_path / 'p31.sqlite', TASK, checkpoint)
    try:
        a = service.create('mt', 'fresh_a', 'A')
        b = service.create('chatgpt', 'fresh_b', 'B')
        a_report = service.append('mt', a['task_id'], head=a['head'],
                                  message_id='same_id', kind='report', body='private A')
        b_request = service.append('chatgpt', b['task_id'], head=b['head'],
                                   message_id='request', kind='task', body='private question')
        b_report = service.append('mt', b['task_id'], head=b_request['commit'],
                                  message_id='same_id', kind='report', body='private B')
        # Pilot excluded even if it is registered and contains a valid MT reply.
        pilot_start = service.append('chatgpt', TASK, head=api.initial,
                                     message_id='pilot_task', kind='task', body='pilot')
        service.append('mt', TASK, head=pilot_start['commit'],
                       message_id='pilot_report', kind='report', body='pilot secret')
        api.calls.clear()
        cursor, events = service.appends.notification_page_all(
            service.dialog, service.registry, checkpoint_row=checkpoint,
            exclude_task_id=TASK)
        assert cursor == 0
        assert {e['task_id'] for e in events} == {a['task_id'], b['task_id']}
        assert {e['head'] for e in events} == {a_report['commit'], b_report['commit']}
        assert all(e['message_id'] == 'same_id' for e in events)
        assert all('body' not in e for e in events)
        assert not writes(api)
        for e in events:
            journal.record(e)
        assert journal.pending()[1]['task_id'] in {a['task_id'], b['task_id']}
        assert b'private' not in (tmp_path / 'p31.sqlite').read_bytes()
        assert b'historical' not in (tmp_path / 'p31.sqlite').read_bytes()
    finally:
        journal.close()


def test_journal_reopens_same_checkpoint_and_owner_without_replay(tmp_path):
    path = tmp_path / 'events.sqlite'
    digest = owner_digest('owner')
    j = MultiEventJournal(path, TASK, 7)
    j.bind(digest)
    a = {'source': 'git-dialog', 'queue': 'test',
         'task_id': 'task_one', 'head': 'a'*40, 'message_id': 'rep',
         'seq': 1, 'from': 'mt', 'to': 'chatgpt'}
    j.record(a)
    pending = j.pending()
    assert pending == (event_id(a), a)
    j.acknowledge(pending[0])
    j.close()
    j = MultiEventJournal(path, TASK, 9999)
    try:
        assert j.checkpoint_row == 7
        assert j.owner_hash == digest
        assert j.pending() is None
        j.record(a)
        assert j.pending() is None
        with pytest.raises(DialogError, match='git_event_owner_mismatch'):
            j.bind(owner_digest('foreign'))
        with pytest.raises(DialogError, match='multi_event_pilot_rejected'):
            j.record(dict(a, task_id=TASK))
    finally:
        j.close()


def test_multi_pump_delivers_same_event_to_native_subscription_and_acks(catalog, tmp_path):
    api, service = catalog
    checkpoint = service.appends.notification_checkpoint()
    a = service.create('mt', 'multi_dialog', 'Multi')
    receipt = service.append('mt', a['task_id'], head=a['head'],
                             message_id='reply', kind='report', body='only in Git')
    j = MultiEventJournal(tmp_path / 'events.sqlite', TASK, checkpoint)
    j.bind(owner_digest('owner'))
    native_calls = []
    class Delivery:
        def submit(self, eid, value, digest):
            assert digest == owner_digest('owner')
            native_calls.append((eid, value))
            f = Future(); f.set_result(True); return f
    t = [0]
    pump = MultiDialogNotificationPump(
        j, service.appends, service.dialog, Delivery(), service.registry,
        clock=lambda: t[0])
    try:
        api.calls.clear()
        pump.tick()
        assert len(native_calls) == 1
        assert native_calls[0][1]['task_id'] == a['task_id']
        assert native_calls[0][1]['head'] == receipt['commit']
        pump.tick()
        assert j.pending() is None
        t[0] = 31
        pump.tick()
        assert len(native_calls) == 1
        assert not writes(api)
    finally:
        pump.close(); j.close()


def test_native_multi_delivery_authenticates_subscription_owner_and_task():
    async def run():
        class Store:
            calls = []
            def active_subscriptions(self):
                return [
                    {'id':'wrong', 'owner':'foreign', 'event':'hermes.message.created',
                     'arguments': {'queue':'test'}},
                    {'id':'right', 'owner':'owner', 'event':'hermes.message.created',
                     'arguments': {'queue':'test'}},
                ]
            def enqueue_delivery(self, sid, eid, payload):
                self.calls.append((sid, eid, payload))
                return 1
        store = Store()
        delivery = MultiDialogNativeDelivery(store, asyncio.get_running_loop())
        value = {'source':'git-dialog','queue':'test','task_id':'task_new_dialog',
                 'head':'b'*40,'message_id':'sample','seq':3,'from':'mt','to':'chatgpt'}
        assert await delivery.deliver(event_id(value), value, owner_digest('owner'))
        assert store.calls == [('right', event_id(value), value)]
        with pytest.raises(DialogError):
            await delivery.deliver(event_id(value), dict(value, task_id='bad/id'),
                                   owner_digest('owner'))
    asyncio.run(run())
