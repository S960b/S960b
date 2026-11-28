"""NEW temp-only Git publication -> native-compatible metadata delivery proof.

Standalone only: real Unix peer credentials, real native lifespan owner thread,
original asyncio loop and private SQLite deliveries with the reported native
uniqueness contract. Fake OAuth owner + in-memory Git, NOT live OAuth/Git or an
import of the original bridge. No pytest fixture relaxation/new dependencies.
"""
import argparse
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time

from . import DialogError
from .authority_acceptance import _MemoryDialog, _mt
from .gateway_lifespan import NativeGatewayHost
from .git_events import NativeDelivery, KEYS
from .lifecycle import LifecycleRegistry
from .native_gateway import register_mcp
from .unix_authority import UnixClient


class _NativeStore:
    """Only the reported PUBLIC contract; fixture is not the live BridgeStore."""
    def __init__(self, path):
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute('CREATE TABLE deliveries(sub_id TEXT,event_id TEXT,payload TEXT,terminal_reason TEXT)')
        self.conn.execute('CREATE UNIQUE INDEX delivery_event_unique ON deliveries(sub_id,event_id) '
                          'WHERE terminal_reason IS NULL OR terminal_reason != "duplicate"')
        self.conn.commit()
        self.thread, self.calls = threading.get_ident(), 0

    def active_subscriptions(self):
        assert threading.get_ident() == self.thread
        return [dict(id=owner, owner=owner, event='hermes.message.created', arguments={'queue':'test'})
                for owner in ('isolated_owner','foreign_owner')]

    def enqueue_delivery(self, sub_id, event_id, payload):
        assert threading.get_ident() == self.thread
        self.calls += 1
        envelope = {'eventId':event_id,'name':'hermes.message.created','timestamp':'fixture',
                    'data':payload,'cursor':None}
        cursor = self.conn.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?,?,NULL)',
                                  (sub_id,event_id,json.dumps(envelope)))
        self.conn.commit()
        return cursor.rowcount


async def _server(directory, pipe):
    host = store = None
    try:
        dialog = _MemoryDialog(); task = dialog.snapshot.state.task_id
        registry = LifecycleRegistry(directory/'r.sqlite')
        registry.register(task, dialog.snapshot.head); registry.close()
        store = _NativeStore(directory/'deliveries.sqlite')
        host = NativeGatewayHost(lambda:dialog, registry=directory/'r.sqlite',
            append_store=directory/'a.sqlite', task_id=task,
            paths={'chatgpt':directory/'cg.sock','mt':directory/'mt.sock'},
            worker_journal=directory/'worker.sqlite')
        host.configure_notifications(NativeDelivery(store, asyncio.get_running_loop(), task),
            path=directory/'events.sqlite', checkpoint_head=dialog.snapshot.head,
            checkpoint_seq=len(dialog.snapshot.messages))
        await asyncio.to_thread(host.start, seconds=3)
        handlers = {}
        class Registration:
            def tool(self, **kw):
                def register(fn): handlers[fn.__name__] = fn; return fn
                return register
        class Bridge:
            _shared_gateway_host = host
            allowed = False
            def require_owner(self):
                if not self.allowed: raise PermissionError()
                return 'isolated_owner'
        bridge = Bridge()
        register_mcp(Registration(), bridge, client=UnixClient(directory/'cg.sock'))
        # Actual OAuth guard ordering in this handler, no unauthenticated bind.
        try: await handlers['dialog_view'](task)
        except PermissionError: pass
        else: raise DialogError('git_event_acceptance_auth_failed')
        if host.notification_owner is not None: raise DialogError('git_event_acceptance_auth_failed')
        bridge.allowed = True
        view = await handlers['dialog_view'](task)
        if host.notification_owner is None: raise DialogError('git_event_acceptance_binding_failed')
        pipe.send({'ready':True,'head':view['head'],'task_id':task})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not host.ready: raise DialogError('git_event_acceptance_host_failed')
            if pipe.poll():
                command = pipe.recv()
                if command['op'] == 'stop': break
                if command['op'] == 'append':
                    pipe.send(await handlers['dialog_append'](task, command['head'], 'event_request', 'task',
                        'inert notification acceptance handoff'))
                elif command['op'] == 'events':
                    rows = store.conn.execute('SELECT sub_id,payload FROM deliveries').fetchall()
                    pipe.send({'rows':[(s,json.loads(p)) for s,p in rows], 'calls':store.calls})
                else: raise DialogError('git_event_acceptance_command_failed')
            await asyncio.sleep(0.01)
    except Exception:
        try: pipe.send({'error':'git_event_acceptance_failed'})
        except (OSError, EOFError): pass
        raise
    finally:
        if host is not None: await asyncio.to_thread(host.stop, seconds=3)
        if store is not None: store.conn.close()
        pipe.close()


