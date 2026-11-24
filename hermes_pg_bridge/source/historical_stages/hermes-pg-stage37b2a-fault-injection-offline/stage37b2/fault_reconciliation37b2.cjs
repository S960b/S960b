#!/usr/bin/env node
'use strict';
/* Offline fault injection against exact installed managed plugin source.
 * No eval of plugin top-level code, imports, FS operations, SDK or DB access.
 * Only three pure/runtime functions are transplanted into a VM with mocked dependencies.
 */
const fs = require('node:fs');
const crypto = require('node:crypto');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const PATH = '/home/kali/hermes-pg-stage34eb-pilot-candidate/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs';
const PIN = 'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730';
const TASK = 'task_b6c26aa18220412bafdbf32393f16568';
const MID = 'pgv2-stage36-task-20261123-b6c26aa1-v1';
const REPORT_MID = 'pgv2-mg-' + TASK.slice(5) + '-1-report';
const TOKEN = '123e4567-e89b-42d3-a456-426614174000';

function segment(src, begin, finish) {
  assert.equal(src.split(begin).length, 2, 'source begin anchor must be unique: '+begin);
  const from = src.indexOf(begin);
  const to = src.indexOf(finish, from + begin.length);
  assert(to > from, 'missing end anchor '+finish);
  return src.slice(from, to).trim() + '\n';
}
function functionsFromSource(src) {
  const prefix = segment(src, 'function managedReportId(', 'function buildPrompt(');
  const verified = segment(src, 'function confirmedMtResponseAfter(', 'async function reconcileAnsweredInflight(');
  const reconcile = segment(src, 'async function reconcileAnsweredInflight(', 'export async function runTick(');
  const tickStart = src.indexOf('export async function runTick(');
  assert(tickStart >= 0 && src.indexOf('export async function runTick(', tickStart+1) === -1);
  const endNeedle = '    releaseLock(ctx.paths, holder);\n  }\n}';
  const tickEnd = src.indexOf(endNeedle, tickStart);
  assert(tickEnd > tickStart, 'runTick tail not recognized');
  const tick = src.slice(tickStart, tickEnd + endNeedle.length).replace(/^export /,'');
  const actual = prefix + verified + reconcile + tick + '\nthis.runTick = runTick; this.confirmedMtResponseAfter = confirmedMtResponseAfter;';
  assert(actual.includes('state.inflight[taskId]'));
  assert(actual.includes('managed_inflight_ack_confirmed'));
  assert(actual.includes('await reconcileAnsweredInflight'));
  return actual;
}

const bytes = fs.readFileSync(PATH);
assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), PIN, 'candidate changed; do not test unpinned code');
const source = bytes.toString('utf8');
const functions = functionsFromSource(source);

