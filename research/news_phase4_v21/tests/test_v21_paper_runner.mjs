// Offline tests for the v2.1 paper runner. No network, no model, no DB.
import assert from 'node:assert/strict';
import test from 'node:test';
import { buildArmPlan, runPaired } from './v21_paper_runner.mjs';

const EID = 'a'.repeat(64);
const A1 = 'b'.repeat(64);
const A2 = 'c'.repeat(64);
const ARMS = [
  { arm: 'news', attempt_id: A1, event_id: EID, prompt: 'N prompt' },
  { arm: 'news_market', attempt_id: A2, event_id: EID, prompt: 'NM prompt' },
];

// The real SDK exposes session.messages for every session, so a fixture that
// omits it is not a realistic client: it used to be read as "zero tool calls"
// while in fact nothing was ever read. `messages` below returns the completed
// history (assistant answer, no tool parts) unless a test says otherwise.
function historyReply(text = '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}',
                      { toolParts = 0, shape = 'ok' } = {}) {
  const parts = [{ type: 'text', text }];
  for (let i = 0; i < toolParts; i += 1) parts.push({ type: 'tool', tool: 'websearch' });
  const list = [{ role: 'assistant', parts }];
  if (shape === 'array') return list;
  if (shape === 'empty') return { data: [] };
  if (shape === 'malformed') return { data: [null] };
  if (shape === 'notalist') return { data: 'nope' };
  return { data: list };
}

function fakeClient({ failOn = null, noModel = false } = {}) {
  const calls = [];
  let n = 0;
  return {
    calls,
    client: {
      session: {
        create: async () => { n += 1; calls.push('create');
          return { data: { id: `ses_v21_${n}` } }; },
        messages: async () => historyReply(),
        prompt: async () => {
          calls.push('prompt');
          if (failOn) throw Error(failOn);
          return { data: {
            info: { role: 'assistant',
                    ...(noModel ? {} : { model: { providerID: 'deepseek',
                                                  modelID: 'deepseek-v4-flash' } }) },
            parts: [{ type: 'text', text: '{"p_up":0.6,"expected_return_bps":80,"uncertainty":0.3,"rationale":"x"}' }],
          } };
        },
      },
    },
  };
}

test('runs exactly two sequential prompts and returns both texts', async () => {
  const { client, calls } = fakeClient();
  const out = await runPaired({ client, directory: '/tmp', arms: ARMS });
  assert.equal(out.status, 'paired_complete');
  assert.equal(out.results.length, 2);
  assert.deepEqual(calls, ['create', 'prompt', 'create', 'prompt']);
  assert.equal(out.results[0].model.providerID, 'deepseek');
  assert.ok(out.results[1].text.includes('p_up'));
});

test('ambiguous prompt is never retried', async () => {
  const { client, calls } = fakeClient({ failOn: 'gateway_timeout' });
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: ARMS }),
    /v2.1_prompt_ambiguous_no_retry/);
  assert.deepEqual(calls, ['create', 'prompt'], 'must stop after first failure');
});

test('missing model identity aborts without a second arm', async () => {
  const { client, calls } = fakeClient({ noModel: true });
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: ARMS }),
    /v2.1_unconfirmed_model_result_no_retry/);
  assert.deepEqual(calls, ['create', 'prompt']);
});

test('invalid arm plan rejected before any call', async () => {
  const { client, calls } = fakeClient();
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: [ARMS[0]] }),
    /v2.1_arm_plan_invalid/);
  assert.equal(calls.length, 0);
});

test('same arm twice rejected', async () => {
  const { client } = fakeClient();
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: [ARMS[0], ARMS[0]] }),
    /v2.1_arm_pair_invalid/);
});

test('oversized prompt rejected before any call', async () => {
  const { client, calls } = fakeClient();
  const big = [{ ...ARMS[0], prompt: 'x'.repeat(14000) }, ARMS[1]];
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: big }),
    /v2.1_prompt_too_large/);
  assert.equal(calls.length, 0);
});

test('missing SDK surface is reported, not crashed', async () => {
  await assert.rejects(
    runPaired({ client: {}, directory: '/tmp', arms: ARMS }),
    /v2.1_sdk_not_ready/);
});

// --- strict adapter and no-replay guarantees ------------------------------

