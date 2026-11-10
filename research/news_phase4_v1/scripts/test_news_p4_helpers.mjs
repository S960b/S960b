import test from 'node:test';
import assert from 'node:assert/strict';
import {runPairOnce,assistantText,observedModel} from './news-p4-helpers.mjs';

const E='a'.repeat(64);
const A='b'.repeat(64);
const B='c'.repeat(64);
function assistant(text,providerID='gateway',modelID='deepseek') {
  return {data:{info:{role:'assistant',model:{providerID,modelID}},
    parts:[{type:'text',text}]}};
}
function make() {
  const calls=[];
  let sessions=0;
  const runner=async (command,args=[],input='')=>{
    calls.push(['python',command]);
    if(command==='prepare')return {status:'ready',event_id:E,
      arms:[{arm:'news',attempt_id:A,prompt:'news data'},
            {arm:'news_market',attempt_id:B,prompt:'news market data'}]};
    if(command==='session')return {status:'session_bound'};
    if(command==='prompt')return {status:'prompt_attempted'};
    if(command==='finalize'){
      const value=JSON.parse(input);
      assert.equal(value.model.providerID,'gateway');
      return {status:'completed'};
    }
    if(command==='quote')return {status:'entry_recorded'};
    throw Error('unknown_command');
  };
  const client={session:{
    create:async()=>{sessions++;calls.push(['sdk','create']);return {data:{id:'session_000'+sessions}};},
    prompt:async()=>{calls.push(['sdk','prompt']);return assistant(
      '{"direction":"UP","confidence":0.5,"rationale":"Test"}');},
  }};
  return {client,runner,calls};
}
test('disabled by default, zero model calls',async()=>{
  const f=make();
  const out=await runPairOnce({...f,directory:'/tmp/p4'});
  assert.equal(out.status,'dry_run_or_disabled');
  assert.equal(f.calls.length,0);
});
test('explicit N/NM requires two reservations and exactly two prompts',async()=>{
  const f=make();
  const result=await runPairOnce({...f,directory:'/tmp/p4',
    modelEnabled:true,dryRun:false,dailyCap:2});
  assert.equal(result.status,'paired_entry_recorded');
  assert.deepEqual(f.calls.map(x=>x.join(':')),[
    'python:prepare','sdk:create','python:session','python:prompt',
    'sdk:prompt','python:finalize','sdk:create','python:session',
    'python:prompt','sdk:prompt','python:finalize','python:quote']);
});
test('unknown prompt is never automatically replayed',async()=>{
  const f=make();let num=0;
  f.client.session.prompt=async()=>{num++;throw Error('SDK_unknown');};
  await assert.rejects(runPairOnce({...f,directory:'/tmp/p4',
    modelEnabled:true,dryRun:false,dailyCap:2}));
  assert.equal(num,1);
  assert.equal(f.calls.some(([x,y])=>y==='finalize'),false);
});
test('unverified gateway model fails closed',async()=>{
  const f=make();
  f.client.session.prompt=async()=>({data:{parts:[{type:'text',text:'{"direction":"UP"}'}]}});
  await assert.rejects(runPairOnce({...f,directory:'/tmp/p4',
    modelEnabled:true,dryRun:false,dailyCap:2}),/model_identity_unverified/);
  assert.equal(f.calls.some(([x,y])=>y==='quote'),false);
});
test('SDK unavailable blocks before reservation',async()=>{
  let called=false;
  await assert.rejects(runPairOnce({client:{session:{}},directory:'/tmp/p4',
    runner:async()=>{called=true;},modelEnabled:true,dryRun:false,dailyCap:2}),
    /session_sdk_unavailable/);
  assert.equal(called,false);
});
test('invalid budget blocks before reservation',async()=>{
  const f=make();
  await assert.rejects(runPairOnce({...f,directory:'/tmp/p4',
    modelEnabled:true,dryRun:false,dailyCap:30}),/invalid_p4_budget/);
  assert.equal(f.calls.length,0);
});
test('assistant text and model from assistant metadata only',()=>{
  const r=assistant('{"direction":"UP"}');
  assert.equal(assistantText(r),'{"direction":"UP"}');
  assert.deepEqual(observedModel(r),{providerID:'gateway',modelID:'deepseek'});
  assert.equal(observedModel({data:{info:{role:'user',model:{providerID:'gateway',modelID:'bad'}}}}),null);
});
test('conflicting assistant model identity blocks',()=>{
  assert.throws(()=>observedModel({data:{messages:[
    {info:{role:'assistant',model:{providerID:'p1',modelID:'m1'}}},
    {info:{role:'assistant',model:{providerID:'p2',modelID:'m1'}}},
  ]}}),/model_identity_conflict/);
});
test('bad Python pair prevents SDK sessions',async()=>{
  const f=make();
  f.runner=async()=>({status:'ready',event_id:E,arms:[]});
  await assert.rejects(runPairOnce({...f,directory:'/tmp/p4',
    modelEnabled:true,dryRun:false,dailyCap:2}),/invalid_pair_plan/);
  assert.equal(f.calls.length,0);
});
