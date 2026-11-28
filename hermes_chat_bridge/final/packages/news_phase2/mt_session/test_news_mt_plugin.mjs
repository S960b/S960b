import test from 'node:test';
import assert from 'node:assert/strict';
import { runOnce, probeModel, extractAssistantText, extractFromHistory } from './news-mt-helpers.mjs';
import { server } from './news-mt-plugin.mjs';

const JSON_REPLY = '{"direction":"NEUTRAL","confidence":0.5,"rationale":"No catalyst"}';
const ID = 'a'.repeat(24);
const SESSION = 'ses_test_123';

function mockClient(answer = { data: { info: { role: 'assistant' }, parts: [{ type: 'text', text: JSON_REPLY }] } }) {
  const calls = [];
  return {
    calls,
    session: {
      create: async args => { calls.push(['create', args]); return { data: { id: SESSION } }; },
      prompt: async args => { calls.push(['prompt', args]); return answer; },
      messages: async args => { calls.push(['messages', args]); return { data: [
        { info: { role: 'user' }, parts: [{ type: 'text', text: 'prompt' }] },
        { info: { role: 'assistant' }, parts: [{ type: 'text', text: JSON_REPLY }] },
      ] }; },
    },
  };
}

test('extract assistant response, never user message', () => {
  assert.equal(extractAssistantText({ data: { info: { role: 'assistant' }, parts: [{ type: 'text', text: JSON_REPLY }] } }), JSON_REPLY);
  assert.equal(extractAssistantText({ data: { info: { role: 'user' }, parts: [{ type: 'text', text: 'PROMPT' }] } }), null);
  assert.equal(extractFromHistory({ data: [
    { info: { role: 'user' }, parts: [{ type: 'text', text: 'user prompt' }] },
    { info: { role: 'assistant' }, parts: [{ type: 'text', text: JSON_REPLY }] },
  ] }), JSON_REPLY);
});

test('default dry_run does not prepare or invoke MT', async () => {
  const client = mockClient();
  const result = await runOnce({ client, root: '/tmp', directory: '/tmp',
    pythonRunner: async () => { throw Error('python_should_not_run'); } });
  assert.deepEqual(result, { status: 'dry_run' });
  assert.equal(client.calls.length, 0);
});

test('not eligible news skips the session', async () => {
  const client = mockClient();
  const operations = [];
  const result = await runOnce({ client, dryRun: false, root: '/tmp', directory: '/tmp',
    pythonRunner: async mode => { operations.push(mode); return { status: 'skip', reason: 'no_recent_new_headlines' }; } });
  assert.equal(result.status, 'skip');
  assert.deepEqual(operations, ['prepare']);
  assert.equal(client.calls.length, 0);
});

test('manual cycle calls prepare, binds session, marks prompt, finalizes once', async () => {
  const client = mockClient();
  const operations = [];
  const pythonRunner = async (mode, args, stdin) => {
    operations.push({ mode, args, stdin });
    if (mode === 'prepare') return { status: 'ready', batch_id: ID, news: 2, prompt: 'News data JSON' };
    if (mode === 'session') return { status: 'session_created' };
    if (mode === 'prompt') return { status: 'prompt_attempted' };
    if (mode === 'finalize') return { status: 'created', prediction_id: 'pid' };
    throw Error('unexpected mode');
  };
  const result = await runOnce({ client, root: '/tmp', directory: '/tmp', dryRun: false, pythonRunner });
  assert.equal(result.status, 'created');
  assert.deepEqual(operations.map(x => x.mode), ['prepare', 'session', 'prompt', 'finalize']);
  assert.equal(JSON.parse(operations[3].stdin).response, JSON_REPLY);
  assert.equal(JSON.parse(operations[3].stdin).session_id, SESSION);
  assert.deepEqual(client.calls.map(x => x[0]), ['create', 'prompt']);
  assert.equal(client.calls[1][1].body.parts[0].text, 'News data JSON');
});

