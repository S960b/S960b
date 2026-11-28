"""Bounded standalone IPC acceptance, separate from the offline pytest suite.

Only owned temporary sockets/SQLite, in-memory dialogue, own forked children.
No Git HTTP, original bridge import, PAT/key, live service, existing state or
historical canary IDs. Optional already-installed MCP SDK registration only.
Never called from pytest (its network/subprocess fixture remains untouched).
"""
import argparse
import asyncio
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time

from . import DialogError, parse_snapshot, prepare_append
from .append_store import AppendStore
from .authority import CommonAuthority
from .dialog_catalog import CreationStore, DialogCatalog
from .github import Receipt
from .lifecycle import LifecycleRegistry
from .native_gateway import register_mcp
from .panel import canary_snapshot
from .unix_authority import Runtime, UnixClient, UnixServer


class _MemoryDialog:
    repository = 'S960b/agent-dialogs'
    def __init__(self): self.snapshot = canary_snapshot()
    def load(self, task_id):
        if task_id != self.snapshot.state.task_id: raise DialogError('foreign_task')
        return self.snapshot
    def append(self, task_id, expected_head, **kwargs):
        prepared = prepare_append(self.load(task_id), expected_head, task_id=task_id, **kwargs)
        if prepared.created:
            head = hashlib.sha1((expected_head + json.dumps(prepared.message.to_dict(), sort_keys=True)).encode()).hexdigest()
            self.snapshot = parse_snapshot(self.snapshot.task, prepared.state.to_dict(),
                [m.to_dict() for m in self.snapshot.messages] + [prepared.message.to_dict()], head)
        return Receipt(self.snapshot.head, prepared.message, prepared.created)
    def reconcile(self, task_id, message):
        if message not in self.load(task_id).messages:
            raise DialogError('publication_not_observed')
        return Receipt(self.snapshot.head, message, False)


def _serve(directory, bridge_pid, pipe, native=False):
    store = registry = server = native_host = creations = None
    try:
        directory = Path(directory)
        dialog = _MemoryDialog()
        registry = LifecycleRegistry(directory / 'lifecycle.sqlite')
        registry.register(dialog.snapshot.state.task_id, dialog.snapshot.head)
        paths = {'chatgpt': directory/'cg.sock', 'mt': directory/'mt.sock'}
        if native:
            from .gateway_lifespan import NativeGatewayHost
            registry.close(); registry = None
            native_host = NativeGatewayHost(lambda: dialog,
                registry=directory/'lifecycle.sqlite', append_store=directory/'append.sqlite',
                task_id=dialog.snapshot.state.task_id, paths=paths, bridge_pid=bridge_pid,
                worker_journal=directory/'worker.sqlite')
            native_host.start(seconds=3)
        else:
            store = AppendStore(directory / 'append.sqlite')
            creations = CreationStore(directory / 'creations.sqlite')
            host = Runtime(CommonAuthority(dialog, registry, store, dialog.snapshot.state.task_id),
                           bridge_pid=bridge_pid, catalog=DialogCatalog(dialog, registry, store, creations))
            server = UnixServer(host, paths)
        pipe.send({'ready': True})
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if pipe.poll() and pipe.recv() == 'stop': break
            if native_host is not None:
                if not native_host.ready: raise DialogError('authority_acceptance_failed')
                time.sleep(0.02)
            else: server.serve_once(0.05)
    except Exception:
        pipe.send({'ready': False})
    finally:
        if native_host is not None: native_host.stop(seconds=3)
        if server is not None: server.close()
        if store is not None: store.close()
        if creations is not None: creations.close()
        if registry is not None: registry.close()
        pipe.close()


def _mt(path, request, pipe):
    try: pipe.send(UnixClient(path).call(request))
    except Exception: pipe.send({'error': 'acceptance_failed'})
    finally: pipe.close()


