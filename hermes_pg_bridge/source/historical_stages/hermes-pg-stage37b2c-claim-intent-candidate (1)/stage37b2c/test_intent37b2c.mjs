import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {stage37b2cIntentPresent as exists,
        stage37b2cWriteIntent as write,
        stage37b2cClearAfterAck as clear,
        stage37b2cFinalizeAfterConfirmedAck as finalize}
  from './intent37b2c.mjs';
const TASK='task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const MSG='pgv2-stage37b2c-message-1';
const root=fs.mkdtempSync(path.join(os.tmpdir(),'stage37b2c-js-'));
const dir=path.join(root,'journal');
let n=0;
function test(name,fn){try{fn();n++;console.log('STAGE37B2C_CASE_OK_'+name)}catch(e){console.error('STAGE37B2C_CASE_FAIL_'+name,e.stack);process.exitCode=2;throw e}}
try {
  test('journal_initially_absent',()=>assert.equal(exists(TASK,dir),false));
  test('reject_invalid_task',()=>assert.throws(()=>write('bad',1,MSG,dir)));
  test('reject_invalid_seq',()=>assert.throws(()=>write(TASK,0,MSG,dir)));
  test('reject_bad_mid',()=>assert.throws(()=>write(TASK,1,'bad\nmid',dir)));
  test('fsync_preclaim_intent_success',()=>assert.equal(write(TASK,1,MSG,dir),true));
  test('journal_seen_after_process_restart',()=>assert.equal(exists(TASK,dir),true));
  test('journal_dir_private',()=>assert.equal(fs.statSync(dir).mode&0o077,0));
  test('journal_file_private',()=>assert.equal(fs.statSync(path.join(dir,TASK+'.json')).mode&0o077,0));
  test('reclaim_same_task_forbidden',()=>assert.throws(()=>write(TASK,1,MSG,dir),{code:'EEXIST'}));
  test('lost_lease_cannot_clear_without_local_ack',()=>assert.throws(()=>finalize({inflight:{},seen:{}},TASK,1,MSG,dir)));
  test('wrong_mid_cannot_clear',()=>assert.throws(()=>clear(TASK,1,'different',dir)));
  test('stale_local_seen_cannot_clear',()=>assert.throws(()=>finalize({inflight:{},seen:{[TASK]:0}},TASK,1,MSG,dir)));
  test('inflight_still_present_cannot_clear',()=>assert.throws(()=>finalize({inflight:{[TASK]:{}},seen:{[TASK]:1}},TASK,1,MSG,dir)));
  test('clear_after_confirmed_ack',()=>assert.equal(finalize({inflight:{},seen:{[TASK]:1}},TASK,1,MSG,dir),true));
  test('intent_gone_after_confirmed_ack',()=>assert.equal(exists(TASK,dir),false));
  test('new_seq_after_ack_allowed',()=>assert.equal(write(TASK,3,'new-message',dir),true));
  test('wrong_seq_does_not_clear',()=>assert.throws(()=>clear(TASK,1,'new-message',dir)));
  test('correct_seq_clears',()=>assert.equal(clear(TASK,3,'new-message',dir),true));
  test('recovered_unknown_record_blocks_reclaim',()=>{fs.writeFileSync(path.join(dir,TASK+'.json'),'{corrupted',{mode:0o600,flag:'wx'});assert.equal(exists(TASK,dir),true);assert.throws(()=>write(TASK,3,MSG,dir));});
  console.log('STAGE37B2C_HELPER_BEHAVIORAL_CASES_PASSED '+n);
} finally { fs.rmSync(root,{recursive:true,force:true}); }
