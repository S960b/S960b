#!/usr/bin/env python3
"""Stage 40: SHA-pinned, no-schema-change taskboard refresh optimization.

No network or service operations here. The wrapper handles the service restart.
"""
from __future__ import annotations
import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path('/home/kali/bridge-pg-v2')
STATE = Path('/home/kali/.local/state/bridge-pg-v2/stage40')
FILES = {
    'bridge_pg/taskboard_v2.py': '5f99e801cab427ef01e8cd8c3cdd444efe28a536d83905fb3da176cf3166f0d5',
    'bridge_pg/store.py': '3a690b4cdcfba1e22cd92e03288c9074cc0aa92f02f3a7300c5bae8f3889e320',
    'bridge_pg/static/taskboard.js': '9122842988e13e82372de80051e03107a6b8d68f0ab795486e8a96f1d0360902',
}
OTHER_PINS = {
    'bridge_pg/compat.py': '16fd51f3be10f93a3c7a3358b8cef19535aa0b36a9f1e2dbd7c19e3b696eaced',
    'bridge_pg/static/taskboard.html': '8a69be65260d34f3ca8f347ca2eb02edcb6dd0ac177aee0cf94a957579db55ca',
}

OLD_ONE = '''    def _one(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        try:
            with self._connect() as cn:
                with cn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '10000ms'")
                    cur.execute(sql, args)
                    row = cur.fetchone()
                    return row['result'] if row is not None else None
        except psycopg.Error as e:
            code = (getattr(getattr(e, 'diag', None), 'message_primary', None) or '').strip()
            if e.sqlstate == 'P0001' and code in KNOWN_ERRORS:
                raise BridgeError(code) from e
            # Never leak DSN, passwords, query parameters, or server paths.
            raise BridgeError('database_unavailable') from e
'''
NEW_ONE = '''    def _run_one_on_connection(self, cn, sql: str, args: tuple[Any, ...]) -> Any:
        with cn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '10000ms'")
            cur.execute(sql, args)
            row = cur.fetchone()
            return row['result'] if row is not None else None

    @contextmanager
    def taskboard_read_batch(self):
        """One private DB connection per HTTP request, never shared across threads.

        The contextvar is reset even on errors. Per-operation transactions below
        make expected per-dialog errors fail independently rather than poisoning
        all subsequent reads in a PostgreSQL transaction.
        """
        if self._taskboard_scope.get() is not None:
            raise BridgeError('request_rejected')
        try:
            with self._connect() as cn:
                token = self._taskboard_scope.set(cn)
                try:
                    yield
                finally:
                    self._taskboard_scope.reset(token)
        except psycopg.Error as e:
            raise BridgeError('database_unavailable') from e

    def _one(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        try:
            scoped = self._taskboard_scope.get()
            if scoped is None:
                # All non-Taskboard users retain the existing short connection.
                with self._connect() as cn:
                    return self._run_one_on_connection(cn, sql, args)
            # Committed independently: a recoverable error in one row must not
            # abort the next dialog's metadata/last-message lookup.
            with scoped.transaction():
                return self._run_one_on_connection(scoped, sql, args)
        except psycopg.Error as e:
            code = (getattr(getattr(e, 'diag', None), 'message_primary', None) or '').strip()
            if e.sqlstate == 'P0001' and code in KNOWN_ERRORS:
                raise BridgeError(code) from e
            # Never leak DSN, passwords, query parameters, or server paths.
            raise BridgeError('database_unavailable') from e
'''

