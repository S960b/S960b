// MT Git Inbox — serialized, crash-conservative receiver (P32.4 review).
// No Git writes. One active Git dialogue -> one persistent MT agent session.
// A shared exclusive file lock serializes ALL instances; a durable in-flight
// reservation blocks automatic redispatch after an ambiguous SDK result.
import { appendFileSync, closeSync, existsSync, fsyncSync, lstatSync, watch,
         mkdirSync, openSync, readFileSync, renameSync, unlinkSync,
         writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { spawn, spawnSync } from "node:child_process";

const HOME = "/home/kali";
const CODE = "/home/kali/bridge-pg-v2";
const PYTHON = join(CODE, ".venv/bin/python");
const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox");
const TICK_MS = 30_000; // P36.1: slow backup poll; metadata-only fs.watch is primary
const CLI_TIMEOUT_MS = 60_000;
// Upper bound for one session.prompt. The prompt runs while the exclusive tick
// lock is held, so an unsettled prompt would otherwise stall every dialogue.
// Generous: a real agent turn can legitimately run for many minutes.
const PROMPT_TIMEOUT_MS = 30 * 60_000;
// At most one read-only reconciliation check per held task every 5 minutes.
const INFLIGHT_RECONCILE_MS = 5 * 60_000;
const MAX_DISPATCH_PER_TICK = 2;
// A malformed/0-byte lock is only reaped if it is older than this grace
// period: a brand-new unparseable file is presumed to be a holder that is
// still mid-write. mtime is a conservative gate, never the sole liveness
// decision — live holders are detected by pid, not by mtime.
const LOCK_MALFORMED_GRACE_MS = 10 * TICK_MS;
// Throttle identical lock-refusal diagnostics so a held lock does not spam
// the log every tick while still staying observable.
const LOCK_REFUSAL_THROTTLE_MS = 5 * TICK_MS;
// Heartbeat interval: log a healthy tick even with nothing to dispatch so
// silence is distinguishable from a stopped timer.
const HEARTBEAT_MS = 10 * TICK_MS;

function pathsFor(dir) {
  return { dir, state: join(dir, "state.json"), lock: join(dir, "inbox.lock"),
           reaper: join(dir, "inbox.lock.reaper"), log: join(dir, "inbox.log") };
}
function logger(path) {
  return (message) => {
    try {
      mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
      appendFileSync(path, new Date().toISOString() + " " + message + "\n");
    } catch { /* Logging cannot grant or retry delivery. */ }
  };
}
function own(o) {
  return o && typeof o === "object" && !Array.isArray(o);
}
function emptyState() {
  return { sessions: {}, seen: {}, inflight: {} };
}
function loadState(paths) {
  let value;
  try { value = JSON.parse(readFileSync(paths.state, "utf8")); }
  catch (e) {
    if (e?.code === "ENOENT") return emptyState();
    throw new Error("state_unreadable_or_invalid");
  }
  if (!own(value) || !own(value.sessions) || !own(value.seen) ||
      (value.inflight !== undefined && !own(value.inflight))) {
    throw new Error("state_schema_invalid");
  }
  for (const id of Object.values(value.sessions)) {
    if (typeof id !== "string" || !id) throw new Error("session_state_invalid");
  }
  for (const seq of Object.values(value.seen)) {
    if (!Number.isSafeInteger(seq) || seq < 0) throw new Error("seen_state_invalid");
  }
  const inflight = value.inflight || {};
  for (const item of Object.values(inflight)) {
    if (!own(item) || !Number.isSafeInteger(item.seq) || item.seq < 1 ||
        typeof item.message_id !== "string" || !item.message_id) {
      throw new Error("inflight_state_invalid");
    }
  }
  return { sessions: value.sessions, seen: value.seen, inflight };
}
function syncDir(path) {
  const fd = openSync(path, "r");
  try { fsyncSync(fd); } finally { closeSync(fd); }
}
function saveState(paths, state) {
  mkdirSync(paths.dir, { recursive: true, mode: 0o700 });
  const tmp = paths.state + "." + process.pid + "." + randomUUID() + ".tmp";
  let fd;
  try {
    fd = openSync(tmp, "wx", 0o600);
    writeFileSync(fd, JSON.stringify(state));
    fsyncSync(fd);
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
  renameSync(tmp, paths.state);
  syncDir(paths.dir);
  // Deliberately propagate any write error. Never silently treat a
  // non-durable cursor as an acknowledged delivery.
}
function pidAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return true;
  try { process.kill(pid, 0); return true; }
  catch (e) { return e?.code !== "ESRCH"; }
}
function lockInfo(path) {
  try {
    const data = JSON.parse(readFileSync(path, "utf8"));
    if (Number.isSafeInteger(data?.pid) && data.pid > 0 &&
        typeof data?.id === "string" && data.id) return data;
  } catch {}
  return null; // Uncertain ownership: fail closed.
}
function createExclusive(path, value) {
  const fd = openSync(path, "wx", 0o600);
  try { writeFileSync(fd, JSON.stringify(value)); fsyncSync(fd); }
  finally { closeSync(fd); }
}
function readLockNode(paths, target = paths.lock) {
  // Classify a lock file without following symlinks; never act on an ambiguous
  // or foreign node. Returns a safe static category for diagnostics.
  // `target` lets callers classify the REAPER file: classifying paths.lock here
  // made the reaper see the main lock's owner, so a dead reaper was never
  // reclaimed while any live tick held the main lock.
  let st;
  try { st = lstatSync(target); }
  catch (e) { return e?.code === "ENOENT" ? { kind: "absent" }
    : { kind: "foreign", code: e?.code || (e && e.message ? "read_error" : "read_error") }; }
  if (st.isSymbolicLink()) return { kind: "symlink", stat: st };
  if (!st.isFile()) return { kind: "foreign", stat: st };
  const data = lockInfo(target);
  if (!data) return { kind: "malformed", stat: st };
  return { kind: "ok", data, stat: st };
}
function backupName(prefix) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  return prefix + ".broken-" + ts + "-" + process.pid + "-" + randomUUID().slice(0, 8);
}
const _refusalLast = {};
function throttledLog(log, key, msg) {
  const now = Date.now();
  if (now - (_refusalLast[key] || 0) < LOCK_REFUSAL_THROTTLE_MS) return;
  _refusalLast[key] = now;
  log(msg);
}
function sameNode(a, b) {
  return a && b && a.dev === b.dev && a.ino === b.ino &&
    a.mtimeMs === b.mtimeMs && a.size === b.size;
}
function recoverStaleLock(paths, log) {
  // Conservative recovery. Re-verify twice (inode+mtime) before touching the
  // lock; never steal a live, young, symlinked or unknown node; rename to a
  // timestamped backup rather than deleting, so nothing is lost unguarded.
  const first = readLockNode(paths);
  if (first.kind === "absent") return true; // nothing to reap
  if (first.kind !== "ok" && first.kind !== "malformed") {
    log("lock_refused_" + first.kind + " task_lock " + paths.lock);
    return false;
  }
  const isMalformed = first.kind === "malformed";
  if (!isMalformed && pidAlive(first.data.pid)) {
    throttledLog(log, "live", "lock_refused_live " + paths.lock + " pid=" + first.data.pid);
    return false; // never steal from a live holder
  }
  // Malformed (or dead-holder) node: it must be old enough to be considered
  // conclusively stale, and stable across a second lstat.
  const ageMs = Date.now() - first.stat.mtimeMs;
  if (isMalformed && ageMs < LOCK_MALFORMED_GRACE_MS) {
    throttledLog(log, "young", "lock_refused_young " + paths.lock
      + " age_ms=" + Math.round(ageMs));
    return false; // a fresh unparseable file is likely a holder mid-write
  }
  const second = readLockNode(paths);
  if (second.kind !== first.kind) return false;
  if (!sameNode(first.stat, second.stat)) return false; // changed under us
  if (isMalformed) {
    // Core safety: only rename a CONFIRMED old malformed regular file. The
    // reaper ownership (held exclusively) plus the double lstat makes this
    // safe against a concurrent creator racing to publish a valid lock.
    const backup = backupName(paths.lock);
    try { renameSync(paths.lock, backup); }
    catch { return false; }
    syncDir(paths.dir);
    log("lock_recovered_malformed " + paths.lock + " -> " + backup);
    return true;
  }
  if (!(second.data.id === first.data.id && second.data.pid === first.data.pid)) return false;
  if (pidAlive(second.data.pid)) return false; // re-checked just before rename
  const backup = backupName(paths.lock);
  try { renameSync(paths.lock, backup); }
  catch { return false; }
  syncDir(paths.dir);
  log("lock_recovered_dead_holder " + paths.lock + " pid=" + first.data.pid
    + " -> " + backup);
  return true;
}
function acquireReaper(paths, log, holderId) {
  // Reaper ownership: only one reaper may act at a time. The reaper is itself
  // recoverable: a reaper left by a dead process is renamed to backup.
  const holder = { pid: process.pid, id: holderId };
  try { createExclusive(paths.reaper, holder); return true; }
  catch (e) {
    if (e?.code !== "EEXIST") throw e;
  }
  const node = readLockNode(paths, paths.reaper); // classify the REAPER (present)
  const rnode = { kind: node.kind, data: node.data, stat: node.stat };
  if (rnode.kind === "ok") {
    if (pidAlive(rnode.data.pid)) {
      throttledLog(log, "reaper_live", "lock_refused_reaper_live " + paths.reaper);
      return false; // another live host is reaping
    }
    // Dead reaper holder: reclaim it by backup-rename.
    try {
      const backup = backupName(paths.reaper);
      renameSync(paths.reaper, backup);
      syncDir(paths.dir);
    } catch { return false; }
    try { createExclusive(paths.reaper, holder); return true; }
    catch { return false; }
  }
  if (rnode.kind === "malformed") {
    const ageMs = Date.now() - rnode.stat.mtimeMs;
    if (ageMs < LOCK_MALFORMED_GRACE_MS) {
      throttledLog(log, "reaper_young", "lock_refused_reaper_young " + paths.reaper);
      return false;
    }
    try {
      const backup = backupName(paths.reaper);
      renameSync(paths.reaper, backup);
      syncDir(paths.dir);
    } catch { return false; }
    try { createExclusive(paths.reaper, holder); return true; }
    catch { return false; }
  }
  throttledLog(log, "reaper_foreign", "lock_refused_reaper_foreign " + paths.reaper);
  return false; // symlink/foreign reaper: refuse, never follow
}
function releaseReaper(paths, holderId) {
  try {
    const node = readLockNode(paths, paths.reaper);
    if (node.kind === "ok" && node.data.id === holderId && node.data.pid === process.pid) {
      unlinkSync(paths.reaper);
    }
  } catch {}
}
function reapDeadLock(paths, log, holderId) {
  if (!acquireReaper(paths, log, holderId)) return false;
  try {
    return recoverStaleLock(paths, log);
  } finally {
    releaseReaper(paths, holderId);
  }
}
function acquireLock(paths, log) {
  mkdirSync(paths.dir, { recursive: true, mode: 0o700 });
  const holder = { pid: process.pid, id: randomUUID() };
  try { createExclusive(paths.lock, holder); return holder; }
  catch (e) {
    if (e?.code !== "EEXIST") throw e;
  }
  if (!reapDeadLock(paths, log, holder.id)) return null;
  try { createExclusive(paths.lock, holder); return holder; }
  catch (e) { if (e?.code === "EEXIST") return null; throw e; }
}
function releaseLock(paths, holder) {
  // No release of another instance's lock after a reaper/restart race.
  const current = lockInfo(paths.lock);
  if (current?.id !== holder.id || current.pid !== holder.pid) return;
  unlinkSync(paths.lock);
}

