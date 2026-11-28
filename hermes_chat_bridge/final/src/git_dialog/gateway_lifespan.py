"""Native lifespan-owned gateway; no credential discovery or bootstrap grants.

The trusted host supplies a dialog factory. It runs on the SAME dedicated
thread as every SQLite connection, Unix request and cleanup. Missing credential
binding is a startup error, never an empty/fake/unauthenticated Git fallback.
"""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sqlite3
import sys
import threading
from concurrent.futures import Future

from . import DialogError, validate_task_id
from .append_store import APPLICATION_ID as APPEND_ID, AppendStore
from .authority import CommonAuthority
from .lifecycle import LifecycleRegistry
from .preflight import TASK_ID
from .unix_authority import STATE, Runtime, UnixServer, _directory, _file

REGISTRY = STATE / 'lifecycle.p21.sqlite'
APPENDS = STATE / 'authority-appends.p21.sqlite'
WORKER_JOURNAL = STATE / 'authority-worker.p23.sqlite'
EVENT_JOURNAL = STATE / 'authority-events.p26.sqlite'
MULTI_EVENT_JOURNAL = STATE / 'authority-events.p31.sqlite'
DIRECT_EVENT_JOURNAL = STATE / 'authority-events.p36.sqlite'
CREATIONS = STATE / 'authority-creations.p30.sqlite'

# Only fixed, operator-actionable socket startup errors are exposed. Generic
# errors retain the pre-existing sanitization (no exception body or traceback).
_SOCKET_START_CODES = frozenset({
    'authority_socket_exists_or_rejected',
    'authority_socket_busy',
    'authority_socket_probe_unconfirmed',
    'authority_start_lock_rejected',
    'authority_start_locked',
    'authority_socket_binding_rejected',
})


