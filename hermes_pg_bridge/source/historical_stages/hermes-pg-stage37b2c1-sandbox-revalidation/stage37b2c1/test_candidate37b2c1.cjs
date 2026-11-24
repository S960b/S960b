'use strict';
/* Stage37B2C1: sandbox execution of the ACTUAL, SHA-verified candidate runTick.
 * No candidate imports, SDK, SQL, real CLI, network, secrets or actual journaling.
 * The old Stage37B2C harness incorrectly omitted ctx.taskIds, triggering the
 * fail-closed Stage34EB allowlist *before* the intent guard.
 */
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const candidate=process.argv[2];
if (!candidate) throw Error('candidate argument required');
const code=fs.readFileSync(candidate,'utf8');
const TICK='export async function runTick(ctx) {';
const NEXT='export function diagnosticContext(';
const ALLOW='if (!ctx.taskIds || !ctx.taskIds.includes(dialog?.task_id)) continue;';
const GUARD='if (stage37b2cIntentPresent(dialog.task_id)) {';
const LOG='stage37b2c_uncertain_claim_hold';
assert.equal(code.split(TICK).length,2,'one tick function');
assert.equal(code.split(NEXT).length,2,'one diagnostic function');
assert.equal(code.split(ALLOW).length,2,'original Stage34EB filter present');
assert.equal(code.split(GUARD).length,2,'intent guard present once');
const tick=code.slice(code.indexOf(TICK),code.indexOf(NEXT)).replace(TICK,'async function runTick(ctx) {');
assert.ok(tick.indexOf(ALLOW)>=0&&tick.indexOf(GUARD)>tick.indexOf(ALLOW), 'filter precedes intent guard');
assert.ok(tick.includes(LOG));
const tid='task_b6c26aa18220412bafdbf32393f16568';
const alien='task_00000000000000000000000000000000';
const msg='stage37b2c1-test-message';
async function scenario(name,settings,checks){
  let sdk=0, read=0, views=0, log=[], listCalls=0, presentChecks=0, reconciled=0;
  const state={inflight: settings.inflight ? {[tid]:{seq:1,message_id:msg}}:{},seen:{}};
  const sandbox={
    stage37b2cIntentPresent:(task)=>{assert.equal(task,tid);presentChecks++;return !!settings.intent},
    stage37b2cFinalizeAfterConfirmedAck:()=>{throw Error('unexpected finalize without ACK')},
    acquireLock:()=>({}),releaseLock:()=>{},loadState:()=>state,
    reconcileAnsweredInflight:async()=>{reconciled++;return false},
    listAll:async()=>{listCalls++;return [{task_id:tid,lifecycle:'active'}]},
    latestPending:()=>({seq:1,message_id:msg}),
    dispatch:async()=>{sdk++;return false},
    MAX_DISPATCH_PER_TICK:10,
  };
  vm.createContext(sandbox,{name:'stage37b2c1-offline-'+name});
  vm.runInContext(tick+'\nglobalThis.testRunTick=runTick;',sandbox,{timeout:1000});
  const ctx={paths:{},log:s=>log.push(String(s)),dryRun:!!settings.dryRun,
    ...(settings.taskIds===undefined?{}:{taskIds:settings.taskIds}),
    cliCall:async(req)=>{
      if(req.op==='view'){views++;return {seq:1,history:[]}}
      if(req.op==='read'){read++;return {task_id:tid,seq:1,message_id:msg,from:'chatgpt',to:'mt',kind:'task',body:'offline fixture'};}
      throw Error('unexpected CLI operation: '+req.op);
    }
  };
  const result=await sandbox.testRunTick(ctx);
  assert.equal(result.status,'ok');
  assert.equal(listCalls,1);
  checks({sdk,read,views,log,presentChecks,reconciled,result});
  console.log('STAGE37B2C1_CASE_OK_'+name);
}
async function main(){
  await scenario('authorized_uncertain_intent_holds_without_sdk',
    {intent:true,taskIds:[tid]},({sdk,read,views,log,presentChecks})=>{
      assert.equal(sdk,0);assert.equal(read,0);assert.equal(views,0);
      assert.equal(presentChecks,1);assert(log.some(s=>s.startsWith(LOG+' '+tid)));
    });
  await scenario('authorized_clear_intent_makes_sdk_path_reachable',
    {intent:false,taskIds:[tid]},({sdk,read,views,log,presentChecks})=>{
      assert.equal(sdk,1);assert.equal(read,1);assert.equal(views,1);
      assert.equal(presentChecks,1);assert.equal(log.some(s=>s.startsWith(LOG)),false);
    });
  await scenario('missing_allowlist_fails_closed_before_intent_guard',
    {intent:true},({sdk,read,views,log,presentChecks})=>{
      assert.equal(sdk,0);assert.equal(read,0);assert.equal(views,0);
      assert.equal(presentChecks,0);assert.equal(log.some(s=>s.startsWith(LOG)),false);
    });
  await scenario('empty_allowlist_fails_closed',
    {intent:false,taskIds:[]},({sdk,read,views,presentChecks})=>{
      assert.equal(sdk,0);assert.equal(read,0);assert.equal(views,0);assert.equal(presentChecks,0);
    });
  await scenario('alien_allowlist_fails_closed',
    {intent:false,taskIds:[alien]},({sdk,read,views,presentChecks})=>{
      assert.equal(sdk,0);assert.equal(read,0);assert.equal(views,0);assert.equal(presentChecks,0);
    });
  await scenario('preexisting_inflight_blocks_even_without_intent',
    {intent:false,taskIds:[tid],inflight:true},({sdk,read,views,presentChecks,reconciled})=>{
      assert.equal(sdk,0);assert.equal(read,0);assert.equal(views,0);
      assert.equal(presentChecks,0);assert.equal(reconciled,1);
    });
  await scenario('dry_run_will_not_invoke_sdk',
    {intent:false,taskIds:[tid],dryRun:true},({sdk,read,views,presentChecks})=>{
      assert.equal(sdk,0);assert.equal(read,1);assert.equal(views,1);assert.equal(presentChecks,1);
    });
  console.log('STAGE37B2C1_SANDBOX_BEHAVIORAL_CASES_PASSED_7');
  console.log('STAGE37B2C1_CANDIDATE_SANDBOX_PASSED_NO_LIVE_CALLS');
}
main().catch(e=>{console.error('STAGE37B2C1_STOP_SANDBOX '+e.stack);process.exitCode=2});
