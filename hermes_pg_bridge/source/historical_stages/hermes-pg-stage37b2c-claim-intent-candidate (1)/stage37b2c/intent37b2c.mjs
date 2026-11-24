/* Stage 37B2C claim intent journal. No SQL/SDK dependencies.
 * A missing lease receipt must never be interpreted as permission to re-run an SDK action.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';

export const DEFAULT_INTENT_DIR = '/home/kali/.local/state/mt-pg-inbox-managed-stage34e/claim-intents37b2c';
const TASK_RE = /^task_[0-9a-f]{32}$/;
function valid(taskId, seq, messageId) {
  if (!TASK_RE.test(taskId) || !Number.isSafeInteger(seq) || seq < 1 ||
      typeof messageId !== 'string' || !/^[A-Za-z0-9._:-]{1,256}$/.test(messageId)) {
    throw new Error('stage37b2c_invalid_claim_identity');
  }
}
function nameFor(taskId, directory) {
  if (!TASK_RE.test(taskId)) throw new Error('stage37b2c_invalid_task_id');
  return path.join(directory, taskId + '.json');
}
function dirStat(directory, create=false) {
  if (create) {
    try { fs.mkdirSync(directory,{mode:0o700}); syncDir(path.dirname(directory)); }
    catch(e) { if (e?.code!=='EEXIST') throw e; }
  }
  const st = fs.lstatSync(directory);
  if (!st.isDirectory() || st.isSymbolicLink() ||
      st.uid !== process.getuid() || (st.mode & 0o077) !== 0) {
    throw new Error('stage37b2c_untrusted_journal_dir');
  }
}
function syncDir(directory) {
  const fd=fs.openSync(directory,fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
export function stage37b2cIntentPresent(taskId,directory=DEFAULT_INTENT_DIR) {
  const filename=nameFor(taskId,directory);
  try { dirStat(directory); }
  catch(e) { if (e?.code==='ENOENT') return false; throw e; }
  try { fs.lstatSync(filename); return true; }
  catch(e) { if (e?.code==='ENOENT') return false; throw e; }
}
export function stage37b2cWriteIntent(taskId,seq,messageId,directory=DEFAULT_INTENT_DIR) {
  valid(taskId,seq,messageId);
  dirStat(directory,true);
  const filename=nameFor(taskId,directory);
  const record=JSON.stringify({version:1,phase:'claim_outcome_uncertain',task_id:taskId,
                               seq,message_id:messageId});
  // Exclusive create; no overwrite, no symlink follow. Any failed fsync leaves a
  // blocking marker and no claim is attempted by the patched dispatcher.
  const flags=fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW;
  const fd=fs.openSync(filename,flags,0o600);
  try {
    fs.writeFileSync(fd,record+'\n',{encoding:'utf8'});
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  syncDir(directory);
  return true;
}
export function stage37b2cClearAfterAck(taskId,seq,messageId,directory=DEFAULT_INTENT_DIR) {
  valid(taskId,seq,messageId);
  dirStat(directory);
  const filename=nameFor(taskId,directory);
  const st=fs.lstatSync(filename);
  if (!st.isFile() || st.isSymbolicLink() || st.uid!==process.getuid() ||
      st.nlink!==1 || (st.mode & 0o077)!==0 || st.size>1024) {
    throw new Error('stage37b2c_untrusted_intent_file');
  }
  const fd=fs.openSync(filename,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
  let raw;
  try {
    const fst=fs.fstatSync(fd);
    if (fst.ino!==st.ino || fst.dev!==st.dev || fst.size!==st.size)
      throw new Error('stage37b2c_intent_file_race');
    raw=fs.readFileSync(fd,'utf8');
  } finally { fs.closeSync(fd); }
  const record=JSON.parse(raw);
  if (record.version!==1 || record.phase!=='claim_outcome_uncertain' ||
      record.task_id!==taskId || record.seq!==seq || record.message_id!==messageId) {
    throw new Error('stage37b2c_intent_mismatch');
  }
  fs.unlinkSync(filename);
  syncDir(directory);
  return true;
}
export function stage37b2cFinalizeAfterConfirmedAck(state,taskId,seq,messageId,
                                                     directory=DEFAULT_INTENT_DIR) {
  valid(taskId,seq,messageId);
  if (state?.inflight?.[taskId] || !Number.isSafeInteger(state?.seen?.[taskId]) ||
      state.seen[taskId] < seq) {
    throw new Error('stage37b2c_ack_not_durable_in_local_state');
  }
  // Caller must only invoke this after the existing pinned ACK success path.
  return stage37b2cClearAfterAck(taskId,seq,messageId,directory);
}
