// NEWS v2.1 paper runner: one paired N/NM inference through the native SDK.
// PAPER ONLY. No order, withdraw or private-key endpoint exists here.
// Python SQLite remains the authority for quotas and records; this file only
// drives exactly two sequential model calls and then returns the raw texts.
import { assistantText, observedModel } from
  './news-p4-v2-helpers.mjs';

const ARM_ORDER = ['news', 'news_market'];
const HEX64 = /^[a-f0-9]{64}$/;

/**
 * Strict adapter from the prepared pair (Python) to the runner plan.
 *
 * v21_paper_prep.prepare() returns {prompts:{news,news_market},
 * attempts:[id,id], prompts_sha256:{...}, event_id}. This maps arm names to
 * their attempt ids, refuses duplicates or unknown ids, and verifies that the
 * two prompts are genuinely different - an identical pair measures nothing.
 */
export function buildArmPlan(prepared) {
  const prompts = prepared?.prompts;
  const ids = prepared?.attempts;
  const digests = prepared?.prompts_sha256;
  if (!prompts || !Array.isArray(ids) || ids.length !== 2) {
    throw Error('v2.1_prepared_contract_invalid');
  }
  if (new Set(ids).size !== 2) {
    throw Error('v2.1_attempt_ids_duplicated');
  }
  for (const id of ids) {
    if (!HEX64.test(id)) throw Error('v2.1_attempt_id_malformed');
  }
  const arms = ARM_ORDER.map((arm, i) => {
    const prompt = prompts[arm];
    if (typeof prompt !== 'string' || prompt.length === 0) {
      throw Error(`v2.1_prompt_missing:${arm}`);
    }
    return { arm, attempt_id: ids[i], event_id: prepared.event_id, prompt };
  });
  if (arms[0].prompt === arms[1].prompt) {
    throw Error('v2.1_degenerate_pair');
  }
  if (digests && (digests.news === digests.news_market)) {
    throw Error('v2.1_degenerate_pair_digest');
  }
  return arms;
}

// NEWS v2.1 paper runner: one paired N/NM inference through the native SDK.
// PAPER ONLY. No order, withdraw or private-key endpoint exists here.
// Python SQLite remains the authority for quotas and records; this file only
// drives exactly two sequential model calls and then returns the raw texts.
const ID = /^[a-f0-9]{64}$/;
const SID = /^[A-Za-z0-9_-]{5,160}$/;
const EXPECTED_PROVIDER = 'deepseek';

function requireSession(client) {
  if (typeof client?.session?.create !== 'function' ||
      typeof client?.session?.prompt !== 'function') {
    throw Error('v2.1_sdk_not_ready');
  }
}

/**
 * Run exactly one paired attempt. Never retries: if a call is ambiguous the
 * reservation stays and the caller must reconcile manually.
 */
export const TOOL_ZERO = 'known_zero';
export const TOOL_POSITIVE = 'known_positive';
export const TOOL_UNKNOWN = 'unknown';

/**
 * Read one arm's tool history and classify it without inventing a zero.
 *
 * A session that answered, yet reports an empty history, is a contradiction:
 * the assistant message itself is missing, so the view was truncated or taken
 * too early. That is unknown, not clean. Same for a missing API, a transport
 * error and any non-array payload.
 */
async function toolEvidence(client, { sid, directory, arm }) {
  if (typeof client?.session?.messages !== 'function') {
    return { state: TOOL_UNKNOWN, count: null, reason: 'no_history_api',
             provenance: 'sdk_session_messages_unavailable' };
  }
  let hist;
  try {
    hist = await client.session.messages({
      path: { id: sid }, query: { directory },
    });
  } catch {
    return { state: TOOL_UNKNOWN, count: null, reason: 'transport_error',
             provenance: 'sdk_session_messages_throw' };
  }
  const list = Array.isArray(hist?.data) ? hist.data
    : Array.isArray(hist) ? hist : null;
  if (list === null) {
    return { state: TOOL_UNKNOWN, count: null, reason: 'history_not_a_list',
             provenance: 'sdk_session_messages_shape' };
  }
  const messages = list.filter((m) => m && typeof m === 'object');
  if (list.length > 0 && messages.length !== list.length) {
    // Part of the history is unreadable, so the zero would be a guess.
    return { state: TOOL_UNKNOWN, count: null, reason: 'history_malformed',
             provenance: 'sdk_session_messages_malformed' };
  }
  if (list.length === 0) {
    return { state: TOOL_UNKNOWN, count: null, reason: 'empty_snapshot',
             provenance: 'empty_but_unproven_history' };
  }
  const toolParts = messages.reduce((n, m) => n + (Array.isArray(m.parts)
    ? m.parts.filter((p) => p && p.type === 'tool').length : 0), 0);
  return toolParts > 0
    ? { state: TOOL_POSITIVE, count: toolParts, reason: 'tool_part_present',
        provenance: `sdk_session_messages:${arm}` }
    : { state: TOOL_ZERO, count: 0, reason: 'no_tool_parts',
        provenance: `sdk_session_messages:${arm}` };
}

