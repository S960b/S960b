import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import server from '../source/v21_plugin.mjs';

test('probe performs exactly one real exchange and writes a result', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'probe-'));
  let creates = 0, prompts = 0;
  const client = { session: {
    async create() { creates++; return { data: { id: 'session_probe' } }; },
    async prompt() { prompts++;
      return { parts: [{ type: 'text',
        text: '{"probe":"ok","project":"news-v21"}' }],
        info: { role: 'assistant', providerID: 'deepseek',
                modelID: 'deepseek-v4-flash',
                tokens: { input: 12, output: 8 } } }; },
  } };
  writeFileSync(join(dir, '.mt-v21-probe-request'), JSON.stringify({ nonce: 't1' }));
  const s = await server({ client }, {
    root: dir, directory: dir, paper_enabled: true,
    auto_enabled: false, label_auto_enabled: false, python: '/usr/bin/python3' });
  await new Promise((r) => setTimeout(r, 1500));
  await s.dispose();
  const res = JSON.parse(readFileSync(join(dir, '.mt-v21-probe-result.json'), 'utf8'));
  assert.equal(res.status, 'ok');
  assert.equal(creates, 1, 'exactly one session');
  assert.equal(prompts, 1, 'exactly one prompt, no retry');
  assert.equal(res.model.providerID, 'deepseek');
  assert.match(res.response_text, /"probe":"ok"/);
  assert.equal(existsSync(join(dir, '.mt-v21-probe-request')), false);
});

test('probe refuses without an SDK client', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'probe2-'));
  writeFileSync(join(dir, '.mt-v21-probe-request'), JSON.stringify({ nonce: 't2' }));
  const s = await server({}, {
    root: dir, directory: dir, paper_enabled: false,
    auto_enabled: false, label_auto_enabled: false, python: '/usr/bin/python3' });
  await new Promise((r) => setTimeout(r, 800));
  await s.dispose();
  const res = JSON.parse(readFileSync(join(dir, '.mt-v21-probe-result.json'), 'utf8'));
  assert.equal(res.status, 'error');
  assert.equal(res.error, 'probe_sdk_unavailable');
});