function copy(v) { return JSON.parse(JSON.stringify(v)); }
function data() {
  return {
    seen: {},
    inflight: { [TASK]: {seq:1,message_id:MID,delivery_id:17,lease_token:TOKEN} },
  };
}
function successfulProof() {
  return {
    task_id:TASK, seq:2,
    history:[
      {seq:1, message_id:MID, from:'chatgpt', to:'mt', kind:'task'},
      {seq:2, message_id:REPORT_MID, from:'mt', to:'chatgpt', kind:'report'},
    ],
  };
}
function fixture(opts={}) {
  let durable = copy(opts.state || data());
  let fakeNow = opts.clockStart || 1_000_000;
  let ackCalls=0, sdkCalls=0, cliCalls=0, saves=0;
  const logs=[];
  const commands=[];
  const scope = {
    Date:{now:()=>fakeNow},
    INFLIGHT_RECONCILE_MS:30_000,
    MAX_DISPATCH_PER_TICK:1,
    acquireLock:()=>({lock:'synthetic'}),
    releaseLock:()=>{},
    loadState:()=>copy(durable),
    saveState:(_p,state)=>{saves++;durable=copy(state);},
    listAll:async()=> opts.dialogs || [],
    latestPending:()=> null,
    dispatch:async()=>{sdkCalls++;throw new Error('mock_sdk_must_never_run');},
  };
  const context=vm.createContext(scope,{codeGeneration:{strings:false,wasm:false}});
  new vm.Script(functions,{filename:'extracted-pinned-managed-functions.js'}).runInContext(context,{timeout:2000});
  async function tick() {
    const ctx={paths:{},dryRun:!!opts.dryRun,log:(s)=>logs.push(s),cliCall:async(a)=>{
      cliCalls++;commands.push(a.op);
      if(a.op==='view') {
        if(opts.unavailable) throw Error('simulated transient DB unavailable');
        if(a.limit===1) return opts.first || {seq:opts.unanswered?1:2};
        if(a.limit===2) return opts.proof || successfulProof();
      }
      if(a.op==='ack'){
        ackCalls++;
        if(typeof opts.ack==='function') return opts.ack(ackCalls);
        return opts.ack || {acked:true};
      }
      throw Error('unexpected CLI method: '+a.op);
    }};
    return await context.runTick(ctx);
  }
  return {tick,advance:(ms=31_000)=>{fakeNow+=ms;},
    get durable(){return copy(durable)},get ackCalls(){return ackCalls},
    get sdkCalls(){return sdkCalls},get cliCalls(){return cliCalls},get saves(){return saves},logs,commands};
}

