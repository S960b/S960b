import os,stat,json,re,fcntl,subprocess
from pathlib import Path
from contextlib import contextmanager
from .compat import version_token
from .store import BridgeError
SCOPE="task_b6c26aa18220412bafdbf32393f16568"
ROOT=Path("/home/kali/.local/state/mt-pg-inbox-managed-stage34e/owner-turn57-requests")
CODE=Path("/home/kali/hermes-pg-stage57-owner-turn")
APP=Path("/home/kali/bridge-pg-v2")
PY=APP/".venv/bin/python"
SITE=APP/".venv/lib/python3.13/site-packages"
RUNTIME=Path("/home/kali/hermes-pg-stage35c-static-guard")
HEX32=re.compile(r"[0-9a-f]{32}\Z")
HEX40=re.compile(r"[0-9a-f]{40}\Z")
def require(ok,code="request_rejected"):
    if not ok:raise BridgeError(code)
def directory(p):
    s=p.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,"owner_turn_unavailable")
def private(s):
    return stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600
@contextmanager
def lock(root):
    directory(root)
    fd=os.open(root/"requests.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        require(private(os.fstat(fd)),"owner_turn_unavailable")
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:os.close(fd)
def read_record(p):
    try:fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    with os.fdopen(fd,"rb") as f:
        s=os.fstat(f.fileno());require(private(s) and 0<s.st_size<=2048,"owner_turn_unavailable")
        b=f.read(2049);require(len(b)==s.st_size,"owner_turn_unavailable")
        return json.loads(b)
def write_record(root,p,r):
    import tempfile
    fd,n=tempfile.mkstemp(prefix=".request-",dir=root)
    try:
        with os.fdopen(fd,"wb") as f:
            os.fchmod(f.fileno(),0o600)
            f.write(json.dumps(r,separators=(",",":")).encode());f.flush();os.fsync(f.fileno())
        os.replace(n,p)
        d=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if os.path.lexists(n):os.unlink(n)
def worker(req):
    directory(CODE)
    import hashlib
    p=CODE/'worker57.py'
    require(private(p.lstat()) and hashlib.sha256(p.read_bytes()).hexdigest()=='76633e83912ae1bdfd096f2a849451b07e317ee8e088c1ab7b8db6f9e713e1ba','owner_turn_unavailable')
    # Fixed executable and module paths; no inherited PG/Python or auth environment.
    env={"HOME":"/home/kali","PATH":"/usr/bin:/bin","LANG":"C.UTF-8",
         "PYTHONPATH":os.pathsep.join(map(str,(CODE,RUNTIME,APP,SITE)))}
    try:
        p=subprocess.run([str(PY),"-S","-B",str(CODE/"worker57.py")],
            input=json.dumps(req,separators=(",",":")).encode(),stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,cwd=APP,env=env,timeout=25)
        if p.returncode!=0 or len(p.stdout)>2048:return {"status":"uncertain"}
        r=json.loads(p.stdout)
        return r
    except (OSError,ValueError,subprocess.TimeoutExpired):return {"status":"uncertain"}
def response(r):
    require(type(r) is dict,"owner_turn_unavailable")
    s=r.get("status")
    if s in ("waiting","uncertain","stale","already_actor","applied"):
        out={"status":s}
        if s=="waiting":out["reason"]="safe_boundary"
        if s in ("applied","already_actor"):
            require(r.get("target") in ("chatgpt","agent"),"owner_turn_unavailable")
            out["target"]=r["target"]
        return out
    return {"status":"uncertain"}
def handle_owner_turn(cat,obj,root=ROOT,call=worker):
    require(type(obj) is dict and set(obj)=={"task_id","head","target","request_id"})
    require(obj["task_id"]==SCOPE and obj["target"] in ("chatgpt","agent"))
    require(type(obj["request_id"]) is str and HEX32.fullmatch(obj["request_id"]))
    require(type(obj["head"]) is str and HEX40.fullmatch(obj["head"]))
    root=Path(root);p=root/(obj["request_id"]+".json")
    with lock(root) as acquired:
        if not acquired:return {"status":"waiting","reason":"safe_boundary"}
        rec=read_record(p)
        if rec is None:
            d=cat.metadata(SCOPE)
            v=d.get("version")
            require(type(v) is int and 0<=v<=9007199254740991)
            if obj["head"]!=version_token(SCOPE,v):return {"status":"stale"}
            rec={"request":dict(obj),"version":v,"result":None}
            write_record(root,p,rec)
        require(type(rec) is dict and set(rec)=={"request","version","result"} and rec["request"]==obj)
        v=rec["version"]
        require(type(v) is int and 0<=v<=9007199254740991 and version_token(SCOPE,v)==obj["head"])
        if rec["result"] is not None:return response(rec["result"])
        r=response(call({"tid":SCOPE,"version":v,"target":obj["target"],"request_id":obj["request_id"]}))
        if r["status"] in ("applied","already_actor","stale"):
            if "target" in r:require(r["target"]==obj["target"],"owner_turn_unavailable")
            rec["result"]=r;write_record(root,p,rec)
        return r
