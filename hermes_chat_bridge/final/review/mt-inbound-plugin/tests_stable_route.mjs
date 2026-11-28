// P32.5: routing must be deterministic. A durable binding for a dialogue always
// wins; the live-chat lookup is only a first-contact hint. Switching chats or
// restarting the app must never move an established dialogue, and must never
// cause a second delivery of the same message.
import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync, readFileSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {diagnosticContext, runTick} from "./mt-git-inbox.mjs";

const task = "task_p325_route_test";
const dialog = {task_id: task, lifecycle: "active", title: "Route test"};
const CHAT_A = "ses_e5ed4f014bcffeNUFuI742WTjLgm";
const CHAT_B = "ses_e5e5e2ee845ffeRm9GTs3zQEAMAF";

function msg(seq) {
  return {seq, message_id: "route_seq_" + seq, kind: "task",
          from: "chatgpt", to: "mt", body: "proceed"};
}

function setup(t, {live, history}) {
  const stateDir = mkdtempSync(join(tmpdir(), "p325-route-"));
  t.after(() => rmSync(stateDir, {recursive: true, force: true}));
  const prompts = [];
  const created = [];
  const box = {history: history ? [history] : [], live};
  const cliCall = async (q) => q.op === "list_dialogs"
    ? {dialogs: [dialog], next_offset: null}
    : {waiting_for: "mt", history: box.history};
  const client = {session: {
    create: async () => {
      const id = "ses_created_" + created.length;
      created.push(id);
      return {data: {id}};
    },
    prompt: async (arg) => {
      prompts.push(arg);
      box.history = [];
      return {};
    }
  }};
  const ctx = diagnosticContext({stateDir, cliCall, client,
                                 sessionDirectory: () => "/tmp/p325-route-dir"});
  ctx.resolveLiveChat = () => box.live;
  return {ctx, prompts, created, stateDir, box};
}

function readState(ctx) {
  return JSON.parse(readFileSync(ctx.paths.state, "utf8"));
}

function seed(ctx, state) {
  writeFileSync(ctx.paths.state, JSON.stringify(state));
}

test("durable binding wins over the live chat", async (t) => {
  const f = setup(t, {live: {id: CHAT_B, directory: "/home/kali"}, history: msg(5)});
  seed(f.ctx, {sessions: {[task]: CHAT_A}, seen: {}, inflight: {}});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 1);
  assert.equal(f.prompts.length, 1);
  assert.equal(f.prompts[0].path.id, CHAT_A);
  assert.equal(readState(f.ctx).sessions[task], CHAT_A);
});

test("switching the active chat does not move the dialogue", async (t) => {
  const f = setup(t, {live: {id: CHAT_A, directory: "/home/kali"}, history: msg(5)});
  seed(f.ctx, {sessions: {[task]: CHAT_A}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.prompts[0].path.id, CHAT_A);

  f.box.live = {id: CHAT_B, directory: "/home/kali"};
  f.box.history = [msg(6)];
  const second = await runTick(f.ctx);
  assert.equal(second.dispatched, 1);
  assert.equal(f.prompts.length, 2);
  assert.equal(f.prompts[1].path.id, CHAT_A);
});

test("restart (fresh context) keeps the persisted session", async (t) => {
  const f = setup(t, {live: {id: CHAT_B, directory: "/home/kali"}, history: msg(5)});
  seed(f.ctx, {sessions: {[task]: CHAT_A}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.prompts[0].path.id, CHAT_A);

  const restarted = diagnosticContext({
    stateDir: f.stateDir,
    cliCall: f.ctx.cliCall,
    client: f.ctx.client,
    sessionDirectory: () => "/tmp/p325-route-dir"
  });
  restarted.resolveLiveChat = () => ({id: CHAT_B, directory: "/home/kali"});
  f.box.history = [msg(6)];
  const result = await runTick(restarted);
  assert.equal(result.dispatched, 1);
  assert.equal(f.prompts.length, 2);
  assert.equal(f.prompts[1].path.id, CHAT_A);
});

test("no binding: live chat is adopted and then persisted", async (t) => {
  const f = setup(t, {live: {id: CHAT_A, directory: "/home/kali"}, history: msg(5)});
  seed(f.ctx, {sessions: {}, seen: {}, inflight: {}});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 1);
  assert.equal(f.prompts[0].path.id, CHAT_A);
  assert.equal(f.created.length, 0);
  assert.equal(readState(f.ctx).sessions[task], CHAT_A);
});

test("adopted live chat becomes authoritative on the next tick", async (t) => {
  const f = setup(t, {live: {id: CHAT_A, directory: "/home/kali"}, history: msg(5)});
  seed(f.ctx, {sessions: {}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.prompts[0].path.id, CHAT_A);

  f.box.live = {id: CHAT_B, directory: "/home/kali"};
  f.box.history = [msg(6)];
  await runTick(f.ctx);
  assert.equal(f.prompts[1].path.id, CHAT_A);
  assert.equal(readState(f.ctx).sessions[task], CHAT_A);
});

test("no binding and no live chat: own session is created and persisted", async (t) => {
  const f = setup(t, {live: null, history: msg(5)});
  seed(f.ctx, {sessions: {}, seen: {}, inflight: {}});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 1);
  assert.equal(f.created.length, 1);
  assert.equal(f.prompts[0].path.id, "ses_created_0");
  assert.equal(readState(f.ctx).sessions[task], "ses_created_0");
});

test("the same message is never delivered twice", async (t) => {
  const f = setup(t, {live: {id: CHAT_A, directory: "/home/kali"}, history: msg(5)});
  seed(f.ctx, {sessions: {[task]: CHAT_A}, seen: {}, inflight: {}});
  const first = await runTick(f.ctx);
  assert.equal(first.dispatched, 1);
  f.box.history = [msg(5)];
  const second = await runTick(f.ctx);
  assert.equal(second.dispatched, 0);
  assert.equal(f.prompts.length, 1);
  assert.equal(readState(f.ctx).seen[task], 5);
});

test("an unconfirmed inflight blocks redelivery after a restart", async (t) => {
  const f = setup(t, {live: {id: CHAT_A, directory: "/home/kali"}, history: msg(7)});
  seed(f.ctx, {
    sessions: {[task]: CHAT_A},
    seen: {},
    inflight: {[task]: {seq: 7, message_id: "route_seq_7", phase: "prompt_attempted"}}
  });
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 0);
  assert.equal(result.held, 1);
  assert.equal(f.prompts.length, 0);
});
