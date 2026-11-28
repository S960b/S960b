import os,json,uuid,stat
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass
from owner_turn57 import SCOPE,Action,decide,revoke,need,TurnError,MID,valid_seq
# Must use actual inbox.lock; do not reap a stale/unknown lock from owner side.
@contextmanager
def owner_lock(path):
    path=Path(path)
    d=path.parent.lstat()
    need(stat.S_ISDIR(d.st_mode) and d.st_uid==os.geteuid() and stat.S_IMODE(d.st_mode)==0o700,"unsafe_lock_dir")
    holder={"pid":os.getpid(),"id":str(uuid.uuid4())}
    try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        yield False
        return
    try:
        with os.fdopen(fd,"wb") as f:
            os.fchmod(f.fileno(),0o600)
            f.write(json.dumps(holder,separators=(",",":")).encode())
            f.flush();os.fsync(f.fileno())
        yield True
    finally:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,"rb") as f:
            s=os.fstat(f.fileno())
            need(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size<=256,"lock_unsafe")
            need(json.load(f)==holder,"lock_replaced")
            n=path.lstat()
            need((n.st_dev,n.st_ino)==(s.st_dev,s.st_ino),"lock_changed")
            path.unlink()
@dataclass(frozen=True)
class Head:
    tid:str
    version:int
    next_actor:str
    seq:int
    mid:str|None
    kind:str|None
    delivery:str|None
    returned:bool
    active:bool
    enabled:bool
@dataclass(frozen=True)
class Request:
    tid:str
    version:int
    target:str
    request_id:str
def check_head(h):
    need(isinstance(h,Head) and h.tid==SCOPE and type(h.version) is int and h.version>=0 and type(h.seq) is int and 0<=h.seq<=9007199254740991,"head_invalid")
    need(type(h.active) is bool and h.active and type(h.enabled) is bool and h.enabled,"scope_disabled")
    need(type(h.returned) is bool,"head_invalid")
    if h.seq==0:need(h.mid is None and h.kind is None and h.delivery is None and not h.returned,"head_invalid")
    else:need(type(h.mid) is str and MID.fullmatch(h.mid),"head_invalid")
    need(h.next_actor in ("chatgpt","agent"),"head_invalid")
    need(h.kind in (None,"task","report","ack") and h.delivery in (None,"pending","leased","delivered","cancelled"),"head_invalid")
    need(h.returned==(h.delivery=="cancelled"),"head_invalid")
def check_req(r):
    need(isinstance(r,Request) and r.tid==SCOPE and type(r.version) is int and r.version>=0 and r.target in ("chatgpt","agent"),"request_invalid")
    need(type(r.request_id) is str and len(r.request_id)==32 and all(c in "0123456789abcdef" for c in r.request_id),"request_invalid")
CONTINUATION="Владелец вернул ход агенту. Прочитай последнее собственное сообщение в текущем диалоге. Не повторяй ранее выполненные команды автоматически. Верни отчёт о текущем состоянии, возможных незавершённых действиях и следующем безопасном шаге. Не изменяй файлы, систему, данные или разрешения без отдельного задания."
def execute(r,*,lock_path,revocations,read_head,local_idle,cancel,send,send_status):
    # Adapters are trusted local code; never instantiate from HTTP/DB content.
    # Caller is already owner-authenticated, request has stable ID and exact version.
    check_req(r)
    with owner_lock(lock_path) as locked:
        if not locked:return {"status":"waiting","reason":"receiver_busy"}
        h=read_head(r.tid);check_head(h)
        mid="owner57-"+r.request_id
        # A repeated successful send may have changed the head. Read durable
        # exact receipt, never issue a different ID or infer success from turn alone.
        if r.target=="agent":
            receipt=send_status(r.tid,mid)
            if receipt is not None:
                need(receipt=={"tid":r.tid,"mid":mid,"sender":"chatgpt","recipient":"agent","seq":receipt.get("seq"),"returned":False} and valid_seq(receipt["seq"]),"receipt_invalid")
                return {"status":"applied","seq":receipt["seq"],"target":"agent"}
        if h.version!=r.version:
            if h.next_actor==r.target:
                if r.target=="chatgpt" and not local_idle(r.tid,h):
                    return {"status":"waiting","reason":"unsafe_boundary"}
                return {"status":"already_actor","target":r.target}
            return {"status":"stale","version":h.version}
        idle=local_idle(r.tid,h)
        action=decide(target=r.target,next_actor=h.next_actor,latest_kind=h.kind,delivery=h.delivery,local_idle=idle)
        if action==Action.NOOP:return {"status":"already_actor","target":r.target}
        if action==Action.WAIT:return {"status":"waiting","reason":"unsafe_boundary"}
        if action==Action.CANCEL_PENDING:
            # Only unclaimed task; no state change if revocation cannot persist.
            need(h.kind=="task" and h.delivery=="pending" and not h.returned,"cancel_precondition")
            revoke(revocations,r.tid,h.seq,h.mid)
            # On failure/uncertainty NEVER remove revocation.
            result=cancel(r.tid,h.mid,h.version)
            need(result=={"tid":r.tid,"mid":h.mid,"returned":True,"next_actor":"chatgpt","version":h.version+1},"cancel_unconfirmed")
            return {"status":"applied","target":"chatgpt","version":h.version+1}
        need(action==Action.SIGN_NEW,"unknown_action")
        # fixed harmless prompt; no use of untrusted stored body as signing input.
        result=send(r.tid,mid,CONTINUATION,h.version)
        need(result=={"tid":r.tid,"mid":mid,"seq":h.seq+1,"version":h.version+1,"sender":"chatgpt","recipient":"agent","returned":False},"send_unconfirmed")
        return {"status":"applied","target":"agent","seq":h.seq+1}
