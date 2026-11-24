#!/usr/bin/env python3
"""Provision two separate PostgreSQL actor logins, without switching clients.

No secrets printed, no code executed on the client from the DB.  Only the root
SSH authority applies role LOGIN + pre-hashed SCRAM verifiers in one transaction.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import os
import pwd
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys

CFG = Path('/home/kali/.config/bridge-pg-v2')
ROOT = CFG / 'actor-credentials'
ROLES = ('bridge_chatgpt', 'bridge_agent')
CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'

class SafeStop(Exception):
    pass


def stop(label: str):
    raise SafeStop(label)


def scram_verifier(password: str, salt: bytes, iterations: int = 4096) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]{64}', password):
        raise ValueError('invalid_password_format')
    if len(salt) != 16 or iterations < 4096:
        raise ValueError('invalid_scram_parameters')
    salted = hashlib.pbkdf2_hmac('sha256', password.encode('ascii'), salt, iterations)
    stored = hashlib.sha256(hmac.digest(salted, b'Client Key', 'sha256')).digest()
    server = hmac.digest(salted, b'Server Key', 'sha256')
    b64 = lambda b: base64.b64encode(b).decode('ascii')
    return f'SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored)}:{b64(server)}'


def config_path(role: str) -> Path:
    if role not in ROLES:
        raise ValueError('unknown_role')
    return ROOT / f'{role}.pgpass'


def check_root_dir() -> None:
    if os.getuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'kali' or os.environ.get('HOME') != '/home/kali':
        stop('WRONG_LOCAL_USER')
    if not CFG.is_dir() or CFG.is_symlink():
        stop('CONFIG_DIR_MISSING_OR_SYMLINK')
    if ROOT.exists() or ROOT.is_symlink():
        stop('CREDENTIAL_DIR_ALREADY_EXISTS_NO_OVERWRITE')
    # Config file parent may contain other v2 services; don't chmod it.
    if CFG.stat().st_uid != os.getuid():
        stop('CONFIG_DIR_UNEXPECTED_OWNER')


def ssh_psql(sql: str, *, timeout: int = 25) -> str:
    target = os.environ.get('PG_V2_SSH_TARGET','')
    if not target or '\n' in target or target.startswith('-'):
        stop('PRIVATE_SSH_TARGET_MISSING_OR_UNSAFE')
    argv = ['ssh', '-T', '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','ConnectTimeout=8','--',target,
            'runuser -u postgres -- psql -X -q -A -t -v ON_ERROR_STOP=1 -d bridge']
    try:
        proc = subprocess.run(argv, input=sql, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        stop('SSH_PSQL_UNAVAILABLE')
    if proc.returncode != 0:
        # Remote SQL errors can contain private host names / material; never print stderr.
        stop('SSH_PSQL_FAILED_STOP_NO_RETRY')
    return proc.stdout.strip()


PREFLIGHT = r'''\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='5000ms';
SELECT CASE WHEN current_database()='bridge'
 AND (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge')=25
 AND (SELECT count(*) FROM pg_roles WHERE rolname IN ('bridge_chatgpt','bridge_agent')
   AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolbypassrls AND NOT rolinherit)=2
 AND (SELECT count(*) FROM pg_roles WHERE rolname='bridge_owner' AND NOT rolcanlogin)=1
 AND NOT has_function_privilege('bridge_chatgpt','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE')
 AND NOT has_function_privilege('bridge_agent','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE')
 AND NOT has_function_privilege('bridge_chatgpt','bridge.ag_send_message(text,text,text,text,bigint)','EXECUTE')
 AND NOT has_function_privilege('bridge_agent','bridge.cg_send_message(text,text,text,text,bigint)','EXECUTE')
 AND has_function_privilege('bridge_chatgpt','bridge.cg_send_message(text,text,text,text,bigint)','EXECUTE')
 AND has_function_privilege('bridge_agent','bridge.ag_send_message(text,text,text,text,bigint)','EXECUTE')
 AND NOT has_table_privilege('bridge_chatgpt','bridge.messages','SELECT')
 AND NOT has_table_privilege('bridge_agent','bridge.messages','SELECT')
 AND has_function_privilege('bridge_app','bridge.send_message(text,text,text,text,text,bigint)','EXECUTE')
 THEN 'STAGE33B_SHADOW_PREFLIGHT_OK' ELSE 'STAGE33B_SHADOW_PREFLIGHT_FAILED' END;
COMMIT;
'''


def install_sql(verifiers: dict[str, str]) -> str:
    if set(verifiers) != set(ROLES) or not all(re.fullmatch(r'SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+', s) for s in verifiers.values()):
        raise ValueError('invalid_verifier')
    return '''\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='15000ms';
DO $verify$
BEGIN
 IF current_database() <> 'bridge'
 OR (SELECT count(*) FROM pg_roles WHERE rolname IN ('bridge_chatgpt','bridge_agent') AND NOT rolcanlogin) <> 2
 OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge') <> 25
 THEN RAISE EXCEPTION 'stage33b_precondition_changed'; END IF;
END $verify$;
ALTER ROLE bridge_chatgpt LOGIN PASSWORD '%s';
ALTER ROLE bridge_agent LOGIN PASSWORD '%s';
COMMIT;
SELECT 'STAGE33B_ACTOR_LOGIN_ROLES_ENABLED';
''' % (verifiers['bridge_chatgpt'], verifiers['bridge_agent'])


def mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        stop('CREDENTIAL_DIR_PERMISSIONS')


def write_pgpass(role: str, password: str) -> None:
    path = config_path(role)
    fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(f'127.0.0.1:15432:bridge:{role}:{password}\n')
            f.flush()
            os.fsync(f.fileno())
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            stop('PGPASS_PERMISSIONS')
    except BaseException:
        raise


def verify_roles() -> None:
    # Only now import Kali's installed psycopg3, not from a random pip environment.
    try:
        import psycopg
    except ImportError:
        stop('PSYCOPG_NOT_INSTALLED_USE_BRIDGE_VENV')
    # Never let an inherited legacy password override the explicit passfile.
    os.environ.pop('PGPASSWORD', None)
    expected = {'bridge_chatgpt':'cg_send_message', 'bridge_agent':'ag_send_message'}
    denied = {'bridge_chatgpt':'ag_send_message', 'bridge_agent':'cg_send_message'}
    for role in ROLES:
        pgpass = config_path(role)
        if not pgpass.is_file() or pgpass.is_symlink() or stat.S_IMODE(pgpass.stat().st_mode) != 0o600:
            stop('UNSAFE_CREDENTIAL_FILE')
        # Use libpq passfile path, never embed password in DSN or process args.
        try:
            with psycopg.connect(host='127.0.0.1',port=15432,dbname='bridge',user=role,
                                 passfile=str(pgpass),connect_timeout=6,autocommit=True,
                                 application_name='pgbridge-stage33b-verify') as conn:
                with conn.cursor() as cur:
                    cur.execute('SET default_transaction_read_only = on')
                    cur.execute('SELECT session_user, current_user')
                    if tuple(cur.fetchone()) != (role,role):
                        stop('SESSION_IDENTITY_MISMATCH')
                    cur.execute('SELECT bridge.health()')
                    if cur.fetchone()[0].get('ok') is not True:
                        stop('ACTOR_HEALTH_FAILED')
                    cur.execute('SELECT has_function_privilege(current_user,%s,\'EXECUTE\'),has_function_privilege(current_user,%s,\'EXECUTE\')',
                                (f'bridge.{expected[role]}(text,text,text,text,bigint)', f'bridge.{denied[role]}(text,text,text,text,bigint)'))
                    if tuple(cur.fetchone()) != (True,False):
                        stop('ACTOR_WRAPPER_PERMISSIONS_WRONG')
                    cur.execute('SELECT has_table_privilege(current_user,\'bridge.messages\',\'SELECT\'),has_schema_privilege(current_user,\'bridge\',\'CREATE\')')
                    if tuple(cur.fetchone()) != (False,False):
                        stop('ACTOR_TABLE_OR_SCHEMA_PERMISSION_LEAK')
                    cur.execute('SELECT has_function_privilege(current_user,\'bridge.send_message(text,text,text,text,text,bigint)\',\'EXECUTE\')')
                    if cur.fetchone()[0] is not False:
                        stop('RAW_SEND_EXECUTE_LEAK')
                    # One negative call per autocommit connection: the only allowed
                    # failure is an authentication/permission-denied 42501.
                    try:
                        cur.execute('SELECT bridge.'+denied[role]+'(%s,%s,%s,%s,%s)',
                                    (CANARY,'stage33b-not-a-message','task','DO_NOT_DELIVER',None))
                    except psycopg.Error as e:
                        if e.sqlstate != '42501':
                            stop('UNEXPECTED_DENIAL_SQLSTATE')
                    else:
                        stop('FORBIDDEN_FUNCTION_EXECUTED')
                    try:
                        cur.execute('SET ROLE '+denied_role(role))
                    except psycopg.Error as e:
                        if e.sqlstate != '42501':
                            stop('SET_ROLE_UNEXPECTED_SQLSTATE')
                    else:
                        stop('CROSS_ACTOR_SET_ROLE_ALLOWED')
        except SafeStop:
            raise
        except psycopg.Error:
            stop('ACTOR_LOGIN_OR_QUERY_FAILED')
    print('STAGE33B_REAL_LOGIN_CHATGPT_AND_AGENT_OK')
    print('STAGE33B_CROSS_ACTOR_FUNCTION_AND_SET_ROLE_DENIED')
    print('STAGE33B_NO_DIRECT_TABLE_ACCESS')
    print('STAGE33B_READ_ONLY_VERIFICATION_COMPLETE')


def denied_role(role: str) -> str:
    return 'bridge_agent' if role == 'bridge_chatgpt' else 'bridge_chatgpt'


def preflight_role_state(expected_login: bool) -> bool:
    sql = """\\set ON_ERROR_STOP on
