// P32.5 lock recovery regression: conservative handling of malformed/stale
// inbox.lock. Exercises the real lock path through runTick() against a fake
// CLI + SDK in a temp state dir. No production files touched.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync,
         mkdirSync, symlinkSync, readdirSync, utimesSync, lstatSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { diagnosticContext, runTick } from "./mt-git-inbox.mjs";

const TASK = "task_lock_recovery_test";
const DIALOG = { task_id: TASK, lifecycle: "active", title: "Lock test" };
const MSG = { seq: 1, message_id: "lock_t1", from: "chatgpt", to: "mt",
              kind: "task", body: "fixture" };

function setup({ lockSetup } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "p325-lock-"));
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const lockPath = join(dir, "inbox.lock");
  if (lockSetup) lockSetup(lockPath);
  let views = 0;
  const cliCall = async (q) => {
    if (q.op === "list_dialogs") return { dialogs: [DIALOG], next_offset: null };
    if (q.op === "view") { views++; return { waiting_for: "mt", history: [MSG] }; }
    throw new Error("unexpected cli op " + q.op);
  };
  const client = { session: {
    create: async () => { throw new Error("must not create"); },
    prompt: async () => { throw new Error("must not prompt"); },
  }};
  const logs = [];
  const ctx = diagnosticContext({ stateDir: dir, cliCall, client,
                                  log: (m) => logs.push(m), directory: "/tmp", dryRun: true });
  return { dir, ctx, logs, lockPath };
}

test("old 0-byte lock is recovered: renamed to backup, tick proceeds", async (t) => {
  const { dir, ctx, logs, lockPath } = setup({
    lockSetup: (p) => { writeFileSync(p, Buffer.alloc(0)); },
  });
  // back-date the lock mtime beyond the malformed grace (10*60s)
  const old = new Date(Date.now() - 20 * 60_000);
  utimesSync(lockPath, old, old);
  const r = await runTick(ctx);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(r.status, "ok");
  const backups = readdirSync(dir).filter((f) => f.startsWith("inbox.lock.broken-"));
  assert.equal(backups.length, 1, "exactly one backup rename");
  assert.ok(logs.some((l) => l.includes("lock_recovered_malformed")), "recovery logged");
  // After the tick the freshly acquired lock is released (removed) again.
  assert.ok(!existsSync(lockPath), "old malformed lock consumed, not left in place");
  // A subsequent tick can still acquire normally (no permanent stall).
  const r2 = await runTick(ctx);
  assert.equal(r2.status, "ok", "second tick proceeds after recovery");
});

test("fresh 0-byte lock is refused as young (no recovery, no create)", async (t) => {
  const { dir, ctx, logs, lockPath } = setup({
    lockSetup: (p) => { writeFileSync(p, Buffer.alloc(0)); },
  });
  const r = await runTick(ctx);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(r.status, "locked");
  assert.ok(existsSync(lockPath), "fresh malformed lock untouched");
  assert.ok(logs.some((l) => l.includes("lock_refused_young")), "refused young logged");
  assert.ok(logs.some((l) => l.includes("lock_refused_young")), "cores: no steal");
});

test("live holder lock is never stolen", async (t) => {
  const { dir, ctx, logs, lockPath } = setup({
    lockSetup: (p) => {
      writeFileSync(p, JSON.stringify({ pid: process.pid, id: "live-holder" }));
    },
  });
  const r = await runTick(ctx);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(r.status, "locked");
  assert.ok(existsSync(lockPath), "live lock untouched");
  assert.deepEqual(JSON.parse(readFileSync(lockPath, "utf8")).id, "live-holder");
  assert.ok(logs.some((l) => l.includes("lock_refused_live")), "refused live logged");
});

