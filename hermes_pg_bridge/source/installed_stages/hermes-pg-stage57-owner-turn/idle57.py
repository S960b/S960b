import json,os,stat
from pathlib import Path
from owner_turn57 import SCOPE,need,is_revoked,valid_seq
def read_private_json(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,"rb") as f:
        s=os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=65536,"unsafe_state")
        b=f.read(65537)
        need(len(b)==s.st_size,"state_size")
    def pairs(p):
        d={}
        for k,v in p:
            need(k not in d,"duplicate_json_key")
            d[k]=v
        return d
    return json.loads(b,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_json")))
def private_dir(p):
    s=Path(p).lstat()
    need(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,"unsafe_directory")
def local_idle(root,revocations,tid,h):
    # ONLY called with shared receiver lock held by owner coordinator.
    # Conservative: any intent or inflight state blocks owner action. No removal.
    need(tid==SCOPE and h.tid==tid,"scope_mismatch")
    root=Path(root)
    private_dir(root);private_dir(root/"claim-intents37b2c");private_dir(revocations)
    if any((root/"claim-intents37b2c").iterdir()):return False
    state=read_private_json(root/"state.json")
    need(type(state) is dict and set(state)=={"sessions","seen","inflight"} and all(type(state[x]) is dict for x in state),"invalid_state")
    need(all(type(x) is str and x for x in state["sessions"].values()),"invalid_sessions")
    need(all(type(x) is int and 0<=x<=9007199254740991 for x in state["seen"].values()),"invalid_seen")
    if state["inflight"]:return False
    need(tid in state["seen"] and valid_seq(state["seen"][tid]),"missing_seen")
    seen=state["seen"][tid]
    if h.kind=="task" and h.delivery=="pending":
        # If the receiver has already delivered this sequence, DB pending is not proof.
        return seen<h.seq
    if h.kind=="report":
        return h.seq>=2 and seen>=h.seq-1 and seen<h.seq
    if h.kind=="task" and h.delivery=="cancelled":
        return seen<h.seq and is_revoked(revocations,tid,h.seq,h.mid)
    return False