test('history lookup if prompt returns no assistant parts', async () => {
  const client = mockClient({ data: { info: { role: 'user' }, parts: [] } });
  const operations = [];
  await runOnce({ client, root: '/tmp', directory: '/tmp', dryRun: false,
    pythonRunner: async mode => {
      operations.push(mode);
      return mode === 'prepare' ? { status: 'ready', batch_id: ID, prompt: '{}' } : { status: 'ok' };
    },
  });
  assert.deepEqual(client.calls.map(x => x[0]), ['create', 'prompt', 'messages']);
  assert.equal(operations.at(-1), 'finalize');
});

test('no response means no finalize and no model replay', async () => {
  const client = mockClient({ data: { info: { role: 'user' }, parts: [] } });
  client.session.messages = async () => ({ data: [] });
  const operations = [];
  await assert.rejects(runOnce({ client, root: '/tmp', directory: '/tmp', dryRun: false,
    pythonRunner: async mode => {
      operations.push(mode);
      return mode === 'prepare' ? { status: 'ready', batch_id: ID, prompt: '{}' } : { status: 'ok' };
    },
  }), /model_result_unconfirmed_manual_reconcile/);
  assert.deepEqual(operations, ['prepare', 'session', 'prompt']);
  assert.equal(client.calls.filter(x => x[0] === 'prompt').length, 1);
});

test('synthetic model probe never touches NEWS SQLite', async () => {
  const client = mockClient();
  const result = await probeModel({ client, directory: '/tmp', dryRun: false });
  assert.equal(result.status, 'probe_ok');
  assert.equal(result.direction, 'NEUTRAL');
  assert.deepEqual(client.calls.map(x => x[0]), ['create', 'prompt']);
});

test('synthetic probe invalid schema is rejected', async () => {
  const client = mockClient({ data: { info: { role: 'assistant' }, parts: [{ type: 'text', text: '{"bad":true}' }] } });
  await assert.rejects(probeModel({ client, directory: '/tmp', dryRun: false }), /probe_invalid_schema/);
});

test('plugin starts with manual-only watcher and disposes without model', async () => {
  const plugin = await server({ client: mockClient() }, { root: '/tmp', dry_run: true, poll_ms: 10000,
    trigger_file: '/tmp/nonexistent-news-trigger-9ae2f', log_file: '/tmp/news-plugin-tests.log' });
  assert.equal(typeof plugin.dispose, 'function');
  await plugin.dispose();
});

test('auto slot is stable inside a 15-minute window and advances after', async () => {
  const { slotFor, AUTO_INTERVAL_MS } = await import('./news-mt-helpers.mjs');
  assert.equal(AUTO_INTERVAL_MS, 900000);
  const t0 = 1793966400000;
  assert.equal(slotFor(t0), slotFor(t0 + 899000));
  assert.notEqual(slotFor(t0), slotFor(t0 + 900000));
});

test('auto runner skips when python reports stop and never calls the model', async () => {
  const { runAutoOnce } = await import('./news-mt-helpers.mjs');
  let modelCalls = 0;
  const client = { session: {
    create: async () => { modelCalls++; return { data: { id: 'ses_auto12345' } }; },
    prompt: async () => { modelCalls++; return {}; },
  } };
  const pythonRunner = async (mode) => ({ status: 'skip', reason: 'auto_stopped_after_first_prediction' });
  const out = await runAutoOnce({ client, directory: '/tmp', root: '/tmp',
    pythonRunner, slotMs: 1793966400000, dryRun: false, log: () => {} });
  assert.equal(out.reason, 'auto_stopped_after_first_prediction');
  assert.equal(modelCalls, 0);
});

test('auto runner does nothing in dry_run', async () => {
  const { runAutoOnce } = await import('./news-mt-helpers.mjs');
  let touched = false;
  const out = await runAutoOnce({
    client: { session: { create: async () => { touched = true; }, prompt: async () => { touched = true; } } },
    directory: '/tmp', root: '/tmp', slotMs: 1, dryRun: true, log: () => {},
  });
  assert.equal(out.status, 'dry_run');
  assert.equal(touched, false);
});

