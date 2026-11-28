// P32.5 release-gate isolated tests.
// Imports the LIVE installed plugin. Fake SDK exposes ONLY session.create and
// session.prompt (deliberately NO session.get). Temp state dir. No production
// files/sockets/services touched.
import { mkdtempSync, rmSync, readFileSync, existsSync, mkdirSync, writeFileSync, renameSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import {
  runTick,
  diagnosticContext,
} from "./mt-git-inbox.mjs";

const TASK = "task_df06d95a56c65e80d8a2245386cb2e97";
const MSG = { seq: 900, to: "mt", message_id: "test_only_v1", kind: "task",
              body: "RELEASE GATE TEST — throwaway prompt, fake SDK, never real MT session." };

function freshSdk(behavior, opts = {}) {
  // SDK exposes ONLY session.create and session.prompt.
  const calls = { create: 0, prompt: 0, promptsTo: [], okTo: [], failedTo: [], get: 0 };
  let seq = 0;
  const lives = new Set(opts.liveIds || []);
  const api = {
    session: {
      create: async ({ body, query }) => {
        calls.create++;
        const id = behavior.createBehavior(opts, calls, api);
        if (id instanceof Error) throw id;
        if (id === "__throw__") throw behavior.throwError(opts, "create");
        seq++;
        const fresh = "ses_test_new_" + seq;
        calls._lastCreated = fresh;
        return { data: { id: fresh } };
      },
      prompt: async ({ path }) => {
        calls.prompt++;
        calls.promptsTo.push(path && path.id);
        const err = behavior.promptBehavior(opts, calls, api, path && path.id);
        if (err) {
          calls.failedTo.push(path && path.id);
          throw err;
        }
        calls.okTo.push(path && path.id);
        return { ok: true };
      },
    },
  };
  // Stub in case plugin ever calls get — test must FAIL loudly if it does.
  defineGetter(api.session, () => { calls.get++; });
  return { api, calls };
}

function defineGetter(obj, fn) {
  // Expose get even if absent in production, to prove the plugin never calls it.
  Object.defineProperty(obj, "get", { enumerable: false, configurable: true, get() { fn(); return undefined; } });
}

const behaviors = {
  // target session missing -> confirmed session-not-found on prompt -> recreate once -> prompt to new id
  missingSession: {
    createBehavior() { return undefined; },
    promptBehavior(opts, calls, api, id) {
      if (calls.promptsTo.length === 1 && id === opts.deadId) {
        return mkError("NotFoundError", "Session not found: " + id);
      }
      return null;
    },
    throwError(opts, phase) { return mkError("TimeoutError", "timeout in " + phase); },
  },
  // old session still valid -> one prompt, zero create
  liveSession: {
    createBehavior() { return undefined; },
    promptBehavior() { return null; },
    throwError() { return mkError("Error", "x"); },
  },
  // transient errors -> no recreate, inflight kept
  transient: {
    createBehavior() { return undefined; },
    promptBehavior(opts, calls) {
      if (calls.prompt === 1) return mkError("TimeoutError", "ETIMEDOUT");
      if (calls.prompt === 2) return mkError("TypeError", "undefined is not a function");
      if (calls.prompt === 3) return { status: 503, message: "Service Unavailable" };
      return null;
    },
    throwError() { return mkError("Err", "x"); },
  },
  // NotFound but NOT for the target session (unrelated object) -> no recreate
  unrelatedNotFound: {
    createBehavior() { return undefined; },
    promptBehavior(opts, calls, api, id) {
      if (calls.prompt === 1) return mkError("NotFoundError", "User not found: someone-else");
      return null;
    },
    throwError() { return mkError("Err", "x"); },
  },
  // NotFound shape with no stable session id, but DB probe says session exists -> no recreate
  notFoundButDbAlive: {
    createBehavior() { return undefined; },
    promptBehavior() {
      return mkError("NotFoundError", "Session not found: ses_dead_but_db_says_alive");
    },
    throwError() { return mkError("Err", "x"); },
  },
  // crash / process death during prompt: state durable, no retry of ambiguous
  crash: {
    createBehavior() { return undefined; },
    promptBehavior(opts, calls) {
      if (calls.prompt === 1) return mkError("Error", "process killed OOM");
      return null;
    },
    throwError() { return mkError("Err", "x"); },
  },
};

function mkError(name, message) {
  const e = new Error(message);
  e.name = name;
  return e;
}

function ctxFor(stateDir, sdk, log, opts = {}) {
  let views = 0;
  const cliCall = async (req) => {
    if (req.op === "list_dialogs") {
      return { dialogs: [{ task_id: TASK, lifecycle: "active", title: "P32.5 gate" }] };
    }
    if (req.op === "view") {
      views++;
      return { waiting_for: "mt", history: [MSG] };
    }
    return {};
  };
    const c = diagnosticContext({ stateDir, cliCall, client: sdk.api, log, directory: "/tmp/p325-relgate/dir", dryRun: false,
                                  sessionDirectory: () => "/tmp/p325-relgate/dir" });
  // override sessionExistsInDb with a controllable probe to keep tests hermetic
  c.sessionExists = opts.dbProbe || (() => null);
  c._views = views;
  return c;
}

function setState(stateDir, state) {
  mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  writeAtomic(join(stateDir, "state.json"), state);
}
function writeAtomic(file, obj) {
  const tmp = file + "." + process.pid + "." + randomUUID() + ".tmp";
  writeFileSync(tmp, JSON.stringify(obj), { mode: 0o600 });
  renameSync(tmp, file);
}

const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond, detail });
}