def run(*, sdk=False, native=False, writers=False):
    if writers and not native:
        raise DialogError('authority_acceptance_native_required')
    context = multiprocessing.get_context('fork')
    children, server_pipe = [], None
    handlers = {}
    sdk_version = None
    # Actual handler registration can be checked on the original runtime with
    # its existing mcp package; NEVER install a new dependency for this test.
    if sdk:
        from importlib.metadata import version
        from mcp.server.mcpserver import MCPServer
        sdk_version = version('mcp')
        real_mcp = MCPServer(name='Isolated gateway acceptance', version='1', debug=False)
    class Registration:
        def tool(self, **kwargs):
            def register(fn):
                if sdk: real_mcp.tool(**kwargs)(fn)
                handlers[fn.__name__] = fn
                return fn
            return register
    class Bridge:
        allowed = False
        def require_owner(self):
            if not self.allowed: raise PermissionError('denied')
            return 'isolated_owner'
    with tempfile.TemporaryDirectory(prefix='authority-acceptance-') as temp:
        directory = Path(temp)
        parent, child = context.Pipe()
        server_pipe = parent
        process = context.Process(target=_serve, args=(temp, os.getpid(), child, native))
        children.append(process); process.start(); child.close()
        try:
            if not parent.poll(3) or parent.recv() != {'ready': True}:
                raise DialogError('authority_acceptance_failed')
            client = UnixClient(directory/'cg.sock')
            bridge = Bridge()
            register_mcp(Registration(), bridge, client=client)
            task_id = _MemoryDialog().snapshot.state.task_id
            try: asyncio.run(handlers['dialog_view'](task_id))
            except PermissionError: pass
            else: raise DialogError('authority_acceptance_auth_failed')
            bridge.allowed = True
            view = asyncio.run(handlers['dialog_view'](task_id))
            first = asyncio.run(handlers['dialog_append'](task_id, view['head'], 'ipc_request', 'task',
                                                           'ISOLATED_IPC_NO_EXECUTION'))
            reply_parent, reply_child = context.Pipe()
            worker = context.Process(target=_mt, args=(directory/'mt.sock',
                {'op':'append', 'task_id':task_id, 'head':first['commit'], 'message_id':'ipc_report', 'kind':'report',
                 'body':'ISOLATED_IPC_RESPONSE_NO_EXECUTION'}, reply_child))
            children.append(worker); worker.start(); reply_child.close()
            if not reply_parent.poll(3): raise DialogError('authority_acceptance_failed')
            reply = reply_parent.recv(); reply_parent.close()
            if reply.get('message_id') != 'ipc_report': raise DialogError('authority_acceptance_failed')
            try: UnixClient(directory/'mt.sock').call({'op':'view','offset':0,'limit':20})
            except DialogError: pass  # MCP PID cannot impersonate local MT.
            else: raise DialogError('authority_acceptance_peer_failed')
            result = asyncio.run(handlers['dialog_view'](task_id))
            if result['total_messages'] != 4 or result['waiting_for'] != 'chatgpt':
                raise DialogError('authority_acceptance_failed')
            if writers:
                def mt_call(request):
                    left, right = context.Pipe()
                    child = context.Process(target=_mt, args=(directory/'mt.sock', request, right))
                    children.append(child); child.start(); right.close()
                    try:
                        if not left.poll(3): raise DialogError('authority_acceptance_failed')
                        answer = left.recv()
                    finally: left.close()
                    if 'error' in answer: raise DialogError('authority_acceptance_failed')
                    return answer
                action = json.dumps({'protocol':'git-dialog-action','version':1,'op':'sha256',
                                     'text':'shared host acceptance'})
                task = asyncio.run(handlers['dialog_append'](task_id, result['head'], 'ipc_worker_task', 'task', action))
                report = mt_call({'op':'worker_step','request_id':'ipc_worker_task',
                                  'report_id':'ipc_worker_report','run_id':'ipc_worker_run'})
                if report.get('status') != 'published': raise DialogError('authority_acceptance_failed')
                status = mt_call({'op':'writer_status','message_id':'ipc_worker_report'})
                if not status.get('attempted') or status.get('confirmed_commit') != report['confirmed_commit']:
                    raise DialogError('authority_acceptance_failed')
                task = asyncio.run(handlers['dialog_append'](task_id, report['confirmed_commit'],
                    'ipc_adoption_request', 'task', 'inert writer adoption handoff'))
                report = mt_call({'op':'canary_report','head':task['commit'],
                    'message_id':'ipc_adoption_report','body':'inert writer adoption report'})
                status = mt_call({'op':'writer_status','message_id':'ipc_adoption_report'})
                if not status.get('attempted') or status.get('confirmed_commit') != report['commit']:
                    raise DialogError('authority_acceptance_failed')
                result = asyncio.run(handlers['dialog_view'](task_id))
                if result['total_messages'] != 8 or result['waiting_for'] != 'chatgpt':
                    raise DialogError('authority_acceptance_failed')
        finally:
            try: server_pipe.send('stop')
            except (OSError, EOFError): pass
            for child in children:
                child.join(2)
                if child.is_alive(): child.terminate(); child.join(1)
            server_pipe.close()
        if any(child.is_alive() or child.exitcode != 0 for child in children):
            raise DialogError('authority_acceptance_shutdown_failed')
        if (directory/'cg.sock').exists() or (directory/'mt.sock').exists():
            raise DialogError('authority_acceptance_shutdown_failed')
    return {'status':'authority_acceptance_passed', 'real_unix_peer_credentials':True,
        'mcp_auth_before_ipc':True, 'both_actors_same_runtime':True, 'messages':8 if writers else 4,
        'mcp_sdk':sdk_version, 'git_network_used':False, 'live_services_changed':False,
        'temporary_state_only':True, 'owned_children_stopped':True, 'native_lifespan_host':native,
        'shared_worker_canary':writers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', required=True)
    parser.add_argument('--installed-sdk', action='store_true')
    parser.add_argument('--native-host', action='store_true')
    parser.add_argument('--writers', action='store_true')
    args = parser.parse_args(argv)
    try: result = run(sdk=args.installed_sdk, native=args.native_host, writers=args.writers)
    except Exception: result = {'error':'authority_acceptance_failed', 'retry_authorized':False}
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
