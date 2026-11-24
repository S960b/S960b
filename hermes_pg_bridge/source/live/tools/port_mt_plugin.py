"""Deterministic, fail-closed local port of MT Git Inbox to PG Bridge v2.

This tool is authored by ChatGPT. Execute only on a *separate* plugin copy on
Kali; it never modifies mt-git-inbox.mjs. Upstream function boundaries must
match exact markers, otherwise abort without writing a destination.
"""
from pathlib import Path
import hashlib
import re
import sys

SOURCE=Path('/home/kali/.config/gigatool/plugins/mt-git-inbox.mjs')
TARGET=Path(__file__).resolve().parents[1]/'dist/mt-pg-inbox.mjs'

CLI=r'''function cli(request, timeoutMs = CLI_TIMEOUT_MS) {
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
'''
PROMPT=r'''function buildPrompt(dialog, message) {
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
'''

def replace_between(src, begin, end, replacement):
    if src.count(begin)!=1 or src.count(end)!=1:
        raise RuntimeError('source layout mismatch: '+begin)
    a=src.index(begin);b=src.index(end,a)
    return src[:a]+replacement+src[b:]


def main():
    if TARGET.exists(): raise SystemExit('REFUSING: PG plugin target already exists')
    TARGET.parent.mkdir(parents=True,exist_ok=True)
    if SOURCE.is_symlink() or not SOURCE.is_file():raise SystemExit('SOURCE not a regular file')
    old=SOURCE.read_text('utf-8')
    if 'export async function runTick(ctx)' not in old or 'session.prompt' not in old:
        raise SystemExit('UNKNOWN plugin source; abort')
    new=old.replace('const CODE = "/home/kali/agent-dialog-beta/code";',
                    'const CODE = "/home/kali/bridge-pg-v2";')
    new=new.replace('const PYTHON = join(CODE, ".venv-beta/bin/python");',
                    'const PYTHON = join(CODE, ".venv/bin/python");')
    new=new.replace('const STATE_DIR = join(HOME, ".local/state/mt-git-inbox");',
                    'const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox");')
    new=new.replace('const TICK_MS = 120_000;', 'const TICK_MS = 30_000;')
    if new==old or old.count('const STATE_DIR = join(HOME, ".local/state/mt-git-inbox");')!=1:
        raise RuntimeError('CONFIG source mismatch')
    new=replace_between(new,'function cli(request, timeoutMs = CLI_TIMEOUT_MS) {',
                        'function buildPrompt(dialog, message) {',CLI)
    new=replace_between(new,'function buildPrompt(dialog, message) {',
                        'function latestPending(snapshot, seenSeq) {',PROMPT)
    # Match exactly one durable reservation statement, NOT its indentation.
    # Both the archived and the live plugin vary the whitespace before `phase`.
    reservation = re.compile(
        r'(?m)^  state\.inflight\[key\] = \{ seq: message\.seq, message_id: message\.message_id,\r?\n'
        r'[ \t]+phase: "reserved" \};$'
    )
    after='''  // PostgreSQL outbox claim is atomic with cancel/return-turn checks.
  // Never dispatch an already returned message.
  const lease = await ctx.cliCall({op:"claim", task_id:key, seq:message.seq,
                                   message_id:message.message_id});
  if (!lease?.claimed || !lease?.lease_token || !lease?.delivery_id) return false;
  state.inflight[key] = { seq: message.seq, message_id: message.message_id,
                         phase: "reserved", delivery_id: lease.delivery_id,
                         lease_token: lease.lease_token };'''
    matches=list(reservation.finditer(new))
    if len(matches)!=1:raise RuntimeError('SOURCE dispatch reservation changed')
    hit=matches[0]
    new=new[:hit.start()]+after+new[hit.end():]
    finish='''  state.seen[key] = message.seq;
  delete state.inflight[key];
  saveState(ctx.paths, state);'''
    ack='''  // No 'seen' receipt until PostgreSQL confirms this specific lease.
  const acked = await ctx.cliCall({op:"ack",delivery_id:lease.delivery_id,
                                  lease_token:lease.lease_token});
  if (!acked?.acked) throw new Error("pg_ack_unconfirmed");
  state.seen[key] = message.seq;
  delete state.inflight[key];
  saveState(ctx.paths, state);'''
    if new.count(finish)!=1:raise RuntimeError('SOURCE post-dispatch changed')
    new=new.replace(finish,ack)
    new=new.replace('const dryRun = options.dry_run !== false;',
                    'const dryRun = options.dry_run !== false;')
    # Avoid publishing on first launch; require explicit configured dry_run:false.
    if new.count('const dryRun = options.dry_run !== false;')!=1:
        raise RuntimeError('SOURCE dry-run gate changed')
    TARGET.write_text(new,'utf-8')
    TARGET.chmod(0o600)
    print('PLUGIN_PORT_OK source_sha256='+hashlib.sha256(old.encode()).hexdigest(),
          'target_sha256='+hashlib.sha256(new.encode()).hexdigest())

if __name__=='__main__':main()