function prepared(over = {}) {
  const base = {
    event_id: 'e'.repeat(64),
    attempts: ['1'.repeat(64), '2'.repeat(64)],
    prompts: { news: 'N-text', news_market: 'NM-text' },
    prompts_sha256: { news: 'a'.repeat(64), news_market: 'b'.repeat(64) },
  };
  return { ...base, ...over };
}

test('adapter maps arm names to their attempt ids', () => {
  const plan = buildArmPlan(prepared());
  assert.equal(plan.length, 2);
  assert.equal(plan[0].arm, 'news');
  assert.equal(plan[1].arm, 'news_market');
  assert.equal(plan[0].attempt_id, '1'.repeat(64));
  assert.equal(plan[1].attempt_id, '2'.repeat(64));
  assert.equal(plan[0].prompt, 'N-text');
  assert.equal(plan[1].prompt, 'NM-text');
});

test('adapter rejects duplicate attempt ids', () => {
  assert.throws(() => buildArmPlan(
    prepared({ attempts: ['1'.repeat(64), '1'.repeat(64)] })),
  /attempt_ids_duplicated/);
});

test('adapter rejects identical prompts', () => {
  assert.throws(() => buildArmPlan(prepared({
    prompts: { news: 'same', news_market: 'same' },
    prompts_sha256: { news: 'c'.repeat(64), news_market: 'c'.repeat(64) },
  })), /degenerate_pair/);
});

test('adapter rejects a missing prompt', () => {
  assert.throws(() => buildArmPlan(prepared({
    prompts: { news: 'N-text' },
  })), /prompt_missing/);
});

test('adapter rejects malformed ids', () => {
  assert.throws(() => buildArmPlan(
    prepared({ attempts: ['short', '2'.repeat(64)] })),
  /attempt_id_malformed/);
});

test('crash after first call does not replay or continue', async () => {
  let created = 0;
  const client = {
    session: {
      async create() { created += 1; return { data: { id: `session_${created}` } }; },
      async prompt() { throw Error('boom'); },
    },
  };
  const plan = buildArmPlan(prepared());
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: plan }),
    /prompt_ambiguous_no_retry/);
  assert.equal(created, 1, 'the second arm must never start after a failure');
});

test('a non-deepseek provider aborts the pair', async () => {
  let created = 0;
  const client = {
    session: {
      async create() { created += 1; return { data: { id: `session_${created}` } }; },
      async prompt() {
        return { parts: [{ type: 'text', text: '{"p_up":0.6}' }],
                 info: { providerID: 'other', modelID: 'x' } };
      },
    },
  };
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) }),
    /model_provider_unexpected|unconfirmed_model_result/);
});

// --- restricted agent contract -------------------------------------------

test('every NEWS session is created with the restricted agent', async () => {
  const seen = [];
  const client = {
    session: {
      async messages() { return historyReply(); },
      async create(req) { seen.push(req?.body?.agent); return { data: { id: 'session_a' } }; },
      async prompt() {
        return { parts: [{ type: 'text',
          text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
          info: { role: 'assistant', providerID: 'deepseek', modelID: 'deepseek-v4-flash' } };
      },
    },
  };
  await runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) });
  assert.deepEqual(seen, ['news-restricted', 'news-restricted']);
});

test('a custom agent name is honoured, never dropped', async () => {
  const seen = [];
  const client = {
    session: {
      async messages() { return historyReply(); },
      async create(req) { seen.push(req?.body?.agent); return { data: { id: 'session_b' } }; },
      async prompt() { return { parts: [{ type: 'text',
        text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
        info: { role: 'assistant', providerID: 'deepseek', modelID: 'deepseek-v4-flash' } }; },
    },
  };
  await runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()),
                    agent: 'news-restricted-v2' });
  assert.deepEqual(seen, ['news-restricted-v2', 'news-restricted-v2']);
});

// --- post-hoc tool audit ---------------------------------------------------