function cli(request, timeoutMs = CLI_TIMEOUT_MS) {
  const op = request?.op;
  let args;
  if (op === "list_dialogs") {
    args = ["list", "--offset", String(request.offset || 0),
            "--limit", String(request.limit || 50)];
  } else if (op === "view") {
    args = ["peek", "--task-id", request.task_id,
            "--offset", String(request.offset || 0),
            "--limit", String(request.limit || 1)];
  } else if (op === "read") {
    args = ["read", "--task-id", request.task_id,
            "--seq", String(request.seq), "--message-id", request.message_id];
  } else if (op === "claim") {
    args = ["claim", "--task-id", request.task_id,
            "--seq", String(request.seq), "--message-id", request.message_id,
            "--consumer", "mt-pg-inbox"];
  } else if (op === "ack") {
    args = ["ack", "--delivery-id", String(request.delivery_id),
            "--lease-token", request.lease_token,
            "--consumer", "mt-pg-inbox"];
  } else {
    return Promise.reject(new Error("pg_unsupported_operation"));
  }
  return new Promise((resolve, reject) => {
    const env = { ...process.env, PYTHONPATH: CODE };
    delete env.APPDIR;
    delete env.APPIMAGE;
    const child = spawn(PYTHON, ["-m", "bridge_pg.cli", ...args], {
      cwd: CODE, env, stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    const killTimer = setTimeout(() => { try { child.kill("SIGKILL"); } catch {} }, timeoutMs);
    child.stdout.on("data", (d) => {
      out += d.toString("utf8");
      if (out.length > 250_000) { try { child.kill("SIGKILL"); } catch {} }
    });
    child.stderr.on("data", () => {});
    child.on("error", () => { clearTimeout(killTimer); reject(new Error("pg_spawn_failed")); });
    child.on("close", (code) => {
      clearTimeout(killTimer);
      const last = out.trim().split("\n").filter(Boolean).pop() || "";
      if (code !== 0 || !last) return reject(new Error("pg_request_failed"));
      let parsed;
      try { parsed = JSON.parse(last); } catch { return reject(new Error("pg_invalid_response")); }
      if (parsed?.ok === false || parsed?.error) return reject(new Error("pg_rejected"));
      if (op === "view") {
        if (!Array.isArray(parsed.messages)) return reject(new Error("pg_invalid_index"));
        return resolve({ ...parsed, history: parsed.messages });
      }
      resolve(parsed);
    });
  });
}
function buildPrompt(dialog, message) {
  return [
    "PostgreSQL Bridge v2. Диалог " + dialog.task_id + " («" + (dialog.title || "без названия") + "»).",
    "Адресовано MT, seq " + message.seq + ", message_id " + message.message_id + ".",
    "", "Текст:", String(message.body || ""), "",
    "Ответ публикуется в PostgreSQL, а НЕ в Git.",
    "Для ответа используй только локальный инструмент:",
    "/home/kali/bridge-pg-v2/.venv/bin/python -m bridge_pg.cli",
    "peek --task-id " + dialog.task_id + " для получения актуального VERSION-токена (поле head).",
    "Сохрани тело ответа в локальном UTF-8 файле и выполни:",
    "append --task-id " + dialog.task_id +
       " --head <ACTUAL_HEAD> --message-id <UNIQUE_ID> --kind report --body-file <PATH>.",
    "Если исход неизвестен, вызови status с тем же message-id; не создавай другой ID для повтора.",
    "Git-операции из старого моста больше не используются для этого task_id.",
  ].join("\n");
}
function latestPending(snapshot, seenSeq) {
  if (snapshot?.waiting_for !== "mt" || !Array.isArray(snapshot.history)) return null;
  // A turn-based dialogue has ONE currently actionable MT-directed message.
  // Never replay older messages from the same newest-first history page.
  const last = snapshot.history[0];
  if (last?.to !== "mt" || last.kind === "return" ||
      !Number.isSafeInteger(last.seq) ||
      last.seq <= seenSeq || typeof last.message_id !== "string") return null;
  return last;
}
async function listAll(cliCall) {
  const all = [];
  let offset = 0;
  for (let page = 0; page < 100; page++) {
    const result = await cliCall({ op: "list_dialogs", offset, limit: 50 });
    if (!Array.isArray(result?.dialogs)) throw new Error("invalid_dialog_listing");
    all.push(...result.dialogs);
    if (result.next_offset == null) return all;
    if (!Number.isSafeInteger(result.next_offset) || result.next_offset <= offset)
      throw new Error("invalid_dialog_pagination");
    offset = result.next_offset;
  }
  throw new Error("dialog_page_limit");
}
function looksLikeMissingSession(e) {
  // Weak signals only. Never sufficient on its own: must be confirmed against
  // the application database before any recreation.
  const status = e?.status ?? e?.response?.status ?? e?.data?.status ?? e?.cause?.status;
  const parts = [e?.name, e?.code, e?.message, e?.error, e?.data?.message,
                 e?.cause?.message, e?.cause?.name, String(e?.constructor?.name || "")];
  try { parts.push(JSON.stringify(e)); } catch (_) {}
  const text = parts.filter((x) => typeof x === "string" && x).join(" ");
  return status === 404 || e?.code === "NOT_FOUND" || /not\s*found/i.test(text)
    || /NotFound/i.test(text) || /Unexpected end of JSON input/i.test(text);
}

function sessionExistsInDb(sessionId) {
  // Objective check: the session table is the source of truth. Read-only probe.
  // Returns true / false / null (unknown -> caller must NOT recreate).
  if (typeof sessionId !== "string" || !sessionId) return null;
  const py = "\nimport sqlite3,sys\n" +
    "try:\n" +
    "    c=sqlite3.connect('file:/home/kali/.local/share/gigatool/opencode.db?mode=ro',uri=True)\n" +
    "    n=c.execute('select count(*) from session where id=?',(sys.argv[1],)).fetchone()[0]\n" +
    "    print('YES' if n else 'NO')\n" +
    "except Exception:\n" +
    "    print('UNKNOWN')\n";
  try {
    const r = spawnSync("/usr/bin/python3", ["-c", py, sessionId], { timeout: 5000, encoding: "utf8" });
    const out = String(r?.stdout || "").trim();
    if (out === "YES") return true;
    if (out === "NO") return false;
    return null;
  } catch (_) {
    return null;
  }
}

function sessionDirectoryInDb(sessionId) {
  // The directory a session was created in. The SDK resolves the request
  // workspace from query.directory, so writing to a session from a different
  // directory runs the turn against the wrong workspace. Read-only and bounded;
  // null means unknown and the caller falls back to its own directory.
  if (typeof sessionId !== "string" || !sessionId) return null;
  const py = "\nimport sqlite3,sys\n" +
    "try:\n" +
    "    c=sqlite3.connect('file:/home/kali/.local/share/gigatool/opencode.db?mode=ro',uri=True)\n" +
    "    r=c.execute('select directory from session where id=?',(sys.argv[1],)).fetchone()\n" +
    "    print(r[0] if r and r[0] else 'NONE')\n" +
    "except Exception:\n" +
    "    print('UNKNOWN')\n";
  try {
    const r = spawnSync("/usr/bin/python3", ["-c", py, sessionId], { timeout: 5000, encoding: "utf8" });
    const out = String(r?.stdout || "").trim();
    if (!out || out === "NONE" || out === "UNKNOWN") return null;
    return out.startsWith("/") ? out : null;
  } catch (_) {
    return null;
  }
}

function isSessionNotFound(e) {
  // SDK errors arrive in several shapes: {name,message}, {status}, {data:{message}},
  // wrapped causes, or plain strings. Match on a flattened text, never on structure.
  const status = e?.status ?? e?.response?.status ?? e?.data?.status ?? e?.cause?.status;
  const parts = [e?.name, e?.code, e?.message, e?.error, e?.data?.message,
                 e?.cause?.message, e?.cause?.name, String(e?.constructor?.name || "")];
  try { parts.push(JSON.stringify(e)); } catch (_) {}
  const text = parts.filter((x) => typeof x === "string" && x).join(" ");
  if (status === 404 || e?.code === "NOT_FOUND"
      || /not\s*found/i.test(text) || /NotFound/i.test(text)) return true;
  // Observed in production: writing to a deleted session returns an empty body,
  // so the SDK fails while parsing it. Treat that specific parse failure as a
  // missing session; any other syntax error stays transient.
  return /Unexpected end of JSON input/i.test(text);
}
function latestSessionForDirectory(directory) {
  // First-contact hint only: the chat this plugin instance was started for.
  // Read-only and bounded; any uncertainty returns null so the caller falls back
  // to creating its own session. Never used when a durable binding already
  // exists, so it can never move an established dialogue to another chat.
  if (typeof directory !== "string" || !directory) return null;
  const py = "\nimport sqlite3,sys\n" +
    "try:\n" +
    "    c=sqlite3.connect('file:/home/kali/.local/share/gigatool/opencode.db?mode=ro',uri=True)\n" +
    "    q=('select id from session where directory=? '\n" +
    "       'order by time_updated desc limit 1')\n" +
    "    r=c.execute(q,(sys.argv[1],)).fetchone()\n" +
    "    print(r[0] if r else 'NONE')\n" +
    "except Exception:\n" +
    "    print('UNKNOWN')\n";
  try {
    const r = spawnSync("/usr/bin/python3", ["-c", py, directory], { timeout: 5000, encoding: "utf8" });
    const out = String(r?.stdout || "").trim();
    if (!out || out === "NONE" || out === "UNKNOWN") return null;
    return /^ses_[A-Za-z0-9]+$/.test(out) ? { id: out, directory } : null;
  } catch (_) {
    return null;
  }
}

async function dispatch(ctx, dialog, message, state) {
  const key = dialog.task_id;
  if (!ctx.client?.session?.create || !ctx.client?.session?.prompt) {
    ctx.log("sdk_session_api_missing");
    return false;
  }
  // Save a durable claim BEFORE any potentially irreversible SDK call.
  // An ambiguous create/prompt failure is NEVER automatically retried.
  // PostgreSQL outbox claim is atomic with cancel/return-turn checks.
  // Never dispatch an already returned message.
  const lease = await ctx.cliCall({op:"claim", task_id:key, seq:message.seq,
                                   message_id:message.message_id});
  if (!lease?.claimed || !lease?.lease_token || !lease?.delivery_id) return false;
  state.inflight[key] = { seq: message.seq, message_id: message.message_id,
                         phase: "reserved", delivery_id: lease.delivery_id,
                         lease_token: lease.lease_token };
  saveState(ctx.paths, state);
  // Routing is deterministic: a durable binding for this dialogue always wins,
  // so the destination cannot drift when the owner switches chats or when the
  // app restarts and loads the plugin again. The live-chat lookup is only a
  // first-contact hint for a dialogue that has no binding yet; whatever it
  // resolves is persisted immediately and then stays authoritative.
  let sessionID = state.sessions[key];
  // Resolved below: null means "not known yet" and forces the fail-closed gate.
  let promptDirectory = null;
  if (typeof sessionID === "string" && sessionID) {
    if (ctx.log) ctx.log("route_durable_binding " + key + " session=" + sessionID);
  }
  // P35+: never adopt a pre-existing chat for a new dialogue. Adoption bound
  // bridge work to whatever chat happened to be newest in the plugin directory
  // (in practice the LinkedIn cron session in /home/kali), which the owner
  // cannot see in the sidebar. A dialogue without a durable binding always gets
  // its own dedicated, owner-visible session below.
  if (!sessionID) {
    delete state.sessions[key];
    state.inflight[key].phase = "create_attempted";
    saveState(ctx.paths, state);
    const created = await ctx.client.session.create({
      body: { title: "Bridge: " + (dialog.title || key.slice(0, 12)) },
      query: { directory: ctx.bridgeDirectory },
    });
    sessionID = created?.data?.id ?? created?.id;
    if (typeof sessionID !== "string" || !sessionID)
      throw new Error("sdk_session_create_unconfirmed");
    state.sessions[key] = sessionID;
    state.inflight[key].phase = "session_created";
    saveState(ctx.paths, state);
    // A session we just created is known to live in bridgeDirectory; that is
    // the only case where it is an acceptable prompt directory.
    promptDirectory = ctx.bridgeDirectory;
  }
  // The SDK's InstanceMiddleware resolves the workspace from `query.directory`
  // (query -> x-opencode-directory -> cwd) and seeds the whole request with it,
  // while the session itself is looked up by id alone. Prompting a session
  // whose directory cannot be resolved would therefore silently run the turn in
  // the wrong workspace. Fail closed instead: keep the durable reservation, log
  // a static category, and never retry automatically.
  if (promptDirectory === null) {
    const boundDirectory = ctx.sessionDirectory
      ? ctx.sessionDirectory(sessionID) : null;
    if (!boundDirectory) {
      if (ctx.log) ctx.log("route_session_directory_unknown " + key + " session=" + sessionID);
      return false;
    }
    promptDirectory = boundDirectory;
    if (ctx.log && boundDirectory !== ctx.directory)
      ctx.log("route_session_directory " + key + " using=" + boundDirectory);
  }
  state.inflight[key].phase = "prompt_attempted";
  saveState(ctx.paths, state);
  // The directory is a parameter, never captured: a recovery recreates the
  // session in a possibly different directory, and the retry must send the
  // prompt into the same directory the session was just created in.
  // The prompt is bounded: it runs while the cross-instance tick lock is held,
  // and a prompt that never settles used to keep that lock forever, stalling
  // every other dialogue. A timeout leaves the durable reservation in place
  // (never auto-retried) instead of blocking the whole bridge indefinitely.
  const send = (directory) => {
    const request = ctx.client.session.prompt({
      path: { id: sessionID }, query: { directory },
      body: { parts: [{ type: "text", text: buildPrompt(dialog, message) }] },
    });
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("sdk_prompt_timeout")), PROMPT_TIMEOUT_MS);
      if (typeof timer?.unref === "function") timer.unref();
    });
    return Promise.race([request, timeout]).finally(() => clearTimeout(timer));
  };
  try {
    await send(promptDirectory);
  } catch (e) {
    // The SDK has no reliable session.get in the plugin client, so recovery is
    // driven by the write itself: a CONFIRMED "not found" is deterministic and
    // safe to repair exactly once by creating a fresh session.
    if (!looksLikeMissingSession(e)) throw e;
    const probe = ctx.sessionExists ? ctx.sessionExists(sessionID) : sessionExistsInDb(sessionID);
    if (probe !== false) {
      // Not confirmed as deleted (or unknown): stay fail-closed, keep inflight.
      ctx.log("session_recovery_unconfirmed " + key + " exists=" + String(probe));
      throw e;
    }
    delete state.sessions[key];
    saveState(ctx.paths, state);
    ctx.log("session_missing_recreated " + key);
    state.inflight[key].phase = "create_attempted";
    saveState(ctx.paths, state);
    // A replacement session is created in the plugin's own directory, so the
    // prompt that follows must use exactly that directory too. Anything else
    // would resolve the request workspace to a directory the session does not
    // live in — the failure mode this gate exists to prevent.
    const recoveryDirectory = ctx.bridgeDirectory;
    const created = await ctx.client.session.create({
      body: { title: "Bridge: " + (dialog.title || key.slice(0, 12)) },
      query: { directory: recoveryDirectory },
    });
    sessionID = created?.data?.id ?? created?.id;
    if (typeof sessionID !== "string" || !sessionID)
      throw new Error("sdk_session_create_unconfirmed");
    state.sessions[key] = sessionID;
    state.inflight[key].phase = "session_created";
    saveState(ctx.paths, state);
    state.inflight[key].phase = "prompt_attempted";
    saveState(ctx.paths, state);
    await send(recoveryDirectory);
  }
  // No 'seen' receipt until PostgreSQL confirms this specific lease.
  const acked = await ctx.cliCall({op:"ack",delivery_id:lease.delivery_id,
                                  lease_token:lease.lease_token});
  if (!acked?.acked) throw new Error("pg_ack_unconfirmed");
  state.seen[key] = message.seq;
  delete state.inflight[key];
  saveState(ctx.paths, state);
  ctx.log("delivered " + key + " seq=" + message.seq + " to one MT session");
  return true;
}

