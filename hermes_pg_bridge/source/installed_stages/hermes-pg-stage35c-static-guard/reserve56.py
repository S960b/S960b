import hashlib,os,sqlite3
from contextlib import closing,contextmanager
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from wire56 import payload,sign_wire
class Reservation:
    def __init__(self,path,sk,tid):
        self.path,self.sk,self.tid=path,sk,tid
        self.pin=hashlib.sha256(sk.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).hexdigest()
    @contextmanager
    def tx(self):
        # fixture path is trusted; never recreate missing store during normal use.
        with closing(sqlite3.connect(self.path.resolve().as_uri()+"?mode=rw",uri=True,isolation_level=None,timeout=5)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:db.execute("ROLLBACK")
                raise
    def initialize(self):
        fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd)
        with self.tx() as db:
            db.execute("CREATE TABLE scope(tid TEXT NOT NULL,pin TEXT NOT NULL)")
            db.execute("INSERT INTO scope VALUES(?,?)",(self.tid,self.pin))
            db.execute("CREATE TABLE reservations(mid TEXT PRIMARY KEY,seq INTEGER UNIQUE NOT NULL,ver INTEGER UNIQUE NOT NULL,body_hash TEXT NOT NULL,wire TEXT NOT NULL)")
    def reserve(self,mid,body,expected_version,head):
        # Validate original caller inputs before using any head metadata.
        payload(self.tid,mid,1,expected_version,body)
        h=hashlib.sha256(body.encode("utf8")).hexdigest()
        with self.tx() as db:
            if db.execute("SELECT tid,pin FROM scope").fetchall()!=[(self.tid,self.pin)]:
                raise ValueError("scope")
            old=db.execute("SELECT seq,ver,body_hash,wire FROM reservations WHERE mid=?",(mid,)).fetchone()
            if old:
                seq,ver,old_hash,wire=old
                if (ver,old_hash)!=(expected_version,h):raise ValueError("message conflict")
                # Detect local accidental wire corruption; deterministic Ed25519, no DB body.
                if wire!=sign_wire(self.sk,self.tid,mid,seq,ver,body):raise ValueError("stored wire mismatch")
                return seq,wire
            if type(head) is not dict or head.get("task_id")!=self.tid or head.get("state")!="active" or head.get("next_actor")!="chatgpt" or head.get("dispatch_enabled") is not True:
                raise ValueError("head")
            ver,seq0=head.get("version"),head.get("last_seq")
            if type(ver) is not int or ver!=expected_version or type(seq0) is not int or not 0<=seq0<2**53-1:raise ValueError("head counters")
            seq=seq0+1
            hi=db.execute("SELECT max(seq),max(ver) FROM reservations").fetchone()
            if hi[0] is not None and (seq<=hi[0] or ver<=hi[1]):raise ValueError("counter regression or unresolved reservation")
            wire=sign_wire(self.sk,self.tid,mid,seq,ver,body)
            db.execute("INSERT INTO reservations VALUES(?,?,?,?,?)",(mid,seq,ver,h,wire))
            return seq,wire
