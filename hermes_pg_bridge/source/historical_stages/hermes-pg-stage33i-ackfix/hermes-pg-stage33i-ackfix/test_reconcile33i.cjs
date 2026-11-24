/* Functional JS tests of the ACTUAL generated candidate's reconcile code.
   Fake DB, fake snapshots; NEVER imports/executes MultiTool, never calls SDK. */
const fs=require('node:fs');
const assert=require('node:assert/strict');
const path=process.argv[2];
if (!path) throw Error('MISSING_CANDIDATE_PATH');
const source=fs.readFileSync(path,'utf8');
const begin='async function reconcileAnsweredInflight(ctx, state, dialog) {';
const end='export async function runTick(ctx) {';
assert.equal(source.split(begin).length,2);
assert.equal(source.split(end).length,2);
const fnSource=source.slice(source.indexOf(begin),source.indexOf(end));
assert.ok(fnSource.includes('inflight_reconciled_ack_confirmed'));
assert.ok(!fnSource.includes('session.prompt('));
let saves=0;
const reconcile=new Function('saveState','confirmedMtResponseAfter','INFLIGHT_RECONCILE_MS',
  fnSource+'\nreturn reconcileAnsweredInflight;')(
  (_p,_s)=>{saves++},(snap)=>snap?.confirmed===true,100);
const task='task_f2c64cfdf7894537b35a19a6def977c6';
const token='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
function setup(outcome,confirmed=true,lease=true){
  const reserved={seq:5,message_id:'pgv2-stage33f-sdk-task-v1',
    delivery_id:lease?42:undefined, lease_token:lease?token:undefined};
  const state={inflight:{[task]:reserved},seen:{}};
  let called=0;
  const logs=[];
  const ctx={paths:{},log:(x)=>logs.push(x),cliCall:async req=>{
    if(req.op==='view') return {confirmed};
    assert.equal(req.op,'ack');
    assert.ok(state.inflight[task],'ACK MUST PRECEDE CLEAR');
    assert.equal(req.delivery_id,42);
    assert.equal(req.lease_token,token);
    called++;
    if(outcome==='throw') throw Error('transient');
    return {acked:outcome===true};
  }};
  return {state,ctx,logs,get calls(){return called}};
}
(async()=>{
  let n=0;
  const a=setup(true);
  assert.equal(await reconcile(a.ctx,a.state,{task_id:task}),true);
  assert.equal(a.calls,1);assert.equal(a.state.seen[task],5);
  assert.equal(a.state.inflight[task],undefined);n++;
  const b=setup('throw');
  assert.equal(await reconcile(b.ctx,b.state,{task_id:task}),false);
  assert.ok(b.state.inflight[task]);assert.equal(b.state.seen[task],undefined);n++;
  const c=setup(false);
  assert.equal(await reconcile(c.ctx,c.state,{task_id:task}),false);
  assert.ok(c.state.inflight[task]);n++;
  const d=setup(true,false);
  assert.equal(await reconcile(d.ctx,d.state,{task_id:task}),false);
  assert.equal(d.calls,0);assert.ok(d.state.inflight[task]);n++;
  const e=setup(true,true,false);
  assert.equal(await reconcile(e.ctx,e.state,{task_id:task}),false);
  assert.equal(e.calls,0);assert.ok(e.state.inflight[task]);n++;
  // After a transient ACK failure, the next permitted tick retries ACK only.
  const f=setup('throw');
  assert.equal(await reconcile(f.ctx,f.state,{task_id:task}),false);
  f.state.inflight[task].reconcile_checked_ms=0;
  f.ctx.cliCall=async req=>req.op==='view'?{confirmed:true}:{acked:true};
  assert.equal(await reconcile(f.ctx,f.state,{task_id:task}),true);
  assert.equal(f.state.inflight[task],undefined);n++;
  console.log('STAGE33I_NODE_RECONCILE_'+n+'_TESTS_OK');
})().catch(e=>{console.error('STAGE33I_NODE_TEST_FAILED',e.message);process.exitCode=2});
