'use strict';
/* Evaluate only TWO extracted pure functions from the byte-pinned live plugin.
 * NEVER import the plugin, MultiTool SDK, or production durable state.
 * The isolated real DB ACK is performed by stage33j.py --ack-only as bridge_agent.
 */
const fs=require('node:fs');
const assert=require('node:assert/strict');
const cp=require('node:child_process');
const path=require('node:path');
const crypto=require('node:crypto');
const ROOT='/home/kali/hermes-pg-stage33j-ack-recovery';
const LIVE_SHA='220da977562f9c0763d6b5d5640f686f54f041d357ed397c8482c0d4363f525c';
const PY='/home/kali/bridge-pg-v2/.venv/bin/python';
const BEFORE='function confirmedMtResponseAfter(snapshot, taskId, reserved) {';
const RECON='async function reconcileAnsweredInflight(ctx, state, dialog) {';
const AFTER='export async function runTick(ctx) {';
const SHA=b=>crypto.createHash('sha256').update(b).digest('hex');
function extracted(srcFile){
  const original=fs.readFileSync(srcFile);
  assert.equal(SHA(original),LIVE_SHA,'LIVE_PLUGIN_HASH_MISMATCH');
  const src=original.toString('utf8');
  for(const s of [BEFORE,RECON,AFTER]) assert.equal(src.split(s).length,2,'JS_LAYOUT_UNEXPECTED');
  const a=src.indexOf(BEFORE), b=src.indexOf(RECON), c=src.indexOf(AFTER);
  assert.ok(a>=0&&b>a&&c>b);
  const source=src.slice(a,c);
  assert.ok(source.includes('inflight_reconciled_ack_confirmed'));
  assert.ok(source.includes('reserved.delivery_id'));
  assert.ok(source.includes('reserved.lease_token'));
  assert.ok(source.includes('acked?.acked !== true'));
  assert.ok(source.indexOf('acked?.acked !== true')<source.indexOf('delete state.inflight[taskId]'));
  assert.ok(!source.includes('session.prompt(')&&!source.includes('session.create('));
  const saves=[];
  let onSave=()=>{};
  const realSave=(_p,state)=>onSave(state);
  const f=new Function('saveState','INFLIGHT_RECONCILE_MS', source+'\nreturn {confirmedMtResponseAfter,reconcileAnsweredInflight};');
  return {funcs:f(realSave,100),setSave(fn){onSave=fn;}};
}
function snapshotFor(tid,mid){
  return {task_id:tid,head:'a'.repeat(40),seq:2,history:[
    {seq:1,message_id:mid,from:'chatgpt',to:'mt',kind:'task'},
    {seq:2,message_id:'test-report',from:'mt',to:'chatgpt',kind:'report'}]};
}
function preflight(ex){
  const tid='task_'+'a'.repeat(32),mid='test-message';
  const {confirmedMtResponseAfter,reconcileAnsweredInflight}=ex.funcs;
  assert.equal(confirmedMtResponseAfter(snapshotFor(tid,mid),tid,{seq:1,message_id:mid}),true);
  assert.equal(confirmedMtResponseAfter(snapshotFor(tid,'wrong'),tid,{seq:1,message_id:mid}),false);
  assert.equal(confirmedMtResponseAfter({...snapshotFor(tid,mid),seq:1},tid,{seq:1,message_id:mid}),false);
  assert.equal(typeof reconcileAnsweredInflight,'function');
  process.stdout.write('STAGE33J_LIVE_FUNCTION_EXTRACTED_AND_VERIFIED\n');
}
function safeWriteState(file,state){
  const temp=file+'.'+process.pid+'.tmp';
  const fd=fs.openSync(temp,'wx',0o600);
  try{fs.writeSync(fd,JSON.stringify(state));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  try{fs.renameSync(temp,file);}finally{try{fs.unlinkSync(temp);}catch{}}
  const dirfd=fs.openSync(path.dirname(file),'r');
  try{fs.fsyncSync(dirfd);}finally{fs.closeSync(dirfd);}
}
function readFixture(p){
  const st=fs.lstatSync(p);
  assert.ok(st.isFile() && !st.isSymbolicLink() && (st.mode&0o077)===0 && st.nlink===1);
  const obj=JSON.parse(fs.readFileSync(p,'utf8'));
  assert.equal(obj.stage,'33J');
  assert.equal(obj.request_id,'pgv2-stage33j-reconcile-fault-20261122-v1');
  assert.match(obj.task_id,/^task_[0-9a-f]{32}$/);
  assert.equal(obj.seq,1);
  assert.equal(obj.message_id,'pgv2-stage33j-no-sdk-task-v1');
  assert.equal(obj.consumer,'stage33j-ack-reconcile-test');
  assert.ok(Number.isSafeInteger(obj.delivery_id)&&obj.delivery_id>0);
  assert.match(obj.lease_token,/^[0-9a-fA-F-]{36}$/);
  assert.equal(obj.snapshot.task_id,obj.task_id);
  assert.equal(obj.snapshot.seq,2);
  assert.match(obj.snapshot.head,/^[a-f0-9]{40}$/);
  return obj;
}
function ackViaRealAgent(lease){
  const r=cp.spawnSync(PY,[path.join(ROOT,'stage33j.py'),'ack-only'],{
    cwd:ROOT,encoding:'utf8',timeout:20000,maxBuffer:8192,
    input:JSON.stringify({delivery_id:lease.delivery_id,lease_token:lease.lease_token,consumer:lease.consumer}),
    env:Object.fromEntries(Object.entries(process.env).filter(([k])=>k!=='APPDIR'&&k!=='APPIMAGE'))
  });
  assert.equal(r.status,0,'ROLE_BOUND_ACK_HELPER_FAILED');
  const obj=JSON.parse(String(r.stdout).trim());
  assert.equal(obj.acked,true);
  return obj;
}
async function run(ex, fixture){
  const data=readFixture(fixture);
  assert.equal(ex.funcs.confirmedMtResponseAfter(data.snapshot,data.task_id,
        {seq:1,message_id:data.message_id}),true);
  const statefile=path.join(ROOT,'isolated-state33j.json');
  assert.equal(fs.existsSync(statefile),false,'ISOLATED_STATE_ALREADY_EXISTS');
  const state={sessions:{},seen:{},inflight:{[data.task_id]:{
    seq:1,message_id:data.message_id,delivery_id:data.delivery_id,lease_token:data.lease_token,
    phase:'prompt_attempted'}}};
  ex.setSave((_state)=>safeWriteState(statefile,_state));
  safeWriteState(statefile,state);
  let ackCalls=0, sdkCalls=0;
  const ctx={paths:{state:statefile},log:()=>{},cliCall:async (q)=>{
    if(q.op==='view')return data.snapshot;
    assert.equal(q.op,'ack');
    assert.ok(state.inflight[data.task_id],'INFLIGHT_LOST_BEFORE_ACK');
    assert.equal(q.delivery_id,data.delivery_id);
    assert.equal(q.lease_token,data.lease_token);
    ackCalls++;
    const receipt=ackViaRealAgent({...data,delivery_id:q.delivery_id,lease_token:q.lease_token});
    if(ackCalls===1){
      assert.equal(receipt.already_delivered,false);
      throw new Error('SIMULATED_LOST_ACK_REPLY_AFTER_PG_COMMIT');
    }
    assert.equal(receipt.already_delivered,true);
    return {acked:true};
  }};
  const reconcile=ex.funcs.reconcileAnsweredInflight;
  assert.equal(await reconcile(ctx,state,{task_id:data.task_id}),false,
               'LOST_ACK_RESPONSE_MUST_KEEP_INFLIGHT');
  assert.ok(state.inflight[data.task_id],'INFLIGHT_MISSING_AFTER_LOSS');
  assert.equal(state.seen[data.task_id],undefined);
  const persisted=JSON.parse(fs.readFileSync(statefile,'utf8'));
  assert.ok(persisted.inflight[data.task_id], 'INFLIGHT_NOT_PERSISTED');
  // Synthetic time advance. No timers, no actual SDK call.
  state.inflight[data.task_id].reconcile_checked_ms=0;
  assert.equal(await reconcile(ctx,state,{task_id:data.task_id}),true,
               'EXACTLY_ONCE_ACK_RECOVERY_FAILED');
  assert.equal(ackCalls,2);
  assert.equal(sdkCalls,0);
  assert.equal(state.seen[data.task_id],1);
  assert.equal(state.inflight[data.task_id],undefined);
  const done=JSON.parse(fs.readFileSync(statefile,'utf8'));
  assert.equal(done.seen[data.task_id],1);
  assert.equal(done.inflight[data.task_id],undefined);
  process.stdout.write('STAGE33J_ACTUAL_JS_ACK_LOST_REPLY_RECOVERED\n');
}
(async()=>{
  const [mode,src,fixture]=process.argv.slice(2);
  assert.ok(mode==='--preflight-only'||mode==='--run-once');
  const ex=extracted(src);
  preflight(ex);
  if(mode==='--run-once')await run(ex,fixture);
})().catch(err=>{console.error('STAGE33J_NODE_STOP_'+String(err?.code||err?.message||'error').slice(0,70).replace(/[^a-zA-Z0-9_-]/g,'_'));process.exitCode=2;});
