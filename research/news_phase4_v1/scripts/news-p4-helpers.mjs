// NEWS Intelligence Phase 4 native MultiTool SDK adapter (helpers only).
// No raw response/model prompt logging. All quotas and durable state live in
// Python SQLite. This module makes no external model calls without BOTH
// explicit modelEnabled=true and dryRun=false. No direct AI API keys or trades.
import { spawn } from 'node:child_process';
import { isAbsolute, join } from 'node:path';

const MAX_OUT=42_000;
const MAX_PROMPT=13_000;
const SESSION=/^[A-Za-z0-9_-]{5,160}$/;
const ID=/^[a-f0-9]{64}$/;
const MODEL=/^[A-Za-z0-9_.-]{2,100}$/;

export function assistantText(response) {
  const root=response?.data ?? response;
  const list=Array.isArray(root) ? root : root?.messages;
  if (Array.isArray(list)) {
    for (let i=list.length-1; i>=0; i--) {
      const item=list[i];
      const info=item?.info ?? item?.message?.info;
      const role=info?.role ?? item?.role ?? item?.message?.role;
      if (role!=='assistant') continue;
      const text=assistantText(item);
      if (text) return text;
    }
    return null;
  }
  const candidates=[root,root?.message,root?.assistant];
  for (const item of candidates) {
    if (!item || typeof item!=='object') continue;
    const info=item?.info ?? item?.message?.info;
    const role=info?.role ?? item?.role;
    if (role && role!=='assistant') continue;
    const parts=item?.parts ?? item?.message?.parts;
    if (!Array.isArray(parts)) continue;
    const text=parts.filter(p=>p?.type==='text' && typeof p?.text==='string')
      .map(p=>p.text).filter(Boolean).join('\n').trim();
    if (text) return text;
  }
  return null;
}

function identityOf(item) {
  const root=item?.data ?? item;
  const info=root?.info ?? root?.message?.info;
  if (!info || info?.role!=='assistant') return null;
  const m=info?.model && typeof info.model==='object' ? info.model : info;
  if (typeof m.providerID!=='string' || typeof m.modelID!=='string' ||
      !MODEL.test(m.providerID) || !MODEL.test(m.modelID)) return null;
  return {providerID:m.providerID,modelID:m.modelID};
}
export function observedModel(response) {
  const root=response?.data ?? response;
  const list=Array.isArray(root)?root:root?.messages;
  if (Array.isArray(list)) {
    let verified=null;
    for (const item of list) {
      const m=identityOf(item);
      if (!m) continue;
      if (verified && (verified.providerID!==m.providerID || verified.modelID!==m.modelID))
        throw new Error('model_identity_conflict_manual_reconcile');
      verified=m;
    }
    return verified;
  }
  return identityOf(root);
}

export function pythonRunner(root, python='/usr/bin/python3') {
  if (!isAbsolute(root) || !isAbsolute(python)) throw new Error('invalid_runner_paths');
  const script=join(root,'p4_model_cycle.py');
  return async (command,args=[],input='') => {
    if (!['prepare','session','prompt','finalize','quote','status'].includes(command))
      throw new Error('invalid_cycle_command');
    if (!Array.isArray(args) || args.some(x=>typeof x!=='string' || x.length>200))
      throw new Error('invalid_cycle_args');
    return await new Promise((resolve,reject)=>{
      const child=spawn(python,[script,command,'--root',
            join(process.env.HOME ?? '/home/kali','.local/share/news-phase4'),...args],{
        cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},
        windowsHide:true,stdio:['pipe','pipe','pipe']
      });
      let output='',errors=0,finished=false;
      const timer=setTimeout(()=>{child.kill('SIGTERM');fail('python_timeout_unknown');},150_000);
      timer.unref?.();
      function fail(code) {
        if (finished) return;
        finished=true;clearTimeout(timer);reject(new Error(code));
      }
      child.on('error',()=>fail('python_start_failed'));
      child.stdout.on('data',buf=>{
        output+=String(buf);
        if (Buffer.byteLength(output,'utf8')>MAX_OUT) {
          child.kill('SIGTERM');fail('python_output_too_large');
        }
      });
      child.stderr.on('data',buf=>{errors+=buf.length;});
      child.stdin.on('error',()=>{});
      child.stdin.end(input);
      child.on('close',code=>{
        if (finished) return;
        finished=true;clearTimeout(timer);
        if (code!==0) return reject(new Error('python_phase4_failed'));
        try {
          const value=JSON.parse(output);
          if (!value || typeof value!=='object') throw Error('bad');
          resolve(value);
        } catch { reject(new Error('invalid_python_json')); }
      });
    });
  };
}

