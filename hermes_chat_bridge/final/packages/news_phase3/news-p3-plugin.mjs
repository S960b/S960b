// NEWS Phase 3 native MultiTool session adapter.
// Default: manual-trigger watcher + dry_run=true. No periodic NEWS schedule.
// Uses the app's existing provider; no Hermes CLI, no direct model API, no keys.
//
// Only `server` (and `default`) are exported here ON PURPOSE. This app's legacy
// plugin loader calls EVERY exported function of a plugin module as a server
// hook; earlier versions of this file exported helper functions that return
// null/undefined, which the app pushed into its `hooks` list and then crashed
// with "Cannot read properties of null (reading 'provider')". Helpers now live
// in ./news-p3-helpers.mjs (still importable for tests) and are NOT re-exported.

import { appendFileSync, existsSync, mkdirSync, unlinkSync } from 'node:fs';
import { join, dirname, isAbsolute } from 'node:path';

import { runOnce, probeModel, makePythonRunner, runAutoOnce, runLabels, slotFor, AUTO_INTERVAL_MS, LABEL_INTERVAL_MS } from './news-p3-helpers.mjs';

const DEFAULT_ROOT = '/home/kali/research/news-phase3';

export const server = async (input = {}, options = {}) => {
  const root = typeof options.root === 'string' ? options.root : DEFAULT_ROOT;
  if (!isAbsolute(root)) throw new Error('root_must_be_absolute');
  const directory = typeof options.directory === 'string' ? options.directory : root;
  if (!isAbsolute(directory)) throw new Error('directory_must_be_absolute');
  const dryRun = options.dry_run !== false;
  const python = typeof options.python === 'string' ? options.python : 'python3';
  const trigger = typeof options.trigger_file === 'string' ? options.trigger_file : join(root, '.mt-news-p3-run-now');
  const probeTrigger = typeof options.probe_trigger_file === 'string' ? options.probe_trigger_file : join(root, '.mt-news-p3-probe-now');
  const logFile = typeof options.log_file === 'string' ? options.log_file : join(root, 'news-p3-plugin.log');
  const pollMs = Math.max(2000, Number(options.poll_ms) || 30_000);
  const autoEnabled = options.auto_phase3_enabled === true;
  const labelsEnabled = options.label_phase3_enabled === true;
  const autoIntervalMs = Math.max(60_000, Number(options.auto_interval_ms) || AUTO_INTERVAL_MS);
  const labelIntervalMs = Math.max(60_000, Number(options.label_interval_ms) || LABEL_INTERVAL_MS);
  const client = input?.client;
  let busy = false;
  let closed = false;
  const log = message => {
    try {
      mkdirSync(dirname(logFile), { recursive: true, mode: 0o700 });
      appendFileSync(logFile, new Date().toISOString() + ' ' + String(message) + '\n', { mode: 0o600 });
    } catch { /* logging should not break a session */ }
  };
  const tick = async () => {
    if (closed || busy || (!existsSync(trigger) && !existsSync(probeTrigger))) return;
    busy = true;
    try {
      const isProbe = existsSync(probeTrigger);
      // One trigger = one attempt. Never restore a consumed trigger after an ambiguous result.
      try { unlinkSync(isProbe ? probeTrigger : trigger); }
      catch { log('trigger_remove_failed'); return; }
      const outcome = isProbe
        ? await probeModel({ client, directory, dryRun, log })
        : await runOnce({ client, directory, root, dryRun, log,
            pythonRunner: makePythonRunner(root, python) });
      log('manual_cycle status=' + String(outcome?.status));
    } catch (error) {
      // Never log body/model response, only a stable error category.
      const kind = String(error?.message || 'unknown');
      log('manual_cycle_error=' + (/^[a-zA-Z0-9_]+$/.test(kind) ? kind : 'unknown'));
    } finally { busy = false; }
  };
  let autoTimer = null;
  let lastAutoSlot = null;
  let lastLabelAt = 0;
  let labelTimer = null;
  let labelsStopped = false;
  let autoStopped = false;
  const autoTick = async () => {
    if (closed || autoStopped || dryRun) return;
    const now = Date.now();
    const slot = slotFor(now, autoIntervalMs);
    if (lastAutoSlot === slot) return;
    const outcome = await runAutoOnce({
      client, directory, root, slotMs: slot, dryRun,
      pythonRunner: makePythonRunner(root, python), log,
    });
    // Nothing is claimed if the slot was skipped for budget/pending reasons:
    // retrying the same slot would only repeat the same skip.
    lastAutoSlot = slot;
    if (outcome?.status === 'skip'
        && String(outcome?.reason ?? '').startsWith('auto_stopped')) {
      autoStopped = true;
      if (autoTimer) clearInterval(autoTimer);
      log('auto_disabled_after_phase3_study_cap');
    }
    if (outcome?.sampling_complete === true) {
      autoStopped = true;
      if (autoTimer) clearInterval(autoTimer);
      log('auto_stopped: phase3 hard cap reached');
    }
  };
  const labelTick = async () => {
    if (closed || dryRun || labelsStopped) return;
    const now = Date.now();
    if (now - lastLabelAt < labelIntervalMs) return;
    lastLabelAt = now;
    const result = await runLabels({ root, pythonRunner: makePythonRunner(root, python),
                                    slotMs: slotFor(now, labelIntervalMs), log });
    if (result?.study_complete === true) {
      labelsStopped = true;
      if (labelTimer) clearInterval(labelTimer);
      log('labels_schedule_stopped: all 1h_4h_24h outcomes persisted');
    }
  };
  const guard = fn => () => fn().catch(e => {
    const kind = String(e?.message || 'unknown');
    log('tick_error=' + (/^[a-zA-Z0-9_]+$/.test(kind) ? kind : 'unknown'));
  });
  log('plugin loaded phase3=true dry_run=' + dryRun + ' auto=' + autoEnabled + ' labels=' + labelsEnabled
      + ' auto_interval_ms=' + autoIntervalMs);
  const timer = setInterval(() => { tick().catch(() => log('tick_failed')); }, pollMs);
  timer.unref?.();
  const timers = [timer];
  if (autoEnabled) {
    autoTimer = setInterval(guard(autoTick), autoIntervalMs);
    autoTimer.unref?.();
    timers.push(autoTimer);
    log('auto_schedule_active interval_ms=' + autoIntervalMs + ' next_slot_in_ms=' + (autoIntervalMs - (Date.now() % autoIntervalMs)));
  }
  if (labelsEnabled) {
    labelTimer = setInterval(guard(labelTick), labelIntervalMs);
    labelTimer.unref?.();
    timers.push(labelTimer);
  }
  return { dispose: async () => { closed = true; for (const t of timers) clearInterval(t); log('plugin disposed'); } };
};

export default server;