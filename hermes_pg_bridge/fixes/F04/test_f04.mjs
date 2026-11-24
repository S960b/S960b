// F04 isolated regression: exercises audited runTick with fake SQL/SDK and a private journal.
// Never contacts a real database, SDK server, tunnel or Taskboard.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {pathToFileURL} from "node:url";

const plugin = fs.readFileSync(path.resolve(process.argv[2]), "utf8");
const helper = fs.readFileSync(path.resolve(process.argv[3]), "utf8");
const taskId = "task_" + "a".repeat(32);
const dialog = {task_id:taskId,title:"synthetic",lifecycle:"active"};
const temp = fs.mkdtempSync(path.join(os.tmpdir(),"hermes-f04-test-"));
try {
  for(const mode of ["normal","lost-reply-and-session-removed","http404-but-effect-unknown"]){
    const dir=path.join(temp,mode);
    fs.mkdirSync(dir,{mode:0o700});
    const journal=path.join(dir,"journal");
    const h=helper.replace("/home/kali/.local/state/mt-pg-inbox-managed-stage34e/claim-intents37b2c",journal);
    const p=plugin.replace("/home/kali/hermes-pg-stage37b2c-claim-intent/intent37b2c.mjs","./helper.mjs");
    assert.notEqual(h,helper,"journal must be redirected");
    assert.notEqual(p,plugin,"helper import must be redirected");
    fs.writeFileSync(path.join(dir,"helper.mjs"),h);
    fs.writeFileSync(path.join(dir,"inbox.mjs"),p);
    const {diagnosticContext,runTick}=await import(pathToFileURL(path.join(dir,"inbox.mjs")));
    let seq=1,prompts=0,claims=0,acks=0,creates=0,probes=0;
    const message=()=>({seq,message_id:"msg"+seq,kind:"task",from:"chatgpt",to:"mt",body:"count only"});
    const result=()=>({seq:seq+1,message_id:"pgv2-mg-"+taskId.slice(5)+"-"+seq.toString(36)+"-report",kind:"report",from:"mt",to:"chatgpt"});
    const cliCall=async req=>{
      switch(req.op){
        case "list_dialogs":return {dialogs:[dialog],next_offset:null};
        case "view":return req.limit===1?{task_id:taskId,waiting_for:"mt",seq,history:[message()]}:{task_id:taskId,seq:seq+1,history:[result(),message()]};
        case "read":return message();
        case "claim":++claims;return {claimed:true,delivery_id:1,lease_token:"00000000-0000-4000-8000-000000000000"};
        case "ack":++acks;return {acked:true};
        default:throw Error("unexpected synthetic op");
      }
    };
    const client={session:{
      create:async()=>({id:"synthetic"+(++creates)}),
      prompt:async()=>{
        ++prompts;
        if(mode==="lost-reply-and-session-removed"&&prompts===1)throw new SyntaxError("Unexpected end of JSON input");
        if(mode==="http404-but-effect-unknown"&&prompts===1)throw Object.assign(new Error("not found"),{status:404});
      }
    }};
    const ctx={...diagnosticContext({stateDir:dir,cliCall,client,log:()=>{}}),
      taskIds:[taskId],bridgeDirectory:dir,sessionDirectory:()=>dir,
      sessionExists:()=>{++probes;return false;}};
    await runTick(ctx);
    const statePath=path.join(dir,"state.json");
    const state=fs.existsSync(statePath)?JSON.parse(fs.readFileSync(statePath,"utf8")):{};
    const inflight=state.inflight?.[taskId];
    const intent=fs.existsSync(path.join(journal,taskId+".json"));
    assert.equal(claims,1,"exactly one SQL claim");
    assert.equal(prompts,1,"no automatic second SDK prompt");
    assert.equal(probes,0,"no session-absence replay inference");
    if(mode==="normal"){
      assert.equal(acks,1);assert.equal(state.seen?.[taskId],1);
      assert.equal(!!inflight,false);assert.equal(intent,false);
    } else {
      assert.equal(acks,0);assert.equal(state.seen?.[taskId]??0,0);
      assert.equal(inflight?.phase,"prompt_attempted");assert.equal(intent,true);
      await runTick(ctx);
      assert.equal(prompts,1,"second tick must not re-prompt");
      assert.equal(claims,1,"second tick must not reclaim");
    }
    console.log("STAGE42_F04_TEST_"+mode+"_OK",JSON.stringify({claims,prompts,acks,inflight:!!inflight,intent}));
  }
} finally {
  fs.rmSync(temp,{recursive:true,force:true});
}
console.log("STAGE42_F04_OFFLINE_REGRESSION_OK");
