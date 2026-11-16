// NEWS v2.1 plugin — separate from the legacy v2 plugin.
// PAPER ONLY: two sequential model calls per paired event, public quote, no
// orders, no margin, no private keys. The Python authority owns every record.
import { appendFileSync, existsSync, mkdirSync, readFileSync, unlinkSync,
         writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { assistantText, observedModel } from
  './news-p4-v2-helpers.mjs';
import { buildArmPlan, runPaired } from './v21_paper_runner.mjs';

const ROOT = process.env.NEWS_V21_ROOT || dirname(fileURLToPath(new URL('.', import.meta.url)));
const SLOT_MS = 900_000;

function runner(python, script) {
  return (command, args = [], input = '') => new Promise((resolve, reject) => {
    const child = spawn(python, [script, command, ...args], {
      cwd: dirname(script),
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
      if (Buffer.byteLength(raw) > 80_000) {
        child.kill('SIGTERM');
        finish(1);
      }
    });
    child.stderr.on('data', () => {});
    child.stdin.on('error', () => {});
    child.stdin.end(input);
    child.on('close', finish);
  });
}

export const server = async (input = {}, options = {}) => {
  const root = typeof options.root === 'string' ? options.root : ROOT;
  if (!isAbsolute(root)) throw Error('v21_absolute_paths_required');
  const paperOn = options.paper_enabled === true;
  const autoOn = options.auto_enabled === true;
  const labelOn = options.label_auto_enabled === true;
  const directory = typeof options.directory === 'string'
    ? options.directory : root;
  const python = typeof options.python === 'string'
    ? options.python : '/usr/bin/python3';
  const script = join(root, 'v21_cycle.py');
  const logPath = join(root, 'news-v21-plugin.log');
  const trigger = join(root, '.mt-v21-run-now');
  const client = input?.client;
  const call = runner(python, script);
  // The pilot ledger is settled by its own command, never by the production
  // labeler, so a pilot row can never be settled against production data.
  const pilotScript = join(root, 'v21_pilot_cycle.py');
  const pilotCall = runner(python, pilotScript);
  // Process-unique identity so two contexts can be told apart in one log, and
  // so a reader never has to infer liveness from a historical load line.
  const instanceId = `${process.pid}-${Date.now().toString(36)}`;
  let closed = false;
  let busy = false;
  let labelBusy = false;
  let lastSlot = null;

  const log = (message) => {
    const clean = String(message)
      .replace(/[^A-Za-z0-9_ =.:-]/g, '').slice(0, 260);
    try {
      mkdirSync(dirname(logPath), { recursive: true, mode: 0o700 });
      appendFileSync(logPath, `${new Date().toISOString()} ${clean}\n`,
        { mode: 0o600 });
    } catch { /* never log raw payloads */ }
  };

  const quote = async () => {
    const q = await call('quote');
    if (q?.status !== 'ok' || typeof q.price !== 'number') {
      throw Error('v21_quote_unavailable');
    }
    return q;
  };

  const cycle = async () => {
    if (!paperOn) return { status: 'disabled', instance: instanceId };
    if (typeof client?.session?.create !== 'function' ||
        typeof client?.session?.prompt !== 'function') {
      return { status: 'no_sdk_session' };
    }
    const prepared = await call('prepare');
    if (prepared?.status !== 'ready') return prepared;
    const arms = buildArmPlan(prepared);
    // Entry price is sampled before the calls and again after; both feed the
    // journal so the 4h label always has a recorded entry.
    // Entry quote is taken AFTER both responses, so the stored entry price is
    // the one the 4h label is anchored to. The pre-prompt quote is kept only
    // as a diagnostic.
    const before = await quote();
    let paired;
    try {
      paired = await runPaired({ client, directory, arms, log });
    } catch (err) {
      // Ambiguous: reservation stays, nothing is written, never replayed.
      const name = String(err?.message ?? 'v21_unknown');
      log(`v21_pair_aborted=${/^[A-Za-z0-9_.:]+$/.test(name) ? name : 'unknown'}`);
      return { status: 'ambiguous_no_retry', reason: name };
    }
    const after = await quote();
    const body = {
      event_id: prepared.event_id,
      cutoff_ms: prepared.market_cutoff_ms ?? prepared.cutoff_ms,
      market_cutoff_ms: prepared.market_cutoff_ms ?? prepared.cutoff_ms,
      decision_time_ms: prepared.decision_time_ms,
      prompts_sha256: prepared.prompts_sha256,
      price: after.price,
      observed_ms: after.observed_ms,
      results: paired.results.map((r) => ({
        arm: r.arm,
        attempt_id: r.attempt_id,
        session_id: r.session_id,
        text: r.text,
        model: r.model,
        // Measured by the runner per arm; the fallback only covers a mock.
        completed_ms: Number.isFinite(r.completed_ms) ? r.completed_ms
          : Date.now(),
      })),
      entry_before: { price: before.price, observed_ms: before.observed_ms },
    };
    return call('finish', [], JSON.stringify(body));
  };


  // One-shot diagnostic: a REAL exchange through the same client the paper path
  // uses, written OUTSIDE every NEWS ledger. Triggered by a request file that
  // contains only the target and a nonce - never a credential. Runs at most
  // once per request file, never retries an ambiguous call.
  const PROBE_TEXT_MAX = 64 * 1024;

  function boundedText(text, limit) {
    if (typeof text !== 'string') return { text: null, length: 0, truncated: false };
    if (text.length <= limit) {
      return { text, length: text.length, truncated: false };
    }
    // Refusing to guess: an oversized answer is reported as absent rather than
    // handed over in a silently mangled form.
    return { text: null, length: text.length, truncated: true };
  }

  async function probeToolEvidence(c, { sid, directory }) {
    if (typeof c?.session?.messages !== 'function') {
      return { complete: false, count: null, terminal: null,
               provenance: 'sdk_session_messages_unavailable' };
    }
    let hist;
    try {
      hist = await c.session.messages({ path: { id: sid }, query: { directory } });
    } catch {
      return { complete: false, count: null, terminal: null,
               provenance: 'sdk_session_messages_throw' };
    }
    const list = Array.isArray(hist?.data) ? hist.data
      : Array.isArray(hist) ? hist : null;
    if (list === null || list.length === 0 ||
        list.some((m) => !m || typeof m !== 'object')) {
      return { complete: false, count: null, terminal: null,
               provenance: 'sdk_session_messages_incomplete' };
    }
    const count = list.reduce((n, m) => n + (Array.isArray(m.parts)
      ? m.parts.filter((x) => x && x.type === 'tool').length : 0), 0);
    return { complete: true, count, terminal: 'completed',
             provenance: 'sdk_session_messages' };
  }

  const probeRequest = join(root, '.mt-v21-probe-request');
  const probeResult = join(root, '.mt-v21-probe-result.json');
  let probeBusy = false;
  const runProbe = async () => {
    if (probeBusy || !existsSync(probeRequest)) return;
    probeBusy = true;
    const started = Date.now();
    const result = { status: 'error', stage: 'start', instance: instanceId };
    try {
      let request = {};
      try { request = JSON.parse(readFileSync(probeRequest, 'utf8')); }
      catch { throw Error('probe_request_invalid'); }
      unlinkSync(probeRequest);
      result.request_nonce = String(request?.nonce ?? '').slice(0, 32);
      if (typeof client?.session?.create !== 'function' ||
          typeof client?.session?.prompt !== 'function') {
        throw Error('probe_sdk_unavailable');
      }
      result.stage = 'session_create';
      const created = await client.session.create({
        body: { title: request?.title || 'NEWS v2.1 probe',
                agent: request?.agent || 'news-restricted' },
        query: { directory },
      });
      const sid = created?.data?.id ?? created?.id;
      if (typeof sid !== 'string') throw Error('probe_session_create_unknown_no_retry');
      result.session_id = sid;
      result.stage = 'prompt';
      const t1 = Date.now();
      const response = await client.session.prompt({
        path: { id: sid },
        query: { directory },
        body: { parts: [{ type: 'text',
          text: typeof request?.text === 'string'
            ? request.text
            : 'Reply with exactly this JSON and nothing else: '
              + '{"probe":"ok","project":"news-v21"}' }] },
      });
      result.latency_ms = Date.now() - t1;
      const text = assistantText(response);
      const model = observedModel(response);
      // D07: the answer used to be cut at 300 characters, which can slice a
      // JSON envelope mid-token. The full text is returned; the length is
      // reported separately so a caller can bound it without silently losing
      // the tail.
      const envelope = boundedText(text, PROBE_TEXT_MAX);
      result.response_text = envelope.text;
      result.response_length = envelope.length;
      result.response_truncated = envelope.truncated;
      result.model = model;

      // D07/D01: the tool evidence a prompt-only claim depends on. It is read
      // here, inside the plugin, because only the plugin owns the SDK client.
      // When the runtime cannot supply it the fields are explicitly false/null
      // and the reader downstream must treat the arm as UNKNOWN - this probe
      // never guesses a zero.
      const evidence = await probeToolEvidence(client, { sid, directory });
      result.tool_history_complete = evidence.complete;
      result.tools = evidence.count;
      result.tool_evidence_provenance = evidence.provenance;
      result.session_terminal = evidence.terminal;
      const usage = response?.data?.info?.tokens ?? response?.info?.tokens;
      if (usage && typeof usage === 'object') {
        result.usage = {
          input: Number(usage.input ?? usage.prompt ?? 0) || 0,
          output: Number(usage.output ?? usage.completion ?? 0) || 0,
        };
      }
      result.stage = 'done';
      result.status = 'ok';
    } catch (err) {
      const name = String(err?.message ?? 'probe_unknown');
      result.error = /^[A-Za-z0-9_.:-]+$/.test(name) ? name : 'probe_error';
    } finally {
      result.total_ms = Date.now() - started;
      result.finished_at = new Date().toISOString();
      try { writeFileSync(probeResult, JSON.stringify(result, null, 1),
                          { mode: 0o600 }); } catch { /* ignore */ }
      log(`v21_probe status=${result.status} stage=${result.stage} ` +
          `latency_ms=${result.latency_ms ?? -1} instance=${instanceId}`);
      probeBusy = false;
    }
  };

  const tick = async (forced = false) => {
    if (closed || busy) return;
    await runProbe();
    if (!paperOn) {
      // Fail closed, but never silently: a disabled context says so on every
      // tick it is asked to work, so a dead lane cannot look like a quiet one.
      log(`v21_idle_not_paper instance=${instanceId}`);
      // Labels cost no model call and must keep maturing while inference is
      // paused; otherwise pausing inference silently stops the outcome lane too.
      if (labelOn && !labelBusy) {
        labelBusy = true;
        try {
          const r = await call('outcomes');
          log(`v21_outcomes settled=${Number(r?.settled ?? 0)} ` +
              `overdue=${(r?.overdue || []).length} instance=${instanceId}`);
          const pilot = await pilotCall('settle');
          log(`v21_pilot_settle settled=${Number(pilot?.settled ?? 0)} ` +
              `pending=${Number(pilot?.pending ?? 0)} instance=${instanceId}`);
        } catch { log(`v21_outcome_error instance=${instanceId}`); }
        finally { labelBusy = false; }
      }
      return;
    }
    if (!forced && !existsSync(trigger)) return;
    if (!forced) {
      try { unlinkSync(trigger); } catch { log('v21_trigger_unlink_failed'); return; }
    }
    busy = true;
    try {
      const result = await cycle();
      log(`v21_cycle status=${String(result?.status ?? 'unknown')} ` +
          `instance=${instanceId}`);
      if (result?.status === 'ambiguous_no_retry') {
        log(`v21_pair_ambiguous_no_retry instance=${instanceId}`);
      }
    } catch (err) {
      const name = String(err?.message ?? 'v21_unknown');
      log(`v21_cycle_error=${/^[A-Za-z0-9_]+$/.test(name) ? name : 'unknown'}`);
    } finally { busy = false; }
  };

  const poll = setInterval(() => { tick().catch(() => log('v21_tick_error')); },
    30_000);
  poll.unref?.();
  const timers = [poll];

  if (autoOn && paperOn) {
    const auto = setInterval(() => {
      const slot = Math.floor(Date.now() / SLOT_MS);
      if (lastSlot === slot) return;
      lastSlot = slot;
      tick(true).catch(() => log('v21_auto_error'));
    }, SLOT_MS);
    auto.unref?.();
    timers.push(auto);
  }

  if (labelOn) {
    const label = setInterval(async () => {
      if (closed || labelBusy) return;
      labelBusy = true;
      try {
        const r = await call('outcomes');
        log(`v21_outcomes settled=${Number(r?.settled ?? 0)} ` +
            `instance=${instanceId}`);
        const pilot = await pilotCall('settle');
        log(`v21_pilot_settle settled=${Number(pilot?.settled ?? 0)} ` +
            `pending=${Number(pilot?.pending ?? 0)} instance=${instanceId}`);
      } catch { log(`v21_outcome_error instance=${instanceId}`); }
      finally { labelBusy = false; }
    }, 300_000);
    label.unref?.();
    timers.push(label);
  }

  let state = 'unknown';
  try {
    const st = await call('status');
    state = `prod_calls=${Number(st?.production?.calls_today ?? -1)}`;
  } catch { state = 'status_unavailable'; }

  // A pending probe request must not wait for the first poll tick.
  runProbe().catch(() => log('v21_probe_boot_error'));
  log(`v21_loaded paper=${paperOn} auto=${autoOn} label=${labelOn} ` +
      `instance=${instanceId} client=${client ? 'present' : 'absent'} ${state}`);
  if (!paperOn) {
    log(`v21_disabled_fail_closed instance=${instanceId} ` +
        `reason=paper_enabled_false`);
  }
  return {
    dispose: async () => {
      closed = true;
      for (const timer of timers) clearInterval(timer);
      log(`v21_disposed instance=${instanceId}`);
    },
  };
};

export default server;
