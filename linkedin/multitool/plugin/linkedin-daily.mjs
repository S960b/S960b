// linkedin-daily.mjs — планировщик ежедневного LinkedIn-цикла ВНУТРИ MultiTool.
//
// Идея: плагин живёт в процессе приложения и в 20:00 МСК сам создаёт сессию и
// вбрасывает в неё уже готовый промпт цикла (тот же текст, что раньше запускал
// Hermes-крон). Дальше работает штатный агент приложения — со скиллом
// linkedin-content-research и всеми инструментами. Никакого внешнего cron,
// никакого Hermes, никаких паролей: всё внутри.
//
// Безопасность: по умолчанию DRY-RUN (ничего не запускает, только пишет в лог).
// Реальный запуск — только если в конфиге передать опции { "dry_run": false }.
//
// Формат плагина (по типам @opencode-ai/plugin):
//   export const server = async (input, options) => Hooks
//   input = { client, project, directory, worktree, experimental_workspace, serverUrl, $ }

import { appendFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const HOME = "/home/kali";
const LINK = join(HOME, "linkedin");
const PROMPT_FILES = [
  join(LINK, "CYCLE_PROMPT.md"),                                    // workspace copy (survives a repo re-clone)
  "/home/kali/S960b/linkedin/prompts/cycle-routine.txt",            // repo copy (single source of truth)
];
const LOG_FILE = join(LINK, "auto-post.log");
let logFile = LOG_FILE;   // можно переопределить опцией log_file (для проверок)
const TRIGGER_FILE = join(LINK, ".run-now");     // ручной запуск: создать этот файл
const LOCK_FILE = join(LINK, ".auto-post.lock");
const CLAIMS_DIR = join(LINK, ".claims");    // атомарный «захват дня» между копиями плагина
const TZ = "Europe/Moscow";
const RUN_HOUR = 20;          // 20:00 МСК
const TICK_MS = 60_000;       // раз в минуту
const LOCK_TTL_MS = 6 * 3600_000;

function log(msg) {
  const line = `${new Date().toISOString()} ${msg}\n`;
  try {
    mkdirSync(dirname(logFile), { recursive: true });
    appendFileSync(logFile, line);
  } catch (_) { /* логирование не должно ломать плагин */ }
}

function mskParts(d = new Date()) {
  const p = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hour12: false,
  }).formatToParts(d);
  const g = (t) => p.find((x) => x.type === t)?.value ?? "";
  return { date: `${g("year")}-${g("month")}-${g("day")}`, hour: Number(g("hour")) };
}

function todayCovered() {
  const { date } = mskParts();
  if (existsSync(join(LINK, "QUEUE", `${date}.md`))) return true;
  try {
    const pub = readFileSync(join(LINK, "PUBLISHED.md"), "utf8");
    // Ищем именно «дату поста», а не любое вхождение строки: заголовок раздела
    // "## YYYY-MM-DD" либо поле "DATE: YYYY-MM-DD". Иначе дата-дедлайн (CISA и
    // т.п.), упомянутая в тексте старого поста, ложно закрывает сегодняшний день.
    return new RegExp(`(^|\\n)\\s*(##\\s*${date}|DATE:\\s*${date})\\b`).test(pub);
  } catch (_) {
    return false;
  }
}

function acquireLock() {
  try {
    if (existsSync(LOCK_FILE)) {
      const since = Number(String(readFileSync(LOCK_FILE, "utf8")).trim() || 0);
      if (Date.now() - since < LOCK_TTL_MS) return false;
    }
    writeFileSync(LOCK_FILE, String(Date.now()));
    return true;
  } catch (_) {
    return false;
  }
}

function releaseLock() {
  try { rmSync(LOCK_FILE, { force: true }); } catch (_) {}
}

