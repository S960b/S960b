'use strict';
// Pure offline behavior tests: no database, SDK, plugin registration or network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const parts = fs.readFileSync(path.join(__dirname,'parts34e.js'),'utf8');
function section(name) {
  const start='// BEGIN STAGE34E '+name+'\n';
  const end='\n// END STAGE34E '+name;
  assert.equal(parts.split(start).length,2);
  return parts.split(start)[1].split(end)[0];
}
const script = [section('PROMPT'),section('CONFIRMED'),section('RECONCILE'),
                section('TICK').replace('export async function','async function'),
                'globalThis.exports34e={managedReportId,buildPrompt,confirmedMtResponseAfter,reconcileAnsweredInflight,runTick};'].join('\n');
const tid = 'task_'+'a'.repeat(32);
const mid = 'original-task-v1';
const lease = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const fresh = () => ({seq:1,message_id:mid,delivery_id:22,lease_token:lease});
function ctxFactory(opts={}) {
  const state=opts.state ?? {seen:{},sessions:{},inflight:{[tid]:fresh()}};
  const calls=[], logs=[], saved=[];
  const sandbox={
    Date, Number, RegExp, Object, String, Promise, Error, Math,
    INFLIGHT_RECONCILE_MS:0, MAX_DISPATCH_PER_TICK:2,
    acquireLock:()=>({pid:123}),releaseLock:()=>{},loadState:()=>state,
    saveState:()=>saved.push(JSON.stringify(state)),
    listAll:async()=>opts.list??[],
    latestPending:()=>opts.pending??null,
    dispatch:async()=>{throw new Error('SDK dispatch MUST NOT be called in these tests');},
  };
  vm.runInNewContext(script,sandbox,{timeout:3000});
  const reportId=sandbox.exports34e.managedReportId(tid,1);
  const proof=()=>({task_id:tid,seq:opts.currentSeq??2,history:[
    {seq:2,message_id:opts.reportMid??reportId,from:'mt',to:'chatgpt',kind:'report'},
    {seq:1,message_id:mid,from:'chatgpt',to:'mt',kind:'task'}]});
  const pg={
    task_id:tid,seq:opts.currentSeq??2,history:[{seq:2,message_id:reportId,from:'mt',to:'chatgpt',kind:'report'}]
  };
  const ctx={paths:{},dryRun:opts.dryRun??false,log:(msg)=>logs.push(msg),
    cliCall:async(request)=>{
      calls.push(request);
      if (opts.throwView && request.op==='view') throw new Error('unavailable');
      if (request.op==='view') return request.limit===1 ? pg : proof();
      if (request.op==='ack') {
        if (opts.ackThrows) throw new Error('lost ack response');
        return {acked:opts.acked??true};
      }
      if (request.op==='read') return opts.readResult??{seq:3,message_id:'new-task',
        from:'chatgpt',to:'mt',kind:'task',body:'hello'};
      return {};
    }
  };
  return {ctx,state,calls,logs,saved,impl:sandbox.exports34e};
}

test('managed report IDs deterministic, per-dialog and under 64 characters',()=>{
  const {impl}=ctxFactory();
  const a=impl.managedReportId(tid,1);
  assert.match(a,/^pgv2-mg-[0-9a-f]{32}-1-report$/);
  assert.ok(a.length<=64);
  assert.notEqual(a,impl.managedReportId(tid,2));
  assert.notEqual(a,impl.managedReportId('task_'+'b'.repeat(32),1));
  assert.throws(()=>impl.managedReportId('not-task',1));
});

test('prompt has exact managed CLI and does not require old canary ACK',()=>{
  const {impl}=ctxFactory();
  const prompt=impl.buildPrompt({task_id:tid,title:'test'}, {seq:1,message_id:mid,body:'test task'});
  assert.match(prompt,/managed_agent_cli34d\.py/);
  assert.match(prompt,/--consumer mt-pg-managed/);
  assert.match(prompt,/pgv2-mg-/);
  assert.match(prompt,/--kind report/);
  assert.doesNotMatch(prompt,/PGV2_ROLE_BOUND_MT_SDK_ACK_20261122/);
});

test('reconciliation works when new dispatch list is EMPTY',async()=>{
  const {ctx,state,calls,impl}=ctxFactory({list:[]});
  const out=await impl.runTick(ctx);
  assert.equal(out.cleared,1);
  assert.equal(state.inflight[tid],undefined);
  assert.equal(state.seen[tid],1);
  assert.equal(calls.filter(c=>c.op==='ack').length,1);
  assert.equal(calls.find(c=>c.op==='ack').task_id,tid);
  assert.equal(calls.find(c=>c.op==='ack').seq,1);
});

test('unconfirmed ACK leaves reservation and does not set seen',async()=>{
  const {ctx,state,impl}=ctxFactory({acked:false});
  const out=await impl.runTick(ctx);
  assert.equal(out.held,1);
  assert.ok(state.inflight[tid]);
  assert.equal(state.seen[tid],undefined);
});

