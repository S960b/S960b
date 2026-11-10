import assert from 'node:assert/strict';
import test from 'node:test';
import { assistantText, observedModel, runPairOnce } from './news-p4-v2-helpers.mjs';

const EID='a'.repeat(64);
const ATTEMPT1='b'.repeat(64);
const ATTEMPT2='c'.repeat(64);

const batch={status:'ready',event_id:EID,arms:[
  {attempt_id:ATTEMPT1,arm:'news',prompt:'NEWS-only text'},
  {attempt_id:ATTEMPT2,arm:'news_market',prompt:'NEWS plus market text'}
]};

function setup({stale=false,missingModel=false}={}) {
  const calls=[];
  let next=0;
  const runner=async (command,args=[],stdin='')=>{
    calls.push({command,args,stdin});
    if (command==='prepare') return batch;
    if (command==='quote') return {status:'entry_recorded'};
    return {status:command==='finish'?'completed':'ok'};
  };
  const client={session:{
    create:async ()=>{
      next++;
      return {data:{id:'v2session_'+next}};
    },
    prompt:async ()=>{
      if (stale) throw Error('gateway_timeout_ambiguous');
      return {data:{info:{role:'assistant',
                          ...(missingModel?{}:{
                            model:{providerID:'deepseek',
                                   modelID:'deepseek-v4-flash'}})},
        parts:[{type:'text',text:JSON.stringify({
          p_up:0.6,expected_return_bps:60,uncertainty:0.4,
          rationale:'synthetic'})}]}};
    }
  }};
  return {calls,runner,client};
}

test('model calls disabled by default',async()=>{
  const x=setup();
  const result=await runPairOnce({
    client:x.client,directory:'/tmp',runner:x.runner});
  assert.equal(result.status,'disabled');
  assert.equal(x.calls.length,0);
});

test('paired proof-of-concept uses durable session and prompt handshakes',async()=>{
  const x=setup();
  const result=await runPairOnce({
    client:x.client,directory:'/tmp',runner:x.runner,
    dryRun:false,modelEnabled:true,dailyCap:4});
  assert.equal(result.status,'v2_pair_recorded');
  assert.deepEqual(x.calls.map(x=>x.command),
    ['prepare','session','prompt','finish','session','prompt','finish','quote']);
  assert.ok(x.calls.filter(x=>x.command==='finish')
    .every(x=>JSON.parse(x.stdin).model.providerID==='deepseek'));
  assert.ok(x.calls.find(x=>x.command==='quote')
    .args.includes('--enable-public-quote'));
});

test('no ambiguous gateway retry or second arm on error',async()=>{
  const x=setup({stale:true});
  await assert.rejects(runPairOnce({
    client:x.client,directory:'/tmp',runner:x.runner,
    dryRun:false,modelEnabled:true,dailyCap:4}),/gateway_timeout_ambiguous/);
  assert.deepEqual(x.calls.map(c=>c.command),
    ['prepare','session','prompt']);
});

test('reject unverified model and never finalize',async()=>{
  const x=setup({missingModel:true});
  await assert.rejects(runPairOnce({
    client:x.client,directory:'/tmp',runner:x.runner,
    dryRun:false,modelEnabled:true,dailyCap:4}),/v2_unconfirmed_model_result_no_retry/);
  assert.equal(x.calls.filter(c=>c.command==='finish').length,0);
});

test('reject invalid daily cap before prepare',async()=>{
  const x=setup();
  await assert.rejects(runPairOnce({
    client:x.client,directory:'/tmp',runner:x.runner,
    dryRun:false,modelEnabled:true,dailyCap:3}),/v2_invalid_daily_cap/);
  assert.equal(x.calls.length,0);
});

test('extract assistant text and verified identity',()=>{
  const data={data:{info:{role:'assistant',model:{
    providerID:'deepseek',modelID:'deepseek-v4-flash'}},
    parts:[{type:'text',text:'{"p_up":0.5}'}]}};
  assert.equal(assistantText(data),'{"p_up":0.5}');
  assert.deepEqual(observedModel(data),{
    providerID:'deepseek',modelID:'deepseek-v4-flash'});
});

test('reject contradictory assistant model identities',()=>{
  const x={data:[{info:{role:'assistant',model:{
    providerID:'aA',modelID:'modelA'}}},
   {info:{role:'assistant',model:{
    providerID:'aA',modelID:'modelB'}}}]};
  assert.throws(()=>observedModel(x),/v2_model_identity_conflict/);
});