test('auto runner stops after a created prediction', async () => {
  const { runAutoOnce } = await import('./news-mt-helpers.mjs');
  const seen = [];
  const pythonRunner = async (mode) => {
    seen.push(mode);
    if (mode === 'auto') return { status: 'ready', batch_id: 'a'.repeat(24), prompt: 'P', news: 2 };
    if (mode === 'session' || mode === 'prompt') return { status: 'ok' };
    if (mode === 'finalize') return { status: 'created', prediction_id: 'p1' };
    return { status: 'ok' };
  };
  const client = { session: {
    create: async () => ({ data: { id: 'ses_auto67890' } }),
    prompt: async () => ({ data: { info: { role: 'assistant' }, parts: [{ type: 'text', text: '{"direction":"UP","confidence":0.6,"rationale":"x"}' }] } }),
  } };
  const out = await runAutoOnce({ client, directory: '/tmp', root: '/tmp',
    pythonRunner, slotMs: 1793966400000, dryRun: false, log: () => {} });
  assert.equal(out.status, 'created');
  assert.deepEqual(seen, ['auto', 'session', 'prompt', 'finalize']);
});

test('plugin with auto enabled keeps only server/default exports and schedules', async () => {
  const mod = await import('./news-mt-plugin.mjs');
  assert.deepEqual(Object.keys(mod).sort(), ['default', 'server']);
  const handle = await mod.server({ client: { session: { create: async () => ({}), prompt: async () => ({}) } } }, {
    root: '/tmp', dry_run: true, auto_until_first_prediction: true,
    poll_ms: 60000, auto_interval_ms: 60000, label_interval_ms: 60000,
    trigger_file: '/tmp/nonexistent-auto-1', probe_trigger_file: '/tmp/nonexistent-auto-2',
    log_file: '/tmp/news-v12-plugin-tests.log',
  });
  assert.equal(typeof handle.dispose, 'function');
  await handle.dispose();
});

test('label tick passes a slot epoch so only one instance claims', async () => {
  const { runLabels, LABEL_INTERVAL_MS } = await import('./news-mt-helpers.mjs');
  assert.equal(LABEL_INTERVAL_MS, 300000);
  const seen = [];
  const pythonRunner = async (mode, args) => {
    seen.push([mode, args]);
    return { status: 'skip', reason: 'label_slot_already_claimed', labels_created: 0 };
  };
  const out = await runLabels({ root: '/tmp', pythonRunner, slotMs: 1793988900000 });
  assert.equal(out.reason, 'label_slot_already_claimed');
  assert.deepEqual(seen, [['labels', ['--slot-ms', '1793988900000']]]);
});

test('manual label call still passes no slot argument', async () => {
  const { runLabels } = await import('./news-mt-helpers.mjs');
  const seen = [];
  const pythonRunner = async (mode, args) => { seen.push([mode, args]); return { status: 'ok', labels_created: 0 }; };
  await runLabels({ root: '/tmp', pythonRunner });
  assert.deepEqual(seen, [['labels', []]]);
});

test('plugin entry still exports only default and server after v1.3', async () => {
  const mod = await import('./news-mt-plugin.mjs');
  assert.deepEqual(Object.keys(mod).sort(), ['default', 'server']);
});


test('label runner returns persistent completion flag from Python', async () => {
  const { runLabels } = await import('./news-mt-helpers.mjs');
  const result = await runLabels({ root: '/tmp', slotMs: 1793988900000,
    pythonRunner: async (mode,args) => {
      assert.equal(mode,'labels');
      assert.deepEqual(args,['--slot-ms','1793988900000']);
      return { status: 'ok', labels_created: 1, pilot_complete: true,
               completed_horizons: 3, expected_horizons: 3 };
    } });
  assert.equal(result.pilot_complete, true);
  assert.equal(result.completed_horizons, 3);
});
