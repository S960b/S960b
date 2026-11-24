#!/usr/bin/env python3
"""Stage 32: bounded, secret-free, read-only security audit of existing PG bridge.

No DB mutations, no secrets, no message bodies. Does not create roles or users.
Run from Kali user with the existing libpq configuration and authorized SSH tunnel.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CANARY_ID = 'task_f2c64cfdf7894537b35a19a6def977c6'
BRIDGE_ROOT = Path('/home/kali/bridge-pg-v2')
CANARY_ROOT = Path('/home/kali/hermes-pg-tunnel-stage31')
EXPECTED_USER = 'kali'

SQL = {
    'context': '''SELECT current_database() AS db, current_user AS login_role,
                 pg_get_userbyid(datdba) AS database_owner
                 FROM pg_database WHERE datname=current_database()''',
    'roles': '''SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
                FROM pg_roles WHERE rolname IN ('bridge_app','bridge_owner','bridge_chatgpt','bridge_agent')
                ORDER BY rolname''',
    'privileges': '''SELECT has_database_privilege(current_user,current_database(),'CREATE') AS db_create,
                          has_schema_privilege(current_user,'bridge','CREATE') AS schema_create,
                          has_table_privilege(current_user,'bridge.messages','SELECT') AS table_select,
                          has_table_privilege(current_user,'bridge.messages','INSERT') AS table_insert,
                          has_function_privilege(current_user,'bridge.send_message(text,text,text,text,text,bigint)','EXECUTE') AS fn_send_exec,
                          has_function_privilege(current_user,'bridge.claim_message(text,bigint,text,text,integer)','EXECUTE') AS fn_claim_exec,
                          has_function_privilege(current_user,'bridge.view_dialog(text,integer,integer)','EXECUTE') AS fn_view_exec''',
    'functions': '''SELECT p.proname AS name, pg_get_function_identity_arguments(p.oid) AS signature,
                          pg_get_userbyid(p.proowner) AS owner,
                          p.prosecdef AS security_definer,
                          has_function_privilege(current_user,p.oid,'EXECUTE') AS app_execute,
                          EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                                  WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute
                   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='bridge' ORDER BY p.proname''',
    # Details deliberately aggregate, not message bodies.
    'canary': '''SELECT (SELECT count(*) FROM bridge.messages WHERE task_id=%s) AS message_count,
                       (SELECT max(seq) FROM bridge.messages WHERE task_id=%s) AS last_seq,
                       (SELECT count(*) FROM bridge.outbox WHERE task_id=%s AND status='delivered') AS delivered_count''',
    'role_function': '''SELECT pg_get_functiondef('bridge.send_message(text,text,text,text,text,bigint)'::regprocedure) AS definition''',
}


def validate_query(s: str) -> None:
    clean = re.sub(r'--[^\n]*', '', s).strip().upper()
    no_literals = re.sub(r"'(?:''|[^'])*'", "''", clean)
    if not clean.startswith('SELECT '):
        raise RuntimeError('non_select_query_rejected')
    if ';' in no_literals or re.search(r'\b(INSERT|UPDATE|DELETE|GRANT|REVOKE|ALTER|CREATE|DROP|CALL|COPY|DO|TRUNCATE)\b', no_literals):
        raise RuntimeError('unsafe_query_rejected')


def sha_small(path: Path) -> dict:
    if path.is_symlink():
        return {'exists': True, 'regular': False, 'reason': 'symlink'}
    if not path.is_file():
        return {'exists': False}
    stat = path.stat()
    if stat.st_size > 1024 * 1024:
        return {'exists': True, 'regular': True, 'bytes': stat.st_size, 'sha256_prefix': None}
    return {'exists': True, 'regular': True, 'bytes': stat.st_size,
            'sha256_prefix': hashlib.sha256(path.read_bytes()).hexdigest()[:16]}


def status(name: str) -> str:
    p = subprocess.run(['systemctl', '--user', 'is-active', name], capture_output=True, text=True, timeout=5)
    v = p.stdout.strip()
    return v if v in {'active', 'inactive', 'failed', 'activating', 'deactivating'} else 'unknown'


def read_all():
    if os.geteuid() == 0 or os.environ.get('USER') != EXPECTED_USER:
        raise RuntimeError('must_run_as_unprivileged_kali')
    if 'APPIMAGE' in os.environ or 'APPDIR' in os.environ:
        raise RuntimeError('clear_AppImage_env_before_running')
    sys.path.insert(0, str(BRIDGE_ROOT))
    from bridge_pg.store import Store
    conn = Store()._connect()
    result = {}
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute("SET LOCAL statement_timeout = '5000ms'")
                for key in ('context', 'roles', 'privileges', 'functions'):
                    validate_query(SQL[key])
                    cur.execute(SQL[key])
                    rows = cur.fetchall()
                    result[key] = rows if key in ('roles', 'functions') else (rows[0] if rows else {})
                validate_query(SQL['canary'])
                cur.execute(SQL['canary'], (CANARY_ID, CANARY_ID, CANARY_ID))
                result['canary'] = cur.fetchone()
                validate_query(SQL['role_function'])
                cur.execute(SQL['role_function'])
                body = cur.fetchone()['definition']
                result['sender_argument_present'] = 'p_sender' in body
                result['db_identity_sender_guard_found'] = bool(re.search(r'\b(CURRENT_USER|SESSION_USER)\b', body, flags=re.I))
    finally:
        conn.close()
    return result


def compact(r):
    roles = {v['rolname']: {'login':v['rolcanlogin'], 'superuser':v['rolsuper'],
                            'createdb':v['rolcreatedb'], 'createrole':v['rolcreaterole'],
                            'bypassrls':v['rolbypassrls']} for v in r['roles']}
    functions = r['functions']
    return {
        'stage': '32_readonly',
        'db': 'bridge' if r['context']['db'] == 'bridge' else 'unexpected',
        'database_owner': r['context']['database_owner'],
        'connection_role': r['context']['login_role'],
        'roles': roles,
        'privileges': r['privileges'],
        'function_count': len(functions),
        'function_execute_count': sum(1 for f in functions if f['app_execute']),
        'public_exec_count': sum(1 for f in functions if f['public_execute']),
        'security_definer_count': sum(1 for f in functions if f['security_definer']),
        'function_names': sorted(set(f['name'] for f in functions)),
        'sender_argument_present': r['sender_argument_present'],
        'db_identity_sender_guard_found': r['db_identity_sender_guard_found'],
        'canary': r['canary'],
        'services': {name: status(name) for name in (
            'hermes-pg-tunnel-one-shot.service','hermes-pg-one-shot-canary.service',
            'bridge-pg-v2-mcp.service','bridge-pg-v2-taskboard.service')},
        'files': {
            'store':sha_small(BRIDGE_ROOT/'bridge_pg/store.py'),
            'compat':sha_small(BRIDGE_ROOT/'bridge_pg/compat.py'),
            'mcp':sha_small(BRIDGE_ROOT/'bridge_pg/mcp_http_v2.py'),
            'one_shot':sha_small(CANARY_ROOT/'canary_server.py'),
        },
        'policy_finding': 'shared_bridge_app_can_claim_both_sender_roles' if (
            r['context']['login_role'] == 'bridge_app' and
            r['sender_argument_present'] and not r['db_identity_sender_guard_found']) else 'manual_review_required',
    }


def main():
    try:
        x = compact(read_all())
        print(json.dumps(x, ensure_ascii=False, sort_keys=True, default=str))
        if x['db'] != 'bridge' or x['function_count'] != 16:
            raise RuntimeError('unexpected_schema_shape')
        print('STAGE32_READ_ONLY_SECURITY_INVENTORY_OK')
        print('STAGE32_NO_DATABASE_WRITES_PERFORMED')
    except Exception as exc:
        print('STAGE32_STOP_' + type(exc).__name__, file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
