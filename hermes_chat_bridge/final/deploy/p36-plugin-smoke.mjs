// P36 no-network plugin acceptance smoke. Does not call Hermes or real Git.
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runTick, diagnosticContext } from "./mt-git-inbox-p36.mjs";

const taskId = "task_" + "a".repeat(32);
const path = mkdtempSync(join(tmpdir(), "p36-plugin-smoke-"));
const seenOps = [];
const logs = [];
const cliCall = async (request) => {
  seenOps.push(request.op);
  if (request.op === "list_dialogs") {
    return { dialogs: [{ task_id: taskId, lifecycle: "active", title: "P36 smoke" }],
             next_offset: null };
  }
  if (request.op === "view") {
    return { task_id: taskId, head: "a".repeat(40), waiting_for: "mt",
             history: [{ seq:1, message_id:"msg1", to:"mt",
                         from:"chatgpt", kind:"task" }] };
  }
  if (request.op === "read") {
    return { task_id: taskId, seq:1, message_id:"msg1", to:"mt",
             from:"chatgpt", kind:"task", body:"Git-only payload" };
  }
  throw Error("unexpected operation " + request.op);
};
try {
  const ctx = diagnosticContext({
    stateDir:path, cliCall, client:{}, dryRun:true, log:x=>logs.push(x),
    sessionDirectory:()=>path,
  });
  const result = await runTick(ctx);
  if (result.status !== "ok" || result.dispatched !== 0 ||
      seenOps.join(",") !== "list_dialogs,view,read" ||
      !logs.some(x=>x.includes("dry_run "))) {
    throw Error("p36-plugin-smoke-failed");
  }
  process.stdout.write("P36_PLUGIN_SMOKE_OK\n");
} finally {
  rmSync(path, { recursive:true, force:true });
}