function readState(stateDir) {
  return JSON.parse(readFileSync(join(stateDir, "state.json"), "utf8"));
}

// ---------------------------------------------------------------------------
// TEST 1: missing old session -> confirmed NotFound -> recreate once -> prompt to new id
// ---------------------------------------------------------------------------
{
  const dir = mkdtempSync(join(tmpdir(), "p325-t1-"));
  const sdk = freshSdk(behaviors.missingSession, { deadId: "ses_old_dead_id" });
  setState(dir, { sessions: { [TASK]: "ses_old_dead_id" }, seen: { [TASK]: 898 }, inflight: {} });
  const logs = [];
  const ctx = ctxFor(dir, sdk, (m) => logs.push(m));
  ctx.sessionExists = (id) => (id === "ses_old_dead_id" ? false : null);
  const r = await runTick(ctx);
  const st = readState(dir);
  check("T1 exactly one create", sdk.calls.create === 1);
  check("T1 exactly one successful prompt to new id",
    sdk.calls.okTo.length === 1 && sdk.calls.okTo[0] === sdk.calls._lastCreated &&
    (sdk.calls.okTo[0] !== "ses_old_dead_id"));
  check("T1 mapping updated to new session", st.sessions[TASK] === sdk.calls._lastCreated);
  check("T1 seen advanced", st.seen[TASK] === 900);
  check("T1 inflight cleared", !st.inflight[TASK]);
  check("T1 no session.get call", sdk.calls.get === 0);
  check("T1 logged session_missing_recreated", logs.some((l) => l.includes("session_missing_recreated")));
  rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
// TEST 2: valid old session -> one prompt, zero create
// ---------------------------------------------------------------------------
{
  const dir = mkdtempSync(join(tmpdir(), "p325-t2-"));
  const sdk = freshSdk(behaviors.liveSession, { liveIds: ["ses_valid"] });
  setState(dir, { sessions: { [TASK]: "ses_valid" }, seen: { [TASK]: 898 }, inflight: {} });
  const ctx = ctxFor(dir, sdk, () => {});
  ctx.sessionExists = () => true;
  const r = await runTick(ctx);
  const st = readState(dir);
  check("T2 zero create", sdk.calls.create === 0);
  check("T2 one prompt to saved id", sdk.calls.prompt === 1 && sdk.calls.promptsTo[0] === "ses_valid");
  check("T2 seen advanced", st.seen[TASK] === 900);
  check("T2 inflight cleared", !st.inflight[TASK]);
  check("T2 no session.get call", sdk.calls.get === 0);
  rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
// TEST 3: timeout / TypeError / 5xx -> no recreate, inflight kept (3 sub-cases)
// ---------------------------------------------------------------------------
for (const sub of [
  { name: "timeout", err: mkError("TimeoutError", "timeout") },
  { name: "typeerror", err: mkError("TypeError", "not a function") },
]) {
  const dir = mkdtempSync(join(tmpdir(), "p325-t3-"));
  const behavior = {
    createBehavior() { return undefined; },
    promptBehavior() { return sub.err; },
    throwError() { return sub.err; },
  };
  const sdk = freshSdk(behavior, {});
  setState(dir, { sessions: { [TASK]: "ses_alive" }, seen: { [TASK]: 898 }, inflight: {} });
  const ctx = ctxFor(dir, sdk, () => {});
  ctx.sessionExists = () => true; // session live => even NotFound wouldn't recreate
  await runTick(ctx);
  const st = readState(dir);
  check(`T3 ${sub.name} zero create`, sdk.calls.create === 0);
  check(`T3 ${sub.name} inflight kept`, !!st.inflight[TASK] && st.inflight[TASK].seq === 900);
  check(`T3 ${sub.name} mapping preserved`, st.sessions[TASK] === "ses_alive");
  check(`T3 ${sub.name} seen NOT advanced`, st.seen[TASK] === 898);
  check(`T3 ${sub.name} no session.get`, sdk.calls.get === 0);
  rmSync(dir, { recursive: true, force: true });
}

// 5xx separate
{
  const dir = mkdtempSync(join(tmpdir(), "p325-t3c-"));
  const behavior = {
    createBehavior() { return undefined; },
    promptBehavior() { return { status: 502, name: "Error", message: "Bad Gateway" }; },
    throwError() { return { status: 502, name: "Error", message: "Bad Gateway" }; },
  };
  const sdk = freshSdk(behavior, {});
  setState(dir, { sessions: { [TASK]: "ses_alive" }, seen: { [TASK]: 898 }, inflight: {} });
  const ctx = ctxFor(dir, sdk, () => {});
  ctx.sessionExists = () => true;
  await runTick(ctx);
  const st = readState(dir);
  check("T3 5xx zero create", sdk.calls.create === 0);
  check("T3 5xx inflight kept", !!st.inflight[TASK] && st.inflight[TASK].seq === 900);
  check("T3 5xx mapping preserved", st.sessions[TASK] === "ses_alive");
  check("T3 5xx seen not advanced", st.seen[TASK] === 898);
  rmSync(dir, { recursive: true, force: true });
}

// NotFound unrelated to session -> no recreate, inflight kept
{
  const dir = mkdtempSync(join(tmpdir(), "p325-t3d-"));
  const sdk = freshSdk(behaviors.unrelatedNotFound, {});
  setState(dir, { sessions: { [TASK]: "ses_alive" }, seen: { [TASK]: 898 }, inflight: {} });
  const ctx = ctxFor(dir, sdk, () => {});
  ctx.sessionExists = () => true;
  await runTick(ctx);
  const st = readState(dir);
  check("T3 unrelated-NotFound zero create", sdk.calls.create === 0);
  check("T3 unrelated-NotFound inflight kept", !!st.inflight[TASK]);
  check("T3 unrelated-NotFound mapping preserved", st.sessions[TASK] === "ses_alive");
  rmSync(dir, { recursive: true, force: true });
}

// NotFound-looking error BUT db probe says session alive -> no recreate (fail-closed)
{
  const dir = mkdtempSync(join(tmpdir(), "p325-t3e-"));
  const sdk = freshSdk(behaviors.notFoundButDbAlive, {});
  setState(dir, { sessions: { [TASK]: "ses_dead_but_db_says_alive" }, seen: { [TASK]: 898 }, inflight: {} });
  const logs = [];
  const ctx = ctxFor(dir, sdk, (m) => logs.push(m));
  ctx.sessionExists = () => true; // DB says YES, session row exists
  await runTick(ctx);
  const st = readState(dir);
  check("T3 db-alive zero create", sdk.calls.create === 0);
  check("T3 db-alive inflight kept", !!st.inflight[TASK]);
  check("T3 db-alive mapping preserved", st.sessions[TASK] === "ses_dead_but_db_says_alive");
  check("T3 db-alive no prompt retry (only the original prompt attempt)", sdk.calls.prompt === 1);
  check("T3 db-alive logged recovery_unconfirmed", logs.some((l) => l.includes("session_recovery_unconfirmed")));
  rmSync(dir, { recursive: true, force: true });
}

//

// ---------------------------------------------------------------------------
// TEST 4: concurrency across 2 plugin instances -> no duplicate prompt
// ---------------------------------------------------------------------------
{
  const dir = mkdtempSync(join(tmpdir(), "p325-t4-"));
  const sdk = freshSdk(behaviors.liveSession, { liveIds: ["ses_shared"] });
  setState(dir, { sessions: { [TASK]: "ses_shared" }, seen: { [TASK]: 898 }, inflight: {} });
  const logs = [];
  const ctx1 = ctxFor(dir, sdk, (m) => logs.push("[i1] " + m));
  const ctx2 = ctxFor(dir, sdk, (m) => logs.push("[i2] " + m));
  ctx1.sessionExists = ctx2.sessionExists = () => true;
  // Run both concurrently; exclusive lock serializes them.
  const [a, b] = await Promise.all([runTick(ctx1), runTick(ctx2)]);
  const st = readState(dir);
  check("T4 total prompts exactly one", sdk.calls.prompt === 1);
  check("T4 one instance dispatched, other saw locked", (a.status === "ok" && b.status === "locked") || (a.status === "locked" && b.status === "ok"));
  check("T4 no duplicate create", sdk.calls.create === 0);
  check("T4 seen advanced once", st.seen[TASK] === 900);
  check("T4 inflight cleared", !st.inflight[TASK]);
  rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
// TEST 5: crash during create/prompt -> durable state, no retry of ambiguous
// ---------------------------------------------------------------------------
{
  const dir = mkdtempSync(join(tmpdir(), "p325-t5-"));
  const sdk = freshSdk(behaviors.crash, {});
  setState(dir, { sessions: { [TASK]: "ses_crash" }, seen: { [TASK]: 898 }, inflight: {} });
  const ctx = ctxFor(dir, sdk, () => {});
  ctx.sessionExists = () => true;
  await runTick(ctx);
  const st = readState(dir);
  // state file exists and is valid JSON (durable)
  check("T5 state durable & valid", existsSync(join(dir, "state.json")) && !!st);
  check("T5 inflight kept (reserved/prompt_attempted)", !!st.inflight[TASK] && st.inflight[TASK].seq === 900);
  check("T5 no ambiguous retry (exactly one create or one prompt)", sdk.calls.create + sdk.calls.prompt === 1);
  check("T5 seen NOT advanced", st.seen[TASK] === 898);
  rmSync(dir, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
const pass = results.filter((r) => r.pass).length;
const fail = results.filter((r) => !r.pass);
console.log("=== P32.5 RELEASE GATE TEST RESULTS ===");
for (const r of results) {
  console.log((r.pass ? "PASS " : "FAIL ") + r.name + (r.pass ? "" : "  <-- " + r.detail));
}
console.log("=== TOTALS: PASS=" + pass + " FAIL=" + fail.length + " TOTAL=" + results.length + " ===");
if (fail.length) { console.error("ONE OR MORE TESTS FAILED"); process.exit(1); }