OLD_ROUTE='''                    result=self.cat.list(off,limit)
                    result['csrf']=self.csrf
                    result['mt_delivery']={}
                    for d in result['dialogs']:
                        try:
                            v=self.cat.view(d['task_id'],0,1)
                            m=v['history'][0] if v['history'] else None
                            receipt=self.cat.delivery_status(d['task_id'],m['seq']) if m else None
                            result['mt_delivery'][d['task_id']]={
                              'source':'ok',
                              'seen_seq':m['seq'] if receipt and receipt['status']=='delivered' else None,
                              'inflight_seq':m['seq'] if receipt and receipt['status']=='leased' else None}
                        except BridgeError:
                            result['mt_delivery'][d['task_id']]={'source':'unavailable','seen_seq':None,'inflight_seq':None}
'''
NEW_ROUTE='''                    # A single connection for the entire list. Catalog and the
                    # authenticated SQL functions retain all existing validation.
                    # A test catalog may be injected without a Store; the real
                    # Catalog uses the scoped connection, while such unit-test
                    # fakes retain their previous behavior.
                    batch=getattr(getattr(self.cat,'store',None),'taskboard_read_batch',None)
                    with batch() if batch is not None else nullcontext():
                        result=self.cat.list(off,limit)
                        result['csrf']=self.csrf
                        result['mt_delivery']={}
                        result['row_details']={}
                        for d in result['dialogs']:
                            tid=d['task_id']
                            try:
                                v=self.cat.view(tid,0,1)
                                m=v['history'][0] if v['history'] else None
                                receipt=self.cat.delivery_status(tid,m['seq']) if m else None
                                result['mt_delivery'][tid]={
                                  'source':'ok',
                                  'seen_seq':m['seq'] if receipt and receipt['status']=='delivered' else None,
                                  'inflight_seq':m['seq'] if receipt and receipt['status']=='leased' else None}
                                # Only fields required to draw table rows: no
                                # message body and no extra /api/history calls.
                                compact={k:m[k] for k in ('message_id','seq','kind','from','to',
                                  'returned','created_at_utc') if k in m} if m else None
                                result['row_details'][tid]={
                                  'title':v['title'], 'status':v['status'],
                                  'waiting_for':v['waiting_for'],
                                  'history':[compact] if compact is not None else []}
                            except BridgeError:
                                result['mt_delivery'][tid]={
                                  'source':'unavailable','seen_seq':None,'inflight_seq':None}
'''

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def exactly(text: str, old: str, new: str, label: str) -> str:
    num = text.count(old)
    if num != 1:
        raise ValueError(f'STAGE40_STOP_ANCHOR_{label}_EXPECTED1_FOUND{num}')
    return text.replace(old,new,1)

def transform(rel: str, text: str) -> str:
    if rel == 'bridge_pg/store.py':
        text=exactly(text,'from pathlib import Path\n',
                     'from pathlib import Path\nfrom contextlib import contextmanager\nfrom contextvars import ContextVar\n','store_imports')
        text=exactly(text,'    def _connect(self):\n',
                     '        self._taskboard_scope = ContextVar("bridge_pg_taskboard_scope", default=None)\n\n    def _connect(self):\n',
                     'store_init_scope')
        return exactly(text,OLD_ONE,NEW_ONE,'store_one')
    if rel == 'bridge_pg/taskboard_v2.py':
        text=exactly(text,'from datetime import datetime, timezone\n',
                     'from datetime import datetime, timezone\nfrom contextlib import nullcontext\n','route_imports')
        return exactly(text,OLD_ROUTE,NEW_ROUTE,'list_route')
    if rel == 'bridge_pg/static/taskboard.js':
        text=exactly(text,'let selectedTask = null, showArchived = false, listGeneration = 0, historyGeneration = 0;',
                     'let selectedTask = null, showArchived = false, listGeneration = 0, historyGeneration = 0;\nlet refreshRunning = false;', 'js_refresh_state')
        text=exactly(text,'    const delivery = result.mt_delivery || {};',
                     '    const delivery = result.mt_delivery || {};\n    const rowDetails = result.row_details || {};','js_row_details')
        text=exactly(text,'''        const d = await firstPage(item.task_id);
        if (generation !== listGeneration) return;
''','''        const d = rowDetails[item.task_id];
        if (!d) throw Error('row_details_unavailable');
''','js_no_n_plus_one')
        text=exactly(text,"$('refresh').onclick = () => {clearCache(); load();};",'''$('refresh').onclick = async () => {
  if (refreshRunning) return;
  refreshRunning = true;
  const control = $('refresh');
  control.disabled = true;
  control.textContent = 'Обновляем…';
  $('notice').textContent = 'Обновляем…';
  try {
    clearCache();
    await load();
  } finally {
    control.textContent = 'Обновить';
    control.disabled = false;
    refreshRunning = false;
  }
};''','js_refresh_click')
        return text
    raise ValueError('STAGE40_STOP_UNEXPECTED_FILENAME')


def file_mode_guard(path:Path):
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1:
        raise ValueError('STAGE40_STOP_UNTRUSTED_SOURCE_FILE '+path.name)
    return st


def read_live(root: Path=ROOT) -> dict[str,bytes]:
    if root.is_symlink() or root.resolve()!=root:
        raise ValueError('STAGE40_STOP_UNTRUSTED_ROOT')
    if not root.is_dir():
        raise ValueError('STAGE40_STOP_ROOT_ABSENT')
    originals={}
    for rel,expected in {**FILES,**OTHER_PINS}.items():
        p=root/rel
        file_mode_guard(p)
        raw=p.read_bytes()
        if sha(raw)!=expected:
            raise ValueError('STAGE40_STOP_SHA_PIN '+rel)
        originals[rel]=raw
    return originals


def generate(originals:dict[str,bytes])->dict[str,bytes]:
    patched={}
    for rel in FILES:
        s=originals[rel].decode('utf-8')
        edited=transform(rel,s)
        if edited==s:raise ValueError('STAGE40_STOP_EMPTY_PATCH')
        if rel.endswith('.py'):
            ast.parse(edited,filename=rel)
        patched[rel]=edited.encode('utf-8')
    if 'await firstPage(item.task_id)' in patched['bridge_pg/static/taskboard.js'].decode():
        raise ValueError('STAGE40_STOP_SECOND_HISTORY_WAVE_PRESENT')
    return patched