function confirmedMtResponseAfter(snapshot, taskId, reserved) {
  // The local direct_git CLI validates the Git history; it returns last N
  // messages in ascending order, regardless of the unused "order" hint.
  if (snapshot?.task_id !== taskId || !/^[0-9a-f]{40}$/.test(snapshot?.head) ||
      !Number.isSafeInteger(snapshot?.seq) || snapshot.seq <= reserved.seq ||
      !Array.isArray(snapshot?.history) || snapshot.history.length > 100) return false;

  const sent = snapshot.history.find((m) =>
    m?.seq === reserved.seq && m.message_id === reserved.message_id &&
    m.from === "chatgpt" && m.to === "mt");
  const answered = snapshot.history.find((m) =>
    m?.seq === reserved.seq + 1 && m.from === "mt" && m.to === "chatgpt" &&
    ["report", "ack", "task", "close"].includes(m.kind));
  // No inference based on elapsed time, SDK status, a newer head alone, or
  // another unrelated MT answer. The exact immutable Git turn must be visible.
  return Boolean(sent && answered);
}

async function reconcileAnsweredInflight(ctx, state, dialog) {
  const taskId = dialog.task_id, reserved = state.inflight[taskId];
  if (!reserved) return false;
  const now = Date.now();
  if (Number.isSafeInteger(reserved.reconcile_checked_ms) &&
      reserved.reconcile_checked_ms <= now &&
      reserved.reconcile_checked_ms > now - INFLIGHT_RECONCILE_MS) return false;

  // The cross-process lock in runTick protects this durable state update.
  // Throttling bounds GitHub API consumption while SDK outcome is unknown.
  reserved.reconcile_checked_ms = now;
  saveState(ctx.paths, state);
  let snapshot;
  try {
    snapshot = await ctx.cliCall({ op: "view", task_id: taskId,
                                  offset: 0, limit: 100, order: "newest" });
  } catch {
    ctx.log("inflight_reconcile_read_unavailable " + taskId);
    return false;
  }
  if (!confirmedMtResponseAfter(snapshot, taskId, reserved)) return false;

  state.seen[taskId] = Math.max(state.seen[taskId] || 0, reserved.seq);
  delete state.inflight[taskId];
  saveState(ctx.paths, state);
  ctx.log("inflight_reconciled_confirmed_response " + taskId +
          " seq=" + reserved.seq);
  return true;
}