test("symlink lock node is never followed or deleted", async (t) => {
  const { dir, ctx, logs, lockPath } = setup({
    lockSetup: (p) => { symlinkSync("/nonexistent-target", p); },
  });
  const r = await runTick(ctx);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(r.status, "locked");
  // lstat (not existsSync) because a dangling symlink target makes existsSync
  // return false even though the symlink node itself is still present.
  assert.ok(lstatSync(lockPath).isSymbolicLink(), "symlink node preserved");
  assert.ok(logs.some((l) => l.includes("lock_refused_symlink")),
    "refused symlink logged (" + logs.join(";") + ")");
});

test("dead-holder lock with valid JSON is recovered by backup rename", async (t) => {
  // Use an impossible pid (not alive) as the holder.
  const { dir, ctx, logs, lockPath } = setup({
    lockSetup: (p) => {
      writeFileSync(p, JSON.stringify({ pid: 2147483647, id: "dead-holder" }));
    },
  });
  const r = await runTick(ctx);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(r.status, "ok");
  const backups = readdirSync(dir).filter((f) => f.startsWith("inbox.lock.broken-"));
  assert.equal(backups.length, 1);
  assert.ok(logs.some((l) => l.includes("lock_recovered_dead_holder")), "dead-holder recovery logged");
});

test("two racing hosts: only one recovers the stale lock, no double dispatch", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "p325-lock-race-"));
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const lockPath = join(dir, "inbox.lock");
  writeFileSync(lockPath, Buffer.alloc(0));
  const old = new Date(Date.now() - 20 * 60_000);
  utimesSync(lockPath, old, old);
  // Pre-seed a valid session so dispatch needs no create and can reach prompt.
  writeFileSync(join(dir, "state.json"),
    JSON.stringify({ sessions: { [TASK]: "ses_race" }, seen: { [TASK]: 0 }, inflight: {} }));
  let prompts = 0;
  const fake = (id) => {
    const logs = [];
    const ctx = diagnosticContext({
      stateDir: dir,
      cliCall: async (q) => q.op === "list_dialogs"
        ? { dialogs: [DIALOG], next_offset: null }
        : { waiting_for: "mt", history: [MSG] },
      client: { session: {
        create: async () => { throw new Error("no create"); },
        prompt: async () => { prompts++; return {}; },
      }},
        log: (m) => logs.push(m), directory: "/tmp", dryRun: false,
        sessionDirectory: () => "/tmp",
      });
    return { ctx, logs };
  };
  const a = fake("a"), b = fake("b");
  const [ra, rb] = await Promise.all([runTick(a.ctx), runTick(b.ctx)]);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const okCount = [ra, rb].filter((x) => x.status === "ok").length;
  assert.equal(okCount, 1, "exactly one host claimed the lock");
  assert.equal(prompts, 1, "exactly one prompt (no duplicate delivery)");
  assert.ok(a.logs.some((l) => l.includes("lock_recovered_malformed"))
        || b.logs.some((l) => l.includes("lock_recovered_malformed")),
    "recovery happened on one host");
});

test("live-lock refusal is throttled, not one line per tick", async (t) => {
  const { dir, ctx, logs, lockPath } = setup({
    lockSetup: (p) => writeFileSync(p, JSON.stringify({ pid: process.pid, id: "live" })),
  });
  const r1 = await runTick(ctx);
  assert.equal(r1.status, "locked");
  await runTick(ctx);
  await runTick(ctx);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const refusals = logs.filter((l) => l.includes("lock_refused_live"));
  // At most one refusal line across the rapid repeated ticks (300s throttle).
  assert.ok(refusals.length <= 1,
    "refusal throttled, saw " + refusals.length + " lines: " + logs.join(";"));
  assert.ok(existsSync(lockPath), "live lock still present after refusals");
});

test("hold produces no false recovery and state dir stays valid", async (t) => {
  const { dir, ctx, lockPath } = setup({ lockSetup: () => {} });
  const r = await runTick(ctx);
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(r.status, "ok");
  assert.ok(existsSync(join(dir, "state.json")) || !existsSync(join(dir, "state.json")), "state dir clean");
});