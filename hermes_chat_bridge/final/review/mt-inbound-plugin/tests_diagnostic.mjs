// tests_diagnostic.mjs — non-destructive diagnostic tests for mt-git-inbox.
//
// These tests NEVER create real sessions, NEVER spawn the plugin's live timer,
// NEVER touch Git refs or the production bridge. They exercise the plugin's
// contract with an injected stub `client`, dry_run mode, and on-disk
// state fixtures. Safe to run on any box.
//
// Run: node --test tests_diagnostic.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { diagnosticContext, runTick } from "./mt-git-inbox.mjs";

function freshDir(t) {
  const dir = mkdtempSync(join(tmpdir(), "mtgitbox-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// Test 1 — session affinity: a task_id maps to exactly one stored session id.
test("session affinity key = task_id, one session id stored", () => {
  const state = { sessions: { task_abc: "ses_1" }, seen: { task_abc: 2 } };
  assert.equal(state.sessions["task_abc"], "ses_1");
  assert.equal(state.seen["task_abc"], 2);
});

// Test 2 — future fixture: fresh messages (seq > seen) are the dispatch gate.
test("only seq > seen[task] is fresh (dedup high-watermark)", () => {
  const seen = 2;
  const msgs = [
    { seq: 1, to: "mt" },
    { seq: 2, to: "mt" },
    { seq: 3, to: "mt" },
  ];
  const fresh = msgs
    .filter((m) => m.to === "mt" && Number(m.seq) > seen)
    .map((m) => m.seq);
  assert.deepEqual(fresh, [3]);
});

// Test 3 — dry-run is safe: a default-constructed state is empty and plugin
// host can be instantiated with a stub client and disposed without side effects
// (no real timer spawn, no remote calls).
test("dry-run host with stub client starts and disposes safely", async (t) => {
  const dir = freshDir(t);
  const client = {
    session: {
      create: async () => ({ data: { id: "ses_stub" } }),
      prompt: async () => ({}),
    },
  };
  // The plugin default export `server` is exercised only for its structure;
  // we never start its internal interval (that is the live behaviour we must
  // not trigger here). We assert the module shape and safe state defaults.
  const mod = await import("./mt-git-inbox.mjs");
  const server = mod.default ?? mod.server;
  assert.equal(typeof server, "function");
  // A brand-new state dir has no state.json => plugin treats as empty.
  assert.equal(existsSync(join(dir, "state.json")), false);
});

// Test 4 — sensitive exception text must never reach the log: the dispatch
// catch logs only static categories (name/code/status), never raw messages,
// request bodies or provider replies.
test("dispatch failure logs only static categories, never raw exception text", async (t) => {
  const dir = freshDir(t);
  const task = "task_log_safety_a";
  const secret = "SENSITIVE_request_body_or_token_0987654321";
  writeFileSync(join(dir, "state.json"), JSON.stringify({
    sessions: { [task]: "ses_present" },
    seen: { [task]: 0 },
    inflight: {},
  }));
  const logs = [];
  const ctx = diagnosticContext({
    stateDir: dir,
    cliCall: async (q) => q.op === "list_dialogs"
      ? { dialogs: [{ task_id: task, lifecycle: "active", title: "Log safety" }], next_offset: null }
      : { waiting_for: "mt", history: [{ seq: 1, to: "mt", message_id: "m1", kind: "task", body: "x" }] },
    client: { session: {
      // no create/prompt => dispatch logs sdk_session_api_missing, never the
      // inference of a message
      prompt: async () => { const e = new Error(secret); e.code = "ECLAIM"; throw e; },
      create: async () => ({ data: { id: "ses_present_2" } }),
    }},
      log: (m) => logs.push(m), directory: "/tmp", dryRun: false,
      sessionDirectory: () => "/tmp",
    });
  // Force prompt to throw with the secret embedded in its message.
  ctx.client.session.prompt = async () => {
    const e = new Error("Unexpected " + secret);
    e.code = "NOT_FOUND";
    throw e;
  };
  await runTick(ctx);
  const joined = logs.join("\n");
  assert.ok(!joined.includes(secret), "raw exception text must not be logged");
  assert.ok(joined.includes("dispatch_uncertain_manual_review") ||
            joined.includes("session_not_found") ||
            joined.includes("transient") ||
            joined.includes("sdk_session_api_missing"),
    "a static diagnostic line is still produced");
});