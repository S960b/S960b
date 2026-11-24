"""Explicit actor-bound SQL client; not installed in running services in Stage 33C.

No actor, sender or recipient argument is accepted. Real DB role and SQL function
ACL must agree. Both actors use separate Stage 33B credentials. No dynamic SQL.
"""
from __future__ import annotations
import os, re, stat
from pathlib import Path
from typing import Any

CFG = Path('/home/kali/.config/bridge-pg-v2/actor-credentials')
ROLES = ('bridge_chatgpt', 'bridge_agent')
HOST = '127.0.0.1'
PORT = 15433

class AdapterError(Exception):
    pass

def _password_for(role: str) -> str:
    if role not in ROLES:
        raise AdapterError('invalid_actor')
    if CFG.is_symlink() or not CFG.is_dir() or CFG.stat().st_uid != os.geteuid() or stat.S_IMODE(CFG.stat().st_mode) != 0o700:
        raise AdapterError('unsafe_actor_credential_directory')
    path = CFG / (role + '.pgpass')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > 256:
                raise AdapterError('unsafe_actor_password_file')
            raw = os.read(fd, 257).decode('ascii')
        finally:
            os.close(fd)
    except (OSError, UnicodeError) as e:
        raise AdapterError('actor_password_file_unavailable') from None
    fields = raw.rstrip('\n').split(':')
    if len(fields) != 5 or fields[:4] != ['127.0.0.1','15432','bridge',role] or not re.fullmatch(r'[A-Za-z0-9_-]{64}', fields[-1]):
        raise AdapterError('actor_password_file_invalid')
    return fields[-1]

class _Base:
    __slots__ = ('_role',)
    def __init__(self, role: str):
        if role not in ROLES:
            raise AdapterError('invalid_actor')
        self._role = role
    def _connect(self):
        if any(os.environ.get(k) for k in ('PGPASSWORD','PGSERVICE','PGOPTIONS')):
            raise AdapterError('inherited_libpq_environment_denied')
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(host=HOST,port=PORT,dbname='bridge',user=self._role,
            password=_password_for(self._role),connect_timeout=6,
            application_name='bridge_pg_v2_actor',row_factory=dict_row)
    def _one(self, sql: str, args: tuple[Any, ...] = ()):  # ONLY static SQL from methods
        try:
            with self._connect() as cn, cn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='10000ms'")
                cur.execute('SELECT session_user AS session, current_user AS current')
                identity = cur.fetchone()
                if identity != {'session': self._role, 'current': self._role}:
                    raise AdapterError('actor_identity_mismatch')
                cur.execute(sql, args)
                d=cur.fetchone()
                return d['result'] if d else None
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError('actor_database_unavailable') from None
    def health(self) -> dict:
        return self._one('SELECT bridge.health() AS result')
    def list_dialogs(self, offset: int = 0, limit: int = 20):
        return self._one('SELECT bridge.list_dialogs(%s,%s) AS result',(offset,limit))
    def view_dialog(self, task_id: str, offset: int=0, limit: int=20):
        return self._one('SELECT bridge.view_dialog(%s,%s,%s) AS result',(task_id,offset,limit))
    def dialog_metadata(self, task_id: str):
        return self._one('SELECT bridge.dialog_metadata(%s) AS result',(task_id,))
    def delivery_status(self, task_id: str, seq: int):
        return self._one('SELECT bridge.delivery_status(%s,%s) AS result',(task_id,seq))
    def send_status(self, task_id: str, message_id: str):
        return self._one('SELECT bridge.send_status(%s,%s) AS result',(task_id,message_id))

class ChatGPTActorDB(_Base):
    def __init__(self): super().__init__('bridge_chatgpt')
    def create_dialog(self, request_id: str, title: str):
        return self._one('SELECT bridge.cg_create_dialog(%s,%s) AS result',(request_id,title))
    def send_message(self, task_id: str, message_id: str, kind: str, body: str, expected_version: int | None = None):
        return self._one('SELECT bridge.cg_send_message(%s,%s,%s,%s,%s) AS result',(task_id,message_id,kind,body,expected_version))
    def return_turn(self, task_id: str, message_id: str, expected_version: int | None = None):
        return self._one('SELECT bridge.cg_return_turn(%s,%s,%s) AS result',(task_id,message_id,expected_version))
    def tombstone(self, task_id: str, expected_version: int):
        return self._one('SELECT bridge.cg_tombstone_dialog(%s,%s) AS result',(task_id,expected_version))
    def creation_status(self, request_id: str):
        return self._one('SELECT bridge.creation_status(%s) AS result',(request_id,))

class AgentActorDB(_Base):
    def __init__(self): super().__init__('bridge_agent')
    def send_report(self, task_id: str, message_id: str, kind: str, body: str, expected_version: int | None = None):
        return self._one('SELECT bridge.ag_send_message(%s,%s,%s,%s,%s) AS result',(task_id,message_id,kind,body,expected_version))
    def claim_message(self, task_id: str, seq: int, message_id: str, consumer: str, lease_seconds: int = 3600):
        return self._one('SELECT bridge.ag_claim_message(%s,%s,%s,%s,%s) AS result',(task_id,seq,message_id,consumer,lease_seconds))
    def claim_outbox(self, consumer: str, limit: int=10, lease_seconds: int=60):
        # Agent claims carry body and lease-token; no analogous public ChatGPT method.
        import psycopg
        try:
            with self._connect() as cn, cn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='10000ms'")
                cur.execute('SELECT session_user AS session, current_user AS current')
                if cur.fetchone() != {'session':self._role,'current':self._role}:
                    raise AdapterError('actor_identity_mismatch')
                cur.execute('SELECT * FROM bridge.ag_claim_outbox(%s,%s,%s)',(consumer,limit,lease_seconds))
                return list(cur.fetchall())
        except AdapterError:
            raise
        except Exception:
            raise AdapterError('actor_database_unavailable') from None
    def ack_outbox(self, delivery_id: int, lease_token: str, consumer: str):
        return self._one('SELECT bridge.ag_ack_outbox(%s,%s::uuid,%s) AS result',(delivery_id,lease_token,consumer))
    def release_outbox(self, delivery_id: int, lease_token: str, consumer: str, retry_seconds: int=10, error: str|None =None):
        return self._one('SELECT bridge.ag_release_outbox(%s,%s::uuid,%s,%s,%s) AS result',(delivery_id,lease_token,consumer,retry_seconds,error))
