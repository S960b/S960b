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

function fakeClient({ failOn = null, noModel = false } = {}) {
  const calls = [];
  let n = 0;
  return {
    calls,
    client: {
      session: {
        create: async () => { n += 1; calls.push('create');
          return { data: { id: `ses_v21_${n}` } }; },
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