def _entry(directory, pipe):
    asyncio.run(_server(Path(directory), pipe))


def run():
    context = multiprocessing.get_context('fork'); children = []
    with tempfile.TemporaryDirectory(prefix='git-events-acceptance-') as temp:
        directory = Path(temp); left, right = context.Pipe()
        server = context.Process(target=_entry, args=(temp,right)); children.append(server)
        server.start(); right.close()
        try:
            if not left.poll(5): raise DialogError('git_event_acceptance_start_failed')
            ready = left.recv()
            if not ready.get('ready'): raise DialogError('git_event_acceptance_start_failed')
            left.send({'op':'append','head':ready['head']})
            if not left.poll(5): raise DialogError('git_event_acceptance_append_failed')
            request = left.recv()
            parent, child = context.Pipe()
            mt = context.Process(target=_mt, args=(directory/'mt.sock',
                {'op':'append','task_id':ready['task_id'],'head':request['commit'],'message_id':'event_report',
                 'kind':'report','body':'inert notification acceptance result'}, child))
            children.append(mt); mt.start(); child.close()
            try:
                if not parent.poll(5): raise DialogError('git_event_acceptance_append_failed')
                receipt = parent.recv()
            finally: parent.close()
            if receipt.get('message_id') != 'event_report': raise DialogError('git_event_acceptance_append_failed')
            deadline = time.monotonic() + 5
            observed = None
            while time.monotonic() < deadline:
                left.send({'op':'events'})
                if not left.poll(2): raise DialogError('git_event_acceptance_delivery_failed')
                observed = left.recv()
                if observed.get('rows'): break
                time.sleep(0.05)
            rows = observed.get('rows', [])
            if len(rows) != 1 or rows[0][0] != 'isolated_owner':
                raise DialogError('git_event_acceptance_owner_failed')
            data = rows[0][1]['data']
            if (set(data) != KEYS or data['head'] != receipt['commit'] or data['message_id'] != 'event_report' or
                    data['seq'] != 4 or data['from'] != 'mt' or data['to'] != 'chatgpt'):
                raise DialogError('git_event_acceptance_metadata_failed')
            # Exact transport replay models the crash-before-ack window in the
            # unit tests; the fixture's reported native uniqueness is real SQL.
            assert all('job_id' not in row[1]['data'] and 'body' not in row[1]['data'] for row in rows)
        finally:
            try: left.send({'op':'stop'})
            except (OSError, EOFError): pass
            for process in children:
                process.join(3)
                if process.is_alive(): process.terminate(); process.join(1)
            left.close()
        if any(p.is_alive() or p.exitcode != 0 for p in children):
            raise DialogError('git_event_acceptance_cleanup_failed')
        if (directory/'cg.sock').exists() or (directory/'mt.sock').exists():
            raise DialogError('git_event_acceptance_cleanup_failed')
    return {'status':'git_event_acceptance_passed','real_unix':True,'native_lifespan_thread':True,
        'native_delivery_on_original_loop':True,'owner_auth_before_binding':True,'foreign_owner_denied':True,
        'metadata_only':True,'conversation_queue_jobs':0,'temporary_state_only':True,
        'git_network_used':False,'live_services_changed':False,'owned_children_stopped':True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', required=True); parser.parse_args(argv)
    try: result = run()
    except Exception: result = {'error':'git_event_acceptance_failed','retry_authorized':False}
    print(json.dumps(result, sort_keys=True)); return int('error' in result)


if __name__ == '__main__': raise SystemExit(main())