BEGIN READ ONLY;
SELECT CASE WHEN (SELECT count(*) FROM pg_roles WHERE rolname IN ('bridge_chatgpt','bridge_agent')
 AND rolcanlogin={login} AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolbypassrls AND NOT rolinherit) = 2
 AND (SELECT count(*) FROM pg_roles WHERE rolname='bridge_app' AND rolcanlogin)=1
 AND (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bridge')=25
 THEN 'STAGE33B_REMOTE_ROLE_STATE_OK' ELSE 'STAGE33B_REMOTE_ROLE_STATE_MISMATCH' END;
COMMIT;
""".format(login='true' if expected_login else 'false')
    return 'STAGE33B_REMOTE_ROLE_STATE_OK' in ssh_psql(sql)


def provision() -> None:
    check_root_dir()
    if not preflight_role_state(False):
        stop('PRE_PROVISION_ROLES_UNEXPECTED')
    print('STAGE33B_SHADOW_PREFLIGHT_OK')
    remote = ssh_psql(PREFLIGHT)
    if 'STAGE33B_SHADOW_PREFLIGHT_OK' not in remote:
        stop('SHADOW_PRIVILEGE_PREFLIGHT_UNEXPECTED')
    passwds = {role: secrets.token_urlsafe(48) for role in ROLES}
    verifiers = {role: scram_verifier(passwds[role],os.urandom(16)) for role in ROLES}
    mkdir_private(ROOT)
    for role in ROLES:
        write_pgpass(role,passwds[role])
    # No password is sent to DB, only SCRAM verifiers. No SQL is printed.
    ddl_result = ssh_psql(install_sql(verifiers),timeout=30)
    if 'STAGE33B_ACTOR_LOGIN_ROLES_ENABLED' not in ddl_result:
        stop('DDL_RESULT_UNEXPECTED')
    print('STAGE33B_SEPARATE_SCRAM_LOGINS_CREATED')
    if not preflight_role_state(True):
        stop('POST_PROVISION_ROLES_UNEXPECTED')
    verify_roles()
    print('STAGE33B_CREDENTIAL_FILES_600_PRIVATE')
    print('STAGE33B_LEGACY_CLIENTS_NOT_SWITCHED')
    print('STAGE33B_READY_FOR_CLIENT_ADAPTER_DESIGN')


def verify_only() -> None:
    if not ROOT.is_dir() or ROOT.is_symlink() or stat.S_IMODE(ROOT.stat().st_mode) != 0o700:
        stop('CREDENTIAL_DIRECTORY_MISSING_OR_UNSAFE')
    if not preflight_role_state(True):
        stop('ROLE_LOGIN_STATE_UNEXPECTED')
    verify_roles()
    print('STAGE33B_VERIFY_ONLY_SUCCESS')


def main() -> int:
    try:
        if len(sys.argv)!=2 or sys.argv[1] not in ('provision','verify'):
            stop('USAGE_PROVISION_OR_VERIFY')
        if os.getuid() == 0 or pwd.getpwuid(os.getuid()).pw_name != 'kali' or os.environ.get('HOME') != '/home/kali':
            stop('WRONG_LOCAL_USER')
        if sys.argv[1]=='provision': provision()
        else: verify_only()
    except SafeStop as exc:
        print(f'STAGE33B_STOP_{exc}',file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print('STAGE33B_STOP_LOCAL_SAFE_FAILURE',file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
