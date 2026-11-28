'use strict';
/* P35: no DOM, network or external package. Test only the Last action formatter. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const file = process.argv[2];
if (!file) throw Error('Expected staged taskboard.js path');
const original = fs.readFileSync(file, 'utf8');
const marker = "$('refresh').onclick";
const cut = original.indexOf(marker);
if (cut < 0) throw Error('Taskboard bootstrap anchor missing');
const context = {module: {exports: {}}, Date, Number, Map, URLSearchParams};
vm.runInNewContext(original.slice(0, cut) +
    '\nmodule.exports = {lastAction, sinceGit};\n', context, {timeout: 3000});
const {lastAction} = context.module.exports;

const fromChat = {seq: 4, from: 'chatgpt', kind: 'task',
                  created_at_utc: new Date(Date.now()-60000).toISOString()};
const fromMT = {seq: 5, from: 'mt', kind: 'report',
                created_at_utc: new Date(Date.now()-120000).toISOString()};
const done = lastAction(fromMT, null);
assert.match(done.label, /^MT ответил/);
assert.match(done.hint, /Git/);
assert.match(lastAction(fromChat, {source: 'ok', seen_seq: 4,
                                  inflight_seq: null}).label, /^Передано MT$/);
assert.match(lastAction(fromChat, {source: 'ok', seen_seq: 3,
                                  inflight_seq: 4}).label, /^Передача не подтверждена$/);
assert.match(lastAction(fromChat, {source: 'unavailable',
                                  seen_seq: null, inflight_seq: null}).label,
             /^ChatGPT записал/);
assert.match(lastAction({...fromChat, kind: 'return'}, null).label, /^Ход возвращён/);
assert.match(lastAction(null, null).label, /^Пока нет сообщений$/);
console.log('P35_JS_PASS');
