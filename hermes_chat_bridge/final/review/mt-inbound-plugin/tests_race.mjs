// P32.4 regression: tests actually execute runTick across shared state/lock.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { diagnosticContext, runTick } from "./mt-git-inbox.mjs";

const TASK = "task_1234567890abcdef1234567890abcdef";
const MSG = { seq: 7, message_id: "task_7", from: "chatgpt", to: "mt",
              kind: "task", body: "dry inert fixture" };
const DIALOG = { task_id: TASK, title: "Test", lifecycle: "active" };
function setup(t, { prompt, create, history = [MSG] } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "mt-p324-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  let creates = 0, prompts = 0, views = 0;
  const cliCall = async (q) => {
    if (q.op === "list_dialogs") return { dialogs: [DIALOG], next_offset: null };
    if (q.op === "view") {
      views++;
      assert.equal(q.limit, 1); assert.equal(q.order, "newest");
      return { waiting_for: "mt", history, total_messages: 7 };
    }
    throw new Error("unexpected CLI");
  };
  const client = { session: {
    create: async (...args) => { creates++; return create
      ? create(...args) : { data: { id: "ses_fixture" } }; },
    prompt: async (...args) => { prompts++; if (prompt) return prompt(...args); return {}; },
  }};
    const make = (opts = {}) => diagnosticContext({
      stateDir: dir, cliCall, client, dryRun: false,
      sessionDirectory: () => "/tmp/p325-race-dir", ...opts,
    });
  const get = () => JSON.parse(readFileSync(join(dir, "state.json"), "utf8"));
  return { dir, make, get, counts: () => ({ creates, prompts, views }) };
}

test("concurrent hosts: exactly one prompt with one shared lock", async (t) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let started;
  const began = new Promise((resolve) => { started = resolve; });
  const f = setup(t, { prompt: async () => { started(); await gate; } });
  const first = runTick(f.make());
  await began; // external side effect in flight; lock must remain held
  const second = await runTick(f.make());
  assert.deepEqual(second, { status: "locked" });
  assert.deepEqual(f.counts(), { creates: 1, prompts: 1, views: 1 });
  release();
  assert.equal((await first).dispatched, 1);
  assert.equal((await runTick(f.make())).dispatched, 0);
  assert.deepEqual(f.counts(), { creates: 1, prompts: 1, views: 2 });
  assert.equal(f.get().seen[TASK], 7);
  assert.equal(f.get().sessions[TASK], "ses_fixture");
  assert.equal(f.get().inflight[TASK], undefined);
});

test("failed prompt: never create replacement session or automatically redeliver", async (t) => {
  const f = setup(t, { prompt: async () => { throw Error("ambiguous SDK failure"); } });
  mkdirSync(f.dir, { recursive: true });
  writeFileSync(join(f.dir, "state.json"), JSON.stringify({
    seen: { [TASK]: 5 }, sessions: { [TASK]: "ses_preexisting" },
  }));
  await runTick(f.make());
  assert.deepEqual(f.counts(), { creates: 0, prompts: 1, views: 1 });
  assert.equal(f.get().inflight[TASK].phase, "prompt_attempted");
  assert.equal(f.get().sessions[TASK], "ses_preexisting");
  assert.equal((await runTick(f.make())).held, 1);
  assert.deepEqual(f.counts(), { creates: 0, prompts: 1, views: 1 });
});

test("crash after creating session: durable id preserved and no second create", async (t) => {
  const f = setup(t, { prompt: async () => { throw Error("connection died"); } });
  await runTick(f.make());
  assert.equal(f.get().sessions[TASK], "ses_fixture");
  assert.equal(f.get().inflight[TASK].message_id, MSG.message_id);
  assert.equal(f.get().inflight[TASK].phase, "prompt_attempted");
  await runTick(f.make());
  assert.deepEqual(f.counts(), { creates: 1, prompts: 1, views: 1 });
});

test("dry-run makes no sessions and never acknowledges a Git message", async (t) => {
  const f = setup(t);
  await runTick(f.make({ dryRun: true }));
  assert.deepEqual(f.counts(), { creates: 0, prompts: 0, views: 1 });
  assert.equal(existsSync(join(f.dir, "state.json")), false);
});

test("newest current task is sole actionable message; older history ignored", async (t) => {
  const f = setup(t, { history: [
    MSG, { seq: 5, message_id: "old_5", to: "mt", body: "old" },
  ] });
  await runTick(f.make());
  assert.deepEqual(f.counts(), { creates: 1, prompts: 1, views: 1 });
  assert.equal(f.get().seen[TASK], 7);
});

test("invalid state is not reset (fail closed, no session created)", async (t) => {
  const f = setup(t);
  writeFileSync(join(f.dir, "state.json"), "{broken-json");
  await assert.rejects(() => runTick(f.make()), /state_unreadable_or_invalid/);
  assert.deepEqual(f.counts(), { creates: 0, prompts: 0, views: 0 });
  assert.equal(readFileSync(join(f.dir, "state.json"), "utf8"), "{broken-json");
});

test("dead-pid legacy exclusive lock is recovered safely, live lock is never stolen", async (t) => {
  const f = setup(t);
  writeFileSync(join(f.dir, "inbox.lock"), JSON.stringify({
    pid: 2147483647, id: "dead-holder",
  }));
  assert.equal((await runTick(f.make())).dispatched, 1);
  assert.equal(f.get().seen[TASK], 7);
  assert.equal(existsSync(join(f.dir, "inbox.lock")), false);
});
