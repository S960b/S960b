// P32.5: the SDK resolves the request workspace from query.directory, so a
// prompt to a bound session must carry that session's own directory, not the
// plugin's. Otherwise a turn written to Chat-45 executes against another
// workspace.
import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync, readFileSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {diagnosticContext, runTick} from "./mt-git-inbox.mjs";

const task = "task_p325_directory_test";
const dialog = {task_id: task, lifecycle: "active", title: "Directory test"};
const CHAT_45 = "ses_e5ed4f014bcffeNUFuI742WTjLgm";
const INBOX = "ses_e5e5e2ee845ffeRm9GTs3zQEAMAF";
const CHAT_DIR = "/home/kali/Documents/MultiTool/HomeChats/Chat-45";
const INBOX_DIR = "/home/kali";
const PLUGIN_DIR = "/home/kali/Documents/MultiTool/HomeChats";

function msg(seq) {
  return {seq, message_id: "dir_seq_" + seq, kind: "task",
          from: "chatgpt", to: "mt", body: "proceed"};
}

function setup(t, {history, sessionDir}) {
  const stateDir = mkdtempSync(join(tmpdir(), "p325-dir-"));
  t.after(() => rmSync(stateDir, {recursive: true, force: true}));
  const prompts = [];
  const created = [];
  const box = {history: history ? [history] : []};
  const cliCall = async (q) => q.op === "list_dialogs"
    ? {dialogs: [dialog], next_offset: null}
    : {waiting_for: "mt", history: box.history};
  const client = {session: {
    create: async (arg) => {
      const id = "ses_created_" + created.length;
      created.push({id, arg});
      return {data: {id}};
    },
    prompt: async (arg) => {
      prompts.push(arg);
      box.history = [];
      return {};
    }
  }};
  const ctx = diagnosticContext({stateDir, cliCall, client});
  ctx.directory = PLUGIN_DIR;
  ctx.resolveLiveChat = () => null;
  ctx.sessionDirectory = (id) => sessionDir[id] ?? null;
  return {ctx, prompts, created, box};
}

function seed(ctx, state) {
  writeFileSync(ctx.paths.state, JSON.stringify(state));
}

function state(ctx) {
  return JSON.parse(readFileSync(ctx.paths.state, "utf8"));
}

