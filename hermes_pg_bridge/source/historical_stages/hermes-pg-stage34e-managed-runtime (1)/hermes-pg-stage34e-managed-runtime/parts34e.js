// Stage 34E: source fragments, inserted only into a pinned and unregistered candidate.
// This file is a PATCH SOURCE, not a plugin, and must never be loaded by MultiTool.

// BEGIN STAGE34E CLI
function cli(request, timeoutMs = CLI_TIMEOUT_MS) {
  const op = request?.op;
  const taskId = request?.task_id;
  let args;
  if (op === "list_dialogs") {
    args = ["list", "--offset", String(request.offset ?? 0),
            "--limit", String(request.limit ?? 50)];
  } else if (op === "view") {
    args = ["peek", "--task-id", taskId,
            "--offset", String(request.offset ?? 0),
            "--limit", String(request.limit ?? 1)];
  } else if (op === "read") {
    args = ["read", "--task-id", taskId,
            "--seq", String(request.seq), "--message-id", request.message_id];
  } else if (op === "claim") {
    args = ["claim", "--task-id", taskId,
            "--seq", String(request.seq), "--message-id", request.message_id,
            "--consumer", "mt-pg-managed"];
  } else if (op === "ack") {
    // Stage34D managed CLI requires BOTH task ID and seq for identity-bound ACK.
    args = ["ack", "--task-id", taskId, "--seq", String(request.seq),
            "--delivery-id", String(request.delivery_id),
            "--lease-token", request.lease_token, "--consumer", "mt-pg-managed"];
  } else {
    return Promise.reject(new Error("managed_operation_denied"));
  }
  if (args.some((arg) => typeof arg !== "string" || !arg || arg.length > 512)) {
    return Promise.reject(new Error("managed_cli_args_invalid"));
  }
  return new Promise((resolve, reject) => {
    const env = { ...process.env, PYTHONPATH: CODE };
    delete env.APPDIR;
    delete env.APPIMAGE;
    const child = spawn(PYTHON, [
      "/home/kali/hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py", ...args
    ], { cwd: CODE, env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let timedOut = false;
    const killer = setTimeout(() => {
      timedOut = true;
      try { child.kill("SIGKILL"); } catch {}
    }, timeoutMs);
    child.stdout.on("data", (b) => {
      out += b.toString("utf8");
      if (out.length > 250_000) {
        timedOut = true;
        try { child.kill("SIGKILL"); } catch {}
      }
    });
    child.stderr.on("data", () => {});
    child.on("error", () => { clearTimeout(killer); reject(new Error("managed_cli_spawn_failed")); });
    child.on("close", (code) => {
      clearTimeout(killer);
      if (timedOut || code !== 0) return reject(new Error("managed_cli_unconfirmed"));
      const line = out.trim().split("\n").filter(Boolean).pop() || "";
      let parsed;
      try { parsed = JSON.parse(line); } catch { return reject(new Error("managed_cli_bad_json")); }
      if (!parsed || parsed.ok === false || parsed.error) return reject(new Error("managed_cli_rejected"));
      if (op === "view" && !Array.isArray(parsed.history))
        return reject(new Error("managed_cli_bad_view"));
      resolve(parsed);
    });
  });
}
// END STAGE34E CLI

// BEGIN STAGE34E PROMPT
function managedReportId(taskId, seq) {
  if (!/^task_[0-9a-f]{32}$/.test(taskId) ||
      !Number.isSafeInteger(seq) || seq < 1) throw new Error("managed_report_id_invalid");
  return "pgv2-mg-" + taskId.slice(5) + "-" + seq.toString(36) + "-report";
}
function buildPrompt(dialog, message) {
  const reportId = managedReportId(dialog.task_id, message.seq);
  return [
    "PostgreSQL Managed Bridge. Диалог " + dialog.task_id + " («" + (dialog.title || "без названия") + "»).",
    "Входящее задание seq=" + message.seq + ", message_id=" + message.message_id + ".",
    "", "Задание:", String(message.body || ""), "",
    "Работай только над этим заданием. Выполняй только разрешённые владельцем действия.",
    "Ответ отправь ТОЛЬКО в PostgreSQL, никаких Git-операций.",
    "Используй локальный CLI под ролью bridge_agent:",
    "/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage34d-write-adapter/managed_agent_cli34d.py",
    "Сначала: peek --task-id " + dialog.task_id + " (получить актуальный head).",
    "Создай приватный UTF-8 файл с ответом в /home/kali/ и chmod 600; размер до 32 KiB.",
    "Однократная отправка отчёта:",
    "append --task-id " + dialog.task_id + " --consumer mt-pg-managed --head <ACTUAL_HEAD>" +
      " --message-id " + reportId + " --kind report --body-file <PRIVATE_0600_FILE>",
    "Если отправка завершилась неопределённо, проверяй:",
    "status --task-id " + dialog.task_id + " --message-id " + reportId,
    "Не создавай другой message_id и не отправляй отчёт повторно без проверки статуса.",
  ].join("\n");
}
// END STAGE34E PROMPT

// BEGIN STAGE34E CONFIRMED
function confirmedMtResponseAfter(snapshot, taskId, reserved) {
  if (snapshot?.task_id !== taskId ||
      !Number.isSafeInteger(snapshot?.seq) || snapshot.seq < reserved.seq + 1 ||
      !Array.isArray(snapshot.history) || snapshot.history.length !== 2) return false;
  const sent = snapshot.history.find((m) =>
    m?.seq === reserved.seq && m.message_id === reserved.message_id &&
    m.from === "chatgpt" && m.to === "mt" && m.kind === "task");
  const answered = snapshot.history.find((m) =>
    m?.seq === reserved.seq + 1 && m.from === "mt" && m.to === "chatgpt" &&
    m.kind === "report" && m.message_id === managedReportId(taskId, reserved.seq));
  return Boolean(sent && answered);
}
// END STAGE34E CONFIRMED

// BEGIN STAGE34E RECONCILE
async function reconcileAnsweredInflight(ctx, state, dialog) {
  const taskId = dialog.task_id;
  const reserved = state.inflight[taskId];
  if (!reserved) return false;
  // Reconciliation is independent of the dispatch list. A dialogue leaves that
  // list when the agent writes its report; the reservation MUST remain visible.
  if (!/^task_[0-9a-f]{32}$/.test(taskId) ||
      !Number.isSafeInteger(reserved.seq) || reserved.seq < 1 ||
      typeof reserved.message_id !== "string" || !reserved.message_id) return false;
  const now = Date.now();
  if (Number.isSafeInteger(reserved.reconcile_checked_ms) &&
      reserved.reconcile_checked_ms <= now &&
      reserved.reconcile_checked_ms > now - INFLIGHT_RECONCILE_MS) return false;
  reserved.reconcile_checked_ms = now;
  saveState(ctx.paths, state);
  let first;
  try {
    first = await ctx.cliCall({op:"view", task_id:taskId, offset:0, limit:1});
  } catch {
    ctx.log("managed_inflight_view_unavailable " + taskId);
    return false;
  }
  if (!Number.isSafeInteger(first?.seq) || first.seq < reserved.seq+1) return false;
  // bridge.view_dialog is newest-first: offset points at expected answer seq+1.
  const offset = first.seq - (reserved.seq + 1);
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 100000) return false;
  let proof;
  try {
    proof = await ctx.cliCall({op:"view", task_id:taskId, offset, limit:2});
  } catch {
    ctx.log("managed_inflight_proof_unavailable " + taskId);
    return false;
  }
  // Detect racing new messages; never ACK on inconsistent pagination.
  if (proof?.seq !== first.seq ||
      !confirmedMtResponseAfter(proof, taskId, reserved)) return false;
  if (!Number.isSafeInteger(reserved.delivery_id) || reserved.delivery_id <= 0 ||
      typeof reserved.lease_token !== "string" ||
      !/^[0-9a-fA-F-]{36}$/.test(reserved.lease_token)) {
    ctx.log("managed_inflight_missing_lease " + taskId);
    return false;
  }
  let ack;
  try {
    ack = await ctx.cliCall({op:"ack", task_id:taskId, seq:reserved.seq,
                             delivery_id:reserved.delivery_id,
                             lease_token:reserved.lease_token});
  } catch {
    ctx.log("managed_inflight_ack_unconfirmed " + taskId);
    return false;
  }
  if (ack?.acked !== true) return false;
  state.seen[taskId] = Math.max(state.seen[taskId] || 0, reserved.seq);
  delete state.inflight[taskId];
  saveState(ctx.paths, state);
  ctx.log("managed_inflight_ack_confirmed " + taskId + " seq=" + reserved.seq);
  return true;
}
// END STAGE34E RECONCILE

// BEGIN STAGE34E TICK
export async function runTick(ctx) {
  const holder = acquireLock(ctx.paths, ctx.log);
  if (!holder) return {status:"locked"};
  try {
    const state = loadState(ctx.paths);
    let cleared = 0, held = 0, dispatched = 0;
    // CRITICAL: inspect ALL durable reservations BEFORE querying eligible work.
    // NEVER clear inflight merely because a dialogue is absent from the list.
    for (const taskId of Object.keys(state.inflight).sort()) {
      if (!/^task_[0-9a-f]{32}$/.test(taskId)) {
        held++; ctx.log("managed_inflight_invalid_task_id"); continue;
      }
      if (ctx.dryRun) { held++; continue; }
      try {
        if (await reconcileAnsweredInflight(ctx, state, {task_id:taskId})) cleared++;
        else { held++; ctx.log("managed_inflight_awaits_receipt " + taskId); }
      } catch {
        held++; ctx.log("managed_inflight_reconcile_error " + taskId);
      }
    }
    const dialogs = await listAll(ctx.cliCall);  // SQL ag_dispatch_list only
    for (const dialog of dialogs) {
      if (dispatched >= MAX_DISPATCH_PER_TICK) break;
      if (!dialog?.task_id || !/^task_[0-9a-f]{32}$/.test(dialog.task_id) ||
          (dialog.lifecycle && dialog.lifecycle !== "active")) continue;
      // Even if reconciliation just succeeded, never dispatch the same task twice
      // during the same tick. The server-side claim checks policy and turn again.
      if (state.inflight[dialog.task_id]) continue;
      let snapshot;
      try {
        snapshot = await ctx.cliCall({op:"view", task_id:dialog.task_id,
                                     offset:0, limit:1});
      } catch { ctx.log("managed_dialog_view_failed " + dialog.task_id); continue; }
      const msg = latestPending(snapshot, state.seen[dialog.task_id] || 0);
      if (!msg) continue;
      let full;
      try {
        full = await ctx.cliCall({op:"read", task_id:dialog.task_id,
                                  seq:msg.seq, message_id:msg.message_id});
        if (!full || full.seq !== msg.seq || full.message_id !== msg.message_id ||
            full.from !== "chatgpt" || full.to !== "mt" ||
            full.kind !== "task" || typeof full.body !== "string")
          throw new Error("managed_message_mismatch");
      } catch { ctx.log("managed_task_read_failed " + dialog.task_id); continue; }
      if (ctx.dryRun) { ctx.log("managed_dry_run " + dialog.task_id + " seq=" + msg.seq); continue; }
      try { if (await dispatch(ctx, dialog, full, state)) dispatched++; }
      catch { ctx.log("managed_dispatch_uncertain_manual_review " + dialog.task_id + " seq=" + msg.seq); }
    }
    return {status:"ok", dispatched, held, cleared};
  } finally {
    releaseLock(ctx.paths, holder);
  }
}
// END STAGE34E TICK
