#!/usr/bin/env node
'use strict';
// Illustrative finite-state fault model. NOT production code; no imports,
// network, filesystem, database connections or SDK execution.
const assert=require('node:assert/strict');
const TASK='task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const MESSAGE='m-1';
const cases=[];
function test(name,fn){fn();cases.push(name);console.log('STAGE37B2B_MODEL_CASE_OK_'+name)}
function fresh(){return {db:'pending',disk:{seen:0,inflight:null,intent:null},sdk:0,claims:0,report:false}}
function baselineCrashAfterClaim(){let s=fresh();s.db='leased';s.claims++;return s}
function baselineRestart(s){
  // Only persistent `inflight` drives reconciliation. No new claim can win
  // while server-side delivery is `leased`.
  if(s.disk.inflight) return 'reconcile';
  if(s.db==='leased')return 'claim_conflict_without_local_lease_token';
  return 'new_dispatch';
}
function intentStep(s,event){
  if(event==='intent'){
    if(s.disk.intent || s.disk.inflight)return 'hold';
    s.disk.intent={task:TASK,message:MESSAGE,phase:'pre_claim'}; return 'ok';
  }
  if(event==='claim'){
    assert(s.disk.intent && s.disk.intent.phase==='pre_claim');
    s.db='leased';s.claims++;return 'ok';
  }
  if(event==='lease_saved'){
    assert(s.disk.intent && s.db==='leased');
    s.disk.inflight={task:TASK,message:MESSAGE,token:'synthetic-token'};
    s.disk.intent.phase='claim_confirmed';return 'ok';
  }
  if(event==='sdk'){
    assert(s.disk.inflight && s.disk.intent && s.disk.intent.phase==='claim_confirmed');
    s.sdk++;return 'ok';
  }
  if(event==='report'){
    assert(s.sdk===1);s.report=true;return 'ok';
  }
  if(event==='ack'){
    assert(s.report);s.db='delivered';s.disk.seen=1;s.disk.inflight=null;s.disk.intent=null;return 'ok';
  }
  throw Error('bad event');
}
function safeRestart(s){
  if(s.disk.intent){
    if(!s.disk.inflight) return 'HOLD_MANUAL_RECONCILE_UNCERTAIN_CLAIM';
    return 'RECONCILE_REPORT_WITHOUT_SDK';
  }
  return s.db==='pending'?'ELIGIBLE_ONCE':'DO_NOT_REEXECUTE';
}
test('baseline_after_claim_crash_leaves_no_local_lease',()=>{
  const s=baselineCrashAfterClaim();assert.equal(s.disk.inflight,null);
  assert.equal(baselineRestart(s),'claim_conflict_without_local_lease_token');
});
test('preclaim_intent_crash_holds_without_new_sdk',()=>{
  const s=fresh();intentStep(s,'intent');assert.equal(safeRestart(s),'HOLD_MANUAL_RECONCILE_UNCERTAIN_CLAIM');assert.equal(s.sdk,0);
});
test('after_claim_before_lease_saved_holds_without_new_sdk',()=>{
  const s=fresh();intentStep(s,'intent');intentStep(s,'claim');
  assert.equal(safeRestart(s),'HOLD_MANUAL_RECONCILE_UNCERTAIN_CLAIM');assert.equal(s.sdk,0);
});
test('after_lease_saved_recovers_without_new_sdk',()=>{
  const s=fresh();for(const e of ['intent','claim','lease_saved'])intentStep(s,e);
  assert.equal(safeRestart(s),'RECONCILE_REPORT_WITHOUT_SDK');assert.equal(s.sdk,0);
});
test('after_sdk_before_report_does_not_retry_sdk',()=>{
  const s=fresh();for(const e of ['intent','claim','lease_saved','sdk'])intentStep(s,e);
  assert.equal(safeRestart(s),'RECONCILE_REPORT_WITHOUT_SDK');assert.equal(s.sdk,1);
});
test('after_report_before_ack_does_not_retry_sdk',()=>{
  const s=fresh();for(const e of ['intent','claim','lease_saved','sdk','report'])intentStep(s,e);
  assert.equal(safeRestart(s),'RECONCILE_REPORT_WITHOUT_SDK');assert.equal(s.sdk,1);
});
test('after_ack_clears_intent_and_wont_restart_sdk',()=>{
  const s=fresh();for(const e of ['intent','claim','lease_saved','sdk','report','ack'])intentStep(s,e);
  assert.equal(safeRestart(s),'DO_NOT_REEXECUTE');assert.equal(s.disk.intent,null);assert.equal(s.sdk,1);
});
test('duplicate_intent_rejected_before_new_claim',()=>{
  const s=fresh();intentStep(s,'intent');assert.equal(intentStep(s,'intent'),'hold');assert.equal(s.claims,0);
});
test('report_id_reuse_does_not_prove_sdk_idempotency',()=>{
  const a={reportId:'fixed-id',sdkCalls:2};assert.equal(a.reportId,'fixed-id');assert(a.sdkCalls>1);
});
console.log('STAGE37B2B_MODEL_CASES_PASSED',cases.length);
console.log('STAGE37B2B_MODEL_IS_EXPLICITLY_NOT_PRODUCTION_PROOF');
