// P32.5: session recovery must keep the creation directory and the prompt
// directory identical. The replacement session is created in the plugin
// directory, so the retry prompt has to go to that same directory — not to the
// stale one captured before the recovery.
import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync, readFileSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {diagnosticContext, runTick} from "./mt-git-inbox.mjs";

const task = "task_p325_recovery_dir_test";
const dialog = {task_id: task, lifecycle: "active", title: "Recovery dir"};
const OLD_SESSION = "ses_e5e5e2ee845ffeRm9GTs3zQEAMAF";
const NEW_SESSION = "ses_replacement_1";
const OLD_DIR = "/home/kali";                 // where the deleted session lived
const PLUGIN_DIR = "/home/kali/Documents/MultiTool/HomeChats";

function msg(seq) {
  return {seq, message_id: "rec_dir_" + seq, kind: "task",
          from: "chatgpt", to: "mt", body: "proceed"};
}

function notFound(id) {
  const e = new Error("Session not found: " + id);
  e.name = "NotFoundError";
  e.status = 404;
  return e;
}

function setup(t, {missing = true, probe = false} = {}) {
  const stateDir = mkdtempSync(join(tmpdir(), "p325-recdir-"));
  t.after(() => rmSync(stateDir, {recursive: true, force: true}));
  const prompts = [];
  const created = [];
  const box = {history: [msg(5)]};
  const cliCall = async (q) => q.op === "list_dialogs"
    ? {dialogs: [dialog], next_offset: null}
    : {waiting_for: "mt", history: box.history};
  const client = {session: {
    create: async (arg) => {
      created.push(arg);
      return {data: {id: NEW_SESSION}};
    },
    prompt: async (arg) => {
      prompts.push(arg);
      if (missing && arg.path.id === OLD_SESSION && prompts.length === 1)
        throw notFound(OLD_SESSION);
      box.history = [];
      return {};
    }
  }};
  const ctx = diagnosticContext({
    stateDir, cliCall, client,
    directory: PLUGIN_DIR,
    sessionDirectory: (id) => (id === OLD_SESSION ? OLD_DIR : null)
  });
  ctx.resolveLiveChat = () => null;
  ctx.sessionExists = () => probe;
  writeFileSync(ctx.paths.state, JSON.stringify({
    sessions: {[task]: OLD_SESSION}, seen: {}, inflight: {}
  }));
  return {ctx, prompts, created, box, stateDir};
}

function readState(ctx) {
  return JSON.parse(readFileSync(ctx.paths.state, "utf8"));
}

test("recovery creates and prompts in the same directory", async (t) => {
  const f = setup(t, {missing: true, probe: false});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 1);
  assert.equal(f.created.length, 1);
  assert.equal(f.created[0].query.directory, PLUGIN_DIR);
  assert.equal(f.prompts.length, 2);
  // First attempt: the old, correctly resolved directory.
  assert.equal(f.prompts[0].path.id, OLD_SESSION);
  assert.equal(f.prompts[0].query.directory, OLD_DIR);
  // Retry after recreation: must match the creation directory exactly.
  assert.equal(f.prompts[1].path.id, NEW_SESSION);
  assert.equal(f.prompts[1].query.directory, PLUGIN_DIR);
  assert.equal(f.prompts[1].query.directory, f.created[0].query.directory);
});

test("the retry never reuses the stale pre-recovery directory", async (t) => {
  const f = setup(t, {missing: true, probe: false});
  await runTick(f.ctx);
  const retry = f.prompts.find((p) => p.path.id === NEW_SESSION);
  assert.ok(retry, "replacement session must be prompted");
  assert.notEqual(retry.query.directory, OLD_DIR);
});

test("mapping, seen and inflight settle after a successful recovery", async (t) => {
  const f = setup(t, {missing: true, probe: false});
  await runTick(f.ctx);
  const st = readState(f.ctx);
  assert.equal(st.sessions[task], NEW_SESSION);
  assert.equal(st.seen[task], 5);
  assert.equal(st.inflight[task], undefined);
});

test("an unconfirmed deletion never recreates and never re-prompts", async (t) => {
  const f = setup(t, {missing: true, probe: null});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 0);
  assert.equal(f.created.length, 0);
  assert.equal(f.prompts.length, 1);
  const st = readState(f.ctx);
  assert.equal(st.sessions[task], OLD_SESSION);
  assert.equal(st.seen[task], undefined);
  assert.equal(st.inflight[task].phase, "prompt_attempted");
});

test("a live session confirmed present is not recreated", async (t) => {
  const f = setup(t, {missing: true, probe: true});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 0);
  assert.equal(f.created.length, 0);
  assert.equal(f.prompts.length, 1);
  assert.equal(readState(f.ctx).sessions[task], OLD_SESSION);
});

test("a second rehearsal of the same message prompts nothing more", async (t) => {
  const f = setup(t, {missing: true, probe: false});
  await runTick(f.ctx);
  const before = f.prompts.length;
  f.box.history = [msg(5)];
  const second = await runTick(f.ctx);
  assert.equal(second.dispatched, 0);
  assert.equal(f.prompts.length, before);
});
