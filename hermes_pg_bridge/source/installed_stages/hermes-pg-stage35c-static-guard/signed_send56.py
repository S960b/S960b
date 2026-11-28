import os,re,stat
from pathlib import Path
from contextlib import closing
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from reserve56 import Reservation
SCOPE="task_b6c26aa18220412bafdbf32393f16568"
ROOT=Path("/home/kali/.config/bridge-task-sign56")
def check_dir(root):
    s=root.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o700:
        raise ValueError("signer directory")
def check_private(s):
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_nlink!=1 or stat.S_IMODE(s.st_mode)!=0o600:
        raise ValueError("signer file")
def load_reservation(tid,root=ROOT):
    if not isinstance(tid,str) or re.fullmatch(r'task_[0-9a-f]{32}',tid) is None:
        raise ValueError("signed scope")
    check_dir(root)
    fd=os.open(root/"task-private.pem",os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,"rb") as f:
        check_private(os.fstat(f.fileno()))
        key=f.read(4097)
    if len(key)>4096:raise ValueError("key size")
    sk=load_pem_private_key(key,password=None)
    if not isinstance(sk,Ed25519PrivateKey):raise ValueError("key type")
    path=root/"reservations.sqlite" if tid==SCOPE else root/"first-turn-reservations"/(tid+".sqlite")
    if path.exists():
        check_private(path.lstat())
    else:
        d=path.parent
        if not d.exists():
            os.mkdir(d,0o700)
        elif stat.S_IMODE(d.lstat().st_mode)!=0o700 or d.lstat().st_uid!=os.geteuid():
            raise ValueError("signer directory")
        r=Reservation(path,sk,tid);r.initialize();return r
    return Reservation(path,sk,tid)
def submit(cg,tid,mid,body,expected_version,reservation):
    if reservation.tid!=tid:raise ValueError("signed scope")
    # Head is metadata only, not source of body. Do not sign database-returned text.
    head=cg._one("SELECT bridge.cg_managed_detail(%s) AS result",(tid,))
    if not isinstance(head,dict) or head.get("registered") is not True:
        raise ValueError("not registered")
    seq,wire=reservation.reserve(mid,body,expected_version,head)
    result=cg._one("SELECT bridge.cg_send_managed_task(%s,%s,%s,%s) AS result",(tid,mid,wire,expected_version))
    if not isinstance(result,dict) or result.get("task_id")!=tid or result.get("message_id")!=mid or type(result.get("seq")) is not int or result["seq"]!=seq or result.get("sender")!="chatgpt" or result.get("recipient")!="agent" or result.get("returned") is not False or type(result.get("created")) is not bool:
        raise ValueError("signed send outcome uncertain")
    return result