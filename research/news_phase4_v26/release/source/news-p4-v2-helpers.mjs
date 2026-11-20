// NEWS Phase4 v2: thin, native MultiTool SDK client for a separate paper cohort.
// Python SQLite is the authority for both attempts. NO order/withdraw API.
import { spawn } from 'node:child_process';
import { isAbsolute, join } from 'node:path';

const ID=/^[a-f0-9]{64}$/;
const SID=/^[A-Za-z0-9_-]{5,160}$/;
const MODEL=/^[A-Za-z0-9_.-]{2,100}$/;
const COMMANDS=new Set(['init','prepare','session','prompt','finish','quote','labels','status','evaluate']);

export function assistantText(response) {
  const root=response?.data??response;
  const list=Array.isArray(root)?root:root?.messages;
  if (Array.isArray(list)) {
    for (let n=list.length-1;n>=0;n--) {
      const item=list[n];
      const role=item?.info?.role??item?.role??item?.message?.info?.role;
      if (role!=='assistant') continue;
      const found=assistantText(item);
      if (found) return found;
    }
    return null;
  }
  for (const item of [root,root?.message,root?.assistant]) {
    if (!item || typeof item!=='object') continue;
    const role=item?.info?.role??item?.role;
    if (role && role!=='assistant') continue;
    const text=(item?.parts??item?.message?.parts);
    if (!Array.isArray(text)) continue;
    const joined=text.filter(p=>p?.type==='text'&&typeof p.text==='string')
      .map(p=>p.text).join('\n').trim();
    if (joined) return joined;
  }
  return null;
}

function identity(item) {
  const root=item?.data??item;
  const info=root?.info??root?.message?.info;
  if (!info || info.role!=='assistant') return null;
  const m=info?.model&&typeof info.model==='object'?info.model:info;
  if (typeof m.providerID!=='string'||typeof m.modelID!=='string'||
      !MODEL.test(m.providerID)||!MODEL.test(m.modelID)) return null;
  return {providerID:m.providerID,modelID:m.modelID};
}

export function observedModel(response) {
  const root=response?.data??response;
  const items=Array.isArray(root)?root:root?.messages;
  if (!Array.isArray(items)) return identity(root);
  let model=null;
  for (const item of items) {
    const next=identity(item);
    if (!next) continue;
    if (model&&(model.providerID!==next.providerID||model.modelID!==next.modelID))
      throw Error('v2_model_identity_conflict');
    model=next;
  }
  return model;
}

export function pythonRunner(root,dbRoot,sourceRoot,python='/usr/bin/python3') {
  for (const path of [root,dbRoot,sourceRoot,python])
    if (typeof path!=='string'||!isAbsolute(path)) throw Error('v2_invalid_paths');
  const script=join(root,'v2_cycle.py');
  return async (command,args=[],input='')=>{
    if (!COMMANDS.has(command)||!Array.isArray(args)||
        args.some(x=>typeof x!=='string'||x.length>250)||
        typeof input!=='string'||Buffer.byteLength(input)>8192)
      throw Error('v2_invalid_runner_args');
    return new Promise((resolve,reject)=>{
      const child=spawn(python,[script,command,'--root',dbRoot,
                               '--source-root',sourceRoot,...args],{
        cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},
        stdio:['pipe','pipe','pipe']});
      let raw='',finished=false;
      const timer=setTimeout(()=>{
        child.kill('SIGTERM');fail('v2_python_timeout_unknown');
      },150000);
      timer.unref?.();
      function fail(code) {
        if (finished) return;
        finished=true;clearTimeout(timer);reject(Error(code));
      }
      child.on('error',()=>fail('v2_runner_start_failed'));
      child.stdout.on('data',buf=>{
        raw+=String(buf);
        if (Buffer.byteLength(raw)>42000){
          child.kill('SIGTERM');fail('v2_python_output_too_large');
        }
      });
      // Consume error stream but never log raw Python errors.
      child.stderr.on('data',()=>{});
      child.stdin.on('error',()=>{});
      child.stdin.end(input);
      child.on('close',code=>{
        if (finished) return;
        finished=true;clearTimeout(timer);
        if (code!==0) return reject(Error('v2_python_failed'));
        try {const r=JSON.parse(raw);
          if (!r||typeof r!=='object') throw Error('invalid');
          resolve(r);
        } catch {reject(Error('v2_json_invalid'));}
      });
    });
  };
}

export async function runPairOnce({client,directory,runner,
                                  modelEnabled=false,dryRun=true,
                                  dailyCap=0,log=()=>{}}){
  if (dryRun || modelEnabled!==true) return {status:'disabled'};
  if (!Number.isInteger(dailyCap)||dailyCap<2||dailyCap>4||dailyCap%2)
    throw Error('v2_invalid_daily_cap');
  if (!isAbsolute(directory)||typeof runner!=='function'||
      typeof client?.session?.create!=='function'||
      typeof client?.session?.prompt!=='function')
    throw Error('v2_sdk_not_ready');
  const pair=await runner('prepare',['--enable-models','--daily-cap',String(dailyCap)]);
  if (pair?.status!=='ready') return pair;
  if (!ID.test(pair?.event_id)||!Array.isArray(pair.arms)||
      pair.arms.length!==2||
      new Set(pair.arms.map(a=>a.arm)).size!==2||
      !pair.arms.some(a=>a.arm==='news')||
      !pair.arms.some(a=>a.arm==='news_market'))
    throw Error('v2_pair_plan_invalid');
  for (const arm of pair.arms) {
    if (!ID.test(arm?.attempt_id)||typeof arm.prompt!=='string'||
        Buffer.byteLength(arm.prompt)>13000)
      throw Error('v2_prompt_plan_invalid');
    const created=await client.session.create({
      body:{title:'NEWS Phase4 v2 '+arm.arm+' '+pair.event_id.slice(0,12)},
      query:{directory}});
    const sid=created?.data?.id??created?.id;
    if (typeof sid!=='string'||!SID.test(sid))
      throw Error('v2_session_create_unknown_no_retry');
    await runner('session',['--attempt-id',arm.attempt_id,'--session-id',sid]);
    await runner('prompt',['--attempt-id',arm.attempt_id]);
    const result=await client.session.prompt({
      path:{id:sid},query:{directory},
      body:{parts:[{type:'text',text:arm.prompt}]}});
    let text=assistantText(result),model=observedModel(result);
    if ((!text||!model)&&typeof client.session.messages==='function') {
      const history=await client.session.messages({
        path:{id:sid},query:{directory}});
      const check=observedModel(history);
      if (model&&check&&(model.providerID!==check.providerID||
                          model.modelID!==check.modelID))
        throw Error('v2_model_identity_conflict');
      if (!text) text=assistantText(history);
      if (!model) model=check;
    }
    if (!text||Buffer.byteLength(text)>7500||!model)
      throw Error('v2_unconfirmed_model_result_no_retry');
    const final=await runner('finish',['--attempt-id',arm.attempt_id],
      JSON.stringify({session_id:sid,response:text,model}));
    if (final?.status!=='completed')
      throw Error('v2_finalize_unknown_no_retry');
    log('v2_arm_done arm='+arm.arm);
  }
  const quote=await runner('quote',['--event-id',pair.event_id,
                                     '--enable-public-quote']);
  if (quote?.status!=='entry_recorded')
    throw Error('v2_quote_unknown_no_retry');
  return {status:'v2_pair_recorded',event_id_prefix:pair.event_id.slice(0,12)};
}
