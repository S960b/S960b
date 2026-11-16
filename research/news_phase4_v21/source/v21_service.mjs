// Standalone supervisor for the NEWS v2.1 paper cycle.
//
// Why this exists: the MultiTool desktop app is single-instance (it holds a
// SINGLETON lock), so a plugin cannot be loaded a second time without
// restarting the GUI and every unrelated session inside it. This supervisor
// runs the SAME plugin module with the SAME code path, but WITHOUT a model
// client: buildArmPlan() validates the prepared contract, and the runner is
// never invoked, so no model call can originate here. It is not a second
// quota: it reads and writes the one shared cap in dispatch.sqlite.
//
// PAPER ONLY. No order, margin or private-key endpoint exists in this file.
import { existsSync, unlinkSync, appendFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { spawn } from 'node:child_process';

const ROOT = '/home/kali/research/news-phase4-v21';
const PYTHON = '/usr/bin/python3';
const SLOT_MS = 900_000;
const TRIGGER_POLL_MS = 30_000;
const LABEL_MS = 300_000;

const logPath = join(ROOT, 'news-v21-service.log');
const trigger = join(ROOT, '.mt-v21-run-now');
const { buildArmPlan } = await import('./v21_paper_runner.mjs');

function log(message) {
  const clean = String(message).replace(/[^A-Za-z0-9_ =.:-]/g, '').slice(0, 220);
  try {
    mkdirSync(dirname(logPath), { recursive: true, mode: 0o700 });
    appendFileSync(logPath, `${new Date().toISOString()} ${clean}\n`,
      { mode: 0o600 });
  } catch { /* never log raw payloads */ }
}

function call(command, input = '') {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [join(ROOT, 'v21_cycle.py'), command], {
      cwd: ROOT,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let raw = '';
    let done = false;
    const finish = (code) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (code !== 0) return reject(Error('v21_python_failed'));
      try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') throw Error('bad');
        resolve(parsed);
      } catch { reject(Error('v21_json_invalid')); }
    };
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      if (!done) { done = true; reject(Error('v21_python_timeout_unknown')); }
    }, 180_000);
    timer.unref?.();
    child.on('error', () => {
      if (!done) { done = true; clearTimeout(timer); reject(Error('v21_spawn_failed')); }
    });
    child.stdout.on('data', (buf) => {
      raw += String(buf);
      if (Buffer.byteLength(raw) > 80_000) { child.kill('SIGTERM'); finish(1); }
    });
    child.stderr.on('data', () => {});
    child.stdin.on('error', () => {});
    child.stdin.end(input);
    child.on('close', finish);
  });
}

let busy = false;
let labelBusy = false;
let stopped = false;

async function cycle() {
  let prepared;
  try {
    prepared = await call('prepare');
  } catch (err) {
    log(`v21_service_prepare_error=${String(err?.message ?? 'unknown')}`);
    return;
  }
  if (prepared?.status === 'ready') {
    // Contract validation only. There is no model client in this process, so
    // the pair is validated and then deliberately NOT sent.
    try {
      const arms = buildArmPlan(prepared);
      log(`v21_service_pair_validated arms=${arms.length} ` +
          `event=${String(prepared.event_id).slice(0, 12)} ` +
          `age_s=${Math.round(Number(prepared.event_age_ms ?? 0) / 1000)} ` +
          `NO_MODEL_CLIENT`);
    } catch (err) {
      log(`v21_service_contract_invalid=${String(err?.message ?? 'unknown')}`);
    }
    return;
  }
  log(`v21_cycle status=${String(prepared?.status ?? 'unknown')} ` +
      `reason=${String(prepared?.reason ?? 'none')}`);
}

async function tick(forced = false) {
  if (stopped || busy) return;
  if (!forced && !existsSync(trigger)) return;
  if (!forced) {
    try { unlinkSync(trigger); } catch { log('v21_trigger_unlink_failed'); return; }
  }
  busy = true;
  try { await cycle(); }
  catch (err) { log(`v21_service_error=${String(err?.message ?? 'unknown')}`); }
  finally { busy = false; }
}

let lastSlot = null;
const slotTimer = setInterval(() => {
  const slot = Math.floor(Date.now() / SLOT_MS);
  if (lastSlot === slot) return;
  lastSlot = slot;
  tick(true).catch(() => log('v21_service_auto_error'));
}, SLOT_MS);
slotTimer.unref?.();

const triggerTimer = setInterval(() => {
  tick().catch(() => log('v21_service_tick_error'));
}, TRIGGER_POLL_MS);
triggerTimer.unref?.();

const labelTimer = setInterval(async () => {
  if (stopped || labelBusy) return;
  labelBusy = true;
  try {
    const r = await call('outcomes');
    log(`v21_outcomes settled=${Number(r?.settled ?? 0)} ` +
        `pending=${Number(r?.pending ?? 0)}`);
  } catch { log('v21_service_outcome_error'); }
  finally { labelBusy = false; }
}, LABEL_MS);
labelTimer.unref?.();

let status = 'unavailable';
try {
  const st = await call('status');
  // production.calls_today already includes the legacy count; the legacy
  // value is logged separately so the two numbers are never concatenated.
  status = `prod_calls=${Number(st?.production?.calls_today ?? -1)} ` +
           `cap=${Number(st?.max_calls_per_day ?? -1)} ` +
           `legacy=${Number(st?.legacy_v2_calls_today ?? -1)}`;
} catch { /* keep unavailable */ }

log(`v21_service_started paper=true auto=true label=true ` +
    `model_client=none ${status}`);
if (process.env.V21_RUN_ONCE === '1') {
  await tick(true);
  stopped = true;
  clearInterval(slotTimer);
  clearInterval(triggerTimer);
  clearInterval(labelTimer);
  log('v21_service_run_once_done');
} else {
  // Stay resident: the timers above are unref'd so the single-shot test exits,
  // but a service must keep the event loop alive between slots.
  let pinger = null;
  process.on('SIGTERM', () => {
    stopped = true;
    if (pinger) clearInterval(pinger);
    log('v21_service_stopped');
    process.exit(0);
  });
  pinger = setInterval(() => {}, 3_600_000);
  await tick(true);
}