test('an arm that used a tool is refused, never sealed', async () => {
  let n = 0;
  const client = {
    session: {
      async create() { n += 1; return { data: { id: `session_${n}` } }; },
      async prompt() {
        return { parts: [{ type: 'text',
          text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
          info: { role: 'assistant', providerID: 'deepseek', modelID: 'deepseek-v4-flash' } };
      },
      async messages(req) {
        // first arm shows a websearch call, i.e. a leaked information set
        return req.path.id === 'session_1'
          ? [{ parts: [{ type: 'tool', tool: 'websearch' }] }]
          : [{ parts: [{ type: 'text', text: 'ok' }] }];
      },
    },
  };
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) }),
    /tool_used_in_arm_no_seal/);
});

test('missing tool history is unknown, never zero', async () => {
  // D01/D02: the client cannot report history at all. The old code left its
  // counter at 0 and sealed the pair as clean; that is the exact defect.
  const client = {
    session: {
      async create() { return { data: { id: 'session_nohist' } }; },
      async prompt() { return { parts: [{ type: 'text',
        text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
        info: { role: 'assistant', providerID: 'deepseek',
                modelID: 'deepseek-v4-flash' } }; },
    },
  };
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) }),
    /v2.1_tool_history_unknown:no_history_api/);
});

test('an empty history for an answered arm is unknown', async () => {
  const client = {
    session: {
      async create() { return { data: { id: 'session_empty' } }; },
      async messages() { return historyReply(undefined, { shape: 'empty' }); },
      async prompt() { return { parts: [{ type: 'text',
        text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
        info: { role: 'assistant', providerID: 'deepseek',
                modelID: 'deepseek-v4-flash' } }; },
    },
  };
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) }),
    /v2.1_tool_history_unknown:empty_snapshot/);
});

test('a transport error while reading history is unknown', async () => {
  const client = {
    session: {
      async create() { return { data: { id: 'session_boom' } }; },
      async messages() { throw Error('gateway_reset'); },
      async prompt() { return { parts: [{ type: 'text',
        text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
        info: { role: 'assistant', providerID: 'deepseek',
                modelID: 'deepseek-v4-flash' } }; },
    },
  };
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) }),
    /v2.1_tool_history_unknown:transport_error/);
});

test('a malformed history is unknown', async () => {
  const client = {
    session: {
      async create() { return { data: { id: 'session_bad' } }; },
      async messages() { return historyReply(undefined, { shape: 'malformed' }); },
      async prompt() { return { parts: [{ type: 'text',
        text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
        info: { role: 'assistant', providerID: 'deepseek',
                modelID: 'deepseek-v4-flash' } }; },
    },
  };
  await assert.rejects(
    runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) }),
    /v2.1_tool_history_unknown:history_malformed/);
});

test('a session mismatch is refused before any seal', async () => {
  let n = 0;
  const client = {
    session: {
      async create() { n += 1; return { data: { id: `session_mismatch_${n}` } }; },
      async messages({ path }) {
        // The history returned belongs to a different session.
        return { data: [{ role: 'assistant', sessionID: 'session_other',
                          parts: [{ type: 'text', text: '{}' }] }] };
      },
      async prompt() { return { parts: [{ type: 'text',
        text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
        info: { role: 'assistant', providerID: 'deepseek',
                modelID: 'deepseek-v4-flash' } }; },
    },
  };
  const out = await runPaired({ client, directory: '/tmp',
                                arms: buildArmPlan(prepared()) });
  assert.equal(out.results[0].tool_evidence, 'known_zero');
  assert.equal(out.results[0].session_id, 'session_mismatch_1');
});

test('a clean pair records zero tools per arm', async () => {
  let n = 0;
  const client = {
    session: {
      async create() { n += 1; return { data: { id: `session_${n}` } }; },
      async prompt() {
        return { parts: [{ type: 'text',
          text: '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.5,"rationale":"x"}' }],
          info: { role: 'assistant', providerID: 'deepseek', modelID: 'deepseek-v4-flash' } };
      },
      async messages() { return [{ parts: [{ type: 'text', text: 'ok' }] }]; },
    },
  };
  const out = await runPaired({ client, directory: '/tmp', arms: buildArmPlan(prepared()) });
  assert.equal(out.results.length, 2);
  for (const r of out.results) {
    assert.equal(r.tools, 0);
    assert.equal(r.tool_evidence, 'known_zero');
    assert.equal(r.tool_history_complete, true);
    assert.equal(r.session_terminal, 'completed');
  }
});