async function main() {
  const suite=[];
  const test=(name,fn)=>suite.push([name,fn]);
  test('valid report + lease ACK clears inflight exactly once',async()=>{
    const f=fixture();const r=await f.tick();assert.equal(r.cleared,1);
    assert.equal(f.ackCalls,1);assert.equal(f.durable.seen[TASK],1);
    assert.deepEqual(f.durable.inflight,{});assert.equal(f.sdkCalls,0);
  });
  test('reboot after ACK leaves no inflight',async()=>{
    const f=fixture();await f.tick();const newProcess=fixture({state:f.durable});
    const r=await newProcess.tick();assert.equal(r.cleared,0);
    assert.equal(newProcess.ackCalls,0);assert.equal(newProcess.sdkCalls,0);
  });
  test('no report means reservation remains after tick',async()=>{
    const f=fixture({unanswered:true});const r=await f.tick();assert.equal(r.held,1);
    assert(f.durable.inflight[TASK]);assert.equal(f.ackCalls,0);
  });
  test('no report remains held across simulated process restart',async()=>{
    const f=fixture({unanswered:true});await f.tick();
    const again=fixture({state:f.durable,unanswered:true});await again.tick();
    assert(again.durable.inflight[TASK]);assert.equal(again.sdkCalls,0);
  });
  test('first view unavailable preserves inflight',async()=>{
    const f=fixture({unavailable:true});await f.tick();
    assert(f.durable.inflight[TASK]);assert.equal(f.ackCalls,0);
  });
  test('transient DB failure then reboot with valid proof ACKs once',async()=>{
    const f=fixture({unavailable:true});await f.tick();
    const again=fixture({state:f.durable,clockStart:1_031_000});await again.tick();
    assert.equal(again.ackCalls,1);assert.deepEqual(again.durable.inflight,{});
  });
  test('incorrect report message id rejects proof and preserves lease',async()=>{
    const bad=successfulProof();bad.history[1].message_id='other-report';
    const f=fixture({proof:bad});await f.tick();
    assert(f.durable.inflight[TASK]);assert.equal(f.ackCalls,0);
  });
  test('inconsistent pagination rejects proof',async()=>{
    const bad=successfulProof();bad.seq=3;
    const f=fixture({proof:bad});await f.tick();
    assert(f.durable.inflight[TASK]);assert.equal(f.ackCalls,0);
  });
  test('invalid lease token holds, not ACK',async()=>{
    const x=data();x.inflight[TASK].lease_token='not-a-uuid';
    const f=fixture({state:x});await f.tick();
    assert(f.durable.inflight[TASK]);assert.equal(f.ackCalls,0);
  });
  test('missing delivery id holds',async()=>{
    const x=data();delete x.inflight[TASK].delivery_id;
    const f=fixture({state:x});await f.tick();
    assert(f.durable.inflight[TASK]);assert.equal(f.ackCalls,0);
  });
  test('ACK rejection holds, not silently delivered',async()=>{
    const f=fixture({ack:{acked:false}});await f.tick();
    assert(f.durable.inflight[TASK]);assert.equal(f.ackCalls,1);
  });
  test('ACK exception after database commit: retry via same lease succeeds',async()=>{
    let committed=false;
    const ack=(call)=>{if(call===1){committed=true;throw Error('lost response');}
                        assert(committed);return {acked:true,already_delivered:true};};
    const f=fixture({ack});const before=await f.tick();assert.equal(before.held,1);
    assert(f.durable.inflight[TASK]);f.advance();
    const after=await f.tick();assert.equal(after.cleared,1);
    assert.equal(f.ackCalls,2);assert.equal(f.sdkCalls,0);
    assert.deepEqual(f.durable.inflight,{});
  });
  test('no premature retry within reconciliation throttle',async()=>{
    const f=fixture({ack:{acked:false}});await f.tick();
    await f.tick();assert.equal(f.ackCalls,1);
    f.advance();await f.tick();assert.equal(f.ackCalls,2);
  });
  test('dry run retains inflight without ACK',async()=>{
    const f=fixture({dryRun:true});await f.tick();assert(f.durable.inflight[TASK]);
    assert.equal(f.ackCalls,0);
  });
  test('dialogue absent from dispatch list still reconciled',async()=>{
    const f=fixture({dialogs:[]});await f.tick();assert.equal(f.ackCalls,1);
    assert.deepEqual(f.durable.inflight,{});
  });
  test('active dispatch list with inflight never calls SDK',async()=>{
    const f=fixture({dialogs:[{task_id:TASK,lifecycle:'active'}],unanswered:true});await f.tick();
    assert.equal(f.sdkCalls,0);assert(f.durable.inflight[TASK]);
  });
  test('recovered reservation with wrong task ID does not ACK',async()=>{
    const x={seen:{},inflight:{'wrong-id':data().inflight[TASK]}};
    const f=fixture({state:x});await f.tick();assert.equal(f.ackCalls,0);
    assert(f.durable.inflight['wrong-id']);
  });
  test('report for different task does not ACK',async()=>{
    const bad=successfulProof();bad.task_id='task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const f=fixture({proof:bad});await f.tick();assert.equal(f.ackCalls,0);
    assert(f.durable.inflight[TASK]);
  });
  test('stale local cursor does not bypass existing reservation',async()=>{
    const x=data();x.seen[TASK]=0;
    const f=fixture({state:x,dialogs:[{task_id:TASK}]});await f.tick();
    assert.equal(f.ackCalls,1);assert.equal(f.sdkCalls,0);
  });
  test('reconciliation is never allowed to change actual disk or DB',async()=>{
    const f=fixture({unanswered:true});await f.tick();
    assert.deepEqual(f.commands,['view']);assert.equal(f.sdkCalls,0);
  });
  let count=0;
  for(const [name,fn] of suite) {
    try{await fn();++count;console.log('STAGE37B2_CASE_OK '+name.replace(/[^a-z0-9]+/gi,'_'));}
    catch(e){console.error('STAGE37B2_STOP_CASE_'+name.replace(/[^a-z0-9]+/gi,'_')+': '+e.message);process.exitCode=2;return;}
  }
  console.log('STAGE37B2_BEHAVIORAL_CASES_PASSED '+count);
  console.log('STAGE37B2_PINNED_PLUGIN_FUNCTIONS_ONLY_NO_SDK_OR_DB_CONNECTIONS');
  console.log('STAGE37B2_KNOWN_LIMIT_CLAIM_BEFORE_DURABLE_INFLIGHT_NOT_COVERED');
}
main().catch(e=>{console.error('STAGE37B2_STOP_'+e.message);process.exitCode=2});
