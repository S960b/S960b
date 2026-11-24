'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/pilot_gate34eb.js','utf8');
function select(options) {
  const sandbox={Object, Array, RegExp, String};
  vm.runInNewContext(source+'\nglobalThis.result=stage34ebPilotTaskIds;',sandbox,{timeout:2000});
  return sandbox.result(options);
}
const canary='task_f2c64cfdf7894537b35a19a6def977c6';
const pilot='task_'+'1'.repeat(32);
const other='task_'+'2'.repeat(32);
test('explicit one pilot accepted',()=>assert.deepEqual([...select({task_ids:[pilot]})],[pilot]));
test('no options deny',()=>assert.equal(select(undefined).length,0));
test('empty list deny',()=>assert.equal(select({task_ids:[]}).length,0));
test('missing task IDs deny',()=>assert.equal(select({}).length,0));
test('multiple allowed IDs deny',()=>assert.equal(select({task_ids:[pilot,other]}).length,0));
test('canary cannot be selected',()=>assert.equal(select({task_ids:[canary]}).length,0));
test('malformed ID cannot be selected',()=>assert.equal(select({task_ids:['task_incorrect']}).length,0));
test('arbitrary string cannot be selected',()=>assert.equal(select({task_ids:'task_'+('1'.repeat(32))}).length,0));
test('selection is immutable',()=>assert.equal(Object.isFrozen(select({task_ids:[pilot]})),true));
test('caller array mutation cannot change selected pilot',()=>{
  const orig=[pilot];const selected=select({task_ids:orig});orig[0]=other;
  assert.deepEqual([...selected],[pilot]);
});
