// One-off PROMPT_ONLY pilot run: two arms, no retry, then seal into pilot.sqlite.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { buildArmPlan, runPaired } from './v21_paper_runner.mjs';

const prep = JSON.parse(readFileSync('/tmp/gigatool/pilot_prep.json', 'utf8'));
const plan = buildArmPlan(prep);
const t0 = Date.now();

const results = [];
let usageCompletions = 0;
let toolCalls = 0;

// The live plugin owns the only SDK client. It exposes a deliberately narrow
// probe channel; the pilot uses it twice, sequentially, and never retries.
import { existsSync, unlinkSync, writeFileSync } from 'node:fs';
const ROOT = '/home/kali/research/news-phase4-v21';
const reqFile = `${ROOT}/.mt-v21-probe-request`;
const resFile = `${ROOT}/.mt-v21-probe-result.json`;

async function ask(prompt, arm) {
  if (existsSync(resFile)) unlinkSync(resFile);
  writeFileSync(reqFile, JSON.stringify({
    nonce: `pilot-${arm}-${Date.now()}`,
    agent: 'news-restricted',
    title: `NEWS v2.1 pilot ${arm}`,
    text: prompt,
  }));
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline && !existsSync(resFile)) {
    await new Promise((r) => setTimeout(r, 1000));
  }
  if (!existsSync(resFile)) throw Error('pilot_probe_timeout_no_retry');
  const body = JSON.parse(readFileSync(resFile, 'utf8'));
  if (body.status !== 'ok') throw Error('pilot_probe_failed:' + body.error);
  return body;
}

try {
  for (const arm of plan) {
    const t = Date.now();
    const body = await ask(arm.prompt, arm.arm);
    // Never coerce a missing count to zero: the ledger treats absence as
    // UNKNOWN, and a coerced 0 here would defeat that at the source.
    const rawTools = body.tools === undefined || body.tools === null
      ? null : Number(body.tools);
    if (Number.isInteger(rawTools)) toolCalls += rawTools;
    usageCompletions += 1;
    results.push({
      arm: arm.arm,
      attempt_id: arm.attempt_id,
      session_id: body.session_id,
      text: body.response_text,
      model: body.model,
      tools: rawTools,
      tool_evidence_provenance: 'pilot_probe_channel',
      tool_history_complete: body.tool_history_complete === true,
      session_terminal: body.session_terminal ?? null,
      completed_ms: Date.now(),
      latency_ms: Date.now() - t,
    });
  }
  const payload = {
    event_id: prep.event_id,
    prompt_version: prep.prompt_version,
    prompt_hashes: prep.prompt_hashes,
    decision_time_ms: prep.decision_time_ms,
    market_cutoff_ms: prep.market_cutoff_ms,
    published_ms: prep.published_ms,
    observed_ms: prep.observed_ms,
    results,
    // Only what was actually counted here. Cost is NOT measured in this script
    // and is never asserted; the owner treats inference as free for research.
    usage: { completions: usageCompletions, cost_usd: null,
             cost_provenance: "not_measured_by_runner" },
  };
  const out = execFileSync('/usr/bin/python3',
    [`${ROOT}/v21_pilot_cycle.py`, 'finish'],
    { input: JSON.stringify(payload), encoding: 'utf8' });
  console.log(out.trim());
} catch (err) {
  console.log(JSON.stringify({ status: 'aborted', reason: String(err.message),
                               tool_calls: toolCalls,
                               elapsed_ms: Date.now() - t0 }));
  process.exitCode = 3;
}
