// DOM behaviour test, no live bridge or network. Feed task_view.PAGE on stdin.
// NODE_PATH=/path/to/jsdom/node_modules node tests/taskboard_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');

(async () => {
  const dom = new JSDOM(fs.readFileSync(0, 'utf8'), {url: 'http://localhost:8787/', runScripts: 'outside-only'});
  const w = dom.window;
  w.setInterval = () => 0;
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); };
  const t = {task_id: 'task_test', title: 'Test task', arrow: 'ChatGPT → MT', message_count: 1,
    people: {ChatGPT: {text: 'ответил', color: 'green'}, MT: {text: 'в работе', color: 'yellow'}},
    updated_at_utc: '2026-10-26T12:00:00Z'};
  let deleted = false, deleteCalls = 0, historyCalls = 0, failDelete = false;
  w.fetch = async (url, opts = {}) => {
    if (url === '/api/tasks') return {ok: true, json: async () => ({tasks: deleted ? [] : [t]})};
    if (url.startsWith('/api/tasks/history?')) {
      historyCalls++;
      return {ok: true, json: async () => ({task: {...t, history: [{from: 'MT', at: t.updated_at_utc, text: '<script>BODY_MARKER</script>'}]}})};
    }
    if (url === '/api/tasks/delete') {
      deleteCalls++;
      assert.equal(opts.method, 'POST');
      assert.deepEqual(JSON.parse(opts.body), {task_id: t.task_id, confirmed: true});
      if (failDelete) return {ok: false, status: 500, json: async () => ({ok: false, error: 'test failure'})};
      deleted = true;
      return {ok: true, json: async () => ({ok: true})};
    }
    throw Error('Unexpected fetch: ' + url);
  };
  w.eval(w.document.querySelector('script').textContent);
  await w.load();
  const actions = w.document.querySelector('.task-actions');
  assert.equal(actions.children[0].textContent, 'История (1)');
  assert.equal(actions.children[1].textContent, 'Удалить задачу');
  assert.equal(w.getComputedStyle(actions).flexDirection, 'column');
  assert.equal(historyCalls, 0, 'polling must not fetch history');
  assert.ok(!w.document.body.textContent.includes('BODY_MARKER'));
  await w.show(t);
  assert.equal(historyCalls, 1);
  assert.equal(w.document.querySelector('#messages pre').textContent, '<script>BODY_MARKER</script>');
  w.document.querySelector('#close').click();
  assert.equal(w.document.querySelector('#messages').childElementCount, 0, 'close must release rendered history');
  const button = w.document.querySelector('.danger');
  w.confirm = () => false;
  await w.deleteTask(t, button);
  assert.equal(deleteCalls, 0, 'cancel must not send a deletion');
  assert.equal(w.document.querySelectorAll('#rows tr').length, 1);
  w.confirm = text => {assert.ok(text.includes(t.task_id)); assert.ok(text.includes(t.title)); return true;};
  failDelete = true;
  await w.deleteTask(t, button);
  assert.equal(w.document.querySelectorAll('#rows tr').length, 1, 'failed delete must retain task');
  assert.match(w.document.querySelector('#err').textContent, /Удаление не выполнено/);
  failDelete = false;
  await w.deleteTask(t, button);
  assert.equal(deleteCalls, 2);
  assert.equal(w.document.querySelectorAll('#rows tr').length, 0);
  console.log('PASS: delete below history, cancel no-op, confirmed delete, error handling, lazy history, close cleanup');
  dom.window.close();
})().catch(e => {console.error(e); process.exitCode = 1;});
