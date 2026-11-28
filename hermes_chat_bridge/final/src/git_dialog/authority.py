"""One composition root for cooperating writers; not deployed authentication.

The names below are trusted host wiring, NEVER body-selected identities. Live
connector/canary adoption must be separately verified; this Python composition
cannot revoke direct GitHub credentials or prove external writers are fenced.
"""
from . import DialogError
from .append_store import AppendStore
from .gateway import WriteGateway
from .worker import WorkerStep

WRITERS = {'chatgpt': 'chatgpt', 'mt': 'mt', 'worker': 'mt',
           'canary_chatgpt': 'chatgpt', 'canary_mt': 'mt'}


class CommonAuthority:
    def __init__(self, dialog, registry, append_store, task_id):
        if type(append_store) is not AppendStore:
            raise DialogError('shared_append_store_required')
        self.gateway = WriteGateway(dialog, registry, task_id, actors=('chatgpt', 'mt'),
                                    append_store=append_store)
        self.registry, self.store, self.task_id = registry, append_store, task_id

    def port(self, writer):
        if not isinstance(writer, str) or writer not in WRITERS:
            raise DialogError('writer_binding_denied')
        return self.gateway.port(WRITERS[writer])

    def worker(self, journal, *, allowed_ops=()):
        return WorkerStep(journal, self.port('worker'), self.registry, self.task_id,
                          allowed_ops=allowed_ops, require_shared=True)

    def dispatcher(self, confirmation, access, *, origin, mutations=False):
        from .ingress import Dispatcher
        if confirmation is not None:
            coordinator = confirmation.coordinator
            if (coordinator.registry is not self.registry or
                    coordinator.dialog is not self.gateway._dialog.dialog):
                raise DialogError('shared_confirmation_binding_required')
        return Dispatcher(self.gateway, confirmation, access, origin=origin, mutations=mutations)

    def capabilities(self):
        return {'task_id': self.task_id, 'writers': dict(WRITERS),
                'shared_lifecycle': True, 'durable_append_grants': True,
                'remote_adapter_integrated': False, 'external_writers_fenced': False,
                'delete_enabled': False}

    def application(self, confirmation, access, *, origin, mutations=False):
        """Construct on the serving SQLite-owner thread; no listener/login."""
        from .gateway_host import GatewayApplication
        return GatewayApplication(self.dispatcher(confirmation, access,
            origin=origin, mutations=mutations))
