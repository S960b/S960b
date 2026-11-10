// Phase4 MultiTool model adapter. Plugin loader ONLY calls server hook.
// Does not touch Phase3 plugin, service, DB or API keys. DEFAULT ALL OFF.
// Owner must separately set dry_run:false, model_calls_enabled:true,
// model_auto_enabled:true AND daily_call_cap within 2..4.
import { appendFileSync, existsSync, mkdirSync, unlinkSync } from 'node:fs';
import { isAbsolute, join, dirname } from 'node:path';
import { pythonRunner, runPairOnce } from './news-p4-helpers.mjs';

const ROOT='/home/kali/research/news-phase4';
const AUTO_INTERVAL_MS=900_000;

export const server=async (input={},options={})=>{
  const root=typeof options.root==='string'?options.root:ROOT;
  const directory=typeof options.directory==='string'?options.directory:root;
  if (!isAbsolute(root) || !isAbsolute(directory)) throw Error('phase4_absolute_paths_required');
  const logPath=join(root,'news-p4-plugin.log');
  const trigger=join(root,'.mt-news-p4-run-now');
  const dryRun=options.dry_run!==false;
  const modelEnabled=options.model_calls_enabled===true;
  const autoEnabled=options.model_auto_enabled===true;
  const dailyCap=Number.isInteger(options.daily_call_cap)?options.daily_call_cap:0;
  const client=input?.client;
  const runner=pythonRunner(root,typeof options.python==='string'?options.python:'/usr/bin/python3');
  let closed=false,busy=false,lastSlot=null;
  const log=msg=>{
    const value=String(msg).replace(/[^a-zA-Z0-9_ =.-]/g,'').slice(0,240);
    try {
      mkdirSync(dirname(logPath),{recursive:true,mode:0o700});
      appendFileSync(logPath,new Date().toISOString()+' '+value+'\n',{mode:0o600});
    } catch { /* no raw logs, logging must not trigger replay */ }
  };
  const tick=async(force=false)=>{
    if (closed || busy) return;
    if (!force && !existsSync(trigger)) return;
    if (!force) {
      try {unlinkSync(trigger);}
      catch {log('trigger_unlink_failed');return;}
    }
    busy=true;
    try {
      const out=await runPairOnce({
        client,directory,runner,dryRun,modelEnabled,dailyCap,log
      });
      log('p4_cycle status='+String(out?.status??'unknown'));
    } catch(e) {
      const kind=String(e?.message??'unknown');
      log('p4_cycle_error='+(/^[A-Za-z0-9_]+$/.test(kind)?kind:'unknown'));
    } finally {busy=false;}
  };
  const poll=setInterval(()=>{tick().catch(()=>log('p4_tick_failed'));},30_000);
  poll.unref?.();
  const timers=[poll];
  if (autoEnabled && modelEnabled && !dryRun) {
    const timer=setInterval(()=>{
      const slot=Math.floor(Date.now()/AUTO_INTERVAL_MS);
      if (slot===lastSlot) return;
      lastSlot=slot;
      tick(true).catch(()=>log('p4_auto_failed'));
    },AUTO_INTERVAL_MS);
    timer.unref?.();
    timers.push(timer);
  }
  log('plugin_loaded p4=true dry_run='+dryRun+' enabled='+modelEnabled+
      ' auto='+autoEnabled+' daily_cap='+dailyCap);
  return {dispose:async()=>{closed=true;for(const timer of timers)clearInterval(timer);
    log('phase4_disposed');}};
};
export default server;
