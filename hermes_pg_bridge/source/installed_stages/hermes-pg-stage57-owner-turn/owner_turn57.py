import os
import re
import stat
from pathlib import Path
from enum import Enum
SCOPE="task_b6c26aa18220412bafdbf32393f16568"
MAX_SEQ=9007199254740991
MID=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z",re.ASCII)
class TurnError(RuntimeError):pass
class Action(str,Enum):
    NOOP="already_actor"
    SIGN_NEW="sign_new_task"
    CANCEL_PENDING="revoke_then_cancel"
    WAIT="wait_for_safe_boundary"
def need(ok,code):
    if not ok:raise TurnError(code)
def valid_seq(seq):
    return type(seq) is int and 1<=seq<=MAX_SEQ
def decide(*,target,next_actor,latest_kind,delivery,local_idle):
    # local_idle=True ONLY while holding actual receiver exclusion lock
    # and after validating no inflight/claim intent/SDK ambiguity.
    need(target in ("chatgpt","agent") and next_actor in ("chatgpt","agent"),"invalid_actor")
    need(type(local_idle) is bool,"invalid_idle")
    need(latest_kind in (None,"task","report","ack"),"invalid_kind")
    need(delivery in (None,"pending","leased","delivered","cancelled"),"invalid_delivery")
    if target==next_actor:return Action.NOOP
    if not local_idle:return Action.WAIT
    if target=="chatgpt":
        if latest_kind=="task" and delivery=="pending":return Action.CANCEL_PENDING
        return Action.WAIT
    # Preserve prior report. Historical report ACK is independent of turn.
    if latest_kind=="report" and delivery in ("pending","delivered"):return Action.SIGN_NEW
    if latest_kind=="task" and delivery=="cancelled":return Action.SIGN_NEW
    if latest_kind is None and delivery is None:return Action.SIGN_NEW
    return Action.WAIT
def _name(tid,seq,mid):
    need(tid==SCOPE and valid_seq(seq) and type(mid) is str and MID.fullmatch(mid),"invalid_identity")
    # Seq alone is a deny-list key, so even a different signed mid at the same
    # sequence is denied. The content binds the original identity for audit.
    return f"{seq}.revoked",f"{tid}\n{seq}\n{mid}\n".encode("ascii")
def _dir(root):
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        need(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,"unsafe_directory")
        return fd
    except BaseException:
        os.close(fd);raise
def initialize(root):
    # Explicit deployment step only; never implicitly recreate missing store.
    root=Path(root)
    root.mkdir(mode=0o700,exist_ok=False)
    pfd=os.open(root.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(pfd)
    finally:os.close(pfd)
    fd=_dir(root)
    os.close(fd)
def _read(fd,name):
    try:f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    except FileNotFoundError:return None
    with os.fdopen(f,"rb") as h:
        s=os.fstat(h.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size<=256,"unsafe_revocation")
        b=h.read(257)
        need(len(b)==s.st_size,"revocation_size")
        return b
def _valid_record(b,tid,seq):
    try:
        t,q,m,empty=b.decode("ascii").split("\n")
        return t==tid and q==str(seq) and empty=="" and bool(MID.fullmatch(m))
    except (UnicodeError,ValueError):return False
def is_revoked(root,tid,seq,mid):
    name,_=_name(tid,seq,mid);fd=_dir(root)
    try:
        b=_read(fd,name)
        if b is None:return False
        need(_valid_record(b,tid,seq),"corrupt_revocation")
        return True
    finally:os.close(fd)
def revoke(root,tid,seq,mid):
    # Caller MUST hold the same cross-process exclusion lock as dispatch.
    # Never remove or overwrite an existing revocation, even on DB error.
    name,data=_name(tid,seq,mid);fd=_dir(root)
    try:
        old=_read(fd,name)
        if old is not None:
            need(old==data,"revocation_identity_conflict")
            os.fsync(fd)
            return False
        try:f=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        except FileExistsError:raise TurnError("revocation_race")
        with os.fdopen(f,"wb") as h:
            os.fchmod(h.fileno(),0o600);h.write(data);h.flush();os.fsync(h.fileno())
        os.fsync(fd)
        return True
    finally:os.close(fd)
