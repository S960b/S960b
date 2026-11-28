// Offline tests: fake MT SDK, no Binance/RSS, no model or user sessions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { runOnce, runAutoOnce, probeModel, resolvedModel, runLabels, slotFor } from './news-p3-helpers.mjs';
import { server } from './news-p3-plugin.mjs';

const SID='ses_test_123';
const BID='a'.repeat(24);
const MODEL={providerID:'deepseek',modelID:'deepseek-v4.1-flash'};
const ANSWER='{"direction":"UP","confidence":0.65,"rationale":"fixture"}';

function sdk({ model=true, text=true, fail=false } = {}) {
  const calls=[];
  const client={session:{
    create:async () => {calls.push('create');return {data:{id:SID}};},
    prompt:async () => {calls.push('prompt');if(fail)throw Error('SDK_UNKNOWN');
      return {data:{info:{role:'assistant',...(model?MODEL:{})},
        parts:text?[{type:'text',text:ANSWER}]:[]}};},
    messages:async () => {calls.push('messages');return {data:[]};}
  }};
  return {client,calls};
}
function pythonFixture(steps, outcome={status:'created',prediction_id:'pid',sampling_complete:false}) {
  return async (mode,args,stdin) => {
    steps.push({mode,args,stdin});
    if(mode==='prepare'||mode==='auto')return {status:'ready',batch_id:BID,prompt:'fixture JSON',news:1};
    if(mode==='finalize')return outcome;
    if(mode==='session')return {status:'session_created'};
    if(mode==='prompt')return {status:'prompt_attempted'};
    throw Error('unexpected_mode');
  };
}

test('plugin exports only default and server for legacy loader',async()=>{
  const mod=await import('./news-p3-plugin.mjs');
  assert.deepEqual(Object.keys(mod).sort(),['default','server']);
});

test('default plugin dry-run has no effects',async()=>{
  const {client,calls}=sdk();
  const handle=await server({client},{root:'/tmp',dry_run:true,
    trigger_file:'/tmp/unused-news-p3-run-91fe',probe_trigger_file:'/tmp/unused-news-p3-probe-91fe',
    log_file:'/tmp/news-p3-test-plugin.log',poll_ms:60000});
  assert.equal(typeof handle.dispose,'function');
  assert.deepEqual(calls,[]);
  await handle.dispose();
});

test('model extraction reads assistant info, not user or guessed label',()=>{
  assert.deepEqual(resolvedModel({data:{info:{role:'assistant',...MODEL}}}),MODEL);
  assert.equal(resolvedModel({data:{info:{role:'user',...MODEL}}}),null);
  assert.equal(resolvedModel({data:{info:{role:'assistant'}}}),null);
  assert.deepEqual(resolvedModel({data:[
    {info:{role:'user',...MODEL}},
    {info:{role:'assistant',...MODEL}}
  ]}),MODEL);
});

test('manual runner records actually observed model and one prompt',async()=>{
  const {client,calls}=sdk(),steps=[];
  const out=await runOnce({client,root:'/tmp',directory:'/tmp',dryRun:false,
    pythonRunner:pythonFixture(steps)});
  assert.equal(out.status,'created');
  assert.deepEqual(calls,['create','prompt']);
  assert.deepEqual(steps.map(x=>x.mode),['prepare','session','prompt','finalize']);
  assert.deepEqual(JSON.parse(steps[3].stdin).model,MODEL);
});

test('missing model identity remains pending: no finalize or retry',async()=>{
  const {client,calls}=sdk({model:false}),steps=[];
  await assert.rejects(runOnce({client,root:'/tmp',directory:'/tmp',dryRun:false,
    pythonRunner:pythonFixture(steps)}),/model_identity_unverified_manual_reconcile/);
  assert.deepEqual(calls,['create','prompt','messages']);
  assert.deepEqual(steps.map(x=>x.mode),['prepare','session','prompt']);
});

test('ambiguous SDK failure never repeats the prompt',async()=>{
  const {client,calls}=sdk({fail:true}),steps=[];
  await assert.rejects(runOnce({client,root:'/tmp',directory:'/tmp',dryRun:false,
    pythonRunner:pythonFixture(steps)}),/SDK_UNKNOWN/);
  assert.deepEqual(calls,['create','prompt']);
  assert.deepEqual(steps.map(x=>x.mode),['prepare','session','prompt']);
});

test('automatic runner does NOT stop on first valid forecast',async()=>{
  const {client,calls}=sdk(),steps=[];
  const out=await runAutoOnce({client,root:'/tmp',directory:'/tmp',dryRun:false,
    slotMs:1800000000000,pythonRunner:pythonFixture(steps)});
  assert.equal(out.sampling_complete,false);
  assert.deepEqual(calls,['create','prompt']);
  assert.deepEqual(JSON.parse(steps.at(-1).stdin).model,MODEL);
  assert.equal(steps.at(0).mode,'auto');
});

test('automatic stop gate never creates a session',async()=>{
  const {client,calls}=sdk();
  const out=await runAutoOnce({client,root:'/tmp',directory:'/tmp',dryRun:false,
    slotMs:1800000000000,pythonRunner:async()=>({status:'skip',reason:'auto_stopped_max_30_predictions'})});
  assert.equal(out.reason,'auto_stopped_max_30_predictions');
  assert.deepEqual(calls,[]);
});

test('dry-run skips all Python/session/model operations',async()=>{
  const {client,calls}=sdk();
  const out=await runAutoOnce({client,root:'/tmp',directory:'/tmp',dryRun:true,
    slotMs:1800000000000,pythonRunner:async()=>{throw Error('should_not_run');}});
  assert.equal(out.status,'dry_run');
  assert.deepEqual(calls,[]);
});

test('label runner passes explicit 5-minute slot and does not call SDK',async()=>{
  const calls=[];
  const out=await runLabels({root:'/tmp',slotMs:1800000000000,pythonRunner:async(mode,args)=>{
    calls.push({mode,args});return {status:'ok',study_complete:true};}});
  assert.equal(out.study_complete,true);
  assert.deepEqual(calls,[{mode:'labels',args:['--slot-ms','1800000000000']}]);
});

test('synthetic probe is opt-in and dry-run skips SDK',async()=>{
  const {client,calls}=sdk();
  const out=await probeModel({client,directory:'/tmp',dryRun:true});
  assert.equal(out.status,'dry_run');
  assert.deepEqual(calls,[]);
});

test('slotFor is stable across a 15-minute period',()=>{
  const t=1800000000000;
  assert.equal(slotFor(t),slotFor(t+1000));
  assert.notEqual(slotFor(t),slotFor(t+900000));
});