test("prompt uses the bound session's own directory, not the plugin's", async (t) => {
  const f = setup(t, {history: msg(5),
                      sessionDir: {[CHAT_45]: CHAT_DIR}});
  seed(f.ctx, {sessions: {[task]: CHAT_45}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.prompts.length, 1);
  assert.equal(f.prompts[0].path.id, CHAT_45);
  assert.equal(f.prompts[0].query.directory, CHAT_DIR);
});

test("an Inbox binding keeps the /home/kali directory", async (t) => {
  const f = setup(t, {history: msg(5),
                      sessionDir: {[INBOX]: INBOX_DIR}});
  seed(f.ctx, {sessions: {[task]: INBOX}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.prompts[0].path.id, INBOX);
  assert.equal(f.prompts[0].query.directory, INBOX_DIR);
});

test("rebinding the dialogue switches the directory too", async (t) => {
  const dirs = {[INBOX]: INBOX_DIR, [CHAT_45]: CHAT_DIR};
  const first = setup(t, {history: msg(5), sessionDir: dirs});
  seed(first.ctx, {sessions: {[task]: INBOX}, seen: {}, inflight: {}});
  await runTick(first.ctx);
  assert.equal(first.prompts[0].query.directory, INBOX_DIR);

  first.ctx.sessionDirectory = (id) => dirs[id] ?? null;
  seed(first.ctx, {sessions: {[task]: CHAT_45}, seen: {[task]: 5}, inflight: {}});
  first.box.history = [msg(6)];
  await runTick(first.ctx);
  assert.equal(first.prompts[1].path.id, CHAT_45);
  assert.equal(first.prompts[1].query.directory, CHAT_DIR);
});

test("unknown session directory fails closed instead of prompting", async (t) => {
  const f = setup(t, {history: msg(5), sessionDir: {}});
  seed(f.ctx, {sessions: {[task]: CHAT_45}, seen: {}, inflight: {}});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 0);
  assert.equal(f.prompts.length, 0);
  const st = state(f.ctx);
  assert.equal(st.sessions[task], CHAT_45);
  assert.equal(st.seen[task], undefined);
  assert.equal(st.inflight[task].phase, "reserved");
});

test("a newly adopted live chat sends its own directory", async (t) => {
  const f = setup(t, {history: msg(5), sessionDir: {}});
  f.ctx.resolveLiveChat = () => ({id: CHAT_45, directory: CHAT_DIR});
  f.ctx.sessionDirectory = (id) => (id === CHAT_45 ? CHAT_DIR : null);
  seed(f.ctx, {sessions: {}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.prompts[0].path.id, CHAT_45);
  assert.equal(f.prompts[0].query.directory, CHAT_DIR);
  assert.equal(state(f.ctx).sessions[task], CHAT_45);
});

test("a created session is prompted in the plugin directory", async (t) => {
  const f = setup(t, {history: msg(5), sessionDir: {}});
  seed(f.ctx, {sessions: {}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.created.length, 1);
  assert.equal(f.created[0].arg.query.directory, PLUGIN_DIR);
  assert.equal(f.prompts[0].path.id, "ses_created_0");
  assert.equal(f.prompts[0].query.directory, PLUGIN_DIR);
});

// Fail-closed gate: a bound or adopted session whose directory cannot be
// resolved must never be prompted with the plugin directory. The reservation
// stays durable and no automatic retry happens.
test("missing session row leaves a durable reservation and prompts nothing", async (t) => {
  const f = setup(t, {history: msg(5), sessionDir: {}});
  seed(f.ctx, {sessions: {[task]: CHAT_45}, seen: {}, inflight: {}});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 0);
  assert.equal(f.prompts.length, 0);
  assert.equal(f.created.length, 0);
  const st = state(f.ctx);
  assert.equal(st.inflight[task].phase, "reserved");
  assert.equal(st.inflight[task].seq, 5);
  assert.equal(st.seen[task], undefined);
});

test("a second tick does not retry the fail-closed prompt", async (t) => {
  const f = setup(t, {history: msg(5), sessionDir: {}});
  seed(f.ctx, {sessions: {[task]: CHAT_45}, seen: {}, inflight: {}});
  const first = await runTick(f.ctx);
  const second = await runTick(f.ctx);
  assert.equal(first.dispatched, 0);
  assert.equal(second.dispatched, 0);
  assert.equal(second.held, 1);
  assert.equal(f.prompts.length, 0);
});

test("an SQLite failure resolves to unknown and stays fail-closed", async (t) => {
  const f = setup(t, {history: msg(5), sessionDir: {}});
  f.ctx.sessionDirectory = () => null; // UNKNOWN/error path collapses to null
  seed(f.ctx, {sessions: {[task]: INBOX}, seen: {}, inflight: {}});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 0);
  assert.equal(f.prompts.length, 0);
  assert.equal(state(f.ctx).inflight[task].phase, "reserved");
});

test("a foreign directory is used as-is, never replaced by the plugin one", async (t) => {
  const foreign = "/home/kali/workspace/other-project";
  const f = setup(t, {history: msg(5), sessionDir: {[CHAT_45]: foreign}});
  seed(f.ctx, {sessions: {[task]: CHAT_45}, seen: {}, inflight: {}});
  await runTick(f.ctx);
  assert.equal(f.prompts.length, 1);
  assert.equal(f.prompts[0].query.directory, foreign);
  assert.notEqual(f.prompts[0].query.directory, PLUGIN_DIR);
});

test("successful path clears the reservation and advances seen", async (t) => {
  const f = setup(t, {history: msg(9), sessionDir: {[CHAT_45]: CHAT_DIR}});
  seed(f.ctx, {sessions: {[task]: CHAT_45}, seen: {}, inflight: {}});
  const result = await runTick(f.ctx);
  assert.equal(result.dispatched, 1);
  assert.equal(f.prompts.length, 1);
  assert.equal(f.prompts[0].path.id, CHAT_45);
  assert.equal(f.prompts[0].query.directory, CHAT_DIR);
  const st = state(f.ctx);
  assert.equal(st.seen[task], 9);
  assert.equal(st.inflight[task], undefined);
});
