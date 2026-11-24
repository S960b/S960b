"""Offline, no DB/network/filesystem mutation beyond test temp dirs."""
import ast
import importlib.util
import pathlib
import re
import tempfile
import unittest

MOD_PATH=pathlib.Path(__file__).with_name('stage40_patch.py')
spec=importlib.util.spec_from_file_location('stage40_patch',MOD_PATH)
p=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

STORE_SRC='''from pathlib import Path
from typing import Any
from contextlib import nullcontext
class Store:
    def __init__(self):
        self.conninfo = 'test'

    def _connect(self):
        return None

'''+p.OLD_ONE
ROUTE_SRC='''def apply(self):
    if True:
        if True:
            if True:
                if True:
'''+p.OLD_ROUTE  # route at 20 spaces still AST requires other context; only transformations tested
JS_SRC="""'use strict';
let selectedTask = null, showArchived = false, listGeneration = 0, historyGeneration = 0;
async function load() {
    const delivery = result.mt_delivery || {};
    for (const item of visible) {
        const d = await firstPage(item.task_id);
        if (generation !== listGeneration) return;
    }
}
$('refresh').onclick = () => {clearCache(); load();};
"""

class PatchTests(unittest.TestCase):
    def test_sha_known(self):self.assertEqual(p.sha(b'abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    def test_three_files(self):self.assertEqual(len(p.FILES),3)
    def test_original_pins_long(self):self.assertTrue(all(len(x)==64 for x in p.FILES.values()))
    def test_readonly_compat_pinned(self):self.assertIn('bridge_pg/compat.py',p.OTHER_PINS)
    def test_readonly_html_pinned(self):self.assertIn('bridge_pg/static/taskboard.html',p.OTHER_PINS)
    def test_store_one_has_scoped(self):
        s=p.transform('bridge_pg/store.py',STORE_SRC)
        self.assertIn('taskboard_read_batch',s)
    def test_store_uses_contextvar(self):
        s=p.transform('bridge_pg/store.py',STORE_SRC)
        self.assertIn('ContextVar',s)
    def test_store_preserves_default_short_connection(self):
        s=p.transform('bridge_pg/store.py',STORE_SRC)
        self.assertIn('if scoped is None:',s)
        self.assertIn('with self._connect() as cn:',s)
    def test_store_transactions_independent(self):
        s=p.transform('bridge_pg/store.py',STORE_SRC)
        self.assertIn('with scoped.transaction():',s)
    def test_store_old_removed(self):
        s=p.transform('bridge_pg/store.py',STORE_SRC)
        self.assertNotIn(p.OLD_ONE,s)
    def test_store_ast(self):
        s=p.transform('bridge_pg/store.py',STORE_SRC)
        ast.parse(s)
    def test_route_one_connection(self):
        s=p.transform('bridge_pg/taskboard_v2.py','from datetime import datetime, timezone\n'+p.OLD_ROUTE)
        self.assertIn('taskboard_read_batch',s)
    def test_route_fake_catalog_fallback(self):
        s=p.transform('bridge_pg/taskboard_v2.py','from datetime import datetime, timezone\n'+p.OLD_ROUTE)
        self.assertIn('nullcontext()',s)
    def test_route_receipt(self):
        s=p.transform('bridge_pg/taskboard_v2.py','from datetime import datetime, timezone\n'+p.OLD_ROUTE)
        self.assertIn('self.cat.delivery_status(tid',s)
    def test_route_strips_body(self):
        s=p.transform('bridge_pg/taskboard_v2.py','from datetime import datetime, timezone\n'+p.OLD_ROUTE)
        self.assertIn("'created_at_utc') if k in m",s)
        self.assertNotIn("'body'",s)
    def test_route_preserves_fail_per_row(self):
        s=p.transform('bridge_pg/taskboard_v2.py','from datetime import datetime, timezone\n'+p.OLD_ROUTE)
        self.assertIn('except BridgeError:',s)
        self.assertIn("'source':'unavailable'",s)
    def test_route_keeps_history_shape(self):
        s=p.transform('bridge_pg/taskboard_v2.py','from datetime import datetime, timezone\n'+p.OLD_ROUTE)
        self.assertIn("'history':[compact] if compact is not None else []",s)
    def test_js_skips_list_history(self):
        s=p.transform('bridge_pg/static/taskboard.js',JS_SRC)
        self.assertNotIn('await firstPage(item.task_id)',s)
        self.assertIn('rowDetails[item.task_id]',s)
    def test_js_disables_refresh(self):
        s=p.transform('bridge_pg/static/taskboard.js',JS_SRC)
        self.assertIn('control.disabled = true;',s)
    def test_js_repeat_guard(self):
        s=p.transform('bridge_pg/static/taskboard.js',JS_SRC)
        self.assertIn('if (refreshRunning) return;',s)
    def test_js_reenables(self):
        s=p.transform('bridge_pg/static/taskboard.js',JS_SRC)
        self.assertIn('control.disabled = false;',s)
    def test_js_has_finally(self):
        s=p.transform('bridge_pg/static/taskboard.js',JS_SRC)
        self.assertIn('} finally {',s)
    def test_js_preserves_api_display(self):
        s=p.transform('bridge_pg/static/taskboard.js',JS_SRC)
        self.assertIn('const delivery = result.mt_delivery || {};',s)
    def test_anchor_fail_closed_absent(self):
        with self.assertRaises(ValueError):p.exactly('x','missing','new','unit')
    def test_anchor_fail_closed_duplicated(self):
        with self.assertRaises(ValueError):p.exactly('foo foo','foo','bar','unit')
    def test_unexpected_file_rejected(self):
        with self.assertRaises(ValueError):p.transform('other.js',JS_SRC)
    def test_scope_nested_protection(self):
        self.assertIn('if self._taskboard_scope.get() is not None:',p.NEW_ONE)
    def test_scope_finally_reset(self):
        self.assertIn('self._taskboard_scope.reset(token)',p.NEW_ONE)
    def test_node_syntax_available(self):
        import shutil
        self.assertTrue(shutil.which('node') or (pathlib.Path.home()/'.hermes/node/bin/node').is_file())

class RuntimeScopeTests(unittest.TestCase):
    """Small simulated psycopg DB: prove one connection per HTTP list."""
    def _store(self):
        import contextlib
        from contextvars import ContextVar
        class DBError(Exception):
            sqlstate = None
            diag = None
        class PG:Error=DBError
        class BridgeError(Exception):pass
        namespace={'Any':object,'contextmanager':contextlib.contextmanager,
                   'ContextVar':ContextVar,'psycopg':PG,'KNOWN_ERRORS':set(),
                   'BridgeError':BridgeError}
        patched=p.transform('bridge_pg/store.py',STORE_SRC)
        exec(compile(patched,'store_test.py','exec'),namespace)
        store=namespace['Store']()
        events=[]
        class FakeConn:
            def __enter__(self):events.append('conn-enter');return self
            def __exit__(self,*a):events.append('conn-exit')
            def transaction(self):
                @contextlib.contextmanager
                def tx():
                    events.append('tx-enter')
                    try: yield
                    finally: events.append('tx-exit')
                return tx()
            def cursor(self):
                @contextlib.contextmanager
                def cur():
                    class Cursor:
                        def execute(self,sql,args=()):events.append('sql:'+sql[:6])
                        def fetchone(self):return {'result':{'ok':True}}
                    yield Cursor()
                return cur()
        def connect():events.append('connect');return FakeConn()
        store._connect=connect
        return store,events

    def test_three_calls_one_connection_in_scope(self):
        store,events=self._store()
        with store.taskboard_read_batch():
            for i in range(3):self.assertEqual(store._one('SELECT 1'),{'ok':True})
        self.assertEqual(events.count('connect'),1)
        self.assertEqual(events.count('tx-enter'),3)
    def test_without_scope_old_one_per_query(self):
        store,events=self._store()
        store._one('SELECT 1');store._one('SELECT 2')
        self.assertEqual(events.count('connect'),2)
        self.assertEqual(events.count('tx-enter'),0)
    def test_scope_closes_on_exception(self):
        store,events=self._store()
        with self.assertRaises(ValueError):
            with store.taskboard_read_batch():raise ValueError('stop')
        self.assertEqual(events.count('conn-exit'),1)
        self.assertIsNone(store._taskboard_scope.get())
    def test_nested_scope_rejected(self):
        store,events=self._store()
        with store.taskboard_read_batch():
            with self.assertRaises(Exception):
                with store.taskboard_read_batch():pass
        self.assertEqual(events.count('connect'),1)
    def test_scope_then_normal_reverts_connection(self):
        store,events=self._store()
        with store.taskboard_read_batch():store._one('SELECT 1')
        store._one('SELECT 2')
        self.assertEqual(events.count('connect'),2)

if __name__=='__main__':unittest.main()