export async function runTick(ctx) {
  const holder = acquireLock(ctx.paths, ctx.log);
  if (!holder) return { status: "locked" };
  try {
    // IMPORTANT: read state ONLY AFTER acquiring exclusive cross-instance lock.
    const state = loadState(ctx.paths);
    const dialogs = await listAll(ctx.cliCall);
    // Auto-clear reservations whose dialogue the owner deleted: a tombstoned
    // or vanished dialogue can never be dispatched again, so its inflight
    // record is stale by definition and must not block future ticks.
    const lifecycle = new Map();
    for (const d of dialogs) if (d && d.task_id) lifecycle.set(d.task_id, d.lifecycle || "active");
    let cleared = 0;
    for (const taskId of Object.keys(state.inflight)) {
      // A dry-run may inspect and log, but must not acknowledge or clear
      // durable delivery records, including tombstoned reservations.
      if (ctx.dryRun) continue;
      const name = lifecycle.get(taskId);
      if (name === "active") continue;
      delete state.inflight[taskId];
      cleared++;
      ctx.log("inflight_cleared_deleted " + taskId + (name ? " (" + name + ")" : " (missing)"));
    }
    if (cleared) saveState(ctx.paths, state);
    let dispatched = 0, held = 0;
    for (const dialog of dialogs) {
      if (dispatched >= MAX_DISPATCH_PER_TICK) break;
      if (!dialog?.task_id || (dialog.lifecycle && dialog.lifecycle !== "active")) continue;
      // Allowlist gate: an excluded dialogue is not viewed, not reconciled and
      // not counted as held, so it costs no GitHub calls at all.
      if (ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)) continue;
      if (state.inflight[dialog.task_id]) {
        if (!ctx.dryRun && await reconcileAnsweredInflight(ctx, state, dialog)) cleared++;
        else {
          held++;
          ctx.log("inflight_manual_reconciliation_required " + dialog.task_id);
          continue;
        }
      }
      let snapshot;
      try {
        snapshot = await ctx.cliCall({ op: "view", task_id: dialog.task_id,
                                      offset: 0, limit: 1, order: "newest" });
      } catch {
        ctx.log("dialog_view_failed " + dialog.task_id);
        continue;
      }
      const msg = latestPending(snapshot, state.seen[dialog.task_id] || 0);
      if (!msg) continue;
      let full;
      try {
        full = await ctx.cliCall({ op: "read", task_id: dialog.task_id,
                                   seq: msg.seq, message_id: msg.message_id });
        if (!full || full.seq !== msg.seq || full.message_id !== msg.message_id ||
            full.to !== "mt" || typeof full.body !== "string") {
          throw new Error("direct_git_message_mismatch");
        }
      } catch {
        ctx.log("direct_git_read_failed " + dialog.task_id);
        continue;
      }
      if (ctx.dryRun) {
        ctx.log("dry_run " + dialog.task_id + " seq=" + msg.seq);
        continue; // never acknowledge a message in dry-run
      }
      try {
        if (await dispatch(ctx, dialog, full, state)) dispatched++;
      } catch (e) {
        // Durable in-flight state stays written; no automatic replay.
        const kind = isSessionNotFound(e) ? "session_not_found" : "transient";
        let shape = "";
        try {
          // Static categories only: exception text may echo session ids,
          // request bodies or provider replies, so it never reaches the log.
          shape = JSON.stringify({
            name: String(e?.name || "").slice(0, 40),
            code: String(e?.code || "").slice(0, 40),
            status: e?.status ?? null,
          });
        } catch (_) { shape = "unserializable"; }
        ctx.log("dispatch_uncertain_manual_review " + dialog.task_id
                + " seq=" + msg.seq + " (" + kind + ") " + shape);
      }
    }
    return { status: "ok", dispatched, held, cleared };
  } finally {
    releaseLock(ctx.paths, holder);
  }
}