export async function runPaired({ client, directory, arms,
                                  agent = 'news-restricted',
                                  log = () => {} }) {
  requireSession(client);
  if (!Array.isArray(arms) || arms.length !== 2) {
    throw Error('v2.1_arm_plan_invalid');
  }
  if (!arms.some((a) => a.arm === 'news') ||
      !arms.some((a) => a.arm === 'news_market')) {
    throw Error('v2.1_arm_pair_invalid');
  }
  for (const arm of arms) {
    if (!ID.test(arm.attempt_id) || typeof arm.prompt !== 'string') {
      throw Error('v2.1_prompt_plan_invalid');
    }
    if (Buffer.byteLength(arm.prompt) > 13000) {
      throw Error('v2.1_prompt_too_large');
    }
  }

  const seen = new Set();
  for (const arm of arms) {
    if (seen.has(arm.attempt_id)) throw Error('v2.1_attempt_ids_duplicated');
    seen.add(arm.attempt_id);
  }

  const results = [];
  for (const arm of arms) {
    // The restricted agent is what makes the sealed information set real: it
    // denies every tool, so the model cannot browse past the cutoff. A session
    // that cannot be created with that agent must NOT fall back to an
    // unrestricted one - that would silently contaminate the experiment.
    const created = await client.session.create({
      body: { title: `NEWS v2.1 ${arm.arm} ${arm.event_id.slice(0, 12)}`,
              agent },
      query: { directory },
    });
    const sid = created?.data?.id ?? created?.id;
    if (typeof sid !== 'string' || !SID.test(sid)) {
      // Unknown session create: do NOT retry, surface for reconciliation.
      throw Error('v2.1_session_create_unknown_no_retry');
    }
    let response;
    try {
      response = await client.session.prompt({
        path: { id: sid },
        query: { directory },
        body: { parts: [{ type: 'text', text: arm.prompt }] },
      });
    } catch (err) {
      // Ambiguous outcome: reservation must be kept, never replayed.
      throw Error('v2.1_prompt_ambiguous_no_retry');
    }
    let text = assistantText(response);
    let model = observedModel(response);
    if ((!text || !model) && typeof client.session.messages === 'function') {
      const history = await client.session.messages({
        path: { id: sid }, query: { directory },
      });
      if (!text) text = assistantText(history);
      if (!model) model = observedModel(history);
    }
    if (!text || !model) {
      throw Error('v2.1_unconfirmed_model_result_no_retry');
    }
    if (model.providerID !== EXPECTED_PROVIDER) {
      // A different provider silently answers a different experiment.
      throw Error(`v2.1_model_provider_unexpected:${model.providerID}`);
    }
    if (results.length > 0 &&
        (results[0].model.modelID !== model.modelID ||
         results[0].model.providerID !== model.providerID)) {
      throw Error('v2.1_model_identity_conflict_across_arms');
    }
    // Per-arm completion time, measured at the moment this arm's answer was
    // confirmed. Never a single timestamp stamped once for both arms.
    // Post-hoc audit: a tool call means the sealed information set did not
    // hold. Detection cannot undo the leak, but it must stop the pair from
    // being sealed as if it were clean.
    //
    // The evidence is typed, not a number with a magic -1. Three outcomes are
    // possible and only one of them permits a seal:
    //   verified zero  -> the whole history was read and holds no tool part
    //   positive usage -> a tool ran, the arm is contaminated
    //   unknown        -> the history could not be read or proven complete
    // "Unknown" is refused exactly like "positive": a count that was never
    // observed must never be written down as zero.
    const evidence = await toolEvidence(client, { sid, directory, arm: arm.arm });
    if (evidence.state === TOOL_UNKNOWN) {
      throw Error(`v2.1_tool_history_unknown:${evidence.reason}:${arm.arm}`);
    }
    if (evidence.state === TOOL_POSITIVE) {
      throw Error(`v2.1_tool_used_in_arm_no_seal:${arm.arm}`);
    }
    // One envelope shared with pilot_run.mjs and the recorder's contract:
    // arm names, model identity, session, terminal state, completion time and
    // the tri-state tool evidence. `history_complete` is repeated under both
    // names because the recorder's validator reads the long one and older
    // readers of the probe payload use the short one.
    results.push({ arm: arm.arm, attempt_id: arm.attempt_id,
                   session_id: sid, text, model, tools: evidence.count,
                   tool_evidence: evidence.state,
                   tool_evidence_provenance: evidence.provenance,
                   tool_history_complete: evidence.state !== TOOL_UNKNOWN,
                   history_complete: evidence.state !== TOOL_UNKNOWN,
                   session_terminal: 'completed',
                   completed_ms: Date.now() });
    log(`v21_arm_done arm=${arm.arm}`);
  }
  return { status: 'paired_complete', results };
}
