import hashlib,os,sqlite3,stat
from pathlib import Path
from owner_turn57 import SCOPE,need,TurnError
from coordinator57 import Head,Request,execute,CONTINUATION
from idle57 import local_idle,private_dir
STATE=Path("/home/kali/.local/state/mt-pg-inbox-managed-stage34e")
REV=STATE/"owner-turn57-revocations"
SIGN=Path("/home/kali/.config/bridge-task-sign56")
def read_reservation(root,mid):
    # Read-only local evidence; never sign DB-returned text.
    root=Path(root);private_dir(root)
    p=root/"reservations.sqlite";s=p.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600,"unsafe_reservations")
    with sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=2) as cn:
        cn.execute("PRAGMA query_only=ON")
        scope=cn.execute("SELECT tid FROM scope").fetchall()
        need(scope==[(SCOPE,)],"reservation_scope")
        row=cn.execute("SELECT seq,ver,body_hash,wire FROM reservations WHERE mid=?",(mid,)).fetchone()
    return row
class Adapter:
    # catalog and runtime are local trusted modules, not user-controlled.
    def __init__(self,catalog,runtime,root=STATE,sign=SIGN,reservation=None):
        self.cat=catalog;self.rt=runtime;self.root=Path(root);self.rev=self.root/"owner-turn57-revocations"
        self.reservation=reservation or (lambda mid:read_reservation(sign,mid))
    def head(self,tid):
        need(tid==SCOPE,"scope_mismatch")
        cg=self.rt.cg_db()
        def detail():
            d=cg._one("SELECT bridge.cg_managed_detail(%s) AS result",(tid,))
            need(type(d) is dict and d.get("registered") is True and d.get("task_id")==tid,"not_registered")
            return d
        d=detail()
        v=self.cat.view(tid,0,1)
        from bridge_pg.compat import version_token
        need(v.get("head")==version_token(tid,d["version"]),"head_race")
        seq=d["last_seq"];hist=v["history"]
        if seq==0:
            need(not hist,"head_race")
            mid=kind=delivery=None;returned=False
        else:
            need(type(hist) is list and len(hist)==1 and hist[0]["seq"]==seq,"head_race")
            m=hist[0];mid=m["message_id"];kind=m["kind"];returned=m["returned"]
            receipt=self.cat.delivery_status(tid,seq)
            need(type(receipt) is dict and receipt.get("status") in ("pending","leased","delivered","cancelled"),"receipt_unavailable")
            delivery=receipt["status"]
            if kind=="task":
                need(m["from"]=="chatgpt" and m["to"]=="mt","task_identity")
            if kind=="report":
                need(m["from"]=="mt" and m["to"]=="chatgpt","report_identity")
        d2=detail()
        keys=("task_id","version","last_seq","next_actor","state","registered","dispatch_enabled","policy_version")
        need(all(d.get(k)==d2.get(k) for k in keys),"head_race")
        return Head(tid,d["version"],d["next_actor"],seq,mid,kind,delivery,returned,d["state"]=="active",d["dispatch_enabled"])
    def idle(self,tid,h):
        if not local_idle(self.root,self.rev,tid,h):return False
        if h.kind=="task" and h.delivery in ("pending","cancelled"):
            # A DB row alone is never evidence of an authorized local task.
            self.checked_wire(tid,h.mid,h.seq)
        return True
    def checked_wire(self,tid,mid,seq,expected_body=None,expected_version=None):
        row=self.reservation(mid)
        need(row is not None and len(row)==4 and row[0]==seq,"reservation_missing")
        _,ver,body_hash,wire=row
        need(type(ver) is int and ver>=0 and type(wire) is str and wire.startswith("HPG1 "),"reservation_invalid")
        if expected_body is not None:
            need(body_hash==hashlib.sha256(expected_body.encode("utf8")).hexdigest() and ver==expected_version,"reservation_conflict")
        m=self.cat.read_message(tid,seq,mid)
        need(m.get("seq")==seq and m.get("message_id")==mid and m.get("from")=="chatgpt" and m.get("to")=="mt" and m.get("kind")=="task" and m.get("body")==wire,"wire_mismatch")
        return m
    def cancel(self,tid,mid,version):
        # caller already persisted revocation under actual inbox.lock
        r=self.cat.store.return_turn(tid,mid,"chatgpt",version)
        need(type(r) is dict,"cancel_unconfirmed")
        return {"tid":r.get("task_id"),"mid":r.get("message_id"),"returned":r.get("returned"),"next_actor":r.get("next_actor"),"version":r.get("version")}
    def send(self,tid,mid,body,version):
        need(tid==SCOPE and body==CONTINUATION and mid.startswith("owner57-"),"owner_send_scope")
        r=self.rt.send(tid,mid,body,version)
        need(type(r) is dict and type(r.get("created")) is bool,"send_unconfirmed")
        return {"tid":r.get("task_id"),"mid":r.get("message_id"),"seq":r.get("seq"),"version":r.get("version"),"sender":r.get("sender"),"recipient":r.get("recipient"),"returned":r.get("returned")}
    def status(self,tid,mid,version):
        r=self.rt.send_status(tid,mid)
        need(type(r) is dict and type(r.get("found")) is bool,"send_status_invalid")
        if not r["found"]:return None
        need(r.get("task_id")==tid and r.get("message_id")==mid and r.get("sender")=="chatgpt" and r.get("kind")=="task" and r.get("returned") is False,"send_receipt_invalid")
        m=self.checked_wire(tid,mid,r["seq"],CONTINUATION,version)
        need(m.get("returned") is False,"send_receipt_returned")
        return {"tid":tid,"mid":mid,"seq":r["seq"],"sender":"chatgpt","recipient":"agent","returned":False}
    def run(self,r):
        return execute(r,lock_path=self.root/"inbox.lock",revocations=self.rev,
                       read_head=self.head,local_idle=self.idle,cancel=self.cancel,
                       send=self.send,send_status=lambda t,m:self.status(t,m,r.version))
