// Separate NEWS Phase4 v2 plugin. Defaults safe: no model, no quote, no labels.
// Does not alter Phase3/Phase4 plugin, SQLite, or timers.
import { appendFileSync, existsSync, mkdirSync, unlinkSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';
import { pythonRunner, runPairOnce } from './news-p4-v2-helpers.mjs';

const ROOT='/home/kali/research/news-phase4-v2';
const DBROOT='/home/kali/.local/share/news-phase4-v2';
const SOURCE='/home/kali/.local/share/news-phase4';
const SLOT_MS=900000;

export const server=async (input={},options={})=>{
  const root=typeof options.root==='string'?options.root:ROOT;
  const dbRoot=typeof options.db_root==='string'?options.db_root:DBROOT;
  const sourceRoot=typeof options.source_root==='string'?options.source_root:SOURCE;
  const directory=typeof options.directory==='string'?options.directory:root;
  if (![root,dbRoot,sourceRoot,directory].every(isAbsolute))
    throw Error('v2_absolute_paths_required');
  const dryRun=options.dry_run!==false;
  const modelEnabled=options.model_calls_enabled===true;
  const autoEnabled=options.model_auto_enabled===true;
  const labelAuto=options.label_auto_enabled===true;
  const dailyCap=Number.isInteger(options.daily_call_cap)?options.daily_call_cap:0;
  const client=input?.client;
  const runner=pythonRunner(root,dbRoot,sourceRoot,
              typeof options.python==='string'?options.python:'/usr/bin/python3');
  const logPath=join(root,'news-p4-v2-plugin.log');
  const trigger=join(root,'.mt-v2-run-now');
  let closed=false,busy=false,labelBusy=false,lastSlot=null;
  const log=(message)=>{
    const clean=String(message).replace(/[^A-Za-z0-9_ =.-]/g,'').slice(0,200);
    try {
      mkdirSync(dirname(logPath),{recursive:true,mode:0o700});
      appendFileSync(logPath,new Date().toISOString()+' '+clean+'\n',{mode:0o600});
    } catch { /* never log raw error payload */ }
  };
  const tick=async(forced=false)=>{
    if (closed||busy) return;
    if (!forced&&!existsSync(trigger)) return;
    if (!forced) {
      try {unlinkSync(trigger);} catch {log('v2_trigger_unlink_failed');return;}
    }
    busy=true;
    try {
      const r=await runPairOnce({
        client,directory,runner,dryRun,modelEnabled,dailyCap,log});
      log('v2_cycle status='+String(r?.status??'unknown'));
    } catch(e) {
      const name=String(e?.message??'v2_unknown');
      log('v2_cycle_error='+(/^[A-Za-z0-9_]+$/.test(name)?name:'unknown'));
    } finally {busy=false;}
  };
  const poll=setInterval(()=>{tick().catch(()=>log('v2_tick_error'));},30000);
  poll.unref?.();
  const timers=[poll];
  if (!dryRun&&autoEnabled&&modelEnabled) {
    const auto=setInterval(()=>{
      const slot=Math.floor(Date.now()/SLOT_MS);
      if (lastSlot===slot) return;
      lastSlot=slot;
      tick(true).catch(()=>log('v2_auto_error'));
    },SLOT_MS);
    auto.unref?.();
    timers.push(auto);
  }
  if (!dryRun&&labelAuto) {
    const label=setInterval(async()=>{
      if (closed||labelBusy) return;
      labelBusy=true;
      try {
        const r=await runner('labels',['--enable-public-labels']);
        log('v2_labels created='+Number(r?.created??0));
      } catch {log('v2_label_error');}
      finally {labelBusy=false;}
    },300000);
    label.unref?.();
    timers.push(label);
  }
  log('v2_loaded dry_run='+dryRun+' model='+modelEnabled+
      ' auto='+autoEnabled+' label_auto='+labelAuto+' daily_cap='+dailyCap);
  return {dispose:async()=>{closed=true;
    for(const timer of timers)clearInterval(timer);
    log('v2_disposed');}};
};
export default server;
