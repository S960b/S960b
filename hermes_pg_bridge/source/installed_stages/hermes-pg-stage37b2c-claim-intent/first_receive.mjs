import {openWire} from './wire56.mjs';

const retired = 'task_f2c64cfdf7894537b35a19a6def977c6';
export function verifyFirstTurnScope(publicKey, taskId, message, state) {
  if (taskId === retired || message.from !== 'chatgpt' || message.to !== 'mt'
      || message.kind !== 'task' || message.returned !== false) {
    throw Error('new_dialogue_message_identity');
  }
  const hasCursor = Object.hasOwn(state.seen, taskId);
  const hasSession = Object.hasOwn(state.sessions, taskId);
  if (!hasCursor && !hasSession) {
    if (message.seq !== 1) throw Error('new_dialogue_missing_cursor');
  } else if (!hasCursor || !hasSession || !Number.isSafeInteger(state.seen[taskId])
             || state.seen[taskId] < 1 || message.seq <= state.seen[taskId]) {
    throw Error('new_dialogue_cursor_mismatch');
  }
  const verified = openWire(publicKey, taskId, message.message_id, message.seq, message.body);
  return Object.freeze({seq: verified.seq, message_id: message.message_id,
    body: verified.body, from:'chatgpt', to:'mt', kind:'task'});
}
