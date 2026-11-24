#!/usr/bin/env python3
"""After ChatGPT confirms report seq6 AND disables dispatch, archive one stale preclaim journal.

No SQL mutations, no permissions issued, no plugin/config/service changes.
Only moves the exact obsolete local journal into private audit storage.
"""
from __future__ import annotations
import json,os,stat,sys
from pathlib import Path
import importlib.util
BASE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('finish38c',BASE/'finish38c.py')
assert spec and spec.loader
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Stop(Exception):pass
def need(ok,reason):
    if not ok:raise Stop('STAGE38C_FINAL_STOP_'+reason)
def dirsync(p):
    fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
def run():
    for path,sha in m.PINS.items():m.pin(path,sha)
    need(m.RECEIPT.is_file() and not m.RECEIPT.is_symlink(),'CHECKPOINT_MISSING')
    m.pin(m.RECEIPT,m.sha(m.RECEIPT.read_bytes()))
    record=json.loads(m.RECEIPT.read_text())
    need(record=={'phase':'seq5_ack_confirmed','task_id':m.TASK,'seq':5,'delivery_id':21,'report_message_id':m.REPORT_MID},'RECOVERY_NOT_COMPLETED')
    need(m.report_status()['attempted'],'REPORT_NOT_FOUND')
    need(m.delivery(5).get('status')=='delivered' and m.delivery(5).get('delivery_id')==21,'SEQ5_DELIVERY_NOT_CONFIRMED')
    need(m.delivery(6).get('status')=='delivered','CHATGPT_SEQ6_ACK_MISSING')
    v=m.checked('peek','--task-id',m.TASK,'--offset','0','--limit','6')
    need(v.get('seq')==6 and v.get('waiting_for')=='chatgpt','DIALOGUE_NOT_AT_SEQ6_CHATGPT')
    rows=v.get('messages',[])
    need(any(x.get('seq')==6 and x.get('message_id')==m.REPORT_MID and x.get('body')==m.CONTENT.decode() and x.get('from')=='mt' and x.get('to')=='chatgpt' for x in rows if isinstance(x,dict)),'REPORT_BODY_NOT_EXACT')
    ops=m.load_ops()
    ops.preflight()  # pins exact journal, candidate, empty inflight, services and watchdog
    need(not ops.PERMIT.exists() and not ops.PERMIT.is_symlink() and not ops.GATE.exists() and not ops.GATE.is_symlink(),'CAPABILITIES_NOT_REVOKED')
    src=ops.INTENTS/(m.TASK+'.json')
    dest=m.ROOT/'archived-intent-seq5-after-ack.json'
    need(not dest.exists() and not dest.is_symlink(),'ALREADY_ARCHIVED')
    st=src.lstat()
    need(stat.S_ISREG(st.st_mode) and not src.is_symlink() and st.st_uid==os.geteuid() and st.st_nlink==1 and (st.st_mode&0o077)==0,'INTENT_CHANGED')
    # Durable atomic relocation on the same /home/kali filesystem.
    os.rename(src,dest)
    dirsync(src.parent);dirsync(dest.parent)
    need(not src.exists() and dest.is_file(),'ARCHIVE_NOT_DURABLE')
    print('STAGE38C_SEQ5_AND_SEQ6_DELIVERED_CONFIRMED')
    print('STAGE38C_JOURNAL_ARCHIVED_AFTER_CHATGPT_ACK')
    print('STAGE38C_SCOPED_CAPABILITIES_ABSENT')
    print('STAGE38C_OLD_UNCERTAIN_CLAIM_CLOSED_NO_SDK_REEXECUTION')
    print('STAGE38C_NEXT_TASK_CAN_USE_EXISTING_BRIDGE')
def main():
    try:
        need(len(sys.argv)==1,'NO_ARGS')
        run()
        return 0
    except (Stop,m.Stop) as e:print(str(e),file=sys.stderr)
    except Exception as e:print('STAGE38C_FINAL_STOP_'+type(e).__name__,file=sys.stderr)
    return 2
if __name__=='__main__':raise SystemExit(main())
