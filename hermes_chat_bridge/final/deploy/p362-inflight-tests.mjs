// P36.2 offline regression tests: no real Git, no network, no MT SDK prompts.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { runTick, diagnosticContext } from './mt-git-inbox-p36.mjs';

const ID = 'task_' + 'a'.repeat(32);
const HEAD = 'b'.repeat(40);
const RESERVED = { seq: 23, message_id: 'chatgpt_seq23', phase: 'prompt_attempted' };
const REQUEST = { seq: 23, message_id: RESERVED.message_id, from: 'chatgpt', to: 'mt', kind: 'task' };
const REPLY = { seq: 24, message_id: 'mt_seq24', from: 'mt', to: 'chatgpt', kind: 'report' };

async function scenario({
  history=[REQUEST,REPLY], head=HEAD, taskId=ID, totalSeq=24,
  waitingFor='chatgpt', readFails=false, initialSeen=17,
  afterReply=null, dryRun=false,
}={}) {
  const directory=mkdtempSync(join(tmpdir(),'p362-inflight-'));
  const events=[], calls=[];
  const state={sessions:{[ID]:'ses_bound'},seen:{[ID]:initialSeen},
               inflight:{[ID]:{...RESERVED}}};
  writeFileSync(join(directory,'state.json'),JSON.stringify(state));
  const cliCall=async req=>{
    calls.push({op:req.op,limit:req.limit});
    if(req.op==='list_dialogs')
      return {dialogs:[{task_id:ID,lifecycle:'active'}],next_offset:null};
    if(req.op==='view') {
      if(readFails) throw new Error('simulated_verified_Git_fetch_failure');
      if(req.limit===100) return {task_id:taskId,head,seq:totalSeq,
                                      waiting_for:waitingFor,history};
      return {task_id:taskId,head,seq:totalSeq,waiting_for:waitingFor,
              history:afterReply ? [afterReply] : history.slice(-1)};
    }
    if(req.op==='read') {
      // A later task remains eligible for delivery, but this offline test
      // deliberately refuses its body to avoid invoking a mocked SDK.
      if(afterReply) throw new Error('simulated_new_task_body_unavailable');
      return {...REPLY,body:'report'};
    }
    throw new Error('unexpected op');
  };
  let prompts=0;
  const client={session:{create:async()=>{prompts++;throw Error('no_create');},
                          prompt:async()=>{prompts++;throw Error('no_prompt');}}};
  const ctx=diagnosticContext({stateDir:directory,cliCall,client,log:s=>events.push(s),
     dryRun, sessionDirectory:()=>directory});
  try{
    const first=await runTick(ctx);
    const persisted=JSON.parse(readFileSync(join(directory,'state.json'),'utf8'));
    return {first,persisted,events,calls,prompts};
  } finally {
    rmSync(directory,{recursive:true,force:true});
  }
}

test('exact validated next MT reply clears held inflight and persists seen',async()=>{
  const r=await scenario();
  assert.equal(r.first.status,'ok');
  assert.equal(r.first.held,0);
  assert.equal(r.first.cleared,1);
  assert.equal(r.first.dispatched,0);
  assert.equal(r.persisted.seen[ID],23);
  assert.equal(r.persisted.inflight[ID],undefined);
  assert.equal(r.prompts,0);
  assert.ok(r.events.some(x=>x.includes('inflight_reconciled_confirmed_response')));
});

test('held prompt remains held without confirmed reply',async()=>{
  const r=await scenario({history:[REQUEST],totalSeq:23,waitingFor:'mt'});
  assert.equal(r.first.held,1);
  assert.equal(r.first.cleared,0);
  assert.equal(r.persisted.seen[ID],17);
  assert.equal(r.persisted.inflight[ID].seq,23);
  assert.equal(r.prompts,0);
});

test('unrelated or wrong-seq reply never releases reservation',async()=>{
  for(const reply of [
    {...REPLY,seq:25},
    {...REPLY,from:'chatgpt',to:'mt'},
    {...REPLY,kind:'return'},
    {...REPLY,seq:24,message_id:'fake',from:'chatgpt'},
  ]) {
    const r=await scenario({history:[REQUEST,reply],totalSeq:25});
    assert.equal(r.first.held,1);
    assert.equal(r.persisted.inflight[ID].seq,23);
    assert.equal(r.prompts,0);
  }
});

test('wrong request message ID, task or missing valid SHA fail closed',async()=>{
  for(const options of [
    {history:[{...REQUEST,message_id:'other'},REPLY]},
    {taskId:'task_'+'c'.repeat(32)},
    {head:'not-a-commit'},
  ]) {
    const r=await scenario(options);
    assert.equal(r.first.held,1);
    assert.equal(r.persisted.inflight[ID].seq,23);
  }
});

test('network uncertainty and truncated history never release hold',async()=>{
  const fail=await scenario({readFails:true});
  assert.equal(fail.first.held,1);
  const missing=await scenario({history:[REPLY],totalSeq:200});
  assert.equal(missing.first.held,1);
});

test('reconcile does not skip a later ChatGPT task',async()=>{
  const next={seq:25,message_id:'chat25',from:'chatgpt',to:'mt',kind:'task'};
  const r=await scenario({history:[REQUEST,REPLY,next],totalSeq:25,
                          waitingFor:'mt',afterReply:next});
  assert.equal(r.first.cleared,1);
  assert.equal(r.persisted.seen[ID],23);
  assert.equal(r.prompts,0);
  assert.ok(r.calls.some(x=>x.op==='read'));
  assert.ok(r.events.some(x=>x.includes('direct_git_read_failed '+ID)));
});


test('dry_run never clears or acknowledges inflight even with confirmed Git reply',async()=>{
  const r=await scenario({dryRun:true});
  assert.equal(r.first.status,'ok');
  assert.equal(r.first.cleared,0);
  assert.equal(r.first.held,1);
  assert.equal(r.persisted.seen[ID],17);
  assert.deepEqual(r.persisted.inflight[ID],RESERVED);
  assert.equal(r.prompts,0);
  assert.equal(r.calls.some(x=>x.op==='view'),false);
});

test('dry_run does not advance the persistent check throttle',async()=>{
  const r=await scenario({dryRun:true,history:[REQUEST],totalSeq:23,
                            waitingFor:'mt'});
  assert.deepEqual(r.persisted.inflight[ID],RESERVED);
  assert.equal(r.calls.some(x=>x.op==='view'),false);
});