export async function runPairOnce({
  client,directory,runner,dryRun=true,modelEnabled=false,dailyCap=0,log=()=>{}
}) {
  if (dryRun || modelEnabled!==true)
    return {status:'dry_run_or_disabled'};
  if (typeof client?.session?.create!=='function' ||
      typeof client?.session?.prompt!=='function')
    throw new Error('session_sdk_unavailable');
  if (!isAbsolute(directory) || !Number.isInteger(dailyCap) ||
      dailyCap<2 || dailyCap>4) throw new Error('invalid_p4_budget');
  if (typeof runner!=='function') throw new Error('python_runner_unavailable');

  // This function reserves TWO attempts atomically and cannot be called twice
  // on the same event. It never re-issues an unknown prompt.
  const batch=await runner('prepare',['--enable-models','--daily-cap',String(dailyCap)]);
  if (batch?.status!=='ready') return batch;
  if (!ID.test(batch?.event_id) || !Array.isArray(batch.arms) ||
      batch.arms.length!==2 || new Set(batch.arms.map(a=>a.arm)).size!==2 ||
      !batch.arms.some(a=>a.arm==='news') ||
      !batch.arms.some(a=>a.arm==='news_market')) throw Error('invalid_pair_plan');
  log('phase4_pair_reserved event='+batch.event_id.slice(0,12));
  for (const item of batch.arms) {
    if (!ID.test(item?.attempt_id) || typeof item?.prompt!=='string' ||
        Buffer.byteLength(item.prompt,'utf8')>MAX_PROMPT)
      throw Error('invalid_prompt_plan');
    // session.create may be ambiguous: reserved attempt remains, do NOT retry.
    const created=await client.session.create({
      body:{title:'NEWS Phase4 '+item.arm+' '+batch.event_id.slice(0,12)},
      query:{directory},
    });
    const sessionId=created?.data?.id ?? created?.id;
    if (typeof sessionId!=='string' || !SESSION.test(sessionId))
      throw Error('session_create_unconfirmed_manual_reconcile');
    await runner('session',['--attempt-id',item.attempt_id,'--session-id',sessionId]);
    // Durable PROMPT_ATTEMPTED before external effect, and never redeliver.
    await runner('prompt',['--attempt-id',item.attempt_id]);
    const result=await client.session.prompt({
      path:{id:sessionId},query:{directory},
      body:{parts:[{type:'text',text:item.prompt}]},
    });
    let text=assistantText(result);
    let model=observedModel(result);
    if ((!text || !model) && typeof client.session.messages==='function') {
      const history=await client.session.messages({path:{id:sessionId},query:{directory}});
      const second=observedModel(history);
      if (model && second && (model.providerID!==second.providerID || model.modelID!==second.modelID))
        throw Error('model_identity_conflict_manual_reconcile');
      if (!text) text=assistantText(history);
      if (!model) model=second;
    }
    if (!text || Buffer.byteLength(text,'utf8')>8192)
      throw Error('model_result_unconfirmed_manual_reconcile');
    if (!model) throw Error('model_identity_unverified_manual_reconcile');
    const final=await runner('finalize',['--attempt-id',item.attempt_id],
      JSON.stringify({session_id:sessionId,response:text,model}));
    if (final?.status!=='completed') throw Error('model_finalization_unconfirmed');
    log('phase4_arm_completed arm='+item.arm);
  }
  const quote=await runner('quote',['--event-id',batch.event_id]);
  if (quote?.status!=='entry_recorded') throw Error('quote_unconfirmed');
  log('phase4_pair_committed event='+batch.event_id.slice(0,12));
  return {status:'paired_entry_recorded',event_id_prefix:batch.event_id.slice(0,16)};
}