test('lost ACK response keeps inflight; later delivered ACK completes without SDK',async()=>{
  const f=ctxFactory({ackThrows:true});
  await f.impl.runTick(f.ctx);
  assert.ok(f.state.inflight[tid]);
  f.ctx.cliCall=async x=>{
    if(x.op==='view') {
      const p=f.impl.managedReportId(tid,1);
      return x.limit===1?{task_id:tid,seq:2,history:[{seq:2,message_id:p}]}:
        {task_id:tid,seq:2,history:[
          {seq:2,message_id:p,from:'mt',to:'chatgpt',kind:'report'},
          {seq:1,message_id:mid,from:'chatgpt',to:'mt',kind:'task'}]};
    }
    if(x.op==='ack')return {acked:true,already_delivered:true};
    return {};
  };
  await f.impl.runTick(f.ctx);
  assert.equal(f.state.seen[tid],1);
  assert.equal(f.state.inflight[tid],undefined);
});

test('wrong report message ID must never trigger ACK',async()=>{
  const f=ctxFactory({reportMid:'unexpected-report'});
  await f.impl.runTick(f.ctx);
  assert.ok(f.state.inflight[tid]);
  assert.equal(f.calls.filter(x=>x.op==='ack').length,0);
});

test('view denied after tombstone holds durable inflight, never purges',async()=>{
  const f=ctxFactory({throwView:true});
  await f.impl.runTick(f.ctx);
  assert.ok(f.state.inflight[tid]);
  assert.equal(f.calls.filter(x=>x.op==='ack').length,0);
});

test('no answer yet means no ack or SDK repeat',async()=>{
  const f=ctxFactory({currentSeq:1});
  await f.impl.runTick(f.ctx);
  assert.ok(f.state.inflight[tid]);
  assert.equal(f.calls.filter(x=>x.op==='ack').length,0);
});

test('missing lease token prevents ACK and keeps inflight',async()=>{
  const state={sessions:{},seen:{},inflight:{[tid]:{seq:1,message_id:mid,delivery_id:22}}};
  const f=ctxFactory({state});
  await f.impl.runTick(f.ctx);
  assert.ok(state.inflight[tid]);
  assert.equal(f.calls.filter(x=>x.op==='ack').length,0);
});

test('dryrun holds outstanding inflight and never ACKs or starts SDK',async()=>{
  const f=ctxFactory({dryRun:true,list:[]});
  const out=await f.impl.runTick(f.ctx);
  assert.equal(out.held,1);
  assert.ok(f.state.inflight[tid]);
  assert.equal(f.calls.length,0);
});

test('dryrun can inspect new work but cannot dispatch',async()=>{
  const newTask='task_'+'b'.repeat(32);
  const f=ctxFactory({dryRun:true,state:{sessions:{},seen:{},inflight:{}},
   list:[{task_id:newTask,lifecycle:'active',title:'queued'}],
   pending:{seq:3,message_id:'new-task'}});
  const out=await f.impl.runTick(f.ctx);
  assert.equal(out.dispatched,0);
  assert.equal(f.calls.filter(x=>x.op==='read').length,1);
});

test('CLI maps ACK and claim with mandatory task/seq and managed consumer',async()=>{
  let captured=[];
  const source=section('CLI');
  const fakeSpawn=(py,argv,options)=>{
    captured.push({py,argv,options});
    const proc=new EventEmitter();
    proc.stdout=new EventEmitter();proc.stderr=new EventEmitter();
    proc.kill=()=>{};
    setImmediate(()=>{proc.stdout.emit('data',Buffer.from('{"acked":true}\n'));proc.emit('close',0)});
    return proc;
  };
  const sandbox={spawn:fakeSpawn, CODE:'/tmp/test',PYTHON:'/fake/python',CLI_TIMEOUT_MS:2000,
   setTimeout,clearTimeout,process:{env:{APPDIR:'bad',APPIMAGE:'bad',SAFE:'yes'}},Promise};
  vm.runInNewContext(source+'\nglobalThis.testCli=cli;',sandbox,{timeout:3000});
  const ack=await sandbox.testCli({op:'ack',task_id:tid,seq:1,delivery_id:22,lease_token:lease},1000);
  assert.equal(ack.acked,true);
  const cmd=captured[0].argv;
  assert.ok(cmd.includes('--task-id') && cmd.includes(tid));
  assert.ok(cmd.includes('--seq') && cmd.includes('1'));
  assert.ok(cmd.includes('--consumer') && cmd.includes('mt-pg-managed'));
  assert.ok(cmd[0].endsWith('/managed_agent_cli34d.py'));
  assert.equal(captured[0].options.env.APPDIR,undefined);
  assert.equal(captured[0].options.env.APPIMAGE,undefined);
});