def verify_js(patched:dict[str,bytes]):
    node=shutil.which('node')
    if node is None:
        home_node=Path.home()/'.hermes/node/bin/node'
        if home_node.is_file(): node=str(home_node)
    if node is None:raise ValueError('STAGE40_STOP_NODE_REQUIRED_FOR_SYNTAX_CHECK')
    fd,path=tempfile.mkstemp(prefix='.stage40-',suffix='.js')
    try:
        with os.fdopen(fd,'wb') as f:f.write(patched['bridge_pg/static/taskboard.js'])
        p=subprocess.run([node,'--check',path],capture_output=True,timeout=10)
        if p.returncode!=0:raise ValueError('STAGE40_STOP_JS_SYNTAX')
    finally:
        os.unlink(path)


def atomic_write(path:Path, raw:bytes, mode:int):
    fd,tmp=tempfile.mkstemp(prefix='.stage40-new-',dir=path.parent)
    try:
        os.fchmod(fd,stat.S_IMODE(mode))
        with os.fdopen(fd,'wb') as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def backup_prepare(root:Path, originals:dict[str,bytes], patched:dict[str,bytes],state:Path):
    state.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(state,0o700)
    folder=state/('backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
    folder.mkdir(mode=0o700)
    for rel in FILES:
        dest=folder/rel
        dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with dest.open('xb') as f:
            f.write(originals[rel]);f.flush();os.fsync(f.fileno())
        dest.chmod(0o600)
    manifest={'root':str(root),'original':{r:sha(originals[r]) for r in FILES},
              'patched':{r:sha(patched[r]) for r in FILES},'state':'prepared'}
    with (folder/'manifest.json').open('x') as f:
        json.dump(manifest,f,indent=2);f.flush();os.fsync(f.fileno())
    (folder/'manifest.json').chmod(0o600)
    return folder


def restore(folder:Path):
    # Owner-requested rollback only. No SQL, no secrets, no service control.
    manifest=json.loads((folder/'manifest.json').read_text())
    if manifest['root']!=str(ROOT):raise ValueError('STAGE40_STOP_WRONG_ROOT')
    for rel in FILES:
        current=(ROOT/rel).read_bytes()
        if sha(current) not in (manifest['patched'][rel],manifest['original'][rel]):
            raise ValueError('STAGE40_STOP_ROLLBACK_DRIFT '+rel)
    for rel in FILES:
        dest=ROOT/rel
        raw=(folder/rel).read_bytes()
        if sha(raw)!=manifest['original'][rel]:raise ValueError('STAGE40_STOP_BACKUP_DAMAGED '+rel)
        atomic_write(dest,raw,dest.stat().st_mode)
    print('STAGE40_ROLLBACK_SOURCES_RESTORED_FROM_VERIFIED_BACKUP')


def install():
    originals=read_live(ROOT)
    patched=generate(originals)
    verify_js(patched)
    print('STAGE40_SOURCE_PINS_5_OK')
    print('STAGE40_PYTHON_AND_JS_SYNTAX_OK')
    folder=backup_prepare(ROOT,originals,patched,STATE)
    print('STAGE40_BACKUP_CREATED '+str(folder),flush=True)
    # Commit only after all anchors, syntax and backups were confirmed.
    try:
        for rel in FILES:
            p=ROOT/rel
            if sha(p.read_bytes())!=FILES[rel]:raise ValueError('STAGE40_STOP_SOURCE_CHANGED '+rel)
            atomic_write(p,patched[rel],p.stat().st_mode)
        for rel in FILES:
            if sha((ROOT/rel).read_bytes())!=sha(patched[rel]):
                raise ValueError('STAGE40_STOP_POSTWRITE_MISMATCH '+rel)
    except BaseException:
        restore(folder)
        raise
    print('STAGE40_PATCH_INSTALLED_3_FILES_NO_SQL_SCHEMA_CHANGE')
    print('STAGE40_RESTORE_DIR '+str(folder))


def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest='command',required=True)
    sub.add_parser('install')
    rb=sub.add_parser('restore');rb.add_argument('backup_dir')
    args=ap.parse_args()
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=STATE/'stage40.lock'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.command=='install':install()
        else:
            folder=Path(args.backup_dir)
            if folder.parent!=STATE or folder.is_symlink():raise ValueError('STAGE40_STOP_BAD_BACKUP_DIR')
            restore(folder)
    finally:
        os.close(fd)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('STAGE40_STOP_'+type(e).__name__+': '+str(e),file=sys.stderr)
        sys.exit(2)