// Атомарный «захват дня»: создание файла с флагом wx (O_CREAT|O_EXCL) — побеждает ровно одна копия плагина.
function claimToday(date) {
  try {
    mkdirSync(CLAIMS_DIR, { recursive: true });
    writeFileSync(join(CLAIMS_DIR, date), String(Date.now()), { flag: "wx" });
    return true;
  } catch (_) {
    return false;
  }
}

async function runCycle(client, directory, { dryRun, force, why }) {
  const { date } = mskParts();
  log(`run start (${why}); date=${date}; dry_run=${dryRun}; force=${force}`);

  if (!force && todayCovered()) {
    log("skip: today's post already covered (PUBLISHED.md or QUEUE)");
    return;
  }
  if (!force && !claimToday(date)) {
    log("skip: another plugin instance already claimed today's run");
    return;
  }
  if (!acquireLock()) {
    log("skip: another run holds the lock");
    return;
  }

  try {
    let prompt, promptFrom;
    for (const candidate of PROMPT_FILES) {
      try {
        prompt = readFileSync(candidate, "utf8");
        promptFrom = candidate;
        break;
      } catch (_) { /* try the next candidate */ }
    }
    if (!prompt) {
      log(`error: cannot read any prompt file (${PROMPT_FILES.join(", ")})`);
      return;
    }
    log(`prompt source: ${promptFrom} (${prompt.length} chars)`);

    if (dryRun) {
      log(`dry-run: would create a session and inject the cycle prompt (${prompt.length} chars)`);
      return;
    }
    if (!client?.session?.create || !client?.session?.prompt) {
      log("error: SDK client without session.create/prompt");
      return;
    }

    const created = await client.session.create({
      body: { title: `LinkedIn cycle ${date}` },
      query: { directory },
    });
    const sessionID = created?.data?.id ?? created?.id;
    if (!sessionID) {
      log(`error: session.create returned no id (${JSON.stringify(created)?.slice(0, 200)})`);
      return;
    }
    log(`session created: ${sessionID}`);

    await client.session.prompt({
      path: { id: sessionID },
      query: { directory },
      body: { parts: [{ type: "text", text: prompt }] },
    });
    log("prompt injected; agent is running the cycle");
  } catch (e) {
    log(`error: ${e?.message ?? e}`);
  } finally {
    releaseLock();
  }
}

export const server = async (input = {}, options = {}) => {
  const dryRun = options.dry_run !== false;          // безопасно по умолчанию
  const force = options.force === true;              // запускать, даже если сегодня уже выходило
  if (typeof options.log_file === "string") logFile = options.log_file;
  const tickMs = Number(options.tick_ms) > 0 ? Number(options.tick_ms) : TICK_MS;
  const directory = input?.directory || LINK;
  const client = input?.client;
  let lastFiredKey = null;
  let busy = false;

  log(`plugin loaded; dry_run=${dryRun}; force=${force}; tick_ms=${tickMs}; directory=${directory}; sdk_session_api=${Boolean(client?.session?.create && client?.session?.prompt)}`);

  const tick = async () => {
    if (busy) return;
    busy = true;
    try {
      if (existsSync(TRIGGER_FILE)) {                // ручной запуск
        try { rmSync(TRIGGER_FILE, { force: true }); } catch (_) {}
        await runCycle(client, directory, { dryRun, force, why: "manual trigger file" });
        return;
      }
      const { date, hour } = mskParts();             // расписание
      const key = `${date}T${hour}`;
      if (hour === RUN_HOUR && lastFiredKey !== key) {
        lastFiredKey = key;
        await runCycle(client, directory, { dryRun, force, why: `schedule ${RUN_HOUR}:00 MSK` });
      }
    } finally {
      busy = false;
    }
  };

  const timer = setInterval(() => { tick().catch((e) => log(`tick error: ${e?.message ?? e}`)); }, tickMs);
  if (typeof timer.unref === "function") timer.unref();

  return {
    dispose: async () => { clearInterval(timer); log("plugin disposed"); },
  };
};

export default server;
