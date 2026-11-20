// Offline checks for the standalone v2.1 supervisor: it must never be able to
// call a model, and it must read the same single shared quota.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(
  new URL('../source/v21_service.mjs', import.meta.url), 'utf8');

test('supervisor contains no model client and no SDK prompt call', () => {
  assert.equal(/session\.create|session\.prompt/.test(SRC), false,
    'the supervisor must not be able to open a model session');
  assert.equal(/runPaired/.test(SRC), false,
    'the supervisor must never dispatch a pair');
  assert.match(SRC, /model_client=none/);
});

test('supervisor delegates the shared cap to the python authority', () => {
  assert.match(SRC, /'prepare'/);
  assert.match(SRC, /'outcomes'/);
  assert.equal(/MAX_CALLS|daily_call_cap\s*=\s*\d/.test(SRC), false,
    'the cap must live in one place, not be re-implemented here');
});

test('supervisor validates the contract but sends nothing', () => {
  assert.match(SRC, /buildArmPlan/);
  assert.match(SRC, /NO_MODEL_CLIENT/);
});

test('supervisor has the documented timers', () => {
  assert.match(SRC, /SLOT_MS = 900_000/);
  assert.match(SRC, /TRIGGER_POLL_MS = 30_000/);
  assert.match(SRC, /LABEL_MS = 300_000/);
});