def open_worker_journal(path):
    """Lazy private provision; never adopt an empty/foreign/legacy journal."""
    from .journal import APPLICATION_ID, ExecutionJournal
    path = Path(path)
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        _file(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute('PRAGMA query_only=ON')
            columns = {r[1] for r in conn.execute('PRAGMA table_info(executions)')}
            if (conn.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or
                    'publication_attempted' not in columns):
                raise DialogError('authority_worker_journal_rejected')
        finally:
            conn.close()
    journal = ExecutionJournal(path, actor='mt')
    try:
        for item, flags in ((path, os.O_RDONLY), (path.parent, os.O_RDONLY | os.O_DIRECTORY)):
            fd = os.open(item, flags | os.O_NOFOLLOW)
            try: os.fsync(fd)
            finally: os.close(fd)
        return journal
    except BaseException:
        journal.close()
        raise


def open_append_store(path):
    """Explicit startup provision, or reopen only an existing valid store.

    Existing empty/foreign DBs are NOT adopted. New artifacts survive failures;
    no overwrite, chmod of existing inode, delete/reset or automatic recovery.
    """
    path = Path(path)
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        _file(path)
        # Serialized local owner, metadata guarded above; no URI tricks that
        # escape the project's temporary-SQLite test boundary.
        conn = sqlite3.connect(path)
        try:
            conn.execute('PRAGMA query_only=ON')
            if conn.execute('PRAGMA application_id').fetchone()[0] != APPEND_ID:
                raise DialogError('authority_append_store_rejected')
        finally:
            conn.close()
    store = AppendStore(path)
    try:
        # Schema validation is in AppendStore; make creation durable before
        # exposing either endpoint. This fsync never reads credential content.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
        return store
    except BaseException:
        store.close()
        raise


class NativeGatewayHost:
    """Start once, own two endpoints, stop without cross-thread SQLite use.

    dialog_factory is trusted startup configuration, not a request/body/CLI
    choice. It must use a separately reviewed normal credential loader. This
    class never reads env/config/secret files or provisions credentials itself.
    """
    def __init__(self, dialog_factory, *, registry=REGISTRY, append_store=APPENDS,
                 task_id=TASK_ID, paths=None, bridge_pid=None, worker_journal=WORKER_JOURNAL,
                 creation_store=None):
        if not callable(dialog_factory):
            raise DialogError('authority_git_binding_required')
        self.factory, self.registry_path, self.append_path = dialog_factory, Path(registry), Path(append_store)
        if self.registry_path == self.append_path:
            raise DialogError('authority_store_alias_rejected')
        # Optional bindings are trusted embedding/isolated-harness arguments,
        # never tool/body inputs. Native production uses these fixed defaults.
        validate_task_id(task_id)
        self.task_id, self.paths = task_id, paths
        self.worker_journal_path = Path(worker_journal)
        if self.worker_journal_path in (self.registry_path, self.append_path):
            raise DialogError('authority_store_alias_rejected')
        self.creation_path = (self.registry_path.parent / CREATIONS.name
                              if creation_store is None else Path(creation_store))
        if self.creation_path in (self.registry_path, self.append_path, self.worker_journal_path):
            raise DialogError('authority_store_alias_rejected')
        self.bridge_pid = os.getpid() if bridge_pid is None else bridge_pid
        if type(self.bridge_pid) is not int or self.bridge_pid <= 0:
            raise DialogError('authority_runtime_binding_rejected')
        self.stop_event, self.started, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.thread, self.failure, self.ready = None, None, False
        self.notification_delivery = None
        self.multi_notification_delivery = None
        self.notification_owner = None
        self.notification_request = None
        self.notification_lock = threading.Lock()

    def configure_notifications(self, delivery, *, path=EVENT_JOURNAL,
                                checkpoint_head=None, checkpoint_seq=None,
                                multi_delivery=None, multi_path=None):
        from .git_events import CHECKPOINT_HEAD, CHECKPOINT_SEQ
        if self.thread is not None or self.notification_delivery is not None:
            raise DialogError('git_event_already_bound')
        self.notification_delivery, self.notification_path = delivery, Path(path)
        self.multi_notification_delivery = multi_delivery
        self.multi_notification_path = (self.notification_path.parent / MULTI_EVENT_JOURNAL.name
                                        if multi_path is None else Path(multi_path))
        self.direct_notification_path = self.multi_notification_path.parent / DIRECT_EVENT_JOURNAL.name
        if self.multi_notification_delivery is not None and self.multi_notification_path in (
                self.notification_path, self.registry_path, self.append_path,
                self.worker_journal_path, self.creation_path):
            raise DialogError('authority_store_alias_rejected')
        if self.notification_path in (self.registry_path, self.append_path, self.worker_journal_path, self.creation_path):
            raise DialogError('authority_store_alias_rejected')
        if self.direct_notification_path in (
                self.registry_path, self.append_path, self.worker_journal_path,
                self.creation_path, self.notification_path, self.multi_notification_path):
            raise DialogError('authority_store_alias_rejected')
        self.notification_checkpoint = (CHECKPOINT_HEAD if checkpoint_head is None else checkpoint_head,
                                       CHECKPOINT_SEQ if checkpoint_seq is None else checkpoint_seq)

    def request_notification_owner(self, principal):
        """Called only after the native OAuth guard; no tool-selected principal.

        Completion means durably bound on the SQLite-owner thread, BEFORE the
        handler dispatches its Git request. No SQLite access on the ASGI loop.
        """
        from .git_events import owner_digest
        digest = owner_digest(principal)
        with self.notification_lock:
            if not self.ready or self.notification_delivery is None:
                raise DialogError('git_event_binding_unavailable')
            if self.notification_owner not in (None, digest):
                raise DialogError('git_event_owner_mismatch')
            if self.notification_request is not None:
                saved, future = self.notification_request
                if saved != digest: raise DialogError('git_event_owner_mismatch')
                return future
            future = Future()
            self.notification_request = digest, future
            return future

    def _run(self):
        registry = store = server = runtime = journal = pump = creations = None
        multi_journal = multi_pump = None
        direct_journal = direct_pump = None
        try:
            _file(self.registry_path)
            registry = LifecycleRegistry(self.registry_path, existing=True)
            # The legacy pilot is a durable *historical identity*, not a
            # requirement that a user-owned dialogue stay active forever.
            # Its registry tombstone must survive deletion, and existing P26
            # / P31 journal checkpoints must keep referencing that original ID.
            legacy = registry.get(self.task_id)
            if (legacy['state'] != 'active' and self.notification_delivery is not None and
                    not (self.notification_path.exists() or self.notification_path.is_symlink())):
                # Never mint a new P26 checkpoint for a deleted remote pilot.
                raise DialogError('git_event_activation_checkpoint_changed')
            # No append-store creation if credential/remote binding fails.
            if self.stop_event.is_set():
                raise DialogError('authority_start_cancelled')
            dialog = self.factory()
            if self.stop_event.is_set():
                raise DialogError('authority_start_cancelled')
            if legacy['state'] == 'active':
                snapshot = dialog.load(self.task_id)
            else:
                # Re-verify the private repository without reopening a
                # missing/deleted legacy branch. No stale Git snapshot is used.
                dialog._private()
                snapshot = None
            if self.stop_event.is_set():
                raise DialogError('authority_start_cancelled')
            store = open_append_store(self.append_path)
            from .dialog_catalog import CreationStore, DialogCatalog
            _directory(self.creation_path.parent)
            creations = CreationStore(self.creation_path)
            catalog = DialogCatalog(dialog, registry, store, creations)
            if self.notification_delivery is not None:
                from .git_events import EventJournal, NotificationPump
                _directory(self.notification_path.parent)
                head, seq = self.notification_checkpoint
                # First activation is pinned to the reviewed existing head.
                # Reopening later retains its immutable checkpoint/owner/outbox.
                if not (self.notification_path.exists() or self.notification_path.is_symlink()):
                    # A retired pilot cannot seed a *new* immutable journal:
                    # its original HEAD is no longer independently observable.
                    if snapshot is None or snapshot.head != head or len(snapshot.messages) != seq:
                        raise DialogError('git_event_activation_checkpoint_changed')
                journal = EventJournal(self.notification_path, self.task_id, head, seq)
                self.notification_owner = journal.owner_hash
                pump = NotificationPump(journal, store, dialog, self.notification_delivery)
                if self.multi_notification_delivery is not None:
                    from .multi_git_events import MultiEventJournal, MultiDialogNotificationPump
                    _directory(self.multi_notification_path.parent)
                    # Existing journal reopens its immutable cursor. First
                    # activation checkpoints old appends, preventing backfill.
                    multi_journal = MultiEventJournal(self.multi_notification_path,
                        self.task_id, store.notification_checkpoint())
                    if (journal.owner_hash is not None and multi_journal.owner_hash not in
                            (None, journal.owner_hash)):
                        raise DialogError('git_event_owner_mismatch')
                    if (multi_journal.owner_hash is not None and journal.owner_hash not in
                            (None, multi_journal.owner_hash)):
                        raise DialogError('git_event_owner_mismatch')
                    # P36: Git is authoritative. Do NOT use append_store as
                    # the event source for multi-dialogue metadata.
                    from .direct_events import DirectGitJournal, DirectGitNotificationPump
                    _directory(self.direct_notification_path.parent)
                    direct_journal = DirectGitJournal(self.direct_notification_path, registry, dialog,
                                                      exclude_task_id=self.task_id)
                    if (journal.owner_hash is not None and
                            direct_journal.owner_hash not in (None, journal.owner_hash)):
                        raise DialogError('git_event_owner_mismatch')
                    direct_pump = DirectGitNotificationPump(
                        direct_journal, dialog, registry, self.multi_notification_delivery,
                        exclude_task_id=self.task_id)
            from .local_wake import notify_plugin
            def on_verified_notice(receipt, writer):
                # No Git publication in this callback: receipt is already
                # verified by DialogCatalog.notice. Only metadata is forwarded.
                if direct_pump is not None:
                    direct_pump.next_scan = 0
                if writer == "chatgpt":
                    notify_plugin(receipt)  # best-effort; plugin polls as fallback

            runtime = Runtime(CommonAuthority(dialog, registry, store, self.task_id),
                              bridge_pid=self.bridge_pid,
                              journal_factory=lambda: open_worker_journal(self.worker_journal_path),
                              catalog=catalog, notice_callback=on_verified_notice)
            server = UnixServer(runtime) if self.paths is None else UnixServer(runtime, self.paths)
            self.ready = True
            self.started.set()
            while not self.stop_event.is_set():
                server.serve_once(0.2)
                if pump is not None:
                    with self.notification_lock:
                        request = self.notification_request
                        if request is not None:
                            digest, future = request
                            # Validate BOTH durable bindings before changing
                            # either one. Never bind a foreign subscriber.
                            if multi_journal is not None and multi_journal.owner_hash not in (None, digest):
                                raise DialogError('git_event_owner_mismatch')
                            journal.bind(digest)
                            if multi_journal is not None:
                                multi_journal.bind(digest)
                            if direct_journal is not None:
                                direct_journal.bind(digest)
                            self.notification_owner = digest
                            if not future.done(): future.set_result(True)
                            self.notification_request = None
                    pump.tick()
                    if direct_pump is not None:
                        direct_pump.tick()
        except BaseException as exc:
            # Never log untrusted exception text, paths or credentials.
            # Preserve only strictly allowlisted fixed codes for startup.
            code = exc.code if isinstance(exc, DialogError) else None
            self.failure = (code if code in _SOCKET_START_CODES
                            else 'authority_host_failed')
            print('authority_start_failure code=' + self.failure,
                  file=sys.stderr, flush=True)
        finally:
            self.ready = False
            with self.notification_lock:
                if self.notification_request is not None:
                    future = self.notification_request[1]
                    if not future.done(): future.set_exception(DialogError('git_event_binding_unavailable'))
                    self.notification_request = None
            for resource in (direct_pump, multi_pump, pump, server, runtime,
                             direct_journal, multi_journal, journal, creations, store, registry):
                if resource is not None:
                    try: resource.close()
                    except BaseException: self.failure = 'authority_cleanup_failed'
            self.started.set()
            self.finished.set()

    def start(self, *, seconds=120):
        if self.thread is not None:
            raise DialogError('authority_host_already_started')
        self.thread = threading.Thread(target=self._run, name='native-shared-authority', daemon=True)
        self.thread.start()
        if not self.started.wait(seconds):
            self.stop_event.set()
            raise DialogError('authority_start_unconfirmed')
        if self.failure or not self.ready:
            raise DialogError(self.failure if self.failure in _SOCKET_START_CODES
                              else 'authority_host_failed')

    def stop(self, *, seconds=15):
        self.stop_event.set()
        if self.thread is None:
            return
        # No socket deletion or DB close on caller/ASGI thread. If a Git call
        # remains blocked, report failure; do not pretend cleanup succeeded.
        self.thread.join(seconds)
        if self.thread.is_alive():
            raise DialogError('authority_shutdown_unconfirmed')
        if self.failure:
            raise DialogError('authority_host_failed')


def install_lifespan(app, bridge, *, dialog_factory, notification_store=None):
    """Bind once AFTER the bridge's original lifespan has been installed.

    The explicit factory must come from reviewed host startup, never OAuth tool
    parameters or a global secret/grant obtained from the model. Merely calling
    register_mcp does not call this function or grant a credential binding.
    """
    if getattr(bridge, '_shared_gateway_host', None) is not None:
        raise DialogError('authority_lifespan_already_bound')
    host = NativeGatewayHost(dialog_factory)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        # Enter original first so its pilot lock/notification finally blocks
        # execute even if gateway startup fails before ASGI is declared ready.
        async with original(application) as state:
            try:
                if notification_store is not None:
                    from .git_events import NativeDelivery
                    from .multi_git_events import MultiDialogNativeDelivery
                    loop = asyncio.get_running_loop()
                    host.configure_notifications(
                        NativeDelivery(notification_store, loop, TASK_ID),
                        multi_delivery=MultiDialogNativeDelivery(notification_store, loop))
                await asyncio.to_thread(host.start)
                yield state
            finally:
                await asyncio.to_thread(host.stop)

    bridge._shared_gateway_host = host
    app.router.lifespan_context = lifespan
    return host
