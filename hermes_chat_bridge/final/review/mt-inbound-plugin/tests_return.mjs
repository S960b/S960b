// P32.5: owner return events must not reach MultiTool SDK.
import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {diagnosticContext, runTick} from "./mt-git-inbox.mjs";

const task = "task_p325_inbox_test";
const dialog = {task_id: task, lifecycle: "active", title: "Return test"};
const marker = {
  seq: 2, message_id: "owner_return_2", kind: "return",
  from: "chatgpt", to: "mt", body: "OWNER_RETURN_LAST:original"
};
function setup(t, message) {
  const stateDir = mkdtempSync(join(tmpdir(), "p325-return-inbox-"));
  t.after(() => rmSync(stateDir, {recursive: true, force: true}));
  let prompts = 0, sessions = 0;
  const cliCall = async (q) => q.op === "list_dialogs"
    ? {dialogs: [dialog], next_offset: null}
    : {waiting_for: "mt", history: [message]};
  const client = {session: {
    create: async () => {sessions++; return {data: {id: "session_test"}};},
    prompt: async () => {prompts++; return {};}
  }};
  return {
    context: diagnosticContext({stateDir, cliCall, client}),
    counts: () => ({prompts, sessions})
  };
}

test("return marker is never prompted to MT", async (t) => {
  const f = setup(t, marker);
  const result = await runTick(f.context);
  assert.equal(result.dispatched, 0);
  assert.deepEqual(f.counts(), {prompts: 0, sessions: 0});
});

test("the later real ChatGPT message can still dispatch normally", async (t) => {
  const f = setup(t, {
    seq: 4, message_id: "replacement_followup", kind: "task",
    from: "chatgpt", to: "mt", body: "proceed"
  });
  const result = await runTick(f.context);
  assert.equal(result.dispatched, 1);
  assert.deepEqual(f.counts(), {prompts: 1, sessions: 1});
});