export function diagnosticContext({ stateDir, cliCall, client, log = () => {},
                                    directory = "/tmp", dryRun = false,
                                    sessionDirectory = null }) {
  // Only used by isolated test fixtures; production server below hardcodes
  // its private state and real Unix authority. sessionDirectory defaults to
  // null, which makes the fail-closed gate refuse to prompt a bound session —
  // fixtures must opt in explicitly, the same way production resolves it.
  return { paths: pathsFor(stateDir), cliCall, client, log, directory, dryRun,
           sessionDirectory };
}
export const server = async (input = {}, options = {}) => {
  try {
    return await startServer(input, options);
  } catch (e) {
    // A plugin that fails to load is invisible in normal logs; record the exact
    // reason outside the app so the failure is diagnosable at all.
    try {
      // Static class only: no message text, no stack, no request data.
      appendFileSync("/tmp/mt-inbox-load-error.log",
        new Date().toISOString() + " load_failed class=" +
        String(e?.name || "Error").slice(0, 40) + "\n");
    } catch (_) {}
    throw e;
  }
};

const startServer = async (input = {}, options = {}) => {
  const dryRun = options.dry_run !== false;
  const tickMs = Number(options.tick_ms) > 0 ? Number(options.tick_ms) : TICK_MS;
  // Optional allowlist: when task_ids is given, only those dialogues are polled.
  // An empty or malformed list is treated as "no filter" so a typo can never
  // silently stop every delivery, and unknown ids are dropped rather than
  // accepted as free-form text.
  const taskIds = Array.isArray(options.task_ids)
    ? options.task_ids.filter((x) => typeof x === "string" && /^task_[0-9a-f]{32}$/.test(x))
    : null;
  const paths = pathsFor(STATE_DIR);
  const log = logger(typeof options.log_file === "string"
    ? options.log_file : paths.log);
      const bridgeDirectory = join(HOME, "Documents/MultiTool/HomeChats/Bridge");
      try { mkdirSync(bridgeDirectory, { recursive: true, mode: 0o755 }); } catch (_) {}
      const ctx = { paths, cliCall: cli, client: input?.client,
                    directory: input?.directory || join(HOME, "Documents/MultiTool/HomeChats"),
                    bridgeDirectory,
                    resolveLiveChat: () => latestSessionForDirectory(
                      input?.directory || join(HOME, "Documents/MultiTool/HomeChats")),
                    sessionDirectory: sessionDirectoryInDb,
                    taskIds: taskIds && taskIds.length ? taskIds : null,
                    dryRun, log };
  let busy = false;
  let active = null;
  let disposed = false;
  let lastOkLog = 0;
  log("plugin loaded; dry_run=" + dryRun + "; tick_ms=" + tickMs
    + "; task_filter=" + (ctx.taskIds ? ctx.taskIds.length : "none"));
  const trigger = () => {
    if (disposed || busy) return;
    busy = true;
    active = runTick(ctx)
      .then((r) => {
        const now = Date.now();
        if (r && r.status === "ok" && now - lastOkLog >= HEARTBEAT_MS) {
          lastOkLog = now;
          log("tick_ok dispatched=" + r.dispatched + " held=" + r.held
              + " cleared=" + r.cleared + " state_json=ok");
        }
      })
      .catch(() => log("tick_error"))
      .finally(() => { busy = false; active = null; });
  };
  // Fast wakeup from an already-verified Git commit notification. The file
  // contains only identifiers/commit SHA, NEVER message body. A missed watch
  // event is harmless: periodic Git polling remains a recovery fallback.
  let observer = null;
  try {
    mkdirSync(STATE_DIR, {recursive:true, mode:0o700});
    observer = watch(STATE_DIR, { persistent:false }, (_event, name) => {
      if (String(name || "") === "p36-notice.json") trigger();
    });
  } catch {
    log("p36_wakeup_watch_unavailable");
  }
  const timer = setInterval(trigger, tickMs);
  if (typeof timer.unref === "function") timer.unref();
  return {
    dispose: async () => {
      disposed = true;
      clearInterval(timer);
      if (observer) observer.close();
      if (active) await active; // do not release lock halfway through prompt
      log("plugin disposed");
    },
  };
};
export default server;
