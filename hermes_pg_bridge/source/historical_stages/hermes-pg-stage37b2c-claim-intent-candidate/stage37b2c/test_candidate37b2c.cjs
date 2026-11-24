'use strict';
/* Execute the exact candidate's runTick function in a sandbox with fake queue.
 * No plugin imports, SQL clients, SDK clients, or filesystem operations.
 */
const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');
const p=process.argv[2];if(!p)throw Error('candidate argument required');
const src=fs.readFileSync(p,'utf8');
const a='export async function runTick(ctx) {';const b='export function diagnosticContext(';
assert.equal(src.split(a).length,2);assert.equal(src.split(b).length,2);
const tick=src.slice(src.indexOf(a),src.indexOf(b)).replace('export async function','async function');
const tid='task_b6c26aa18220412bafdbf32393f16568';const mid='a-valid-message-id';
let journalPresent=true, sdk=0,claim=0,read=0,log=[];
const state={inflight:{},seen:{}};
const sandbox={
 stage37b2cIntentPresent:()=>journalPresent,
 stage37b2cFinalizeAfterConfirmedAck:()=>{},
 acquireLock:()=>({}),releaseLock:()=>{},loadState:()=>state,
 reconcileAnsweredInflight:async()=>false,
 listAll:async()=>[{task_id:tid,lifecycle:'active'}],
 latestPending:()=>({seq:1,message_id:mid}),
 dispatch:async()=>{sdk++;claim++;return false},
 MAX_DISPATCH_PER_TICK:10,
};
vm.createContext(sandbox);
vm.runInContext(tick+'\nglobalThis.testRunTick=runTick;',sandbox,{timeout:1000});
const ctx={paths:{},log:s=>log.push(s),dryRun:false,
 cliCall:async(req)=>{if(req.op==='view')return {seq:1,history:[]};if(req.op==='read'){read++;return {task_id:tid,seq:1,message_id:mid,from:'chatgpt',to:'mt',kind:'task',body:'hello'};}throw Error('unexpected call: '+req.op)}};
async function main(){
 let out=await sandbox.testRunTick(ctx);
 assert.equal(sdk,0);assert.equal(claim,0);assert.equal(read,0);
 assert(log.some(x=>x.startsWith('stage37b2c_uncertain_claim_hold')));
 console.log('STAGE37B2C_CANDIDATE_BLOCKED_UNCERTAIN_INTENT_NO_SDK');
 journalPresent=false;out=await sandbox.testRunTick(ctx);
 assert.equal(sdk,1);assert.equal(claim,1);assert.equal(read,1);
 console.log('STAGE37B2C_CANDIDATE_CLEAN_PATH_REACHABLE_ONLY_WITHOUT_INTENT');
 console.log('STAGE37B2C_SOURCE_TICK_SANDBOX_PASS');
}
main().catch(e=>{console.error('STAGE37B2C_STOP_CANDIDATE_TEST '+e.stack);process.exitCode=2});
